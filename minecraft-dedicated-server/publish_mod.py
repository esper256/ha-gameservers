"""Validate and activate an uploaded Minecraft mod JAR."""

from __future__ import annotations

import argparse
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
    install_atomic,
    profile_dir,
    read_profile,
    state_dir,
    uploaded_mods_dir,
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
MC_TOKEN_RE = re.compile(r"1\.\d+(?:\.\d+)?")


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


def _toml_dep_ranges(toml: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"\[\[dependencies", toml, flags=re.IGNORECASE):
        mod = re.search(r'modId\s*=\s*"([^"]+)"', chunk, re.IGNORECASE)
        rng = re.search(r'versionRange\s*=\s*"([^"]+)"', chunk, re.IGNORECASE)
        if mod and rng:
            out[mod.group(1).strip().lower()] = rng.group(1).strip()
    return out


def _fabric_minecraft_spec(fabric: dict[str, Any]) -> str:
    depends = fabric.get("depends")
    if not isinstance(depends, dict):
        return ""
    raw = depends.get("minecraft")
    if isinstance(raw, list):
        return ",".join(str(item) for item in raw)
    return str(raw or "").strip()


def minecraft_spec_covers(spec: str, world_version: str) -> bool:
    """True if the jar does not name a different Minecraft than this world."""

    spec = (spec or "").strip()
    world = (world_version or "").strip()
    if not spec or spec in {"*", ""} or not world:
        return True
    tokens = MC_TOKEN_RE.findall(spec)
    if not tokens:
        return True
    if world in tokens:
        return True
    return False


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
            "minecraft_spec": _fabric_minecraft_spec(fabric),
        }
    if toml:
        match = MODID_TOML_RE.search(toml)
        mod_id = match.group(1).strip() if match else ""
        side_match = ENV_TOML_RE.search(toml)
        environment = side_match.group(1).strip() if side_match else "*"
        deps = _toml_dep_ranges(toml)
        return {
            "loader": "neoforge",
            "mod_id": mod_id,
            "environment": environment,
            "version": "",
            "name": mod_id,
            "minecraft_spec": deps.get("minecraft") or "",
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
    from golden_boot import mark_attempt_request

    mark_attempt_request(profile_dir(active_world_name()))
    write_restart_request(
        state_dir(), reason="mod-publish", debounce_seconds=DEBOUNCE_SECONDS
    )


def publish(incoming: Path) -> int:
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
    world_mc = str(profile.get("minecraft_version") or "").strip()
    spec = str(info.get("minecraft_spec") or "")
    if world_mc and not minecraft_spec_covers(spec, world_mc):
        return _fail(
            f"This world is Minecraft {world_mc}; the JAR asks for {spec}",
            incoming,
            quarantine,
        )
    dest_dir = uploaded_mods_dir(world)
    if _client_only(str(info.get("environment") or "*")):
        dest_dir = world / "automodpack" / "host-modpack" / "main" / "mods"
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / f"{mod_id}.jar"
    hist = history_root / mod_id
    same = incoming.resolve() == canonical.resolve()
    if canonical.is_file() and not same:
        index = _next_history_index(hist)
        slot = hist / str(index)
        slot.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical, slot / "artifact.jar")
        _prune_history(hist)
    install_atomic(incoming, canonical)
    if not same:
        try:
            incoming.unlink()
        except OSError:
            pass
    _request_restart()
    print(f"Published {mod_id} -> {canonical} (restart in {DEBOUNCE_SECONDS}s)")
    return 0


