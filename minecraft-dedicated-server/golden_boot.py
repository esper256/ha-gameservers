"""Last-known-good boot: sealed mods + Minecraft version + loader.

uploaded_mods/ is the next experiment (Copyparty). The JVM loads a snapshot.
Golden is promoted after stock ready, or after ready+player for extra jars /
pin / loader changes. Minecraft-layer only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from haos_defaults import (
    PROTECTED_MOD_IDS,
    clone_sealed_jar,
    mods_snapshot_dir,
    sealed_jars,
    uploaded_mods_dir,
    write_json,
)

GOLDEN_DIR = "golden_mods"
GOLDEN_META = "golden.json"
BOOT_SESSION = "boot.json"
ATTEMPT_STATE = "attempt_state.json"
ATTEMPT_REQUEST = "attempt.request"

BootMode = Literal["attempt", "golden"]


def extra_player_jars(folder: Path) -> list[Path]:
    extra: list[Path] = []
    for jar in sealed_jars(folder):
        stem = jar.stem.lower()
        if stem.startswith("automodpack") or stem in PROTECTED_MOD_IDS:
            continue
        extra.append(jar)
    return extra


def is_stock_uploads(directory: Path) -> bool:
    return not extra_player_jars(uploaded_mods_dir(directory))


def mark_attempt_request(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ATTEMPT_REQUEST).write_text("1\n", encoding="utf-8")


def consume_attempt_request(directory: Path) -> bool:
    path = directory / ATTEMPT_REQUEST
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return True
    return True


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_golden_meta(directory: Path) -> dict[str, Any] | None:
    data = _read_json(directory / GOLDEN_META)
    if not data:
        return None
    if not str(data.get("minecraft_version") or "").strip():
        return None
    if not str(data.get("loader") or "").strip():
        return None
    if not (directory / GOLDEN_DIR).is_dir():
        return None
    return data


def has_golden(directory: Path) -> bool:
    return load_golden_meta(directory) is not None


def load_boot_session(directory: Path) -> dict[str, Any]:
    return _read_json(directory / BOOT_SESSION)


def load_attempt_state(directory: Path) -> dict[str, Any]:
    return _read_json(directory / ATTEMPT_STATE)


def choose_boot_mode(
    directory: Path, *, ha_version: str, loader: str
) -> BootMode:
    """attempt = stage uploads onto the HA pin; golden = boot the proven snapshot."""

    requested = consume_attempt_request(directory)
    state = load_attempt_state(directory)
    last_ha = str(state.get("last_attempt_ha_version") or "").strip()
    last_loader = str(state.get("last_attempt_loader") or "").strip()
    pin_changed = last_ha != str(ha_version) or last_loader != str(loader)
    session = load_boot_session(directory)
    unproven_attempt = (
        str(session.get("mode") or "") == "attempt"
        and not bool(session.get("proven"))
    )
    if requested or pin_changed or not last_ha:
        return "attempt"
    if unproven_attempt and has_golden(directory):
        return "golden"
    if has_golden(directory):
        return "golden"
    return "attempt"


def record_attempt_state(directory: Path, *, ha_version: str, loader: str) -> None:
    write_json(
        directory / ATTEMPT_STATE,
        {
            "last_attempt_ha_version": ha_version,
            "last_attempt_loader": loader,
        },
    )


def write_boot_session(
    directory: Path,
    *,
    mode: BootMode,
    loader: str,
    minecraft_version: str,
    stock: bool,
    proven: bool = False,
) -> None:
    write_json(
        directory / BOOT_SESSION,
        {
            "mode": mode,
            "loader": loader,
            "minecraft_version": minecraft_version,
            "stock": bool(stock),
            "proven": bool(proven),
            "ready": False,
            "player": False,
        },
    )


def note_ready(directory: Path) -> None:
    session = load_boot_session(directory)
    if not session:
        return
    session["ready"] = True
    write_json(directory / BOOT_SESSION, session)
    _maybe_promote(directory, session)


def note_player(directory: Path) -> None:
    session = load_boot_session(directory)
    if not session:
        return
    session["player"] = True
    write_json(directory / BOOT_SESSION, session)
    _maybe_promote(directory, session)


def _maybe_promote(directory: Path, session: dict[str, Any]) -> None:
    if str(session.get("mode") or "") != "attempt":
        return
    if session.get("proven"):
        return
    stock = bool(session.get("stock"))
    ready = bool(session.get("ready"))
    player = bool(session.get("player"))
    if stock and ready:
        _promote(directory, session)
        return
    if ready and player:
        _promote(directory, session)


def _promote(directory: Path, session: dict[str, Any]) -> None:
    loader = str(session.get("loader") or "neoforge")
    version = str(session.get("minecraft_version") or "")
    dest = directory / GOLDEN_DIR
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for jar in sealed_jars(uploaded_mods_dir(directory)):
        try:
            clone_sealed_jar(jar, dest / jar.name)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    write_json(
        directory / GOLDEN_META,
        {
            "loader": loader,
            "minecraft_version": version,
            "stock": bool(session.get("stock")),
        },
    )
    session["proven"] = True
    write_json(directory / BOOT_SESSION, session)


def stage_golden_snapshot(directory: Path) -> None:
    """Rebuild world/mods from the golden sealed set (leave uploaded_mods)."""

    import errno
    import os

    from haos_defaults import MODS_NEXT, MODS_PREV

    golden = directory / GOLDEN_DIR
    mods = mods_snapshot_dir(directory)
    nxt = directory / MODS_NEXT
    prev = directory / MODS_PREV
    if nxt.exists():
        shutil.rmtree(nxt, ignore_errors=True)
    nxt.mkdir(parents=True)
    if golden.is_dir():
        for jar in sealed_jars(golden):
            try:
                clone_sealed_jar(jar, nxt / jar.name)
            except FileNotFoundError:
                continue
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    continue
                raise
    if prev.exists():
        shutil.rmtree(prev, ignore_errors=True)
    if mods.exists():
        os.replace(mods, prev)
    os.replace(nxt, mods)


def apply_probe_findings(directory: Path, findings: dict[str, Any]) -> None:
    if findings.get("ready"):
        note_ready(directory)
    try:
        count = int(findings.get("player_count"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        count = -1
    if count >= 1:
        note_player(directory)
