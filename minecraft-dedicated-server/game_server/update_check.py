"""Shared result type for SteamCMD and HTTP package update probes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateCheckResult:
    """Result of comparing local vs remote install versions / build ids."""

    update_available: bool
    local_build_id: str | None
    remote_build_id: str | None
    # Set when the check itself failed/was cancelled — not the same as
    # "up to date".
    error: str | None = None

    @property
    def check_ok(self) -> bool:
        return self.error is None
