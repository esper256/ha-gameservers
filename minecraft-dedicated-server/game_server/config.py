"""Load settings from Home Assistant options.json and/or environment variables."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backup import RetentionPolicy, retention_from_profile


OPTIONS_CANDIDATES = (
    Path("/data/options.json"),
)


@dataclass
class SupervisorConfig:
    """Runtime knobs for the generic supervisor."""

    # Update behaviour
    update_on_start: bool = True
    # 0 disables periodic Steam checks. Used when auto_update_check_hour is unset.
    auto_update_interval_minutes: int = 1440
    # Local hour (0-23) for the daily Steam "is there a newer build?" check.
    # Default 05:00 so we ask Steam once a day, not every few minutes.
    auto_update_check_hour: int | None = 5
    update_when_empty_only: bool = True
    # Cap how long "wait for empty" may defer a pending update. 0 = unlimited.
    # Prevents a stuck player-count (or a forever-online session) from blocking
    # Steam updates indefinitely.
    update_empty_max_wait_hours: int = 24
    update_on_version_mismatch: bool = True
    update_window_start_hour: int | None = None
    update_window_end_hour: int | None = None
    # Soft preference only — SteamGate hard-caps retries and uses exponential backoff.
    steamcmd_retries: int = 3
    steamcmd_retry_delay_seconds: int = 60

    # Process supervision
    restart_on_crash: bool = True
    crash_restart_delay_seconds: int = 5
    max_crash_restarts_per_hour: int = 10
    stop_timeout_seconds: int = 0  # 0 = use plugin.stop_timeout_seconds
    run_as_user: str = "gameserver"
    drop_privileges: bool = True

    # Status HTTP / notifications (8099 = Home Assistant Ingress default)
    status_http_enabled: bool = True
    status_http_host: str = "0.0.0.0"
    status_http_port: int = 8099
    ha_notifications: bool = True
    # When false, Ingress hides log-watch / dry-run pattern tooling.
    debug_mode: bool = False

    # Backups
    backup_enabled: bool = True
    # Create cadence for scheduled backups. HA exposes retention profile only;
    # default daily (1440) so create rate matches keep_daily slots. Env override OK.
    backup_interval_minutes: int = 1440
    backup_dir: str = "/data/backups"
    backup_on_update: bool = True
    backup_min_source_bytes: int = 1024
    # One UX knob: minimal | standard | extended
    # (also sets pre-restore safety keep days: 1 / 7 / 30)
    backup_retention: str = "standard"
    backup_max_backoff_minutes: int = 1440

    # Disk
    min_free_disk_mb: int = 512

    # Paths — game install persists on the data volume by default
    steamcmd_dir: str = "/opt/steamcmd"
    install_dir: str = "/data/game"
    state_dir: str = "/data/supervisor"

    # Passthrough game options (everything else from options.json / env)
    game_options: dict[str, Any] = field(default_factory=dict)
    raw_options: dict[str, Any] = field(default_factory=dict)

    def retention(self) -> RetentionPolicy:
        return retention_from_profile(self.backup_retention)


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_options_file() -> Path | None:
    env_path = os.environ.get("OPTIONS_FILE") or os.environ.get("HASSIO_OPTIONS_FILE")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    for candidate in OPTIONS_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def load_options_json(path: Path | None = None) -> dict[str, Any]:
    path = path or find_options_file()
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Failed to parse options JSON at {path}: {exc}") from exc
    return {}


# Supervisor-only env knobs. Game option env vars come from each game plugin
# (see GamePlugin.docker_env_keys) so titles do not leak into this module.
SUPERVISOR_ENV_KEYS = (
    "UPDATE_ON_START",
    "AUTO_UPDATE_INTERVAL_MINUTES",
    "AUTO_UPDATE_CHECK_HOUR",
    "UPDATE_WHEN_EMPTY_ONLY",
    "UPDATE_EMPTY_MAX_WAIT_HOURS",
    "UPDATE_ON_VERSION_MISMATCH",
    "UPDATE_WINDOW_START_HOUR",
    "UPDATE_WINDOW_END_HOUR",
    "STEAMCMD_RETRIES",
    "STEAMCMD_RETRY_DELAY_SECONDS",
    "RESTART_ON_CRASH",
    "CRASH_RESTART_DELAY_SECONDS",
    "MAX_CRASH_RESTARTS_PER_HOUR",
    "STOP_TIMEOUT_SECONDS",
    "RUN_AS_USER",
    "DROP_PRIVILEGES",
    "STATUS_HTTP_ENABLED",
    "STATUS_HTTP_HOST",
    "STATUS_HTTP_PORT",
    "HA_NOTIFICATIONS",
    "DEBUG_MODE",
    "BACKUP_ENABLED",
    "BACKUP_INTERVAL_MINUTES",
    "BACKUP_DIR",
    "BACKUP_ON_UPDATE",
    "BACKUP_MIN_SOURCE_BYTES",
    "BACKUP_RETENTION",
    "BACKUP_MAX_BACKOFF_MINUTES",
    "MIN_FREE_DISK_MB",
    "STEAMCMD_DIR",
    "INSTALL_DIR",
    "STATE_DIR",
)


def _env_overrides(extra_keys: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Read supervisor env knobs plus game-contributed option keys."""

    keys = list(SUPERVISOR_ENV_KEYS)
    seen = {k.upper() for k in keys}
    for key in extra_keys:
        name = str(key or "").strip().upper()
        if not name or name in seen:
            continue
        seen.add(name)
        keys.append(name)
    out: dict[str, Any] = {}
    for key in keys:
        if key in os.environ:
            out[key.lower()] = os.environ[key]
    return out


