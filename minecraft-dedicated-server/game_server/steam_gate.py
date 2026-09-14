"""Serialize SteamCMD access and enforce backoff so we never hammer Steam."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

LOG = logging.getLogger("game_server.steam_gate")

# Substrings in SteamCMD output that mean "back off hard".
RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate limited",
    "too many login",
    "login rate",
    "account logon denied",
)


@dataclass(frozen=True)
class SteamPolicy:
    """Hard safety rails for talking to Steam. Values are floors / caps."""

    # Minimum seconds between any two SteamCMD process starts.
    min_interval_seconds: float = 90.0
    # Install/update attempt budget (hard-capped regardless of options).
    max_retries: int = 3
    retry_base_seconds: float = 60.0
    retry_max_seconds: float = 900.0  # 15 minutes
    retry_jitter_ratio: float = 0.25
    # Cross-call failure backoff (shared by checks + updates).
    failure_backoff_base_seconds: float = 120.0
    failure_backoff_max_seconds: float = 3600.0  # 1 hour
    # Suspected throttle / ban-adjacent response.
    rate_limit_cooldown_seconds: float = 21600.0  # 6 hours
    # Floor for periodic Steam build-id polls (minutes). 0 still disables.
    min_check_interval_minutes: int = 15
    # After this many failed apply cycles, pause applying until cooldown elapses.
    max_apply_failures: int = 3


class SteamGate:
    """Process-wide gate: one SteamCMD at a time + spacing + exponential cooldown."""

    def __init__(
        self,
        state_path: str | Path | None = None,
        policy: SteamPolicy | None = None,
        *,
        time_fn: Any | None = None,
        sleep_fn: Any | None = None,
    ) -> None:
        self.policy = policy or SteamPolicy()
        self.state_path = Path(state_path) if state_path else None
        self._time = time_fn or time.time
        self._sleep = sleep_fn or time.sleep
        self._exclusive = threading.Lock()
        self._state_lock = threading.RLock()
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.last_steam_call_at = 0.0
        self.last_rate_limit_at: float | None = None
        self.last_result: str | None = None
        self.last_kind: str | None = None
        self.call_count = 0
        self._load()

    def _load(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.consecutive_failures = int(data.get("consecutive_failures") or 0)
        self.cooldown_until = float(data.get("cooldown_until") or 0.0)
        self.last_steam_call_at = float(data.get("last_steam_call_at") or 0.0)
        raw_rl = data.get("last_rate_limit_at")
        self.last_rate_limit_at = float(raw_rl) if raw_rl else None
        self.last_result = data.get("last_result")
        self.last_kind = data.get("last_kind")
        self.call_count = int(data.get("call_count") or 0)

    def _save(self) -> None:
        if not self.state_path:
            return
        payload = {
            "consecutive_failures": self.consecutive_failures,
            "cooldown_until": self.cooldown_until,
            "last_steam_call_at": self.last_steam_call_at,
            "last_rate_limit_at": self.last_rate_limit_at,
            "last_result": self.last_result,
            "last_kind": self.last_kind,
            "call_count": self.call_count,
            "policy": asdict(self.policy),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError:
            LOG.warning("Failed persisting steam gate state", exc_info=True)

    def cooldown_remaining(self) -> float:
        with self._state_lock:
            return max(0.0, self.cooldown_until - self._time())

    def seconds_until_next_call(self) -> float:
        with self._state_lock:
            cooldown = max(0.0, self.cooldown_until - self._time())
            spacing = 0.0
            if self.last_steam_call_at > 0:
                spacing = max(
                    0.0,
                    self.policy.min_interval_seconds
                    - (self._time() - self.last_steam_call_at),
                )
            return max(cooldown, spacing)

    def clamp_check_interval_minutes(self, minutes: int) -> int:
        if minutes <= 0:
            return 0
        floor = max(1, int(self.policy.min_check_interval_minutes))
        if minutes < floor:
            LOG.warning(
                "auto_update_interval_minutes=%s is below safe floor %s; using %s",
                minutes,
                floor,
                floor,
            )
            return floor
        return minutes

    def clamp_retries(self, requested: int) -> int:
        hard_max = max(1, int(self.policy.max_retries))
        value = max(1, int(requested or hard_max))
        if value > hard_max:
            LOG.warning(
                "steamcmd_retries=%s exceeds hard max %s; using %s",
                value,
                hard_max,
                hard_max,
            )
            return hard_max
        return value

    def retry_delay_seconds(self, failed_attempt: int) -> float:
        """Delay after `failed_attempt` (1-based) before the next try."""

        exp = max(0, int(failed_attempt) - 1)
        base = self.policy.retry_base_seconds * (2**exp)
        delay = min(self.policy.retry_max_seconds, base)
        jitter = delay * self.policy.retry_jitter_ratio
        return max(1.0, delay + random.uniform(-jitter, jitter))

    def apply_failure_delay_seconds(self, apply_failures: int) -> float:
        exp = max(0, int(apply_failures) - 1)
        base = self.policy.failure_backoff_base_seconds * (2**exp)
        delay = min(self.policy.failure_backoff_max_seconds, base)
        jitter = delay * self.policy.retry_jitter_ratio
        return max(
            self.policy.min_interval_seconds,
            delay + random.uniform(0, jitter),
        )

    @staticmethod
    def looks_rate_limited(output: str) -> bool:
        text = (output or "").lower()
        return any(marker in text for marker in RATE_LIMIT_MARKERS)

    def note_success(self, kind: str = "steamcmd") -> None:
        with self._state_lock:
            self.consecutive_failures = 0
            self.cooldown_until = 0.0
            self.last_result = "success"
            self.last_kind = kind
            self._save()

    def note_failure(self, output: str = "", kind: str = "steamcmd") -> None:
        with self._state_lock:
            self.last_kind = kind
            now = self._time()
            self.consecutive_failures += 1
            self.last_result = "failure"
            if self.looks_rate_limited(output):
                self.last_rate_limit_at = now
                self.cooldown_until = now + self.policy.rate_limit_cooldown_seconds
                LOG.error(
                    "Steam rate-limit / login denial signal detected; "
                    "cooling down for %.0fs",
                    self.policy.rate_limit_cooldown_seconds,
                )
            else:
                exp = min(self.consecutive_failures - 1, 8)
                delay = min(
                    self.policy.failure_backoff_max_seconds,
                    self.policy.failure_backoff_base_seconds * (2**exp),
                )
                self.cooldown_until = max(self.cooldown_until, now + delay)
                LOG.warning(
                    "Steam call failed (consecutive=%s); cooldown %.0fs",
                    self.consecutive_failures,
                    self.cooldown_until - now,
                )
            self._save()

    def _wait_ready(self, stop_event: threading.Event | None = None) -> bool:
        while True:
            wait_for = self.seconds_until_next_call()
            if wait_for <= 0:
                return True
            LOG.info("Steam gate waiting %.0fs before next SteamCMD call", wait_for)
            # Sleep in chunks so stop_event can interrupt.
            deadline = self._time() + wait_for
            while self._time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    return False
                chunk = min(5.0, deadline - self._time())
                if chunk > 0:
                    self._sleep(chunk)
            # Loop again in case another caller advanced last_steam_call_at.

    @contextmanager
    def session(
        self,
        kind: str = "steamcmd",
        stop_event: threading.Event | None = None,
    ) -> Iterator["SteamGate"]:
        """Exclusive SteamCMD session with spacing + cooldown waits."""

        with self._exclusive:
            if not self._wait_ready(stop_event):
                raise InterruptedError("Stopped while waiting for Steam gate")
            with self._state_lock:
                self.last_steam_call_at = self._time()
                self.last_kind = kind
                self.call_count += 1
                self._save()
            LOG.info("Steam gate opening session kind=%s", kind)
            try:
                yield self
            finally:
                LOG.debug("Steam gate closing session kind=%s", kind)

    def to_dict(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "consecutive_failures": self.consecutive_failures,
                "cooldown_remaining_seconds": int(self.cooldown_remaining()),
                "seconds_until_next_call": int(self.seconds_until_next_call()),
                "last_steam_call_at": self.last_steam_call_at or None,
                "last_rate_limit_at": self.last_rate_limit_at,
                "last_result": self.last_result,
                "last_kind": self.last_kind,
                "call_count": self.call_count,
                "policy": asdict(self.policy),
            }


_GATE: SteamGate | None = None
_GATE_LOCK = threading.Lock()


def get_gate() -> SteamGate:
    global _GATE
    with _GATE_LOCK:
        if _GATE is None:
            _GATE = SteamGate()
        return _GATE


def configure_gate(
    state_dir: str | Path,
    policy: SteamPolicy | None = None,
) -> SteamGate:
    global _GATE
    with _GATE_LOCK:
        _GATE = SteamGate(Path(state_dir) / "steam_gate.json", policy)
        return _GATE


def reset_gate_for_tests() -> None:
    global _GATE
    with _GATE_LOCK:
        _GATE = None