def _skip_inbox_path(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    lower = name.lower()
    if lower.endswith(".partial"):
        return True
    if not lower.endswith(".jar"):
        return True
    return False


def publish_paths(paths: list[Path]) -> int:
    """Publish each inbox JAR; used by Copyparty xiu (paths on stdin)."""

    rc = 0
    for raw in paths:
        path = Path(raw)
        if _skip_inbox_path(path):
            continue
        if not path.is_file():
            continue
        result = publish(path)
        if result not in (0,):
            rc = result
    return rc


def publish_from_stdin() -> int:
    text = sys.stdin.read()
    stripped = text.strip()
    if not stripped:
        return 0
    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            paths = [
                Path(str(item.get("ap") or item.get("vp") or ""))
                for item in payload
                if isinstance(item, dict)
            ]
            return publish_paths([p for p in paths if str(p)])
    paths = [Path(line.strip()) for line in text.splitlines() if line.strip()]
    return publish_paths(paths)


def rollback(mod_id: str) -> int:
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
    dest = uploaded_mods_dir(world) / f"{mod_id}.jar"
    dest.parent.mkdir(parents=True, exist_ok=True)
    install_atomic(latest, dest)
    _request_restart()
    print(f"Rolled back {mod_id} from {latest}")
    return 0


def after_delete(path: Path) -> int:
    """Restart after an uploaded JAR is deleted (file is already gone)."""

    if path.suffix.lower() != ".jar":
        return 0
    _request_restart()
    print(f"Removed {path.name}; restart in {DEBOUNCE_SECONDS}s")
    return 0


def _upload_root() -> Path:
    return uploaded_mods_dir(profile_dir()).resolve()


def guard_delete(path: Path) -> int:
    """Block deletes of protected mods; Copyparty xbd ``c`` treats nonzero as deny."""

    uploaded = _upload_root()
    try:
        resolved = path.resolve()
        resolved.relative_to(uploaded)
    except (OSError, ValueError):
        print("Refusing delete outside the upload folder", file=sys.stderr)
        return 2
    if resolved.suffix.lower() != ".jar":
        print("Only JAR deletes are allowed here", file=sys.stderr)
        return 2
    mod_id = resolved.stem
    if resolved.is_file():
        try:
            info = inspect_jar(resolved)
            mod_id = str(info.get("mod_id") or mod_id)
        except (ValueError, zipfile.BadZipFile, OSError):
            pass
    if mod_id in PROTECTED_MOD_IDS or resolved.stem in PROTECTED_MOD_IDS:
        print(f"Refusing to delete protected mod {mod_id}", file=sys.stderr)
        return 2
    return 0


def guard_upload(path: Path) -> int:
    """Block in-place writes onto sealed jars (Copyparty xbu ``c``)."""

    uploaded = _upload_root()
    candidate = path if path.is_absolute() else uploaded / path.name
    try:
        resolved = candidate.resolve()
        resolved.relative_to(uploaded)
    except (OSError, ValueError):
        print("Refusing upload outside the upload folder", file=sys.stderr)
        return 2
    stem = resolved.stem.lower()
    if stem.startswith("automodpack") or stem in PROTECTED_MOD_IDS:
        print(f"Refusing to replace protected mod {stem}", file=sys.stderr)
        return 2
    name = resolved.name
    if name.startswith(".") or name.lower().endswith(".partial"):
        return 0
    if resolved.is_file() and resolved.suffix.lower() == ".jar":
        try:
            info = inspect_jar(resolved)
            if str(info.get("mod_id") or "") in PROTECTED_MOD_IDS:
                print("Refusing to replace a protected mod", file=sys.stderr)
                return 2
        except (ValueError, zipfile.BadZipFile, OSError):
            pass
        print(
            "Refusing in-place overwrite of a sealed jar; upload as a new filename",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Publish a Minecraft mod JAR")
    parser.add_argument("path", nargs="?", help="Uploaded JAR path")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read Copyparty xiu paths (one per line, or JSON list) from stdin",
    )
    parser.add_argument("--rollback", metavar="MOD_ID", help="Restore last archived JAR")
    parser.add_argument(
        "--guard-upload",
        metavar="PATH",
        help="Copyparty before-upload check (protected mods)",
    )
    parser.add_argument(
        "--guard-delete",
        metavar="PATH",
        help="Copyparty before-delete check (protected mods)",
    )
    parser.add_argument(
        "--after-delete",
        metavar="PATH",
        help="Copyparty after-delete restart request",
    )
    args = parser.parse_args(argv[1:])
    if args.guard_upload:
        return guard_upload(Path(args.guard_upload))
    if args.guard_delete:
        return guard_delete(Path(args.guard_delete))
    if args.after_delete:
        return after_delete(Path(args.after_delete))
    if args.rollback:
        return rollback(args.rollback)
    if args.stdin:
        return publish_from_stdin()
    if not args.path:
        parser.error("JAR path required")
    return publish(Path(args.path))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
