"""Prepare filesystem ownership and child-process identity (games / SteamCMD)."""

from __future__ import annotations

import logging
import os
import pwd
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("game_server.privileges")


def resolve_user(username: str) -> tuple[int, int, str]:
    record = pwd.getpwnam(username)
    return record.pw_uid, record.pw_gid, record.pw_dir


def chown_paths(uid: int, gid: int, paths: list[str | Path]) -> None:
    """Best-effort recursive chown so a non-root child can write these trees."""

    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            for root, dirs, files in os.walk(path):
                os.chown(root, uid, gid)
                for name in dirs + files:
                    try:
                        os.chown(os.path.join(root, name), uid, gid)
                    except OSError:
                        pass
            os.chown(path, uid, gid)
        except OSError as exc:
            LOG.warning("chown failed for %s: %s", path, exc)


def make_preexec(
    uid: int | None, gid: int | None
) -> Callable[[], None] | None:
    """Return a ``preexec_fn`` that setgid/setuid in a child process, or None.

    Used when launching SteamCMD or the game so file ownership matches the
    persistent ``/data`` trees. Prefer this over a permanent supervisor drop.
    """

    if uid is None and gid is None:
        return None

    def _drop() -> None:
        if gid is not None:
            os.setgid(gid)
        if uid is not None:
            os.setuid(uid)

    return _drop


def prepare_owned_paths(
    username: str,
    paths: list[str | Path],
) -> tuple[int, int] | None:
    """Create paths and chown them for ``username``.

    Returns ``(uid, gid)`` for child launches, or None when not root / user
    missing (caller continues as the current process user).
    """

    if os.geteuid() != 0:
        LOG.info("Not root; skipping privilege preparation")
        return None
    try:
        uid, gid, _home = resolve_user(username)
    except KeyError:
        LOG.warning("User %s not found; continuing as root", username)
        return None
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)
    chown_paths(uid, gid, paths)
    LOG.info("Prepared paths for user %s (%s:%s)", username, uid, gid)
    return uid, gid


# Older name kept so callers/tests mid-rename stay clear; prefer prepare_owned_paths.
prepare_drop = prepare_owned_paths
