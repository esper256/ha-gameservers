"""Last-known-good boot: sealed mods + Minecraft version + loader.

uploaded_mods/ is the next experiment (Copyparty). An attempt snapshots that
folder into mods/ once; that tree is immutable for the JVM. Golden preserves
that snapshot and releases the previous golden. Minecraft-layer only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from haos_defaults import (
    PROTECTED_MOD_IDS,
    install_snapshot,
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


def attempt_requested(directory: Path) -> bool:
    return (directory / ATTEMPT_REQUEST).is_file()


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


def attempt_needs_player(
    directory: Path,
    *,
    ha_version: str,
    loader: str,
    snapshot: Path | None = None,
) -> bool:
    """True when this combo is not the stock first configuration."""

    folder = snapshot if snapshot is not None else uploaded_mods_dir(directory)
    if extra_player_jars(folder):
        return True
    meta = load_golden_meta(directory)
    if meta is None:
        return False
    return (
        str(meta.get("minecraft_version") or "").strip() != str(ha_version)
        or str(meta.get("loader") or "").strip() != str(loader)
    )


def _pin_matches(payload: dict[str, Any], *, ha_version: str, loader: str) -> bool:
    return (
        str(payload.get("minecraft_version") or payload.get("last_attempt_ha_version") or "").strip()
        == str(ha_version)
        and str(payload.get("loader") or payload.get("last_attempt_loader") or "").strip()
        == str(loader)
    )


def choose_boot_mode(
    directory: Path, *, ha_version: str, loader: str
) -> BootMode:
    """attempt = stage uploads onto the HA pin; golden = boot the proven snapshot."""

    requested = attempt_requested(directory)
    state = load_attempt_state(directory)
    last_ha = str(state.get("last_attempt_ha_version") or "").strip()
    last_loader = str(state.get("last_attempt_loader") or "").strip()
    pin_changed = last_ha != str(ha_version) or last_loader != str(loader)
    session = load_boot_session(directory)
    unproven_attempt = (
        str(session.get("mode") or "") == "attempt"
        and not bool(session.get("proven"))
    )
    meta = load_golden_meta(directory)
    golden_stale = meta is not None and not _pin_matches(
        meta, ha_version=ha_version, loader=loader
    )
    if requested or not last_ha:
        return "attempt"
    # Crash-loop fallback: the JVM just died on this same HA pin. Do not restage.
    if unproven_attempt and meta is not None and _pin_matches(
        session, ha_version=ha_version, loader=loader
    ):
        return "golden"
    # A later start (addon restart, operator stop/start) must retry the HA pin
    # even if last_attempt already recorded it. Otherwise a stopped/unproven
    # pin change boots the old golden forever.
    if pin_changed or golden_stale:
        return "attempt"
    if meta is not None:
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
    needs_player: bool = False,
    proven: bool = False,
) -> None:
    write_json(
        directory / BOOT_SESSION,
        {
            "mode": mode,
            "loader": loader,
            "minecraft_version": minecraft_version,
            "stock": bool(stock),
            "needs_player": bool(needs_player),
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
    if not bool(session.get("ready")):
        return
    needs_player = bool(session.get("needs_player"))
    if needs_player and not bool(session.get("player")):
        return
    _promote(directory, session)


def _promote(directory: Path, session: dict[str, Any]) -> None:
    loader = str(session.get("loader") or "neoforge")
    version = str(session.get("minecraft_version") or "")
    dest = directory / GOLDEN_DIR
    install_snapshot(mods_snapshot_dir(directory), dest)
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

    leftover_prev = directory / "mods.prev"
    if leftover_prev.exists():
        shutil.rmtree(leftover_prev, ignore_errors=True)
    install_snapshot(directory / GOLDEN_DIR, mods_snapshot_dir(directory))


def apply_probe_findings(directory: Path, findings: dict[str, Any]) -> None:
    if findings.get("ready"):
        note_ready(directory)
    try:
        count = int(findings.get("player_count"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        count = -1
    if count >= 1:
        note_player(directory)
