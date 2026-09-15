"""Bridge subprocess/file logs into Home Assistant's Logs tab (container stdout)."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from collections import deque

# CSI / OSC color codes emitted by many game consoles.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|].*?(?:\x1b\\|\x07))")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so logs are readable and regex-friendly."""

    return _ANSI_RE.sub("", text or "")


class FlushStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every record for near-realtime HA logs."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def configure_logging(level: int) -> None:
    """Configure root logging to unbuffered stdout (HA add-on Logs tab)."""

    root = logging.getLogger()
    root.handlers.clear()
    handler = FlushStreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


class RecentLineDeduper:
    """Track recent log lines for de-duplication within a short window."""

    def __init__(self, maxlen: int = 64, ttl_seconds: float = 5.0) -> None:
        self._lock = threading.Lock()
        self._entries: deque[tuple[float, str]] = deque(maxlen=maxlen)
        self._ttl = ttl_seconds

    @staticmethod
    def _normalize(line: str) -> str:
        return strip_ansi(line).strip()

    def _purge_locked(self, now: float) -> None:
        while self._entries and now - self._entries[0][0] > self._ttl:
            self._entries.popleft()

    def remember(self, line: str) -> None:
        """Record a line (even if it was already seen)."""

        key = self._normalize(line)
        if not key:
            return
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            self._entries.append((now, key))

    def seen(self, line: str) -> bool:
        """Return True if this line was recorded within the TTL window."""

        key = self._normalize(line)
        if not key:
            return False
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            return any(existing == key for _, existing in self._entries)

    def remember_if_new(self, line: str) -> bool:
        """Return True if this line is new within the TTL window (and remember it)."""

        key = self._normalize(line)
        if not key:
            return False
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if any(existing == key for _, existing in self._entries):
                return False
            self._entries.append((now, key))
            return True


# Shared across process stdout and file-log mirroring so HA Logs stay de-duplicated.
# Boot floods can be hundreds of unique lines in a couple of seconds; a 64-line
# window lets the file tailer replay the same lines as [game-log].
STDOUT_DEDUPER = RecentLineDeduper(maxlen=4096, ttl_seconds=15.0)
