"""World data backups with generational retention and failure backoff.

Backups follow the active world ``kind`` from ``world_save`` when possible:

- ``file`` — copy the save as-is (no recompress; a game ``.zip`` stays one zip)
- ``directory`` — zip folder contents (same layout as world download/upload)

Explicit ``backup_paths`` remain the fallback when no named world exists yet,
and legacy ``*.tar.gz`` snapshots of those roots are still restorable.
"""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .disk import ensure_free_mb, path_total_bytes
from .world_save import (
    KIND_DIRECTORY,
    KIND_FILE,
    SCOPE_BACKUP_SOURCES,
    SCOPE_MISSING,
    SCOPE_NAMED_PATH,
    ActiveWorld,
    apply_world_upload,
    backup_name_suffix,
    clear_world_artifact,
    effective_world_kind,
    infer_world_kind,
    install_zip_into_directory,
    write_world_backup,
)

LOG = logging.getLogger("game_server.backup")

# Three archive families under backup_dir — each pruned by its own rule:
#   backup-*       scheduled/manual rolling history (generational retention profile)
#   pre-update-*   newest snapshot per world label from a game-code update
#   pre-restore-*  safety copies before restore/empty-world (age window)
ROTATION_GLOB = "backup-*"
PRE_UPDATE_GLOB = "pre-update-*"
PRE_RESTORE_GLOB = "pre-restore-*"
# New by-kind names keep the original save suffix (often .zip) or .zip for
# folder worlds. Legacy snapshots end in .tar.gz.
_ARCHIVE_NAME_RE = re.compile(
    r"^(backup|pre-update|pre-restore)-[A-Za-z0-9._-]+$"
)
# backup-20260826T170225Z-<rest>
_STAMPED_ARCHIVE_RE = re.compile(
    r"^(?:backup|pre-update|pre-restore)-\d{8}T\d{6}Z-(.+)$"
)
# Legacy unlabeled names: reason + suffix only (schedule.tar.gz, schedule.zip).
_UNLABELED_REASON_RE = re.compile(
    r"^(schedule|scheduled|manual|safety|pre-update)(?:\.[A-Za-z0-9.]+)?$",
    re.IGNORECASE,
)
# Legacy pre-update archives used the rotation prefix with a -pre-update reason.
_LEGACY_PRE_UPDATE_RE = re.compile(r"^backup-.+-pre-update(\.tar\.gz)?$")
_LEGACY_TAR_GZ_RE = re.compile(r"\.tar\.gz$", re.IGNORECASE)

# Pending-restore token: wipe world sources and let the game create a fresh world.
EMPTY_WORLD = "__empty__"

_NAMED_SCOPES = frozenset({SCOPE_NAMED_PATH, SCOPE_MISSING})

BACKUP_SUCCESS = "success"
BACKUP_SKIPPED = "skipped"
BACKUP_FAILED = "failed"


@dataclass(frozen=True)
class BackupResult:
    """Explicit outcome so callers can tell success / nothing-to-do / failed."""

    status: str
    path: Path | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {BACKUP_SUCCESS, BACKUP_SKIPPED}


@dataclass
class RetentionPolicy:
    """Cascading retention: daily → weekly → monthly (optional yearly).

    Also carries how long pre-restore safety copies are kept (days).
    """

    keep_recent: int = 0
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 12
    keep_yearly: int = 0
    pre_restore_keep_days: int = 7
    profile: str = "standard"

    def describe(self) -> str:
        parts = [
            f"{self.keep_daily} daily",
            f"{self.keep_weekly} weekly",
            f"{self.keep_monthly} monthly",
        ]
        if self.keep_yearly:
            parts.append(f"{self.keep_yearly} yearly")
        parts.append(f"{self.pre_restore_keep_days}d pre-restore")
        return f"{self.profile} ({', '.join(parts)})"


# Simple UX: one named profile instead of tuning each tier.
# Standard matches the common NAS pattern: dailies for a week, weeklies for a
# month, then monthlies for about a year. Pre-restore safety copies use the
# same profile: minimal=1d, standard=7d, extended=30d.
RETENTION_PROFILES: dict[str, RetentionPolicy] = {
    "minimal": RetentionPolicy(
        keep_daily=3,
        keep_weekly=2,
        keep_monthly=3,
        keep_yearly=0,
        pre_restore_keep_days=1,
        profile="minimal",
    ),
    "standard": RetentionPolicy(
        keep_daily=7,
        keep_weekly=4,
        keep_monthly=12,
        keep_yearly=0,
        pre_restore_keep_days=7,
        profile="standard",
    ),
    "extended": RetentionPolicy(
        keep_daily=7,
        keep_weekly=8,
        keep_monthly=24,
        keep_yearly=2,
        pre_restore_keep_days=30,
        profile="extended",
    ),
}


