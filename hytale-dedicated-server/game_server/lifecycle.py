"""Shared supervisor lifecycle phases for status + HA watchdog (/healthz)."""

from __future__ import annotations

# Prefer this over the old boolean "starting" (= anything not stopped).
# ``failed`` is not healthy: HA watchdog should restart the add-on.
LIFECYCLE_HEALTHY = frozenset(
    {
        "running",
        "installing",
        "updating",
        "restoring",
        "starting",
        "waiting",
        "restarting",
    }
)
