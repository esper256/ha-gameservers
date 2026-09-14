"""Browser/Ingress-friendly log capture and pattern-suggestion helpers.

Goal: tune game log regexes without SSH into Portainer/HAOS containers.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .log_bridge import strip_ansi
from .plugin import GamePlugin

LOG = logging.getLogger("game_server.log_tools")

_TUNING_EXAMPLE_LIMIT = 25
_TUNING_GUESS_LIMIT = 12
_TUNING_CATEGORY_ORDER = (
    "ready",
    "game_version",
    "player_join",
    "player_leave",
    "player_count",
    "players_empty",
    "version_mismatch",
)
_TUNING_HOW_TO_READ = (
    "configured: plugin log_patterns that can change supervisor state, with "
    "matching log lines from this scan plus alternates (guess hits the "
    "configured regex did not capture). not_configured: dry-run guesses that "
    "matched — use the example lines to write a precise regex; do not copy "
    "the guess patterns as-is. Zero-hit guesses are omitted."
)

# Stable "current log" names first, then common dedicated-server fallbacks.
PREFERRED_LOG_NAMES = (
    "latest-server-log.txt",
    "latest.log",
    "server.log",
    "console.log",
)


def _tuning_category_sort_key(category: str) -> tuple[int, str]:
    try:
        return (_TUNING_CATEGORY_ORDER.index(category), category)
    except ValueError:
        return (len(_TUNING_CATEGORY_ORDER), category)


def _unique_example_lines(
    items: Iterable[dict[str, Any]],
    *,
    extra: Iterable[str] | None = None,
    limit: int = _TUNING_EXAMPLE_LIMIT,
) -> list[str]:
    """Newest-first unique example lines from extra, then each pattern's recent_lines."""

    out: list[str] = []
    seen: set[str] = set()
    for line in list(extra or []):
        text = str(line).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            return out
    for item in items:
        recent = item.get("recent_lines")
        lines: list[str]
        if isinstance(recent, list) and recent:
            lines = [str(x) for x in recent if str(x).strip()]
        elif item.get("last_line"):
            lines = [str(item.get("last_line"))]
        else:
            lines = []
        for line in reversed(lines):
            text = str(line).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
            if len(out) >= limit:
                return out
    return out