def backup_generation_key(name: str) -> str:
    """World identity used to group generational retention.

    Labeled archives (``backup-…-0.world.gzip``) keep separate history so
    switching world slots or names does not prune the other world's snapshots.
    Unlabeled / legacy names share one pool.
    """

    match = _STAMPED_ARCHIVE_RE.match(name)
    if not match:
        return ""
    rest = match.group(1)
    if _UNLABELED_REASON_RE.match(rest):
        return ""
    if "-" not in rest:
        return rest
    head, tail = rest.split("-", 1)
    if _UNLABELED_REASON_RE.fullmatch(head):
        return tail
    return rest


def _safe_world_label(label: str | None) -> str:
    text = str(label or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return safe


def retention_from_profile(name: str | None) -> RetentionPolicy:
    key = (name or "standard").strip().lower()
    if key not in RETENTION_PROFILES:
        LOG.warning("Unknown backup_retention %r; using standard", name)
        key = "standard"
    return RETENTION_PROFILES[key]


@dataclass(frozen=True)
class _BackupSubject:
    """What create/clear/restore should operate on."""

    kind: str  # file | directory | roots
    path: Path | None
    paths: list[Path]
    active: ActiveWorld | None
    named: bool


class BackupManager:
    def __init__(
        self,
        backup_dir: str | Path,
        sources: list[str | Path],
        *,
        world_locator: Callable[[], ActiveWorld] | None = None,
        data_dir: str | Path | None = None,
        interval_minutes: int = 1440,
        enabled: bool = True,
        retention: RetentionPolicy | None = None,
        min_source_bytes: int = 1024,
        min_free_disk_mb: int = 512,
        max_backoff_minutes: int = 1440,
    ) -> None:
        self.backup_dir = Path(backup_dir)
        self.sources = [Path(s) for s in sources]
        self._world_locator = world_locator
        self.data_dir = Path(data_dir) if data_dir is not None else None
        # Create cadence for scheduled backups (HA exposes retention profile only;
        # default daily so create rate matches keep_daily slots).
        self.interval_seconds = max(0, interval_minutes) * 60
        self.enabled = enabled
        self.retention = retention or RetentionPolicy()
        self.min_source_bytes = max(0, min_source_bytes)
        self.min_free_disk_mb = max(0, min_free_disk_mb)
        self.max_backoff_seconds = max(self.interval_seconds, max_backoff_minutes * 60)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_backup_at: float | None = None
        self.last_backup_path: str | None = None
        self.last_error: str | None = None
        self.last_skip_reason: str | None = None
        self.backup_count = 0
        self.consecutive_failures = 0
        self.next_eligible_at: float = 0.0
        self._on_failure: Callable[[str], None] | None = None
        self._pre_backup: Callable[[], None] | None = None
        self._lock = threading.Lock()

    def set_failure_callback(self, callback: Callable[[str], None] | None) -> None:
        """Optional hook for HA notifications / status when backups fail."""

        self._on_failure = callback

    def set_pre_backup_callback(self, callback: Callable[[], None] | None) -> None:
        """Optional hook to flush game saves (stdin) before copying files."""

        self._pre_backup = callback

    def start(self) -> None:
        if not self.enabled or self.interval_seconds <= 0:
            LOG.info("Periodic backups disabled")
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="backup", daemon=True)
        self._thread.start()
        LOG.info(
            "Periodic backups every %s minutes into %s (generational retention)",
            self.interval_seconds // 60,
            self.backup_dir,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _current_delay_seconds(self) -> int:
        if self.consecutive_failures <= 0:
            return self.interval_seconds
        # Exponential backoff: interval * 2^failures, capped.
        delay = self.interval_seconds * (2 ** min(self.consecutive_failures, 8))
        return int(min(self.max_backoff_seconds, delay))

    def _loop(self) -> None:
        # First backup after one base interval, not immediately on boot.
        delay = self.interval_seconds
        while not self._stop.wait(delay):
            try:
                result = self.create_backup(reason="schedule")
                if result is None and self.last_skip_reason:
                    self._register_failure(self.last_skip_reason)
                elif result is None and self.last_error:
                    self._register_failure(self.last_error)
                else:
                    self.consecutive_failures = 0
            except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
                # Disk/archive problems are environmental; back off and keep trying.
                self.last_error = str(exc)
                self._register_failure(str(exc))
                LOG.exception("Scheduled backup failed")
            # Other exceptions are backup-code bugs — let the backup thread die
            # so they are not hidden behind endless "scheduled backup failed".
            delay = self._current_delay_seconds()
            LOG.info("Next backup attempt in %s minutes", max(1, delay // 60))

    def _register_failure(self, reason: str) -> None:
        self.consecutive_failures += 1
        self.next_eligible_at = time.time() + self._current_delay_seconds()
        LOG.warning(
            "Backup issue (%s). consecutive_failures=%s; backing off",
            reason,
            self.consecutive_failures,
        )
        if self._on_failure is not None:
            try:
                self._on_failure(reason)
            except Exception:  # noqa: BLE001
                LOG.exception("Backup failure callback failed")

    def _locate_active(self) -> ActiveWorld | None:
        if self._world_locator is None:
            return None
        try:
            return self._world_locator()
        except Exception:  # noqa: BLE001
            LOG.exception("world_locator failed; falling back to backup_paths")
            return None

    def _subject(self) -> _BackupSubject | None:
        """Prefer the named world artifact; else fall back to backup_paths."""

        active = self._locate_active()
        if active is not None:
            kind = effective_world_kind(active)
            if (
                active.scope in _NAMED_SCOPES
                and kind in {KIND_FILE, KIND_DIRECTORY}
                and active.path
            ):
                path = Path(active.path)
                if self._path_has_data(path):
                    return _BackupSubject(
                        kind=kind,
                        path=path,
                        paths=[path],
                        active=active,
                        named=True,
                    )
            if active.scope == SCOPE_BACKUP_SOURCES and active.sources:
                paths = [Path(p) for p in active.sources if Path(p).exists()]
                subject = self._subject_from_paths(paths, active=active, named=False)
                if subject is not None:
                    return subject

        existing = [s for s in self.sources if s.exists()]
        return self._subject_from_paths(existing, active=active, named=False)

    @staticmethod
    def _path_has_data(path: Path) -> bool:
        if not path.exists():
            return False
        if path.is_file():
            try:
                return path.stat().st_size > 0
            except OSError:
                return False
        if path.is_dir():
            return path_total_bytes(path) > 0
        return False

    def _subject_from_paths(
        self,
        paths: list[Path],
        *,
        active: ActiveWorld | None,
        named: bool,
    ) -> _BackupSubject | None:
        existing = [p for p in paths if self._path_has_data(p)]
        if not existing:
            return None
        if len(existing) == 1:
            kind = infer_world_kind(existing[0])
            if kind in {KIND_FILE, KIND_DIRECTORY}:
                return _BackupSubject(
                    kind=kind,
                    path=existing[0],
                    paths=existing,
                    active=active,
                    named=named,
                )
        return _BackupSubject(
            kind="roots",
            path=None,
            paths=existing,
            active=active,
            named=named,
        )

    def source_bytes(self) -> int:
        subject = self._subject()
        if subject is None:
            return 0
        if subject.path is not None:
            return path_total_bytes(subject.path)
        return sum(path_total_bytes(path) for path in subject.paths)

    def sources_have_any_data(self) -> bool:
        """True if any non-empty world artifact / backup source exists.

        Used before destructive world ops. Unlike ``validate_sources``, this does
        not apply ``min_source_bytes`` — a small save is still a save.
        """

        return self._subject() is not None

    def validate_sources(self) -> tuple[bool, str | None]:
        subject = self._subject()
        if subject is None:
            return False, "no world save or backup sources exist yet"
        total = self.source_bytes()
        if self.min_source_bytes and total < self.min_source_bytes:
            return (
                False,
                f"world save only {total} bytes (< {self.min_source_bytes}); "
                "refusing empty/tiny world",
            )
        return True, None

    def _archive_dest(
        self,
        *,
        reason: str,
        outside_rotation: bool,
        suffix: str,
        world_label: str | None = None,
    ) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_reason = re.sub(r"[^A-Za-z0-9._-]+", "-", reason).strip("-") or "manual"
        safe_label = _safe_world_label(world_label)
        if safe_label:
            if outside_rotation:
                name = f"pre-restore-{stamp}-{safe_reason}-{safe_label}"
            elif reason == "pre-update":
                name = f"pre-update-{stamp}-{safe_label}"
            else:
                name = f"backup-{stamp}-{safe_reason}-{safe_label}"
        elif outside_rotation:
            name = f"pre-restore-{stamp}-{safe_reason}{suffix}"
        elif reason == "pre-update":
            name = f"pre-update-{stamp}{suffix}"
        else:
            name = f"backup-{stamp}-{safe_reason}{suffix}"
        return self.backup_dir / name

    def create_backup(
        self,
        reason: str = "manual",
        *,
        outside_rotation: bool = False,
        allow_tiny: bool = False,
        prune_after: bool = True,
    ) -> Path | None:
        """Create a by-kind world backup under backup_dir.

        Returns the archive path on success, otherwise ``None``. Prefer
        ``create_backup_result`` when the caller must distinguish skip vs fail.
        """

        result = self.create_backup_result(
            reason,
            outside_rotation=outside_rotation,
            allow_tiny=allow_tiny,
            prune_after=prune_after,
        )
        return result.path if result.status == BACKUP_SUCCESS else None

    def create_backup_result(
        self,
        reason: str = "manual",
        *,
        outside_rotation: bool = False,
        allow_tiny: bool = False,
        prune_after: bool = True,
    ) -> BackupResult:
        """Create a by-kind world backup; return success / skipped / failed.

        ``outside_rotation=True`` writes a ``pre-restore-*`` safety copy
        (age-pruned via the retention profile's ``pre_restore_keep_days``).

        ``reason="pre-update"`` writes ``pre-update-*`` (newest per world label
        is kept).

        ``allow_tiny=True`` skips ``min_source_bytes`` (required for pre-wipe
        safety copies — a small world is still worth keeping).

        ``prune_after=False`` defers retention pruning (used while a live-world
        wipe still depends on the archive that was just created).
        """

        if not self.enabled and not outside_rotation and reason != "pre-update":
            return BackupResult(BACKUP_SKIPPED, reason="backups disabled")
        self.last_skip_reason = None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        if self._pre_backup is not None and reason == "schedule":
            try:
                self._pre_backup()
            except Exception:
                LOG.exception("Pre-backup flush failed; continuing with copy")

        ok, available = ensure_free_mb(self.backup_dir, self.min_free_disk_mb)
        if not ok:
            self.last_error = (
                f"insufficient disk space ({available:.1f} MiB free)"
                if available is not None
                else "insufficient disk space"
            )
            return BackupResult(BACKUP_FAILED, reason=self.last_error)

        if allow_tiny:
            if not self.sources_have_any_data():
                self.last_skip_reason = "no world data to back up"
                self.last_error = self.last_skip_reason
                LOG.warning("Skipping backup: %s", self.last_skip_reason)
                return BackupResult(BACKUP_SKIPPED, reason=self.last_skip_reason)
        else:
            valid, reason_text = self.validate_sources()
            if not valid:
                self.last_skip_reason = reason_text
                self.last_error = reason_text
                LOG.warning("Skipping backup: %s", reason_text)
                status = (
                    BACKUP_FAILED
                    if self.sources_have_any_data()
                    else BACKUP_SKIPPED
                )
                return BackupResult(status, reason=reason_text)

        subject = self._subject()
        if subject is None:
            self.last_skip_reason = "no world data to back up"
            self.last_error = self.last_skip_reason
            return BackupResult(BACKUP_SKIPPED, reason=self.last_skip_reason)

        if subject.kind in {KIND_FILE, KIND_DIRECTORY} and subject.path is not None:
            suffix = backup_name_suffix(subject.path, subject.kind)
            world_label = None
            if subject.active is not None and subject.active.label:
                world_label = str(subject.active.label)
            elif subject.named:
                world_label = subject.path.name
            archive = self._archive_dest(
                reason=reason,
                outside_rotation=outside_rotation,
                suffix=suffix,
                world_label=world_label,
            )
            LOG.info(
                "Creating by-kind backup %s from %s (kind=%s)",
                archive.name,
                subject.path,
                subject.kind,
            )
            write_world_backup(subject.path, subject.kind, archive)
        else:
            archive = self._archive_dest(
                reason=reason, outside_rotation=outside_rotation, suffix=".zip"
            )
            LOG.info("Creating roots zip backup %s from %s", archive.name, subject.paths)
            self._write_roots_zip(subject.paths, archive)

        # Reject accidental empty archives (never keep a useless file).
        # File copies of tiny saves may be <64 bytes when allow_tiny; only
        # reject truly empty outputs.
        if archive.stat().st_size < 1:
            archive.unlink(missing_ok=True)
            self.last_skip_reason = "archive was empty after creation"
            self.last_error = self.last_skip_reason
            return BackupResult(BACKUP_FAILED, reason=self.last_error)
        if not allow_tiny and archive.stat().st_size < 64 and subject.kind != KIND_FILE:
            archive.unlink(missing_ok=True)
            self.last_skip_reason = "archive was empty/tiny after creation"
            self.last_error = self.last_skip_reason
            return BackupResult(BACKUP_FAILED, reason=self.last_error)

        self.last_backup_at = time.time()
        self.last_backup_path = str(archive)
        self.backup_count += 1
        self.last_error = None
        self.last_skip_reason = None
        if prune_after:
            self.apply_retention()
        return BackupResult(BACKUP_SUCCESS, path=archive)

    @staticmethod
    def _write_roots_zip(paths: list[Path], dest: Path) -> None:
        """Zip one or more fallback backup roots (top-level folder per source)."""

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.partial")
        try:
            with zipfile.ZipFile(tmp, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for source in paths:
                    if source.is_file():
                        zf.write(source, source.name)
                        continue
                    if not source.is_dir():
                        continue
                    for child in sorted(source.rglob("*")):
                        if not child.is_file() or child.is_symlink():
                            continue
                        arc = f"{source.name}/{child.relative_to(source).as_posix()}"
                        zf.write(child, arc)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

    def create_safety_backup(self, reason: str = "safety") -> Path | None:
        """Pre-wipe safety copy: any non-empty world data, no immediate prune."""

        return self.create_backup(
            reason=reason,
            outside_rotation=True,
            allow_tiny=True,
            prune_after=False,
        )

    def apply_retention(self) -> None:
        """Delete archives that fall outside each family's retention rule.

        This is the only intentional bulk-delete of backup archives.
        """

        self._prune_scheduled_generational()
        self._prune_pre_update_keep_newest()
        self._prune_pre_restore_by_age()

    def _iter_family(self, glob_pat: str) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        found: list[Path] = []
        for path in self.backup_dir.glob(glob_pat):
            if not path.is_file():
                continue
            if not _ARCHIVE_NAME_RE.fullmatch(path.name):
                continue
            found.append(path)
        return found

    def _prune_scheduled_generational(self) -> None:
        archives = sorted(
            self._iter_family(ROTATION_GLOB),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Legacy pre-update files are managed by the pre-update keeper, not here.
        archives = [p for p in archives if not _LEGACY_PRE_UPDATE_RE.match(p.name)]
        groups: dict[str, list[Path]] = {}
        for archive in archives:
            groups.setdefault(backup_generation_key(archive.name), []).append(archive)
        keep: set[Path] = set()
        for group in groups.values():
            keep |= select_generational_keepers(group, self.retention)
        self._unlink_except(archives, keep, label="scheduled retention")

    def _prune_pre_update_keep_newest(self) -> None:
        archives = self.list_pre_update_archives()
        if len(archives) <= 1:
            return
        # Newest last from list_*; keep one newest per world label so switching
        # slots does not discard the other world's pre-update snapshot.
        groups: dict[str, list[Path]] = {}
        for archive in archives:
            groups.setdefault(backup_generation_key(archive.name), []).append(archive)
        keep = {group[-1] for group in groups.values()}
        self._unlink_except(archives, keep, label="pre-update keep-one")

    def _prune_pre_restore_by_age(self) -> None:
        archives = self.list_pre_restore_archives()
        keep_days = max(0, int(self.retention.pre_restore_keep_days))
        if keep_days <= 0:
            self._unlink_except(archives, set(), label="pre-restore age")
            return
        cutoff = time.time() - (keep_days * 86400)
        keep = {p for p in archives if p.stat().st_mtime >= cutoff}
        self._unlink_except(archives, keep, label="pre-restore age")

    def _unlink_except(
        self, archives: list[Path], keep: set[Path], *, label: str
    ) -> None:
        for stale in archives:
            if stale in keep:
                continue
            try:
                stale.unlink()
                LOG.info("Pruned old backup %s (%s)", stale.name, label)
            except OSError:
                LOG.warning("Failed to prune %s", stale)

    def list_archives(self) -> list[Path]:
        """Return scheduled/manual backup archives oldest → newest."""

        archives = [
            p
            for p in self._iter_family(ROTATION_GLOB)
            if not _LEGACY_PRE_UPDATE_RE.match(p.name)
        ]
        return sorted(archives, key=lambda p: p.stat().st_mtime)

    def list_pre_update_archives(self) -> list[Path]:
        found = self._iter_family(PRE_UPDATE_GLOB)
        found.extend(
            p
            for p in self._iter_family(ROTATION_GLOB)
            if _LEGACY_PRE_UPDATE_RE.match(p.name)
        )
        # De-dupe while preserving mtime sort below.
        unique = {p.resolve(): p for p in found}
        return sorted(unique.values(), key=lambda p: p.stat().st_mtime)

    def list_pre_restore_archives(self) -> list[Path]:
        return sorted(
            self._iter_family(PRE_RESTORE_GLOB),
            key=lambda p: p.stat().st_mtime,
        )

    def list_restorable_archives(self) -> list[dict[str, Any]]:
        """Archives the UI may offer for restore (all three families)."""

        items: list[dict[str, Any]] = []
        families = (
            (self.list_archives(), "backup"),
            (self.list_pre_update_archives(), "pre-update"),
            (self.list_pre_restore_archives(), "pre-restore"),
        )
        for paths, kind in families:
            for path in paths:
                try:
                    st = path.stat()
                except OSError:
                    continue
                items.append(
                    {
                        "name": path.name,
                        "kind": kind,
                        "bytes": st.st_size,
                        "mtime": st.st_mtime,
                        "generation": backup_generation_key(path.name),
                    }
                )
        items.sort(key=lambda item: float(item["mtime"]), reverse=True)
        return items

    def resolve_archive(self, name: str) -> Path | None:
        """Resolve a backup basename under backup_dir; reject traversal."""

        raw = str(name or "").strip()
        if not raw or not _ARCHIVE_NAME_RE.fullmatch(raw):
            return None
        if "/" in raw or "\\" in raw or raw in {".", ".."}:
            return None
        root = self.backup_dir.resolve()
        path = (self.backup_dir / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path if path.is_file() else None

    def _require_safety_backup_before_wipe(self, prior_safety_backup: Path | None) -> None:
        """Hard gate: never delete live world data without a real safety archive."""

        if not self.sources_have_any_data():
            return
        if prior_safety_backup is None:
            raise RuntimeError(
                "refusing to delete live world data without a successful safety backup"
            )
        path = Path(prior_safety_backup)
        if not path.is_file():
            raise RuntimeError(
                f"refusing to delete live world data; safety backup missing: {path.name}"
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"refusing to delete live world data; cannot stat safety backup: {exc}"
            ) from exc
        if size < 1:
            raise RuntimeError(
                f"refusing to delete live world data; safety backup is empty: {path.name}"
            )
        # Must live under backup_dir (no arbitrary path as a "safety" token).
        root = self.backup_dir.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                "refusing to delete live world data; safety backup is outside backup_dir"
            ) from exc

    def _clear_source_contents(self) -> list[str]:
        """Remove world *data* while keeping existing directory inodes.

        Why not delete + recreate the source directory? Backup roots such as
        ``/data/world`` are prepared once for the non-root game user
        (``gameserver``). If the supervisor (often root) ``rmtree``s that path
        and ``mkdir``s a replacement, the new inode is root-owned and the game
        child can fail with permission errors on the next start. Emptying
        children in place keeps the original ownership and mode.

        File sources are unlinked (parent directory is left alone). Missing
        sources are left missing — do not recreate them here.
        """

        cleared: list[str] = []
        for source in self.sources:
            if not source.exists():
                continue
            if source.is_file():
                LOG.info("Removing current world file: %s", source)
                source.unlink()
                cleared.append(str(source))
                continue
            if not source.is_dir():
                continue
            LOG.info("Clearing world data in place (keep dir ownership): %s", source)
            for child in list(source.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            cleared.append(str(source))
        return cleared

    def clear_world_sources(
        self, *, prior_safety_backup: Path | None
    ) -> dict[str, Any]:
        """Wipe live world data so the next start is a fresh world.

        Prefers clearing the named world artifact (by kind). Falls back to
        emptying ``backup_paths`` when no named save is present.

        Does not stop/start the game process — caller owns lifecycle.
        If any world data exists, ``prior_safety_backup`` must be a successful
        archive under backup_dir (refuse otherwise).
        """

        with self._lock:
            self._require_safety_backup_before_wipe(prior_safety_backup)
            subject = self._subject()
            if (
                subject is not None
                and subject.named
                and subject.active is not None
                and self.data_dir is not None
            ):
                result = clear_world_artifact(subject.active, data_dir=self.data_dir)
                result["safety_backup"] = (
                    prior_safety_backup.name if prior_safety_backup is not None else None
                )
                result["sources"] = [str(s) for s in self.sources]
                return result
            removed = self._clear_source_contents()
        return {
            "ok": True,
            "empty": True,
            "cleared": removed,
            "sources": [str(s) for s in self.sources],
            "safety_backup": (
                prior_safety_backup.name if prior_safety_backup is not None else None
            ),
        }

    def restore_archive(
        self,
        archive: str | Path,
        *,
        prior_safety_backup: Path | None,
    ) -> dict[str, Any]:
        """Restore a backup over the live world.

        - Legacy ``*.tar.gz`` — extract into ``backup_paths`` parents (old layout)
        - By-kind backups — apply onto the active world via the same path as
          world upload (file copy as-is, or zip extract into a folder save)

        Does not stop/start the game process — caller owns lifecycle.
        If any world data exists, ``prior_safety_backup`` must be a successful
        archive under backup_dir (refuse otherwise).
        """

        path = self.resolve_archive(str(archive)) if not isinstance(archive, Path) else archive
        if path is None or not path.is_file():
            raise FileNotFoundError(f"backup archive not found: {archive}")
        path = path.resolve()
        root = self.backup_dir.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"archive outside backup dir: {archive}") from exc

        with self._lock:
            self._require_safety_backup_before_wipe(prior_safety_backup)
            if _LEGACY_TAR_GZ_RE.search(path.name):
                return self._restore_legacy_tar(
                    path, prior_safety_backup=prior_safety_backup
                )
            return self._restore_by_kind(
                path, prior_safety_backup=prior_safety_backup
            )

    def _restore_by_kind(
        self,
        path: Path,
        *,
        prior_safety_backup: Path | None,
    ) -> dict[str, Any]:
        active = self._locate_active()
        data_dir = self.data_dir
        if (
            active is not None
            and data_dir is not None
            and effective_world_kind(active) in {KIND_FILE, KIND_DIRECTORY}
            and active.path
            and active.scope in _NAMED_SCOPES
        ):
            backup_label = backup_generation_key(path.name)
            current_label = _safe_world_label(active.label)
            if backup_label and current_label and backup_label != current_label:
                raise RuntimeError(
                    f"backup {path.name} is for {backup_label}; the active world "
                    f"is {current_label}. Switch World slot/name to match before restoring."
                )
            LOG.info(
                "Restoring by-kind backup %s onto %s (kind=%s)",
                path.name,
                active.path,
                effective_world_kind(active),
            )
            result = apply_world_upload(
                active, path, data_dir=data_dir, strip_wrapper=False
            )
            result["archive"] = path.name
            result["safety_backup"] = (
                prior_safety_backup.name if prior_safety_backup is not None else None
            )
            return result

        # Fallback: extract a zip into a single directory backup root.
        dirs = [s for s in self.sources if s.is_dir() or not s.exists()]
        files = [s for s in self.sources if s.is_file()]
        if len(self.sources) == 1 and (
            self.sources[0].is_file()
            or infer_world_kind(self.sources[0]) == KIND_FILE
        ):
            target = self.sources[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.is_dir():
                shutil.rmtree(target)
            tmp = target.with_name(f".{target.name}.restore-tmp")
            try:
                shutil.copyfile(path, tmp)
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
            return {
                "ok": True,
                "mode": "replace_file",
                "archive": path.name,
                "path": str(target),
                "safety_backup": (
                    prior_safety_backup.name if prior_safety_backup is not None else None
                ),
            }
        if len(dirs) == 1 and not files:
            target = dirs[0]
            target.mkdir(parents=True, exist_ok=True)
            LOG.info("Restoring zip backup %s into %s", path.name, target)
            install_zip_into_directory(path, target, strip_wrapper=True)
            return {
                "ok": True,
                "mode": "extract_zip_into_directory",
                "archive": path.name,
                "path": str(target),
                "safety_backup": (
                    prior_safety_backup.name if prior_safety_backup is not None else None
                ),
            }
        raise RuntimeError(
            "cannot restore by-kind backup without a named world path or a "
            "single backup_paths root"
        )

    def _restore_legacy_tar(
        self,
        path: Path,
        *,
        prior_safety_backup: Path | None,
    ) -> dict[str, Any]:
        # Archives store each source under arcname=source.name (e.g. "world/...").
        # Extract into each source's parent so "world/" lands on the real world root.
        parents = {source.parent.resolve() for source in self.sources}
        if len(parents) != 1:
            raise RuntimeError(
                f"restore requires a single parent for backup sources; got {parents}"
            )
        extract_root = next(iter(parents))

        # Clear contents in place when the source is a directory so we do not
        # replace a gameserver-owned inode with a root-owned one before extract.
        self._clear_source_contents()

        LOG.info("Restoring legacy tar.gz %s into %s", path.name, extract_root)
        with tarfile.open(path, "r:gz") as tar:
            # Python 3.12+: filter='data' blocks unsafe paths when available.
            try:
                tar.extractall(extract_root, filter="data")  # type: ignore[call-arg]
            except TypeError:
                self._extract_safe(tar, extract_root)

        return {
            "ok": True,
            "mode": "legacy_tar_gz",
            "archive": path.name,
            "extract_root": str(extract_root),
            "sources": [str(s) for s in self.sources],
            "safety_backup": (
                prior_safety_backup.name if prior_safety_backup is not None else None
            ),
        }

    @staticmethod
    def _extract_zip_into_directory(
        archive: Path,
        target: Path,
        *,
        strip_wrapper: bool = True,
    ) -> None:
        """Extract zip members under ``target``, rejecting path traversal."""

        from .world_save import _extract_zip_into_directory as extract

        extract(
            archive,
            target,
            strip_wrapper=strip_wrapper,
            wrapper_name=target.name,
        )

    @staticmethod
    def _extract_safe(tar: tarfile.TarFile, extract_root: Path) -> None:
        """Fallback extractor that rejects path traversal members."""

        root = extract_root.resolve()
        for member in tar.getmembers():
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"refusing unsafe tar member: {name}")
            target = (extract_root / name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"refusing tar member outside root: {name}") from exc
            tar.extract(member, extract_root)

    def archive_summary(self) -> dict[str, object]:
        """Count + oldest/newest timestamps for status UI (rotation set only)."""

        archives = self.list_archives()
        if not archives:
            return {
                "count": 0,
                "oldest_at": None,
                "newest_at": None,
                "oldest_name": None,
                "newest_name": None,
            }
        oldest = archives[0]
        newest = archives[-1]
        return {
            "count": len(archives),
            "oldest_at": oldest.stat().st_mtime,
            "newest_at": newest.stat().st_mtime,
            "oldest_name": oldest.name,
            "newest_name": newest.name,
        }

    def to_dict(self) -> dict:
        summary = self.archive_summary()
        archives = self.list_archives()
        return {
            "enabled": self.enabled,
            "backup_dir": str(self.backup_dir),
            "interval_minutes": self.interval_seconds // 60,
            "retention": {
                "profile": self.retention.profile,
                "description": self.retention.describe(),
                "keep_daily": self.retention.keep_daily,
                "keep_weekly": self.retention.keep_weekly,
                "keep_monthly": self.retention.keep_monthly,
                "keep_yearly": self.retention.keep_yearly,
                "pre_restore_keep_days": self.retention.pre_restore_keep_days,
            },
            "pre_update_count": len(self.list_pre_update_archives()),
            "pre_restore_count": len(self.list_pre_restore_archives()),
            "min_source_bytes": self.min_source_bytes,
            "min_free_disk_mb": self.min_free_disk_mb,
            "last_backup_at": self.last_backup_at,
            "last_backup_path": self.last_backup_path,
            "last_error": self.last_error,
            "last_skip_reason": self.last_skip_reason,
            "backup_count": self.backup_count,
            "archive_count": summary["count"],
            "oldest_backup_at": summary["oldest_at"],
            "newest_backup_at": summary["newest_at"],
            "oldest_backup_name": summary["oldest_name"],
            "newest_backup_name": summary["newest_name"],
            "consecutive_failures": self.consecutive_failures,
            "next_delay_minutes": self._current_delay_seconds() // 60,
            "source_bytes": self.source_bytes(),
            "sources": [str(s) for s in self.sources],
            "archives": [p.name for p in archives],
            "restorable": self.list_restorable_archives(),
        }


def select_generational_keepers(
    archives: list[Path],
    policy: RetentionPolicy,
) -> set[Path]:
    """Keep newest recent + one per day/week/month/year slot."""

    keep: set[Path] = set()
    if not archives:
        return keep

    for archive in archives[: max(0, policy.keep_recent)]:
        keep.add(archive)

    def _add_period(fmt: str, limit: int) -> None:
        seen: list[str] = []
        for archive in archives:
            dt = datetime.fromtimestamp(archive.stat().st_mtime, tz=timezone.utc)
            key = dt.strftime(fmt)
            if key in seen:
                continue
            seen.append(key)
            keep.add(archive)
            if len(seen) >= limit:
                break

    _add_period("%Y-%m-%d", policy.keep_daily)
    _add_period("%G-W%V", policy.keep_weekly)
    _add_period("%Y-%m", policy.keep_monthly)
    _add_period("%Y", policy.keep_yearly)
    return keep
