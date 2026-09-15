"""Persist the Ingress-selected world name independently of HA options.

Home Assistant owns add-on options. The live selection lives in
``active_world.json`` under the supervisor state dir so switching worlds
restarts only the game process (not the add-on container).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

LOG = logging.getLogger("game_server.active_world")

ACTIVE_WORLD_FILENAME = "active_world.json"
RESTART_REQUEST_FILENAME = "restart.request"


@dataclass(frozen=True)
class ActiveWorldSelection:
    option_key: str
    value: str


def active_world_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / ACTIVE_WORLD_FILENAME


def restart_request_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / RESTART_REQUEST_FILENAME


def load_active_world(state_dir: str | Path) -> ActiveWorldSelection | None:
    path = active_world_path(state_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Ignoring unreadable %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("option_key") or "").strip()
    value = str(raw.get("value") or "").strip()
    if not key or not value:
        return None
    return ActiveWorldSelection(option_key=key, value=value)


def save_active_world(
    state_dir: str | Path, *, option_key: str, value: str
) -> ActiveWorldSelection:
    selection = ActiveWorldSelection(
        option_key=str(option_key).strip(),
        value=str(value).strip(),
    )
    path = active_world_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"option_key": selection.option_key, "value": selection.value}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return selection


def apply_active_world_to_options(
    options: dict[str, Any],
    selection: ActiveWorldSelection | None,
) -> None:
    """Mutate game_options so launch/backups use the live world name."""

    if selection is None:
        return
    options[selection.option_key] = selection.value


def consume_restart_request(state_dir: str | Path) -> dict[str, Any] | None:
    """Read and delete a script-written restart request, if present."""

    path = restart_request_path(state_dir)
    if not path.is_file():
        return None
    payload: dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            raw = json.loads(text)
            if isinstance(raw, dict):
                payload = raw
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Ignoring unreadable %s: %s", path, exc)
    try:
        path.unlink()
    except OSError:
        LOG.exception("Failed deleting restart request %s", path)
        return None
    return payload


def write_restart_request(
    state_dir: str | Path,
    *,
    reason: str = "script",
    debounce_seconds: float = 0,
) -> None:
    path = restart_request_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": str(reason),
        "debounce_seconds": float(debounce_seconds),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(path)


def try_sync_ha_addon_option(option_key: str, value: str) -> bool:
    """Best-effort merge into Supervisor-stored add-on options. Never restarts."""

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        info = _supervisor_json("GET", "http://supervisor/addons/self/info", headers)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        LOG.info("Could not read add-on info for option sync: %s", exc)
        return False
    data = info.get("data") if isinstance(info.get("data"), dict) else info
    current = data.get("options") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        LOG.info("Add-on info had no options mapping; skip HA world sync")
        return False
    merged = dict(current)
    merged[option_key] = value
    try:
        _supervisor_json(
            "POST",
            "http://supervisor/addons/self/options",
            headers,
            {"options": merged},
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        LOG.info("Could not sync add-on option %s: %s", option_key, exc)
        return False
    LOG.info("Synced HA add-on option %s=%s", option_key, value)
    return True


def fetch_addon_network() -> dict[str, Any] | None:
    """Host port map from Supervisor ``GET addons/self/info``.

    Keys look like ``8765/tcp``; values are the host port or ``None`` when
    that mapping is disabled on the add-on Network tab. Returns ``None`` when
    Supervisor is unavailable so callers can fall back to the container port.
    """

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        info = _supervisor_json("GET", "http://supervisor/addons/self/info", headers)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        LOG.info("Could not read add-on Network map: %s", exc)
        return None
    data = info.get("data") if isinstance(info.get("data"), dict) else info
    if not isinstance(data, dict):
        return None
    network = data.get("network")
    if not isinstance(network, dict):
        return None
    return network


def _supervisor_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("supervisor response was not an object")
    return parsed
