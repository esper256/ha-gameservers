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
    MinecraftPinError,
    active_world_name,
    install_atomic,
    is_automodpack_fingerprint_name,
    profile_dir,
    resolve_world_version,
    state_dir,
    uploaded_mods_dir,
)
from mod_scan import ModHit, classify_jar, requirement_status

HISTORY_KEEP = 10
DEBOUNCE_SECONDS = 20
MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


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


def minecraft_spec_covers(spec: str, world_version: str) -> bool:
    """True unless metadata parses and excludes this world's Minecraft version."""

    hit = ModHit(
        name="",
        loader="fabric",
        minecraft_spec=spec,
        specs=(spec,) if str(spec or "").strip() else (),
    )
    return requirement_status(hit, world_version) != "incompatible"


def inspect_jar(path: Path) -> dict[str, Any]:
    hit = classify_jar(path)
    if hit.loader in {"fabric", "neoforge"}:
        return {
            "loader": hit.loader,
            "mod_id": hit.mod_id,
            "environment": hit.environment,
            "version": hit.version,
            "name": hit.display_name or hit.mod_id,
            "minecraft_spec": hit.minecraft_spec,
            "minecraft_specs": hit.specs,
        }
    if hit.loader == "forge":
        raise ValueError(
            f"{path.name} is a Forge mod (META-INF/mods.toml). "
            "Upload a Fabric or NeoForge jar."
        )
    if hit.loader == "quilt":
        raise ValueError(
            f"{path.name} is a Quilt mod without Fabric metadata. Quilt is not supported."
        )
    if hit.loader == "mixed":
        raise ValueError(f"{path.name} contains both Fabric and NeoForge metadata.")
    if hit.loader == "unknown":
        raise ValueError(hit.detail or f"{path.name} metadata could not be read")
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
    try:
        world_mc = resolve_world_version(world)
    except MinecraftPinError as exc:
        return _fail(str(exc), incoming, quarantine)
    specs = info.get("minecraft_specs")
    if not isinstance(specs, tuple):
        spec = str(info.get("minecraft_spec") or "")
        specs = (spec,) if spec else ()
    hit = ModHit(
        name=incoming.name,
        loader=str(info.get("loader") or "fabric"),
        mod_id=mod_id,
        minecraft_spec=str(info.get("minecraft_spec") or ""),
        specs=tuple(specs),
    )
    status = requirement_status(hit, world_mc)
    if status == "incompatible":
        shown = hit.minecraft_spec or ", ".join(hit.specs)
        return _fail(
            f"This world is Minecraft {world_mc}; the JAR supports {shown}",
            incoming,
            quarantine,
        )
    if status == "unknown":
        print(
            f"Warning: {incoming.name}: could not parse Minecraft requirement "
            f"{hit.minecraft_spec!r}; not blocking",
            file=sys.stderr,
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


def _leave_upload_name(name: str) -> bool:
    """Dotfiles, in-progress Copyparty temps, and the fingerprint stay put."""

    if is_automodpack_fingerprint_name(name):
        return True
    if name.startswith("."):
        return True
    return name.lower().endswith(".partial")


def _skip_inbox_path(path: Path) -> bool:
    return _leave_upload_name(path.name)


def quarantine_non_jar(path: Path) -> int:
    """Move a non-jar out of the drop folder and say why."""

    print(
        f"Removed {path.name}: only .jar files are allowed in the mods folder",
        file=sys.stderr,
    )
    quarantine = publisher_root() / "quarantine"
    if path.is_file():
        quarantine.mkdir(parents=True, exist_ok=True)
        dest = quarantine / path.name
        try:
            if dest.exists():
                dest.unlink()
            shutil.move(str(path), str(dest))
        except OSError:
            path.unlink(missing_ok=True)
    return 1


def sweep_uploaded_non_jars(folder: Path | None = None) -> int:
    """Quarantine non-jars that got into the drop folder. Leave the fingerprint."""

    folder = folder or uploaded_mods_dir(profile_dir())
    if not folder.is_dir():
        return 0
    rc = 0
    for path in list(folder.iterdir()):
        if not path.is_file():
            continue
        if _leave_upload_name(path.name):
            continue
        if path.suffix.lower() == ".jar":
            continue
        quarantine_non_jar(path)
        rc = 1
    return rc


def paths_from_xiu_payload(raw: str) -> list[Path]:
    """Parse Copyparty xiu stdin: newline-joined paths or a JSON list."""

    text = raw.strip()
    if not text:
        return []
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            out: list[Path] = []
            for item in data:
                if isinstance(item, str) and item.strip():
                    out.append(Path(item))
                    continue
                if isinstance(item, dict):
                    ap = item.get("ap") or item.get("path")
                    if ap:
                        out.append(Path(str(ap)))
            if out:
                return out
    return [Path(line.strip()) for line in raw.splitlines() if line.strip()]


def publish_from_stdin(raw: str | None = None) -> int:
    """Publish JARs named on Copyparty xiu stdin (paths or JSON)."""

    if raw is None:
        raw = sys.stdin.read()
    return publish_paths(paths_from_xiu_payload(raw))


def publish_paths(paths: list[Path]) -> int:
    """Publish each inbox JAR; used by Copyparty xiu (paths on stdin)."""

    rc = 0
    for raw in paths:
        path = Path(raw)
        if _skip_inbox_path(path):
            continue
        if path.suffix.lower() != ".jar":
            if path.is_file():
                quarantine_non_jar(path)
                rc = 1
            continue
        if not path.is_file():
            continue
        result = publish(path)
        if result not in (0,):
            rc = result
    if sweep_uploaded_non_jars():
        rc = 1
    return rc


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
    if is_automodpack_fingerprint_name(resolved.name):
        print("Refusing to delete the AutoModpack fingerprint file", file=sys.stderr)
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
    name = resolved.name
    if is_automodpack_fingerprint_name(name):
        print("Refusing to replace the AutoModpack fingerprint file", file=sys.stderr)
        return 2
    stem = resolved.stem.lower()
    if stem.startswith("automodpack") or stem in PROTECTED_MOD_IDS:
        print(f"Refusing to replace protected mod {stem}", file=sys.stderr)
        return 2
    if name.startswith(".") or name.lower().endswith(".partial"):
        return 0
    if resolved.suffix.lower() != ".jar":
        print(
            f"Refusing upload {name}: only .jar files are allowed in the mods folder",
            file=sys.stderr,
        )
        return 2
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
    if not args.path:
        return publish_from_stdin()
    return publish(Path(args.path))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