def load_config(
    *,
    game_env_keys: list[str] | tuple[str, ...] | None = None,
) -> SupervisorConfig:
    """Load supervisor + game options from options.json and env.

    ``game_env_keys`` should be UPPER_SNAKE names contributed by the loaded
    game plugin (typically ``plugin.docker_env_keys()``). Without it, only
    supervisor env knobs are honored — HA ``options.json`` still supplies all
    game options as usual.
    """

    options = load_options_json()
    normalized: dict[str, Any] = {}
    for key, value in options.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
        normalized[norm] = value
    normalized.update(_env_overrides(extra_keys=game_env_keys or ()))

    supervisor_keys = {
        "update_on_start",
        "auto_update_interval_minutes",
        "auto_update_check_hour",
        "update_when_empty_only",
        "update_empty_max_wait_hours",
        "update_on_version_mismatch",
        "update_window_start_hour",
        "update_window_end_hour",
        "steamcmd_retries",
        "steamcmd_retry_delay_seconds",
        "restart_on_crash",
        "crash_restart_delay_seconds",
        "max_crash_restarts_per_hour",
        "stop_timeout_seconds",
        "run_as_user",
        "drop_privileges",
        "status_http_enabled",
        "status_http_host",
        "status_http_port",
        "ha_notifications",
        "debug_mode",
        "backup_enabled",
        "backup_interval_minutes",
        "backup_dir",
        "backup_on_update",
        "backup_min_source_bytes",
        "backup_retention",
        "backup_max_backoff_minutes",
        "min_free_disk_mb",
        "steamcmd_dir",
        "install_dir",
        "state_dir",
    }
    game_options = {k: v for k, v in normalized.items() if k not in supervisor_keys}

    return SupervisorConfig(
        update_on_start=_as_bool(normalized.get("update_on_start"), True),
        auto_update_interval_minutes=_as_int(
            normalized.get("auto_update_interval_minutes"), 1440
        ),
        auto_update_check_hour=_as_optional_int(
            normalized.get("auto_update_check_hour", 5)
        ),
        update_when_empty_only=_as_bool(
            normalized.get("update_when_empty_only"), True
        ),
        update_empty_max_wait_hours=max(
            0,
            _as_int(normalized.get("update_empty_max_wait_hours"), 24),
        ),
        update_on_version_mismatch=_as_bool(
            normalized.get("update_on_version_mismatch"), True
        ),
        update_window_start_hour=_as_optional_int(
            normalized.get("update_window_start_hour")
        ),
        update_window_end_hour=_as_optional_int(
            normalized.get("update_window_end_hour")
        ),
        steamcmd_retries=_as_int(normalized.get("steamcmd_retries"), 3),
        steamcmd_retry_delay_seconds=_as_int(
            normalized.get("steamcmd_retry_delay_seconds"), 60
        ),
        restart_on_crash=_as_bool(normalized.get("restart_on_crash"), True),
        crash_restart_delay_seconds=_as_int(
            normalized.get("crash_restart_delay_seconds"), 5
        ),
        max_crash_restarts_per_hour=_as_int(
            normalized.get("max_crash_restarts_per_hour"), 10
        ),
        stop_timeout_seconds=_as_int(normalized.get("stop_timeout_seconds"), 0),
        run_as_user=str(normalized.get("run_as_user") or "gameserver"),
        drop_privileges=_as_bool(normalized.get("drop_privileges"), True),
        status_http_enabled=_as_bool(normalized.get("status_http_enabled"), True),
        status_http_host=str(normalized.get("status_http_host") or "0.0.0.0"),
        status_http_port=_as_int(normalized.get("status_http_port"), 8099),
        ha_notifications=_as_bool(normalized.get("ha_notifications"), True),
        debug_mode=_as_bool(normalized.get("debug_mode"), False),
        backup_enabled=_as_bool(normalized.get("backup_enabled"), True),
        backup_interval_minutes=_as_int(
            normalized.get("backup_interval_minutes"), 1440
        ),
        backup_dir=str(normalized.get("backup_dir") or "/data/backups"),
        backup_on_update=_as_bool(normalized.get("backup_on_update"), True),
        backup_min_source_bytes=_as_int(
            normalized.get("backup_min_source_bytes"), 1024
        ),
        backup_retention=str(normalized.get("backup_retention") or "standard"),
        backup_max_backoff_minutes=_as_int(
            normalized.get("backup_max_backoff_minutes"), 1440
        ),
        min_free_disk_mb=_as_int(normalized.get("min_free_disk_mb"), 512),
        steamcmd_dir=str(normalized.get("steamcmd_dir") or "/opt/steamcmd"),
        install_dir=str(normalized.get("install_dir") or "/data/game"),
        state_dir=str(normalized.get("state_dir") or "/data/supervisor"),
        game_options=game_options,
        raw_options=normalized,
    )


def format_bool(value: Any, style: str) -> str:
    truthy = _as_bool(value, False)
    if style == "one_zero":
        return "1" if truthy else "0"
    return "true" if truthy else "false"


def format_option_value(value: object, bool_style: str) -> str:
    """Render an option for CLI/templates; coerce common bool spellings.

    Digit strings ``0`` / ``1`` are **not** treated as booleans. Docker/compose
    env values are always strings, and games use those digits as world slots,
    modes, and counts. Actual ``bool`` values and ``true``/``false``/``yes``/
    ``no`` still follow ``bool_style``.
    """

    if isinstance(value, bool) or (
        isinstance(value, str)
        and value.lower() in {"true", "false", "yes", "no"}
    ):
        return format_bool(value, bool_style)
    return str(value)