def format_tuning_report(
    *,
    patterns: Iterable[dict[str, Any]],
    player_tracking_enabled: bool = False,
    version_mismatch_enabled: bool = False,
    source: str = "",
    source_label: str | None = None,
    line_count_analyzed: int | None = None,
    examples: dict[tuple[str, str], list[str]] | None = None,
    how_to_read: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Group a monitor pattern report into configured vs not_configured examples.

    Zero-hit dry-run guesses are omitted so the JSON is a list of real log lines
    rather than every generic regex. Configured categories are always listed so
    operators can see which plugin patterns are wired, even when this scan missed
    them (the live tailer starts at EOF).
    """

    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in patterns:
        category = str(item.get("category") or "").strip()
        if not category:
            continue
        by_category.setdefault(category, []).append(dict(item))

    configured: dict[str, dict[str, Any]] = {}
    not_configured: dict[str, dict[str, Any]] = {}
    for category in sorted(by_category, key=_tuning_category_sort_key):
        items = by_category[category]
        active = [i for i in items if (i.get("mode") or "") == "active"]
        dry = [i for i in items if (i.get("mode") or "") != "active"]
        extra_examples = (examples or {}).get(("active", category), [])
        if active:
            active_examples = _unique_example_lines(
                active, extra=extra_examples
            )
            hitting = [i for i in dry if int(i.get("hits") or 0) > 0]
            hitting.sort(
                key=lambda i: (-int(i.get("hits") or 0), str(i.get("pattern") or ""))
            )
            dry_examples = (examples or {}).get(("dry_run", category), [])
            all_dry = _unique_example_lines(hitting, extra=dry_examples)
            captured = {str(line).strip() for line in active_examples}
            alternates = [
                line for line in all_dry if str(line).strip() not in captured
            ]
            configured[category] = {
                "patterns": [str(i.get("pattern") or "") for i in active],
                "hits": sum(int(i.get("hits") or 0) for i in active),
                "examples": active_examples,
                "alternates": alternates[:_TUNING_EXAMPLE_LIMIT],
            }
            continue
        hitting = [i for i in dry if int(i.get("hits") or 0) > 0]
        if not hitting:
            continue
        hitting.sort(key=lambda i: (-int(i.get("hits") or 0), str(i.get("pattern") or "")))
        dry_examples = (examples or {}).get(("dry_run", category), [])
        not_configured[category] = {
            "guess_patterns": [
                str(i.get("pattern") or "") for i in hitting[:_TUNING_GUESS_LIMIT]
            ],
            "hits": sum(int(i.get("hits") or 0) for i in hitting),
            "examples": _unique_example_lines(hitting, extra=dry_examples),
        }

    report: dict[str, Any] = {
        "how_to_read": how_to_read or _TUNING_HOW_TO_READ,
        "source": source,
        "configured": configured,
        "not_configured": not_configured,
        "player_tracking_enabled": bool(player_tracking_enabled),
        "version_mismatch_enabled": bool(version_mismatch_enabled),
    }
    if source_label:
        report["source_label"] = source_label
    if line_count_analyzed is not None:
        report["line_count_analyzed"] = int(line_count_analyzed)
    if extra:
        report.update(extra)
    return report


def log_search_dirs(
    logs_dir: str | Path,
    data_dir: str | Path | None = None,
) -> list[Path]:
    """Directories where game logs may appear (configured logs_dir + data_dir)."""

    dirs = [Path(logs_dir)]
    if data_dir:
        root = Path(data_dir)
        for candidate in (
            root,
            root / "logs",
            root / "data" / "logs",
        ):
            if candidate not in dirs:
                dirs.append(candidate)
    return dirs


def discover_log_file(
    logs_dir: str | Path,
    data_dir: str | Path | None = None,
) -> Path | None:
    """Pick the best live game log for monitoring / Ingress capture.

    Games do not always honor ``logs_dir`` (some write under ``data_dir`` /
    ``data_dir/data/logs`` instead). Search those roots so live pattern
    monitoring and the log toolkit stay aligned.
    """

    directories = log_search_dirs(logs_dir, data_dir)
    for directory in directories:
        if not directory.is_dir():
            continue
        for preferred in PREFERRED_LOG_NAMES:
            path = directory / preferred
            if path.is_file():
                return path
    # Newest .log / .txt under known dirs (non-recursive, then one level).
    candidates: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in {".log", ".txt"}:
                    candidates.append(path)
                elif path.is_dir():
                    for child in path.iterdir():
                        if child.is_file() and child.suffix.lower() in {
                            ".log",
                            ".txt",
                        }:
                            candidates.append(child)
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


class LogToolbox:
    def __init__(
        self,
        plugin: GamePlugin,
        logs_dir: str | Path,
        state_dir: str | Path,
        recent_lines_provider,
    ) -> None:
        self.plugin = plugin
        self.logs_dir = Path(logs_dir)
        self.state_dir = Path(state_dir)
        self.captures_dir = self.state_dir / "captures"
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.recent_lines_provider = recent_lines_provider

    def _search_dirs(self) -> list[Path]:
        return log_search_dirs(self.logs_dir, self.plugin.data_dir)

    def pick_log_file(self) -> Path | None:
        return discover_log_file(self.logs_dir, self.plugin.data_dir)

    def tail_file(self, path: Path | None = None, lines: int = 400) -> list[str]:
        path = path or self.pick_log_file()
        if path is None or not path.is_file():
            return []
        try:
            # Efficient-ish tail for moderate log files.
            data = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return [strip_ansi(line) for line in data[-max(1, lines) :]]
        except OSError:
            return []

    def raw_tail(self, lines: int = 400) -> dict[str, Any]:
        """Prefer on-disk game logs; fall back to in-memory recent output."""

        path = self.pick_log_file()
        file_lines = self.tail_file(path=path, lines=lines) if path else []
        if file_lines:
            return {
                "source": str(path),
                "source_label": f"Game log file ({path.name})",
                "lines": file_lines,
            }
        recent = [strip_ansi(line) for line in self.recent_lines_provider()]
        clipped = recent[-max(1, lines) :]
        if clipped:
            return {
                "source": "memory:recent_output",
                "source_label": "Live process output (in memory)",
                "lines": clipped,
            }
        return {
            "source": "memory:recent_output",
            "source_label": "Live process output (in memory)",
            "lines": [],
            "empty_hint": (
                "No game process output yet — this view only shows the running "
                "game server. For install, update, and supervisor messages, use "
                "the Home Assistant app Logs tab."
            ),
        }

    def analyze_lines(self, lines: Iterable[str]) -> dict[str, Any]:
        """Rescan lines with a throwaway LogMonitor (active + dry-run regexes)."""

        from .monitor import LogMonitor

        monitor = LogMonitor(self.plugin, self.logs_dir)
        examples: dict[tuple[str, str], list[str]] = {}
        seen: dict[tuple[str, str], set[str]] = {}
        count = 0
        for raw in lines:
            text = strip_ansi(str(raw)).rstrip("\n")
            if not text.strip():
                continue
            count += 1
            monitor.ingest_stdout_line(text)
            highlighted = monitor.state.highlighted_lines
            if not highlighted:
                continue
            last = highlighted[-1]
            if str(last.get("line") or "") != text:
                continue
            for match in last.get("matches") or []:
                mode = str(match.get("mode") or "dry_run")
                category = str(match.get("category") or "")
                if not category:
                    continue
                key = (mode, category)
                bucket = examples.setdefault(key, [])
                already = seen.setdefault(key, set())
                if text in already or len(bucket) >= _TUNING_EXAMPLE_LIMIT:
                    continue
                already.add(text)
                bucket.append(text)
        report = monitor.pattern_report()
        return format_tuning_report(
            patterns=report.get("patterns") or [],
            player_tracking_enabled=bool(report.get("player_tracking_enabled")),
            version_mismatch_enabled=bool(report.get("version_mismatch_enabled")),
            source="line_scan",
            line_count_analyzed=count,
            examples=examples,
        )

    def suggest(self, lines: int = 2000) -> dict[str, Any]:
        """Rescan the on-disk log (fallback: in-memory output) for pattern examples."""

        tail = self.raw_tail(lines=lines)
        file_lines = list(tail.get("lines") or [])
        merged = list(file_lines)
        if str(tail.get("source") or "") != "memory:recent_output":
            seen = set(file_lines)
            for line in self.recent_lines_provider():
                text = strip_ansi(str(line))
                if text in seen:
                    continue
                seen.add(text)
                merged.append(text)
        report = self.analyze_lines(merged)
        report["source"] = str(tail.get("source") or "unknown")
        report["source_label"] = str(
            tail.get("source_label") or "Game log file rescan"
        )
        if tail.get("empty_hint"):
            report["empty_hint"] = tail["empty_hint"]
        return report

    def example_groups_by_category(
        self, lines: int = 2000
    ) -> dict[str, dict[str, list[str]]]:
        """File-rescan matches and alternate guess lines per category."""

        report = self.suggest(lines=lines)
        out: dict[str, dict[str, list[str]]] = {}
        for category, data in (report.get("configured") or {}).items():
            if not isinstance(data, dict):
                continue
            out[str(category)] = {
                "matches": [
                    str(line).strip()
                    for line in list(data.get("examples") or [])
                    if str(line).strip()
                ],
                "alternates": [
                    str(line).strip()
                    for line in list(data.get("alternates") or [])
                    if str(line).strip()
                ],
            }
        for category, data in (report.get("not_configured") or {}).items():
            if not isinstance(data, dict):
                continue
            examples = [
                str(line).strip()
                for line in list(data.get("examples") or [])
                if str(line).strip()
            ]
            if not examples:
                continue
            bucket = out.setdefault(
                str(category), {"matches": [], "alternates": []}
            )
            bucket["matches"] = examples
        return out

    def example_lines_by_category(self, lines: int = 2000) -> dict[str, list[str]]:
        """Unique matching log lines per category from a file rescan."""

        out: dict[str, list[str]] = {}
        for category, data in self.example_groups_by_category(lines=lines).items():
            combined: list[str] = []
            seen: set[str] = set()
            for line in list(data.get("matches") or []) + list(
                data.get("alternates") or []
            ):
                text = str(line).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                combined.append(text)
            if combined:
                out[str(category)] = combined
        return out

    def list_captures(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.captures_dir.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            meta_path = path / "capture.json"
            meta = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    meta = {}
            archive = path / "capture.tar.gz"
            items.append(
                {
                    "id": path.name,
                    "path": str(path),
                    "created_at": meta.get("created_at"),
                    "reason": meta.get("reason"),
                    "has_archive": archive.is_file(),
                    # Relative path so Home Assistant Ingress (X-Ingress-Path) works.
                    "download_path": f"api/logs/captures/{path.name}/download",
                }
            )
        return items

    def capture(
        self,
        reason: str = "manual",
        status: dict[str, Any] | None = None,
        lines: int = 800,
    ) -> dict[str, Any]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        capture_id = f"{stamp}-{reason}"
        dest = self.captures_dir / capture_id
        dest.mkdir(parents=True, exist_ok=True)

        recent = list(self.recent_lines_provider())
        file_lines = self.tail_file(lines=lines)
        (dest / "recent_stdout.txt").write_text(
            "\n".join(recent) + ("\n" if recent else ""),
            encoding="utf-8",
        )
        (dest / "log_tail.txt").write_text(
            "\n".join(file_lines) + ("\n" if file_lines else ""),
            encoding="utf-8",
        )

        src_log = self.pick_log_file()
        if src_log and src_log.is_file():
            try:
                shutil.copy2(src_log, dest / src_log.name)
            except OSError as exc:
                LOG.warning("Could not copy log file: %s", exc)

        analysis = self.analyze_lines(file_lines + recent)
        (dest / "analysis.json").write_text(
            json.dumps(analysis, indent=2),
            encoding="utf-8",
        )
        if status is not None:
            (dest / "status.json").write_text(
                json.dumps(status, indent=2, default=str),
                encoding="utf-8",
            )

        meta = {
            "id": capture_id,
            "reason": reason,
            "created_at": time.time(),
            "created_at_iso": datetime.now(timezone.utc).isoformat(),
            "source_log": str(src_log) if src_log else None,
            "game": self.plugin.name,
            "steam_app_id": self.plugin.steam_app_id,
        }
        (dest / "capture.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        archive = dest / "capture.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in dest.iterdir():
                if path.name == "capture.tar.gz":
                    continue
                tar.add(path, arcname=path.name)

        # Keep only the newest 30 captures
        captures = sorted(
            [p for p in self.captures_dir.iterdir() if p.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        for stale in captures[30:]:
            shutil.rmtree(stale, ignore_errors=True)

        LOG.info("Created log capture %s (%s)", capture_id, reason)
        return {
            "id": capture_id,
            "path": str(dest),
            "download_path": f"api/logs/captures/{capture_id}/download",
            "analysis_summary": {
                "configured": {
                    key: int(vals.get("hits") or 0)
                    for key, vals in (analysis.get("configured") or {}).items()
                },
                "not_configured": {
                    key: int(vals.get("hits") or 0)
                    for key, vals in (analysis.get("not_configured") or {}).items()
                },
            },
        }

    def capture_archive_path(self, capture_id: str) -> Path | None:
        """Resolve a capture archive, rejecting path traversal / odd ids."""

        raw = str(capture_id or "").strip()
        if not raw or not re.fullmatch(r"[A-Za-z0-9._-]+", raw):
            return None
        root = self.captures_dir.resolve()
        path = (self.captures_dir / raw / "capture.tar.gz").resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path if path.is_file() else None
