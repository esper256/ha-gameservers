"""Shared supervisor lifecycle phases for status + HA watchdog (/healthz)."""

from __future__ import annotations

# Prefer this over the old boolean "starting" (= anything not stopped).
# ``failed`` is not healthy: HA watchdog should restart titles that have no
# recovery publisher. Games that must stay up set hold_on_crash_loop and omit
# the add-on watchdog instead of lying here.
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
