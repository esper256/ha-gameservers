"""Stable Linux machine-id for HA add-on / Docker containers.

Home Assistant add-on rules this follows:

- Canonical identity lives under ``/data`` (the add-on volume). Overlay ``/etc``
  is not persistent across container recreate.
- Do not request ``privileged``, ``full_access``, ``host_dbus``, ``udev``, or
  ``SYS_ADMIN``. Supervisor does not bind-mount host ``/etc/machine-id`` into
  add-on containers (only HA core / some plugins get that RO mount).
- Do not write ``/sys`` (DMI product_uuid). That is host kernel sysfs; HA maps
  it only when an add-on opts into ``gpio`` / similar hardware access.
- Do not adopt a *writable* ``/etc/machine-id`` from the image — Debian base
  images often bake one id shared by every install. Overwrite it with ours.
- If ``/etc/machine-id`` is present, valid, and *not* writable (a read-only
  bind), adopt that value on first boot so we do not fight a host mount.

``/etc/machine-id`` is a systemd regular file: 32 lowercase hex digits plus a
newline, mode 0444, so a dropped-privilege game process can read it.
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

LOG = logging.getLogger("game_server.machine_id")

MACHINE_ID_NAME = "machine-id"
_MACHINE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DEFAULT_ETC = Path("/etc/machine-id")
_DEFAULT_DBUS = Path("/var/lib/dbus/machine-id")
_PUBLIC_MODE = 0o444
_PERSIST_MODE = 0o644


def valid_machine_id(text: str) -> str:
    cleaned = (text or "").strip().lower().replace("-", "")
    # systemd machine-id(5): 32 lowercase hex; all zeros is uninitialized.
    if not _MACHINE_ID_RE.fullmatch(cleaned) or cleaned == "0" * 32:
        return ""
    return cleaned


def _write_id_file(path: Path, machine_id: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(machine_id + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(mode)
    except OSError:
        pass


def _read_valid(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return valid_machine_id(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def _copy_public(path: Path, machine_id: str) -> bool:
    if _read_valid(path) == machine_id:
        try:
            path.chmod(_PUBLIC_MODE)
        except OSError:
            pass
        return True
    try:
        _write_id_file(path, machine_id, mode=_PUBLIC_MODE)
        return True
    except OSError:
        return False


def ensure_machine_id(
    *,
    state_dir: str | Path,
    etc_path: str | Path | None = None,
    dbus_path: str | Path | None = None,
) -> str:
    """Return a stable 32-hex machine-id persisted under ``state_dir``."""

    state = Path(state_dir)
    persisted = state / MACHINE_ID_NAME
    etc = Path(etc_path) if etc_path is not None else _DEFAULT_ETC
    dbus = Path(dbus_path) if dbus_path is not None else _DEFAULT_DBUS

    machine_id = _read_valid(persisted)
    adopted_ro = False
    if not machine_id:
        generated = secrets.token_hex(16)
        if _copy_public(etc, generated):
            machine_id = generated
        else:
            existing = _read_valid(etc) or _read_valid(dbus)
            if existing:
                machine_id = existing
                adopted_ro = True
            else:
                machine_id = generated

    if _read_valid(persisted) != machine_id:
        state.mkdir(parents=True, exist_ok=True)
        _write_id_file(persisted, machine_id, mode=_PERSIST_MODE)

    etc_ok = _copy_public(etc, machine_id)
    dbus_ok = _copy_public(dbus, machine_id)

    if adopted_ro:
        LOG.info(
            "Adopted machine-id from a non-writable %s (host bind); persisted under %s",
            etc,
            persisted,
        )
    elif not etc_ok:
        existing = _read_valid(etc)
        if existing and existing != machine_id:
            LOG.warning(
                "%s is not writable and has a different id (read-only bind). "
                "The game will see that file, not the id persisted under %s",
                etc,
                persisted,
            )
        else:
            LOG.warning(
                "Could not write %s; binaries that key secrets to the Linux "
                "hardware UUID may fail or re-authenticate every restart",
                etc,
            )
    else:
        LOG.info("Linux machine-id ready (persisted under %s)", persisted)
    if dbus_ok:
        LOG.debug("machine-id also copied to %s", dbus)
    return machine_id
