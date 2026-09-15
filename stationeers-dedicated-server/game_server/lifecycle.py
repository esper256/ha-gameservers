"""Shared supervisor lifecycle phases for status + HA watchdog (/healthz)."""

from __future__ import annotations

# Prefer this over the old boolean "starting" (= anything not stopped).
# ``failed`` is still a live supervisor (game crash loop). HA watchdog must
# not tear the add-on down or sidecars (upload UIs) and Ingress recovery die.
LIFECYCLE_HEALTHY = frozenset(
    {
        "running",
        "installing",
        "updating",
        "restoring",
        "starting",
        "waiting",
        "restarting",
        "failed",
    }
)
