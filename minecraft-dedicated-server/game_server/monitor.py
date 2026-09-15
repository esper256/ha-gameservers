"""Tail game logs; active patterns trigger events, candidates stay dry-run."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from .log_bridge import STDOUT_DEDUPER, RecentLineDeduper, strip_ansi
from .log_tools import discover_log_file
from .patterns import DEFAULT_CANDIDATE_PATTERNS
from .plugin import PLAYER_TRACKING_PRESENCE, GamePlugin

LOG = logging.getLogger("game_server.monitor")

# Cross-source (stdout vs file) pattern dedupe window. Long enough for delayed
# file flushes; same-source identical lines are NOT suppressed (see _handle_line).
_CROSS_SOURCE_TTL_SECONDS = 120.0
_CROSS_SOURCE_MAXLEN = 512


@dataclass
class PatternStat:
    category: str
    pattern: str
    mode: str  # "active" | "dry_run"
    hits: int = 0
    # Hits since the current game-server process started (reset_session).
    session_hits: int = 0
    first_hit_at: float | None = None
    last_hit_at: float | None = None
    last_line: str | None = None
    # Newest last; Ingress shows up to these recent hits per regex.
    recent_lines: deque[str] = field(default_factory=lambda: deque(maxlen=5))

    def note(self, line: str) -> None:
        now = time.time()
        self.hits += 1
        self.session_hits += 1
        if self.first_hit_at is None:
            self.first_hit_at = now
        self.last_hit_at = now
        self.last_line = line
        self.recent_lines.append(line)

    def begin_process_session(self) -> None:
        """New game binary: keep lifetime hits, restart the session counter."""

        self.session_hits = 0

    def to_dict(self) -> dict[str, Any]:
        prior_hits = max(0, int(self.hits) - int(self.session_hits))
        # Stale = this configured regex used to match (a previous process),
        # but has not matched since the current server binary started.
        # Startup-only lines (game_version) stay healthy for the whole run
        # after they hit once — age since last_hit_at does not matter.
        stale = (
            self.mode == "active"
            and self.session_hits == 0
            and prior_hits > 0
        )
        return {
            "category": self.category,
            "pattern": self.pattern,
            "mode": self.mode,
            "hits": self.hits,
            "session_hits": self.session_hits,
            "first_hit_at": self.first_hit_at,
            "last_hit_at": self.last_hit_at,
            "last_line": self.last_line,
            "recent_lines": list(self.recent_lines),
            "stale": stale,
        }


@dataclass
class MonitorState:
    players: set[str] = field(default_factory=set)
    player_count: int | None = None
    players_known: bool = False
    ready: bool = False
    # Human-readable game version announced in logs (e.g. "1.3.1").
    game_version: str | None = None
    game_version_seen_at: float | None = None
    game_version_line: str | None = None
    version_mismatch_count: int = 0
    last_version_mismatch_at: float | None = None
    last_version_mismatch_line: str | None = None
    # Wall time of the most recent active player_join hit (presence UI).
    last_player_join_at: float | None = None
    last_log_line: str | None = None
    recent_lines: deque[str] = field(default_factory=lambda: deque(maxlen=200))
    # Recent lines that matched any pattern (active or dry-run), newest last.
    highlighted_lines: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=80)
    )
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        count = len(self.players) if self.players else self.player_count
        return {
            "players": sorted(self.players),
            "player_count": count,
            # True when at least one player is known to be online (count or set).
            "players_present": (
                bool(self.players)
                if self.players
                else (None if count is None else int(count) > 0)
            ),
            "players_known": self.players_known,
            "last_player_join_at": self.last_player_join_at,
            "ready": self.ready,
            "game_version": self.game_version,
            "game_version_seen_at": self.game_version_seen_at,
            "game_version_line": self.game_version_line,
            "version_mismatch_count": self.version_mismatch_count,
            "last_version_mismatch_at": self.last_version_mismatch_at,
            "last_version_mismatch_line": self.last_version_mismatch_line,
            "last_log_line": self.last_log_line,
            "highlighted_lines": list(self.highlighted_lines),
            "uptime_seconds": int(time.time() - self.started_at),
        }


@dataclass
class _CompiledPattern:
    category: str
    pattern: str
    mode: str
    regex: re.Pattern[str]
    stat: PatternStat


class LogMonitor:
    def __init__(
        self,
        plugin: GamePlugin,
        logs_dir: str | Path,
        on_version_mismatch: Callable[[str], None] | None = None,
    ) -> None:
        self.plugin = plugin
        self.logs_dir = Path(logs_dir)
        self.on_version_mismatch = on_version_mismatch
        self.state = MonitorState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._compiled: list[_CompiledPattern] = []
        self._stats: dict[tuple[str, str, str], PatternStat] = {}
        # Cross-source echoes: stdout-handled lines must not also fire from file
        # (and vice versa). Same-source repeats still apply (real duplicate events).
        self._stdout_lines = RecentLineDeduper(
            maxlen=_CROSS_SOURCE_MAXLEN, ttl_seconds=_CROSS_SOURCE_TTL_SECONDS
        )
        self._file_lines = RecentLineDeduper(
            maxlen=_CROSS_SOURCE_MAXLEN, ttl_seconds=_CROSS_SOURCE_TTL_SECONDS
        )
        # Ingress /api/ui builds a throwaway monitor every few seconds. Only the
        # live tailer should write HA Logs (game version, [game-log], mismatch).
        self._emit_runtime_logs = False
        self._build_patterns()

    def _build_patterns(self) -> None:
        active = plugin_patterns_as_dict(self.plugin)
        candidates = merge_candidates(
            DEFAULT_CANDIDATE_PATTERNS,
            self.plugin.log_pattern_candidates,
        )

        for category, patterns in active.items():
            for pattern in patterns:
                self._add_compiled(category, pattern, "active")

        for category, patterns in candidates.items():
            for pattern in patterns:
                # Don't duplicate an identical active pattern as dry-run.
                if pattern in active.get(category, []):
                    continue
                self._add_compiled(category, pattern, "dry_run")

    def _log_inactive_pattern_setup(self) -> None:
        """Once per live tailer — not on throwaway Ingress rescans."""

        if not self.player_tracking_enabled:
            LOG.warning(
                "No active player log patterns configured; updates will not wait "
                "for an empty server. Dry-run candidates will only highlight lines."
            )
        if not self.version_mismatch_enabled:
            LOG.info(
                "No active version-mismatch patterns; mismatch will not trigger updates."
            )

    def _add_compiled(self, category: str, pattern: str, mode: str) -> None:
        key = (mode, category, pattern)
        if key in self._stats:
            return
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            LOG.error("Invalid %s pattern for %s (%s): %s", mode, category, pattern, exc)
            return
        stat = PatternStat(category=category, pattern=pattern, mode=mode)
        self._stats[key] = stat
        self._compiled.append(
            _CompiledPattern(
                category=category,
                pattern=pattern,
                mode=mode,
                regex=regex,
                stat=stat,
            )
        )

    @property
    def player_tracking_enabled(self) -> bool:
        active = plugin_patterns_as_dict(self.plugin)
        return bool(
            active.get("player_join")
            or active.get("player_leave")
            or active.get("player_count")
            or active.get("players_empty")
        )

    @property
    def player_tracking_mode(self) -> str:
        return str(getattr(self.plugin, "player_tracking_mode", "count") or "count")

    @property
    def presence_tracking(self) -> bool:
        return self.player_tracking_mode == PLAYER_TRACKING_PRESENCE

    @property
    def version_mismatch_enabled(self) -> bool:
        return bool(plugin_patterns_as_dict(self.plugin).get("version_mismatch"))

    def reset_session(self) -> None:
        """Reset per-session player/ready state; keep cumulative pattern stats."""
        highlighted = self.state.highlighted_lines
        recent = self.state.recent_lines
        self.state = MonitorState()
        # Keep a little continuity in the UI across restarts.
        self.state.highlighted_lines = highlighted
        self.state.recent_lines = recent
        for stat in self._stats.values():
            stat.begin_process_session()

    def pattern_report(self) -> dict[str, Any]:
        stats = [stat.to_dict() for stat in self._stats.values()]
        stats.sort(
            key=lambda item: (
                0 if item["mode"] == "active" else 1,
                item["category"],
                -(item["hits"] or 0),
                item["pattern"],
            )
        )
        return {
            "player_tracking_enabled": self.player_tracking_enabled,
            "version_mismatch_enabled": self.version_mismatch_enabled,
            "active_pattern_count": sum(1 for s in stats if s["mode"] == "active"),
            "dry_run_pattern_count": sum(1 for s in stats if s["mode"] == "dry_run"),
            "patterns": stats,
            "recent_highlights": list(self.state.highlighted_lines)[-40:],
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._emit_runtime_logs = True
        self._log_inactive_pattern_setup()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="log-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _pick_log_file(self) -> Path | None:
        # Same discovery as Ingress log toolkit: configured logs_dir plus data_dir
        # fallbacks (some games write under data_dir even when logs_dir is set).
        return discover_log_file(self.logs_dir, self.plugin.data_dir)

    def _drain_handle(self, handle: TextIO[str]) -> None:
        """Read remaining complete lines before switching away from a log file."""

        try:
            while True:
                line = handle.readline()
                if not line:
                    break
                self._handle_line(line.rstrip("\n"), source="file")
        except OSError:
            LOG.exception("Failed draining log handle before rotate")

    def _run(self) -> None:
        current: Path | None = None
        handle: TextIO[str] | None = None
        inode: int | None = None
        try:
            while not self._stop.is_set():
                path = self._pick_log_file()
                if path is None:
                    if handle is not None:
                        self._drain_handle(handle)
                        handle.close()
                        handle = None
                        current = None
                        inode = None
                    time.sleep(1)
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    if handle is not None:
                        self._drain_handle(handle)
                        handle.close()
                        handle = None
                        current = None
                        inode = None
                    time.sleep(1)
                    continue

                reopen = handle is None or path != current or stat.st_ino != inode
                truncated = False
                if not reopen and handle is not None:
                    try:
                        # Truncate/reuse same inode (copytruncate) leaves the
                        # reader stuck at the old EOF; detect and rewind.
                        if stat.st_size < handle.tell():
                            reopen = True
                            truncated = True
                    except OSError:
                        reopen = True

                if reopen:
                    # Rotation/path change: finish the old file first so trailing
                    # lines are not lost. Truncate: old offset is past EOF — skip.
                    resume_offset: int | None = None
                    prior_inode = inode
                    if handle is not None and not truncated:
                        self._drain_handle(handle)
                        try:
                            resume_offset = handle.tell()
                        except OSError:
                            resume_offset = None
                    if handle:
                        handle.close()
                    try:
                        handle = path.open("r", encoding="utf-8", errors="replace")
                        if truncated:
                            # Same inode shrank — read new content from start.
                            handle.seek(0)
                        elif current is None:
                            # First open of a long-lived server: follow from EOF
                            # so we do not replay hours of history into patterns.
                            handle.seek(0, 2)
                        elif (
                            prior_inode is not None
                            and prior_inode == stat.st_ino
                            and resume_offset is not None
                        ):
                            # Rename of the file we already followed: keep offset
                            # (do not replay from the start via the new path).
                            handle.seek(resume_offset)
                        else:
                            # Brand-new inode/path: read from start so lines written
                            # between create and open are not missed.
                            handle.seek(0)
                        current = path
                        inode = stat.st_ino
                        LOG.info("Monitoring log file %s", path)
                    except OSError:
                        handle = None
                        current = None
                        inode = None
                        time.sleep(1)
                        continue

                line = handle.readline()
                if not line:
                    time.sleep(0.25)
                    continue
                self._handle_line(line.rstrip("\n"), source="file")
        finally:
            if handle:
                handle.close()

    def _handle_line(self, line: str, *, source: str = "file") -> None:
        line = strip_ansi(line)
        if not line.strip():
            return

        # HA Logs mirroring: file lines that already appeared as [game] from
        # process stdout are skipped; file-only lines become [game-log].
        if (
            self._emit_runtime_logs
            and source == "file"
            and STDOUT_DEDUPER.remember_if_new(line)
        ):
            LOG.info("[game-log] %s", line)

        # Pattern/event path: suppress cross-source echoes only. Two identical
        # joins on stdout still both count; a file echo of a stdout line does not.
        if source == "stdout":
            if self._file_lines.seen(line):
                return
            self._stdout_lines.remember(line)
        else:
            if self._stdout_lines.seen(line):
                return
            self._file_lines.remember(line)

        self.state.last_log_line = line
        self.state.recent_lines.append(line)

        matched_meta: list[dict[str, str]] = []
        active_hits: dict[str, re.Match[str]] = {}

        for item in self._compiled:
            match = item.regex.search(line)
            if not match:
                continue
            item.stat.note(line)
            matched_meta.append(
                {
                    "mode": item.mode,
                    "category": item.category,
                    "pattern": item.pattern,
                }
            )
            if item.mode == "active" and item.category not in active_hits:
                active_hits[item.category] = match

        if matched_meta:
            self.state.highlighted_lines.append(
                {
                    "ts": time.time(),
                    "line": line,
                    "matches": matched_meta,
                }
            )

        # Only active patterns mutate runtime state / fire callbacks.
        if "ready" in active_hits:
            self.state.ready = True

        if "player_join" in active_hits:
            match = active_hits["player_join"]
            name = match.groupdict().get("player") or (
                match.group(1) if match.lastindex else None
            )
            if name:
                self.state.players.add(str(name).strip())
            self.state.players_known = True
            self.state.last_player_join_at = time.time()
            if self.presence_tracking:
                # Occupied — exact headcount unknown / unused.
                self.state.player_count = max(1, len(self.state.players))
            else:
                self.state.player_count = len(self.state.players)

        if "player_leave" in active_hits:
            match = active_hits["player_leave"]
            # Prefer steam_id when the pattern captured both (identity must match join).
            groups = match.groupdict()
            name = groups.get("steam_id") or groups.get("player") or (
                match.group(1) if match.lastindex else None
            )
            cleaned = str(name).strip() if name else ""
            removed = False
            if cleaned and cleaned in self.state.players:
                self.state.players.discard(cleaned)
                removed = True
            self.state.players_known = True
            if self.presence_tracking:
                if removed and self.state.players:
                    # Known leave with other tracked names still present.
                    self.state.player_count = max(1, len(self.state.players))
                elif removed:
                    self.state.player_count = 0
                else:
                    # Unknown leave identity: reset to idle. Presence mode cannot
                    # prove remaining players without a matching join name, and
                    # keeping occupancy forever was leaving updates stuck.
                    self.state.players.clear()
                    self.state.player_count = 0
            else:
                self.state.player_count = len(self.state.players)

        if "players_empty" in active_hits:
            self.state.players.clear()
            self.state.player_count = 0
            self.state.players_known = True

        if "player_count" in active_hits:
            match = active_hits["player_count"]
            raw = match.groupdict().get("count") or (
                match.group(1) if match.lastindex else None
            )
            if raw is not None:
                try:
                    self.state.player_count = int(raw)
                    self.state.players_known = True
                    if self.state.player_count <= 0:
                        self.state.players.clear()
                except ValueError:
                    pass

        if "game_version" in active_hits:
            match = active_hits["game_version"]
            raw = match.groupdict().get("version") or (
                match.group(1) if match.lastindex else None
            )
            if raw is not None:
                version = str(raw).strip().rstrip(".,;")
                if version:
                    if version != self.state.game_version and self._emit_runtime_logs:
                        LOG.info("Game version from logs: %s", version)
                    self.state.game_version = version
                    self.state.game_version_seen_at = time.time()
                    self.state.game_version_line = line

        if "version_mismatch" in active_hits:
            self.state.version_mismatch_count += 1
            self.state.last_version_mismatch_at = time.time()
            self.state.last_version_mismatch_line = line
            if self._emit_runtime_logs:
                LOG.warning("Active version-mismatch pattern hit: %s", line)
            if self.on_version_mismatch:
                try:
                    self.on_version_mismatch(line)
                except Exception as exc:  # noqa: BLE001
                    # Keep monitoring logs, but surface the bug once.
                    LOG.exception(
                        "version mismatch callback failed; disabling callback"
                    )
                    self.state.last_version_mismatch_line = (
                        f"callback error: {exc}; last line: {line}"
                    )
                    self.on_version_mismatch = None

    def ingest_stdout_line(self, line: str) -> None:
        self._handle_line(line, source="stdout")


def plugin_patterns_as_dict(plugin: GamePlugin) -> dict[str, list[str]]:
    patterns = plugin.log_patterns
    return {
        "ready": list(patterns.ready or []),
        "player_join": list(patterns.player_join or []),
        "player_leave": list(patterns.player_leave or []),
        "player_count": list(patterns.player_count or []),
        "players_empty": list(patterns.players_empty or []),
        "game_version": list(patterns.game_version or []),
        "version_mismatch": list(patterns.version_mismatch or []),
    }


def merge_candidates(
    defaults: dict[str, list[str]],
    overrides: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {
        key: list(values) for key, values in defaults.items()
    }
    if not overrides:
        return merged
    for key, values in overrides.items():
        bucket = merged.setdefault(key, [])
        for value in values:
            if value not in bucket:
                bucket.append(value)
    return merged
