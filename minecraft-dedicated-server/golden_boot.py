"""Last-known-good snapshot: hardlinked mods + a link to the install they use.

uploaded_mods/ is the next Copyparty experiment. An attempt snapshots that
folder into mods/ once; that tree is immutable for the JVM. Golden is the
last proven copy of that snapshot. Minecraft-layer only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from haos_defaults import (
    PROTECTED_MOD_IDS,
    current_install,
    install_snapshot,
    install_tree,
    mods_snapshot_dir,
    parse_install_ref,
    sealed_jars,
    uploaded_mods_dir,
    write_json,
)

GOLDEN_DIR = "golden_mods"
GOLDEN_META = "golden.json"
GOLDEN_INSTALL = "golden_install"
BOOT_SESSION = "boot.json"
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


def load_golden_install(directory: Path) -> tuple[str, str] | None:
    """Install the golden snapshot points at (symlink, else legacy golden.json)."""

    link = directory / GOLDEN_INSTALL
    if link.exists() or link.is_symlink():
        try:
            ref = parse_install_ref(link.resolve())
        except OSError:
            ref = None
        if ref:
            return ref
    data = _read_json(directory / GOLDEN_META)
    loader = str(data.get("loader") or "").strip().lower()
    version = str(data.get("minecraft_version") or "").strip()
    if loader in {"neoforge", "fabric"} and version:
        return loader, version
    return None


def load_golden_meta(directory: Path) -> dict[str, Any] | None:
    if not (directory / GOLDEN_DIR).is_dir():
        return None
    ref = load_golden_install(directory)
    if ref is None:
        return None
    loader, version = ref
    data = _read_json(directory / GOLDEN_META)
    data["loader"] = loader
    data["minecraft_version"] = version
    return data


def has_golden(directory: Path) -> bool:
    return load_golden_install(directory) is not None and (directory / GOLDEN_DIR).is_dir()


def write_golden_install(directory: Path, *, loader: str, version: str) -> None:
    dest = directory / GOLDEN_INSTALL
    target = install_tree(loader, version)
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(target)


def load_boot_session(directory: Path) -> dict[str, Any]:
    return _read_json(directory / BOOT_SESSION)


def attempt_needs_player(
    directory: Path,
    *,
    ha_version: str = "",
    loader: str = "",
    snapshot: Path | None = None,
) -> bool:
    """True when the snapshot has extra player jars (not stock AutoModpack only)."""

    folder = snapshot if snapshot is not None else uploaded_mods_dir(directory)
    return bool(extra_player_jars(folder))


def _jar_sig(path: Path) -> tuple[str, int, int]:
    st = path.stat()
    return (path.name, st.st_dev, st.st_ino)


def uploads_match_snapshot(directory: Path) -> bool:
    """True when mods/ is already the Copyparty jar set."""

    uploaded = sealed_jars(uploaded_mods_dir(directory))
    snap = sealed_jars(mods_snapshot_dir(directory))
    if not snap and not uploaded:
        return True
    try:
        if sorted(_jar_sig(p) for p in uploaded) == sorted(_jar_sig(p) for p in snap):
            return True
    except OSError:
        return False
    names_u = sorted(p.name for p in uploaded)
    names_s = sorted(p.name for p in snap)
    if names_u != names_s:
        return False
    try:
        return sorted((p.name, p.stat().st_size) for p in uploaded) == sorted(
            (p.name, p.stat().st_size) for p in snap
        )
    except OSError:
        return False


def should_restage(directory: Path, *, loader: str, version: str) -> bool:
    """New untested snapshot when the install link or Copyparty set is not current."""

    if attempt_requested(directory):
        return True
    if not mods_snapshot_dir(directory).is_dir():
        return True
    if current_install(directory) != (loader, version):
        return True
    if uploads_match_snapshot(directory):
        return False
    session = load_boot_session(directory)
    if str(session.get("mode") or "") == "golden" and has_golden(directory):
        return False
    return True


def choose_boot_mode(
    directory: Path, *, ha_version: str = "", loader: str = ""
) -> BootMode:
    """golden = last proven snapshot after an untested crash; else try the live snapshot."""

    session = load_boot_session(directory)
    unproven_attempt = (
        str(session.get("mode") or "") == "attempt"
        and not bool(session.get("proven"))
    )
    if unproven_attempt and has_golden(directory):
        return "golden"
    return "attempt"


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
    ref = current_install(directory)
    loader = (ref[0] if ref else str(session.get("loader") or "neoforge"))
    version = (ref[1] if ref else str(session.get("minecraft_version") or ""))
    dest = directory / GOLDEN_DIR
    install_snapshot(mods_snapshot_dir(directory), dest)
    if loader and version:
        write_golden_install(directory, loader=loader, version=version)
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
