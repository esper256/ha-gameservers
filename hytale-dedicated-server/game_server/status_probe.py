"""Optional status_probe: structured JSON peer to log_patterns.

Games opt in with argv that prints one JSON object. Only keys present in that
object are applied to MonitorState. Omitted keys (and null) mean “no answer”
and must not overwrite log-derived truth with defaults.
"""

from __future__ import annotations

import json
from typing import Any

from .monitor import MonitorState


def parse_status_probe_stdout(text: str) -> dict[str, Any] | None:
    """Return a JSON object from probe stdout, or None if the payload is unusable."""

    raw = (text or "").strip()
    if not raw:
        return None
    candidates = (raw.splitlines()[-1].strip(), raw)
    data: Any = None
    for blob in candidates:
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        break
    if not isinstance(data, dict):
        return None
    return data


def apply_status_probe(state: MonitorState, payload: Any) -> None:
    """Merge asserted probe fields into monitor state. Omitted keys are left alone."""

    if not isinstance(payload, dict):
        return
    if "ready" in payload and payload["ready"] is not None:
        ready = payload["ready"]
        if isinstance(ready, bool):
            state.ready = ready
        elif ready in (0, 1):
            state.ready = bool(ready)
    if "game_version" in payload and payload["game_version"] is not None:
        version = str(payload["game_version"]).strip()
        if version:
            state.game_version = version
    count_asserted = False
    if "player_count" in payload and payload["player_count"] is not None:
        raw = payload["player_count"]
        try:
            if isinstance(raw, bool):
                raise ValueError("bool is not a player count")
            count = int(raw)
        except (TypeError, ValueError):
            count = -1
        if count >= 0:
            state.player_count = count
            state.players_known = True
            count_asserted = True
            if count == 0:
                state.players.clear()
    if "players" in payload and payload["players"] is not None:
        names_raw = payload["players"]
        if isinstance(names_raw, list):
            names = [str(item).strip() for item in names_raw if str(item).strip()]
            state.players = set(names)
            state.players_known = True
            if not count_asserted:
                state.player_count = len(names)
