"""Validate and activate a kid-uploaded Minecraft mod JAR."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from haos_defaults import (  # noqa: E402
    PROTECTED_MOD_IDS,
    active_world_name,
    profile_dir,
    read_profile,
    state_dir,
)

HISTORY_KEEP = 10
DEBOUNCE_SECONDS = 20
MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
MODID_TOML_RE = re.compile(
    r'^\s*(?:modId|modid)\s*=\s*"([^"]+)"',
    re.IGNORECASE | re.MULTILINE,
)
ENV_TOML_RE = re.compile(
    r'^\s*(?:side|displayTest)\s*=\s*"([^"]+)"',
    re.IGNORECASE | re.MULTILINE,
)


def publisher_root() -> Path:
    return Path(os.environ.get("MOD_PUBLISHER_DIR") or "/data/mod-publisher")


def _fail(message: str, incoming: Path | None, quarantine: Path) -> int:
    print(message, file=sys.stderr)
    if incoming is not None and incoming.is_file():
        quarantine.mkdir(parents=True, exist_ok=True)
        dest = quarantine / incoming.name
        try:
            shutil.move(str(incoming), str(dest))
        except OSError:
            incoming.unlink(missing_ok=True)
    return 1


def _read_json_from_zip(zf: zipfile.ZipFile, name: str) -> dict[str, Any] | None:
    try:
        with zf.open(name) as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text_from_zip(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        with zf.open(name) as handle:
            return handle.read().decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return None


def inspect_jar(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        fabric = _read_json_from_zip(zf, "fabric.mod.json")
        toml = None
        for candidate in (
            "META-INF/neoforge.mods.toml",
            "META-INF/mods.toml",
        ):
            if candidate in names:
                toml = _read_text_from_zip(zf, candidate)
                if toml:
                    break
    if fabric:
        mod_id = str(fabric.get("id") or "").strip()
        environment = str(fabric.get("environment") or "*").strip() or "*"
        return {
            "loader": "fabric",
            "mod_id": mod_id,
            "environment": environment,
            "version": str(fabric.get("version") or ""),
            "name": str(fabric.get("name") or mod_id),
        }
    if toml:
        match = MODID_TOML_RE.search(toml)
        mod_id = match.group(1).strip() if match else ""
        side_match = ENV_TOML_RE.search(toml)
        environment = side_match.group(1).strip() if side_match else "*"
        return {
            "loader": "neoforge",
            "mod_id": mod_id,
            "environment": environment,
            "version": "",
            "name": mod_id,
        }
    raise ValueError("JAR is not a Fabric or NeoForge mod (missing metadata)")


def _client_only(environment: str) -> bool:
    text = environment.strip().lower()
    return text in {"client", "clientside", "clientonly"}


def _prune_history(folder: Path) -> None:
    if not folder.is_dir():
        return
    children = sorted(
        [p for p in folder.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name),
    )
    extra = len(children) - HISTORY_KEEP
    for stale in children[: max(0, extra)]:
        shutil.rmtree(stale, ignore_errors=True)


def _next_history_index(folder: Path) -> int:
    if not folder.is_dir():
        return 1
    nums = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
    return (max(nums) + 1) if nums else 1


def _request_restart() -> None:
    from game_server.active_world import write_restart_request

    write_restart_request(
        state_dir(), reason="mod-publish", debounce_seconds=DEBOUNCE_SECONDS
    )


def _lock_file():
    path = publisher_root() / "publish.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def publish(incoming: Path) -> int:
    lock = _lock_file()
    try:
        return _publish_locked(incoming)
    finally:
        lock.close()


def _publish_locked(incoming: Path) -> int:
    quarantine = publisher_root() / "quarantine"
    history_root = publisher_root() / "history"
    if not incoming.is_file():
        return _fail(f"Upload missing: {incoming}", None, quarantine)
    try:
        info = inspect_jar(incoming)
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        return _fail(f"Rejected upload: {exc}", incoming, quarantine)
    mod_id = str(info.get("mod_id") or "")
    if not MOD_ID_RE.fullmatch(mod_id):
        return _fail(f"Invalid mod id {mod_id!r}", incoming, quarantine)
    if mod_id in PROTECTED_MOD_IDS:
        return _fail(f"Refusing to replace protected mod {mod_id}", incoming, quarantine)
    world = profile_dir(active_world_name())
    profile = read_profile(world)
    loader = str(profile.get("loader") or "neoforge").lower()
    jar_loader = str(info.get("loader") or "")
    if jar_loader and jar_loader != loader:
        return _fail(
            f"This world uses {loader}; the JAR is {jar_loader}",
            incoming,
            quarantine,
        )
    dest_dir = world / "mods"
    if _client_only(str(info.get("environment") or "*")):
        dest_dir = world / "automodpack" / "host-modpack" / "main" / "mods"
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / f"{mod_id}.jar"
    hist = history_root / mod_id
    if canonical.is_file():
        index = _next_history_index(hist)
        slot = hist / str(index)
        slot.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical, slot / "artifact.jar")
        _prune_history(hist)
    tmp = dest_dir / f"{mod_id}.jar.partial"
    shutil.copy2(incoming, tmp)
    tmp.replace(canonical)
    try:
        incoming.unlink()
    except OSError:
        pass
    _request_restart()
    print(f"Published {mod_id} -> {canonical} (restart in {DEBOUNCE_SECONDS}s)")
    return 0


def rollback(mod_id: str) -> int:
    lock = _lock_file()
    try:
        return _rollback_locked(mod_id)
    finally:
        lock.close()


def _rollback_locked(mod_id: str) -> int:
    if not MOD_ID_RE.fullmatch(mod_id):
        print("Invalid mod id", file=sys.stderr)
        return 2
    world = profile_dir(active_world_name())
    hist = publisher_root() / "history" / mod_id
    if not hist.is_dir():
        print("No history for that mod", file=sys.stderr)
        return 1
    children = sorted(
        [p for p in hist.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name),
    )
    if not children:
        print("No history for that mod", file=sys.stderr)
        return 1
    latest = children[-1] / "artifact.jar"
    if not latest.is_file():
        print("History artifact missing", file=sys.stderr)
        return 1
    dest = world / "mods" / f"{mod_id}.jar"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest, dest)
    _request_restart()
    print(f"Rolled back {mod_id} from {latest}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Publish a family Minecraft mod JAR")
    parser.add_argument("path", nargs="?", help="Uploaded JAR path")
    parser.add_argument("--rollback", metavar="MOD_ID", help="Restore last archived JAR")
    args = parser.parse_args(argv[1:])
    if args.rollback:
        return rollback(args.rollback)
    if not args.path:
        parser.error("JAR path required")
    return publish(Path(args.path))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
