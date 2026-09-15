"""Resolve supervisor and packaged Home Assistant app / image versions."""

from __future__ import annotations

import os
from pathlib import Path

# Major.minor of the shared supervisor. Game add-on versions are
# ``{this}.{game_patch}`` (e.g. supervisor 3.0 → add-on 3.0.0; a game-only
# reroll is 3.0.1). Bump this when the supervisor changes, then set every
# game ``config.yaml`` to ``{new}.0``.
SUPERVISOR_VERSION = "3.14"

_VERSION_FILES = (
    Path("/etc/hassio_app_version"),
    Path("/APP_VERSION"),
)


def supervisor_version() -> str:
    """Return the vendored supervisor major.minor (not the HA app patch)."""

    return SUPERVISOR_VERSION


def app_version() -> str:
    """Return the baked app version string (best effort)."""

    env = (os.environ.get("APP_VERSION") or os.environ.get("BUILD_VERSION") or "").strip()
    if env and env.lower() != "dev":
        return env

    for path in _VERSION_FILES:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text

    if env:
        return env
    return "unknown"
