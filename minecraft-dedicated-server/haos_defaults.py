"""Minecraft add-on helpers: loader install, world profiles, Copyparty banner.

Kept out of game-server-base so Fabric/NeoForge names stay in this folder.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from mod_scan import scan_mods

HELPER = Path("/opt/mc-image-helper/bin/mc-image-helper")
STARTER_JAR = Path("/opt/server-starter.jar")
PROTECTED_MOD_IDS = frozenset(
    {
        "automodpack",
        "fabric-api",
        "fabricloader",
        "java",
        "minecraft",
        "neoforge",
        "forge",
    }
)
SEALED_MODE = 0o444
UPLOADED_MODS = "uploaded_mods"
MODS_SNAPSHOT = "mods"
AUTOMODPACK_CERT_REL = Path("automodpack") / ".private" / "cert.crt"
AUTOMODPACK_KEY_REL = Path("automodpack") / ".private" / "key.pem"
AUTOMODPACK_FINGERPRINT_NAME = "AUTOMODPACK-FINGERPRINT.txt"
# Outside every world folder. World backups restore automodpack/.private;
# the next prepare/boot points that folder back at this pair.
IDENTITY_DIR_NAME = "automodpack-identity"


class MinecraftPinError(RuntimeError):
    """HA options.json is the only desired pin; refuse a silent fallback."""


def options_path() -> Path:
    return Path(os.environ.get("OPTIONS_FILE") or "/data/options.json")


def options() -> dict[str, Any]:
    return _read_options_file()[0]


def _read_options_file() -> tuple[dict[str, Any], str]:
    """Return (mapping, error). Empty mapping if the file is missing or unreadable."""

    path = options_path()
    if not path.is_file():
        return {}, f"missing {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, f"unreadable {path}: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON {path}: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path} is not an object"
    return data, ""


def env_or_option(key: str, default: str = "") -> str:
    """HA ``options.json`` wins over a leftover process env value."""

    opt = options()
    if key in opt:
        raw = opt.get(key)
        if isinstance(raw, bool):
            return "true" if raw else "false"
        text = str(raw if raw is not None else "").strip()
        if text:
            return text
    env_key = key.upper()
    if os.environ.get(env_key):
        return str(os.environ[env_key]).strip()
    return default


_NEOFORGE_PIN_RE = re.compile(
    r"^(?:latest|beta|[0-9]+(?:\.[0-9]+)+(?:-beta)?)$",
    re.IGNORECASE,
)
_FABRIC_PIN_RE = re.compile(r"^(?:latest|[0-9]+(?:\.[0-9]+)+)$", re.IGNORECASE)


def _normalize_mc_version(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", text):
        return ""
    return text


def _normalize_loader_pin(raw: object, *, kind: str) -> str:
    """Return latest, beta, or an exact loader id. Empty string if invalid."""

    text = str(raw or "").strip()
    if not text:
        return "latest"
    if "/" in text or "\\" in text or ".." in text:
        return ""
    pattern = _NEOFORGE_PIN_RE if kind == "neoforge" else _FABRIC_PIN_RE
    if not pattern.fullmatch(text):
        return ""
    lowered = text.lower()
    if lowered in {"latest", "beta"}:
        return lowered
    return text


# New worlds that never went through the picker, and upgrades that have no
# install to copy, use the same default as the New world form.
DEFAULT_MINECRAFT_VERSION = "1.21.1"
# Package install is a marker only. Each world downloads its own server.
PACKAGE_VERSION = "per-world"
LOADER_CAPTIONS = {
    "vanilla": "Vanilla",
    "fabric": "Fabric",
    "neoforge": "NeoForge",
}
_VANILLA_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def install_id_minecraft_version(install_id: str) -> str:
    """Minecraft release encoded in an install folder suffix (``1.21.1`` or ``1.21.1-beta``)."""

    mc, _sep, _pin = str(install_id or "").partition("-")
    return _normalize_mc_version(mc)


def caption_for(version: str, loader: str) -> str:
    label = LOADER_CAPTIONS.get(loader, loader)
    return f"{version} · {label}"


def legacy_configuration_version() -> str:
    """Old global ``minecraft_version`` option, if a previous install still has it.

    A missing key is not an error. A present but invalid value, or a broken
    options file, still fails so a world is not pinned to a guess.
    """

    path = options_path()
    if not path.is_file():
        return ""
    data, err = _read_options_file()
    if err:
        raise MinecraftPinError(f"Cannot read options from {path}: {err}")
    if "minecraft_version" not in data:
        return ""
    version = _normalize_mc_version(data.get("minecraft_version"))
    if not version:
        raise MinecraftPinError(
            f"minecraft_version missing or invalid in {path}: "
            f"{data.get('minecraft_version')!r}"
        )
    return version


def install_dir() -> Path:
    return Path(os.environ.get("INSTALL_DIR") or "/data/installs")


def worlds_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or "/data/worlds")


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR") or "/data/supervisor")


def active_world_name() -> str:
    path = state_dir() / "active_world.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = str((raw or {}).get("value") or "").strip()
            if value:
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return env_or_option("world_name", "World")


def profile_dir(name: str | None = None) -> Path:
    return worlds_dir() / (name or active_world_name())


def uploaded_mods_dir(directory: Path | None = None) -> Path:
    return (directory or profile_dir()) / UPLOADED_MODS


def mods_snapshot_dir(directory: Path | None = None) -> Path:
    return (directory or profile_dir()) / MODS_SNAPSHOT


def is_automodpack_fingerprint_name(name: str) -> bool:
    return name.strip().lower() == AUTOMODPACK_FINGERPRINT_NAME.lower()


def automodpack_cert_path(directory: Path | None = None) -> Path:
    return (directory or profile_dir()) / AUTOMODPACK_CERT_REL


def automodpack_tls_fingerprint(directory: Path | None = None) -> str | None:
    """SHA-256 of the AutoModpack host cert, openssl -fingerprint style."""

    path = automodpack_cert_path(directory)
    if not path.is_file():
        return None
    try:
        pem = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError:
        return None
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def automodpack_fingerprint_text(fingerprint: str) -> str:
    return (
        "AutoModpack fingerprint\n"
        "\n"
        "Paste this when the Minecraft client warns about connecting "
        "to a server with mods:\n"
        "\n"
        f"{fingerprint}\n"
        "\n"
        "This is public (the hash of the server cert). Same value for "
        "every world and every player. It is not a password.\n"
    )


def write_automodpack_fingerprint_file(
    directory: Path | None = None,
) -> Path | None:
    """Copy the public fingerprint into the Copyparty drop folder when the cert exists."""

    directory = directory or profile_dir()
    fingerprint = automodpack_tls_fingerprint(directory)
    if not fingerprint:
        return None
    uploaded = uploaded_mods_dir(directory)
    uploaded.mkdir(parents=True, exist_ok=True)
    dest = uploaded / AUTOMODPACK_FINGERPRINT_NAME
    body = automodpack_fingerprint_text(fingerprint)
    try:
        if dest.is_file() and dest.read_text(encoding="utf-8") == body:
            return dest
    except OSError:
        pass
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, dest)
        os.chmod(dest, SEALED_MODE)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return dest


def identity_dir() -> Path:
    """Canonical AutoModpack cert+key. Not inside a world, not on the upload page."""

    override = os.environ.get("AUTOMODPACK_IDENTITY_DIR")
    if override:
        return Path(override)
    return worlds_dir().parent / IDENTITY_DIR_NAME


def _identity_files() -> tuple[Path, Path]:
    root = identity_dir()
    return root / "cert.crt", root / "key.pem"


def _world_identity_files(directory: Path) -> tuple[Path, Path]:
    return directory / AUTOMODPACK_CERT_REL, directory / AUTOMODPACK_KEY_REL


def _pair_ready(cert: Path, key: Path) -> bool:
    try:
        return (
            cert.is_file()
            and key.is_file()
            and cert.stat().st_size > 0
            and key.stat().st_size > 0
        )
    except OSError:
        return False


def _copy_identity_file(src: Path, dest: Path, mode: int) -> None:
    data = src.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, dest)
    os.chmod(dest, mode)


def _install_canonical_from(source: Path) -> None:
    src_cert, src_key = _world_identity_files(source)
    canon_cert, canon_key = _identity_files()
    root = canon_cert.parent
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    _copy_identity_file(src_cert, canon_cert, 0o644)
    _copy_identity_file(src_key, canon_key, 0o600)


def _force_symlink(src: Path, dest: Path) -> bool:
    """Point dest at src. Replaces a restored regular file. True if it changed."""

    target = src.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        try:
            if dest.resolve() == target:
                return False
        except OSError:
            pass
    tmp = dest.with_name(f".{dest.name}.link")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, dest)
    return True


def _world_used_at(directory: Path) -> float:
    """Prefer a boot/golden session over the cert's own mtime."""

    for name in ("boot.json", "golden.json"):
        path = directory / name
        try:
            if path.is_file():
                return path.stat().st_mtime
        except OSError:
            pass
    cert, _key = _world_identity_files(directory)
    try:
        if cert.is_file():
            return cert.stat().st_mtime
    except OSError:
        pass
    try:
        return directory.stat().st_mtime
    except OSError:
        return 0.0


def _worlds_with_identity() -> list[Path]:
    root = worlds_dir()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        cert, key = _world_identity_files(child)
        if _pair_ready(cert, key):
            found.append(child)
    return found


def _adoption_source() -> Path | None:
    """Active world's pair, else the most recently used world that has one."""

    found = _worlds_with_identity()
    if not found:
        return None
    active = profile_dir().resolve()
    for directory in found:
        try:
            if directory.resolve() == active:
                return directory
        except OSError:
            continue
    return max(found, key=_world_used_at)


def sync_automodpack_identity(directory: Path) -> None:
    """Make this world use the one shared AutoModpack cert and key.

    AutoModpack 4.0.6 generates ``cert.crt`` and ``key.pem`` only when either
    file is missing, and it reads them through ``File`` / ``Files.exists``,
    which follow symlinks. A symlink is enough to stop it minting a new pair.

    A world backup can restore an old per-world pair. That pair is replaced
    with symlinks on the next prepare/boot. The canonical files are never
    overwritten once they exist.
    """

    directory.mkdir(parents=True, exist_ok=True)
    canon_cert, canon_key = _identity_files()
    if not _pair_ready(canon_cert, canon_key):
        source = _adoption_source()
        if source is not None:
            _install_canonical_from(source)
            print(
                "Adopted AutoModpack certificate from "
                f"{source.name} into {canon_cert.parent} "
                "(shared by every world)",
                flush=True,
            )
    if not _pair_ready(canon_cert, canon_key):
        return
    world_cert, world_key = _world_identity_files(directory)
    changed = _force_symlink(canon_cert, world_cert)
    changed = _force_symlink(canon_key, world_key) or changed
    try:
        os.chmod(world_cert.parent, 0o700)
    except OSError:
        pass
    if changed:
        print(
            f"World {directory.name} uses the shared AutoModpack certificate",
            flush=True,
        )


def _is_partial_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".partial")


def sealed_jars(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") or _is_partial_name(name):
            continue
        if name.lower().endswith(".jar"):
            out.append(path)
    return out


def install_atomic(src: Path, dest: Path) -> None:
    """Seal dest as a new inode (never truncate an existing sealed jar)."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".new")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    os.chmod(dest, SEALED_MODE)


# linux/fs.h FICLONE: _IOW(0x94, 9, int) — CoW clone; new inode, shared extents.
_FICLONE = 0x40049409


def _try_reflink(src: Path, dest: Path) -> bool:
    """Clone extents when the FS supports it (btrfs/xfs). False if unsupported."""

    src_fd = os.open(src, os.O_RDONLY)
    try:
        dest_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SEALED_MODE)
        try:
            fcntl.ioctl(dest_fd, _FICLONE, src_fd)
        except OSError:
            os.close(dest_fd)
            dest.unlink(missing_ok=True)
            return False
        os.close(dest_fd)
        return True
    finally:
        os.close(src_fd)


def clone_sealed_jar(src: Path, dest: Path) -> None:
    """Stage one jar: reflink (isolates later in-place writes), else hardlink, else copy.

    Reflink is a new inode that shares disk until someone writes; that is the
    only clone that still protects the JVM if Copyparty truncates the upload
    name. Hardlinks share the inode (need the no-in-place-write rule). Copies
    isolate always but use extra space. ext4 HA volumes skip FICLONE and use
    the hardlink path.
    """

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    if _try_reflink(src, dest):
        return
    try:
        os.link(src, dest)
        return
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM, getattr(errno, "ENOTSUP", errno.EPERM)}:
            raise
        shutil.copy2(src, dest)
    try:
        os.chmod(dest, SEALED_MODE)
    except OSError:
        pass


def swap_snapshot(nxt: Path, dest: Path) -> None:
    """Install nxt as dest; release the previous dest tree."""

    stale = dest.with_name(dest.name + ".release")
    if stale.exists():
        shutil.rmtree(stale, ignore_errors=True)
    if dest.exists():
        os.replace(dest, stale)
    os.replace(nxt, dest)
    shutil.rmtree(stale, ignore_errors=True)


def fill_snapshot(src_folder: Path, nxt: Path) -> None:
    """Clone sealed jars from src_folder into a fresh nxt directory."""

    if nxt.exists():
        shutil.rmtree(nxt, ignore_errors=True)
    nxt.mkdir(parents=True)
    if not src_folder.is_dir():
        return
    for jar in sealed_jars(src_folder):
        try:
            clone_sealed_jar(jar, nxt / jar.name)
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                continue
            raise


def install_snapshot(src_folder: Path, dest: Path) -> None:
    """Atomically replace dest with a clone of src_folder; release the old dest."""

    nxt = dest.with_name(dest.name + ".next")
    fill_snapshot(src_folder, nxt)
    swap_snapshot(nxt, dest)


def migrate_legacy_mods(directory: Path) -> None:
    """Move jars from a pre-snapshot mods/ tree into uploaded_mods/ once."""

    uploaded = uploaded_mods_dir(directory)
    mods = mods_snapshot_dir(directory)
    uploaded.mkdir(parents=True, exist_ok=True)
    if sealed_jars(uploaded):
        return
    if not mods.is_dir():
        return
    for jar in sealed_jars(mods):
        dest = uploaded / jar.name
        if dest.exists():
            continue
        try:
            os.replace(jar, dest)
        except OSError:
            shutil.copy2(jar, dest)
            jar.unlink(missing_ok=True)
        try:
            os.chmod(dest, SEALED_MODE)
        except OSError:
            pass


def stage_mod_snapshot(directory: Path | None = None) -> None:
    """Snapshot uploaded_mods into mods/ once; release any previous current tree."""

    directory = directory or profile_dir()
    leftover_prev = directory / "mods.prev"
    if leftover_prev.exists():
        shutil.rmtree(leftover_prev, ignore_errors=True)
    uploaded = uploaded_mods_dir(directory)
    uploaded.mkdir(parents=True, exist_ok=True)
    install_snapshot(uploaded, mods_snapshot_dir(directory))


def read_profile(directory: Path) -> dict[str, Any]:
    path = directory / "profile.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def cmd_print_version() -> int:
    print(
        "Minecraft version is pinned on each world; this package marker does not select one",
        file=sys.stderr,
        flush=True,
    )
    print(PACKAGE_VERSION, flush=True)
    return 0


def _run_helper(args: list[str]) -> None:
    cmd = [str(HELPER), *args]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _results_file(install: Path) -> Path:
    return install / ".install.env"


def read_helper_results(install: Path) -> dict[str, str]:
    path = _results_file(install)
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def helper_server_entry(install: Path) -> Path | None:
    raw = (read_helper_results(install).get("SERVER") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = install / path
    return path


def fabric_install_args(output: Path, *, mc_version: str) -> list[str]:
    """Fabric Loader latest for this Minecraft version (no loader pin)."""

    return [
        "install-fabric-loader",
        f"--minecraft-version={mc_version}",
        f"--output-directory={output}",
        f"--results-file={_results_file(output)}",
    ]


def neoforge_install_args(output: Path, *, mc_version: str) -> list[str]:
    """Newest NeoForge build for this Minecraft version."""

    return [
        "install-neoforge",
        f"--minecraft-version={mc_version}",
        "--neoforge-version=latest",
        f"--output-directory={output}",
        f"--results-file={_results_file(output)}",
    ]


def cmd_install() -> int:
    """Mark the package ready. Loader jars are installed per world at boot."""

    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".loaders_ready"
    marker.write_text(f"{PACKAGE_VERSION}\n", encoding="utf-8")
    print(PACKAGE_VERSION, flush=True)
    return 0


def _consume_world_create(directory: Path) -> dict[str, Any]:
    path = directory / "world_create.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    try:
        path.unlink()
    except OSError:
        pass
    return data if isinstance(data, dict) else {}


def resolve_world_version(
    directory: Path, created: dict[str, Any] | None = None
) -> str:
    """Return this world's pinned Minecraft version, writing it the first time.

    A stored ``profile.json`` version always wins. Nothing else — Configuration,
    ``MINECRAFT_VERSION``, or an add-on update — may replace it.
    """

    directory.mkdir(parents=True, exist_ok=True)
    profile = read_profile(directory)
    stored = _normalize_mc_version(profile.get("minecraft_version"))
    if stored:
        return stored
    chosen = ""
    source = ""
    if created:
        chosen = _normalize_mc_version(created.get("minecraft_version"))
        if chosen:
            source = "new world"
    if not chosen:
        current = current_install(directory)
        if current:
            chosen = install_id_minecraft_version(current[1])
            if chosen:
                source = f"current install {current[0]}-{current[1]}"
    if not chosen:
        from golden_boot import load_golden_install

        ref = load_golden_install(directory)
        if ref:
            chosen = install_id_minecraft_version(ref[1])
            if chosen:
                source = f"last proven install {ref[0]}-{ref[1]}"
    if not chosen:
        chosen = legacy_configuration_version()
        if chosen:
            source = f"previous Configuration pin {options_path()}"
    if not chosen:
        chosen = _normalize_mc_version(os.environ.get("MINECRAFT_VERSION"))
        if chosen:
            source = "MINECRAFT_VERSION"
    if not chosen:
        chosen = DEFAULT_MINECRAFT_VERSION
        source = "default for a new world"
    profile["minecraft_version"] = chosen
    write_json(directory / "profile.json", profile)
    print(f"Pinned this world to Minecraft {chosen} ({source})", flush=True)
    return chosen


def apply_world_loader(
    directory: Path, created: dict[str, Any] | None = None
) -> str | None:
    """Pin the version, detect the loader, and seed AutoModpack when needed.

    Returns an error string when the world must not start. The pinned version
    is kept even when the mods are refused.
    """

    version = resolve_world_version(directory, created)
    uploaded = uploaded_mods_dir(directory)
    uploaded.mkdir(parents=True, exist_ok=True)
    scan = scan_mods(uploaded, version)
    for warning in scan.warnings:
        print(f"Warning: {warning}", file=sys.stderr, flush=True)
    if scan.errors:
        return "\n".join(scan.errors)
    loader = scan.loader
    profile = read_profile(directory)
    previous = str(profile.get("loader") or "").strip().lower()
    profile["minecraft_version"] = version
    profile["loader"] = loader
    profile["caption"] = caption_for(version, loader)
    write_json(directory / "profile.json", profile)
    if loader == "vanilla" or (previous and previous != loader):
        _clear_seeded_infrastructure(directory)
    if loader in {"fabric", "neoforge"}:
        _seed_infrastructure(directory, loader, version)
    elif loader != "vanilla":
        return f"Refusing to start: unsupported loader {loader!r}"
    print(
        f"World Minecraft {version} loader {loader} ({profile['caption']})",
        flush=True,
    )
    return None


def cmd_prepare_world() -> int:
    directory = profile_dir()
    directory.mkdir(parents=True, exist_ok=True)
    created = _consume_world_create(directory)
    (directory / "world").mkdir(parents=True, exist_ok=True)
    (directory / "config").mkdir(parents=True, exist_ok=True)
    migrate_legacy_mods(directory)
    uploaded_mods_dir(directory).mkdir(parents=True, exist_ok=True)
    sync_automodpack_identity(directory)
    error = apply_world_loader(directory, created)
    eula = env_or_option("eula", "true").lower() in {"1", "true", "yes", "on"}
    (directory / "eula.txt").write_text(
        f"eula={'true' if eula else 'false'}\n", encoding="utf-8"
    )
    _write_server_properties(directory)
    cmd_write_copyparty_banner()
    if error:
        print(error, file=sys.stderr, flush=True)
        return 1
    return 0


def _write_server_properties(directory: Path) -> None:
    motd = env_or_option("server_motd", "A Minecraft Server")
    slots = env_or_option("server_slots", "8")
    online = env_or_option("online_mode", "true").lower()
    port = os.environ.get("SERVER_PORT") or ""
    rcon_port = os.environ.get("RCON_PORT") or ""
    password = _ensure_rcon_password()
    lines = [
        f"motd={motd}",
        f"max-players={slots}",
        f"online-mode={'true' if online in {'1', 'true', 'yes', 'on'} else 'false'}",
        "white-list=false",
        "enforce-whitelist=false",
        "server-ip=0.0.0.0",
        "level-name=world",
        "enable-status=true",
        "sync-chunk-writes=true",
        "enable-rcon=true",
        f"rcon.password={password}",
        "broadcast-rcon-to-ops=false",
    ]
    if port.strip():
        lines.append(f"server-port={port.strip()}")
    if rcon_port.strip():
        lines.append(f"rcon.port={rcon_port.strip()}")
    else:
        lines.append("rcon.port=25575")
    (directory / "server.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


_LAUNCH_LINKS = (
    "run.sh",
    "run.bat",
    "user_jvm_args.txt",
    "unix_args.txt",
    "libraries",
    "versions",
    "server.jar",
)


def _replace_link(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        return
    dest.symlink_to(src)


def _link_install(directory: Path, loader: str, version: str) -> None:
    install = install_tree(loader, version)
    if not install.is_dir():
        print(f"Install tree missing: {install}", file=sys.stderr)
        return
    for name in _LAUNCH_LINKS:
        _replace_link(install / name, directory / name)
    entry = helper_server_entry(install)
    if entry is not None and entry.exists():
        try:
            entry.resolve().relative_to(install.resolve())
        except ValueError:
            pass
        else:
            _replace_link(entry, directory / entry.name)


def _github_json(url: str) -> Any:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)


def _seed_jar(url: str, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".new")
    if tmp.exists():
        tmp.unlink()
    _download(url, tmp)
    os.replace(tmp, dest)
    os.chmod(dest, SEALED_MODE)


def _clear_seeded_infrastructure(directory: Path) -> None:
    """Drop baked AutoModpack / Fabric API so a new pin can re-seed."""

    uploaded = uploaded_mods_dir(directory)
    if not uploaded.is_dir():
        return
    for path in uploaded.glob("automodpack*.jar"):
        path.unlink(missing_ok=True)
    for path in uploaded.glob("fabric-api*.jar"):
        path.unlink(missing_ok=True)


def _seed_infrastructure(directory: Path, loader: str, version: str) -> None:
    uploaded = uploaded_mods_dir(directory)
    uploaded.mkdir(parents=True, exist_ok=True)
    if any(uploaded.glob("automodpack*.jar")):
        return
    try:
        releases = _github_json(
            "https://api.github.com/repos/Skidamek/AutoModpack/releases?per_page=5"
        )
        needle = f"automodpack-mc{version}-{loader}-"
        url = None
        if isinstance(releases, list):
            for rel in releases:
                for asset in rel.get("assets") or []:
                    name = str(asset.get("name") or "")
                    if needle in name and name.endswith(".jar"):
                        url = asset.get("browser_download_url")
                        break
                if url:
                    break
        if url:
            _seed_jar(str(url), uploaded / "automodpack.jar")
            print(f"Seeded AutoModpack from {url}", flush=True)
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"Could not seed AutoModpack: {exc}", file=sys.stderr)
    if loader == "fabric" and not any(uploaded.glob("fabric-api*.jar")):
        try:
            query = (
                "https://api.modrinth.com/v2/project/P7dR8mSH/version"
                f'?game_versions=["{version}"]&loaders=["fabric"]'
            )
            data = _modrinth(query)
            if isinstance(data, list) and data:
                files = data[0].get("files") or []
                if files:
                    _seed_jar(str(files[0]["url"]), uploaded / "fabric-api.jar")
        except (OSError, json.JSONDecodeError, TimeoutError, KeyError) as exc:
            print(f"Could not seed Fabric API: {exc}", file=sys.stderr)


def _modrinth(url: str) -> Any:
    req = Request(url, headers={"User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def install_tree(loader: str, version: str) -> Path:
    return install_dir() / f"{loader}-{version}"


def parse_install_ref(path: Path) -> tuple[str, str] | None:
    """Read loader + install id (``neoforge-1.21.1`` or legacy ``neoforge-1.21.11-beta``)."""

    name = path.name
    for loader in ("neoforge", "fabric", "vanilla"):
        prefix = f"{loader}-"
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix) :]
        mc, sep, pin = rest.partition("-")
        if not _normalize_mc_version(mc):
            continue
        if not sep:
            return loader, mc
        if loader == "vanilla":
            continue
        kind = "neoforge" if loader == "neoforge" else "fabric"
        if not _normalize_loader_pin(pin, kind=kind):
            continue
        return loader, rest
    return None


def current_install(directory: Path) -> tuple[str, str] | None:
    """Loader+version the live install links currently point at, if any."""

    root = install_dir().resolve()
    for name in _LAUNCH_LINKS:
        dest = directory / name
        if not dest.exists() and not dest.is_symlink():
            continue
        try:
            resolved = dest.resolve()
        except OSError:
            continue
        for parent in (resolved.parent, *resolved.parents):
            try:
                if parent.parent.resolve() != root:
                    continue
            except OSError:
                continue
            ref = parse_install_ref(parent)
            if ref:
                return ref
    return None


def install_tree_ready(loader: str, version: str) -> bool:
    install = install_tree(loader, version)
    if not install.is_dir():
        return False
    if loader == "fabric":
        return helper_server_entry(install) is not None or (install / "server.jar").exists()
    if loader == "vanilla":
        jar = install / "server.jar"
        try:
            return jar.is_file() and jar.stat().st_size > 0
        except OSError:
            return False
    if loader == "neoforge":
        return (install / "server.jar").exists() or (install / "run.sh").exists()
    return False


def _fetch_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _install_vanilla(version: str) -> None:
    dest = install_tree("vanilla", version)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _fetch_json(_VANILLA_MANIFEST)
    versions = manifest.get("versions") if isinstance(manifest, dict) else None
    meta_url = ""
    if isinstance(versions, list):
        for item in versions:
            if isinstance(item, dict) and str(item.get("id") or "") == version:
                meta_url = str(item.get("url") or "")
                break
    if not meta_url:
        raise MinecraftPinError(f"No vanilla server manifest for Minecraft {version}")
    meta = _fetch_json(meta_url)
    downloads = meta.get("downloads") if isinstance(meta, dict) else None
    server = downloads.get("server") if isinstance(downloads, dict) else None
    jar_url = str(server.get("url") or "") if isinstance(server, dict) else ""
    if not jar_url:
        raise MinecraftPinError(f"No vanilla server jar for Minecraft {version}")
    _seed_jar(jar_url, dest / "server.jar")
    _results_file(dest).write_text("SERVER=server.jar\n", encoding="utf-8")


def ensure_loader_install(loader: str, version: str) -> bool:
    """Download the newest loader (or the vanilla server) for this Minecraft version."""

    if install_tree_ready(loader, version):
        return True
    print(
        f"Installing Minecraft {version} ({loader}) into {install_dir()}…",
        file=sys.stderr,
        flush=True,
    )
    try:
        if loader == "vanilla":
            _install_vanilla(version)
        elif loader == "fabric":
            dest = install_tree("fabric", version)
            dest.mkdir(parents=True, exist_ok=True)
            _run_helper(fabric_install_args(dest, mc_version=version))
        elif loader == "neoforge":
            dest = install_tree("neoforge", version)
            dest.mkdir(parents=True, exist_ok=True)
            _run_helper(neoforge_install_args(dest, mc_version=version))
            if STARTER_JAR.is_file() and not (dest / "server.jar").exists():
                shutil.copy2(STARTER_JAR, dest / "server.jar")
        else:
            print(f"Unknown install kind {loader}", file=sys.stderr)
            return False
    except (
        OSError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        TimeoutError,
        MinecraftPinError,
    ) as exc:
        print(
            f"Install failed for Minecraft {version} ({loader}): {exc}",
            file=sys.stderr,
        )
        return False
    if not install_tree_ready(loader, version):
        print(
            f"Install tree missing for Minecraft {version} ({loader}): "
            f"{install_tree(loader, version)}",
            file=sys.stderr,
        )
        return False
    return True


def prepare_game_command() -> list[str] | None:
    """Link install X + stage mods, or restore the last proven snapshot after a crash."""

    from golden_boot import (
        attempt_needs_player,
        choose_boot_mode,
        consume_attempt_request,
        extra_player_jars,
        load_boot_session,
        load_golden_install,
        should_restage,
        stage_golden_snapshot,
        write_boot_session,
    )

    directory = profile_dir()
    sync_automodpack_identity(directory)
    error = apply_world_loader(directory)
    if error:
        print(error, file=sys.stderr, flush=True)
        return None
    profile = read_profile(directory)
    loader = str(profile.get("loader") or "vanilla").strip().lower()
    world_version = _normalize_mc_version(profile.get("minecraft_version"))
    if loader not in {"vanilla", "fabric", "neoforge"} or not world_version:
        print(
            "Refusing to start: world profile is "
            f"{loader!r} / {profile.get('minecraft_version')!r}",
            file=sys.stderr,
        )
        return None
    mode = choose_boot_mode(directory, ha_version=world_version, loader=loader)
    golden_ref = load_golden_install(directory) if mode == "golden" else None
    if mode == "golden" and golden_ref is None:
        mode = "attempt"
    if mode == "golden":
        assert golden_ref is not None
        golden_loader, golden_version = golden_ref
        golden_mc = install_id_minecraft_version(golden_version)
        if golden_mc != world_version:
            print(
                f"Golden snapshot is Minecraft {golden_mc or golden_version}; "
                f"this world is pinned to {world_version}. "
                "Not changing the save's Minecraft version.",
                file=sys.stderr,
                flush=True,
            )
            mode = "attempt"
            golden_ref = None
        elif not install_tree_ready(golden_loader, golden_version):
            print(
                f"Golden install missing ({install_tree(golden_loader, golden_version)}); "
                f"attempting Minecraft {world_version} ({loader})",
                file=sys.stderr,
            )
            mode = "attempt"
            golden_ref = None
    if mode == "golden":
        assert golden_ref is not None
        loader, version = golden_ref
        print(
            f"Boot mode=golden requested={world_version} launching={version} ({loader})",
            flush=True,
        )
        _link_install(directory, loader, version)
        _ensure_rcon_properties(directory)
        stage_golden_snapshot(directory)
        write_boot_session(
            directory,
            mode="golden",
            loader=loader,
            minecraft_version=version,
            stock=not extra_player_jars(mods_snapshot_dir(directory)),
            proven=True,
        )
    else:
        version = world_version
        print(
            f"Boot mode=attempt launching={version} ({loader})",
            flush=True,
        )
        if not ensure_loader_install(loader, version):
            return None
        if should_restage(directory, loader=loader, version=version):
            print(
                f"Boot mode=attempt launching={version} ({loader})",
                flush=True,
            )
            consume_attempt_request(directory)
            _link_install(directory, loader, version)
            _ensure_rcon_properties(directory)
            stage_mod_snapshot(directory)
            snapshot = mods_snapshot_dir(directory)
            write_boot_session(
                directory,
                mode="attempt",
                loader=loader,
                minecraft_version=version,
                stock=not extra_player_jars(snapshot),
                needs_player=attempt_needs_player(
                    directory,
                    snapshot=snapshot,
                ),
                proven=False,
            )
        else:
            print(
                f"Boot launching current snapshot Minecraft {version} ({loader})",
                flush=True,
            )
            _ensure_rcon_properties(directory)
            ref = current_install(directory)
            if ref:
                loader, version = ref
            if ref and load_golden_install(directory) == ref:
                session = load_boot_session(directory)
                write_boot_session(
                    directory,
                    mode="golden",
                    loader=loader,
                    minecraft_version=version,
                    stock=bool(session.get("stock", True)),
                    proven=True,
                )
    install = install_tree(loader, version)
    java_opts = env_or_option("java_opts", "-Xms2G -Xmx4G")
    cmd = ["java", *java_opts.split()]
    if loader == "fabric":
        launch = fabric_launcher_jar(install, directory)
        if launch is None:
            print(
                "Missing Fabric launcher (install results SERVER=)",
                file=sys.stderr,
            )
            return None
        cmd += ["-jar", str(launch), "nogui"]
    else:
        starter = directory / "server.jar"
        if not starter.exists():
            label = "vanilla server.jar" if loader == "vanilla" else "NeoForge server.jar"
            print(f"Missing {label}", file=sys.stderr)
            return None
        cmd += ["-jar", str(starter), "nogui"]
    write_automodpack_fingerprint_file(directory)
    return cmd


def cmd_run() -> int:
    directory = profile_dir()
    cmd = prepare_game_command()
    if cmd is None:
        return 1
    os.chdir(directory)
    os.execvp(cmd[0], cmd)
    return 1


def _pack_varint(value: int) -> bytes:
    out = bytearray()
    n = int(value)
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _pack_mc_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _pack_varint(len(raw)) + raw


def _read_varint(sock) -> int:
    shift = 0
    result = 0
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise OSError("short varint")
        byte = chunk[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
        shift += 7
        if shift > 35:
            raise OSError("varint too long")


def read_server_properties(directory: Path) -> dict[str, str]:
    path = directory / "server.properties"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out


def upsert_server_properties(directory: Path, updates: dict[str, str]) -> None:
    path = directory / "server.properties"
    current = read_server_properties(directory)
    current.update({k: v for k, v in updates.items() if v != ""})
    lines = [f"{key}={value}" for key, value in current.items()]
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rcon_password_path() -> Path:
    return state_dir() / "rcon.password"


def _ensure_rcon_password() -> str:
    path = _rcon_password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    import secrets

    password = secrets.token_urlsafe(18)
    path.write_text(password + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return password


def _ensure_rcon_properties(directory: Path) -> None:
    password = _ensure_rcon_password()
    props = read_server_properties(directory)
    updates = {
        "enable-rcon": "true",
        "rcon.password": password,
        "broadcast-rcon-to-ops": "false",
        "enable-status": "true",
    }
    if not str(props.get("rcon.port") or "").strip():
        updates["rcon.port"] = (os.environ.get("RCON_PORT") or "").strip() or "25575"
    upsert_server_properties(directory, updates)


def _bind_port(props: dict[str, str], key: str, env_key: str) -> int | None:
    raw = str(props.get(key) or os.environ.get(env_key) or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    return port


def parse_java_list_response(text: str) -> int | None:
    """Player count from RCON ``list``. Names after a colon; empty after colon is 0."""

    blob = (text or "").strip()
    if not blob:
        return None
    if ":" not in blob:
        return None
    after = blob.rsplit(":", 1)[-1].strip()
    if not after:
        return 0
    names = [part.strip() for part in after.split(",") if part.strip()]
    return len(names)


def _rcon_recv_exact(sock, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise OSError("short rcon read")
        buf += chunk
    return buf


def rcon_command(host: str, port: int, password: str, command: str) -> str | None:
    import socket
    import struct

    def pack(req_id: int, kind: int, body: str) -> bytes:
        payload = body.encode("utf-8") + b"\x00\x00"
        data = struct.pack("<ii", req_id, kind) + payload
        return struct.pack("<i", len(data)) + data

    def read_packet(sock: socket.socket) -> tuple[int, str]:
        header = _rcon_recv_exact(sock, 4)
        length = struct.unpack("<i", header)[0]
        if length < 10 or length > 4096:
            raise OSError("bad rcon length")
        body = _rcon_recv_exact(sock, length)
        req_id, _kind = struct.unpack("<ii", body[:8])
        payload = body[8:]
        if payload.endswith(b"\x00\x00"):
            payload = payload[:-2]
        return req_id, payload.decode("utf-8", errors="replace")

    sock = socket.create_connection((host, port), timeout=2.0)
    try:
        sock.sendall(pack(1, 3, password))
        req_id, _ = read_packet(sock)
        if req_id == -1:
            return None
        sock.sendall(pack(2, 2, command))
        req_id, text = read_packet(sock)
        if req_id == -1:
            return None
        return text
    finally:
        sock.close()


def minecraft_status_payload(host: str, port: int) -> dict[str, Any]:
    """Java status ping. Only keys the packet actually contained."""

    import socket
    import struct

    out: dict[str, Any] = {}
    sock = socket.create_connection((host, port), timeout=2.0)
    try:
        host_bytes = _pack_mc_str(host)
        handshake = (
            b"\x00"
            + _pack_varint(0)
            + host_bytes
            + struct.pack(">H", int(port))
            + _pack_varint(1)
        )
        sock.sendall(_pack_varint(len(handshake)) + handshake)
        sock.sendall(_pack_varint(1) + b"\x00")
        length = _read_varint(sock)
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                break
            payload += chunk
        if not payload or payload[0] != 0:
            return out
        rest = payload[1:]
        n = 0
        shift = 0
        idx = 0
        while idx < len(rest):
            byte = rest[idx]
            n |= (byte & 0x7F) << shift
            idx += 1
            if not byte & 0x80:
                break
            shift += 7
        raw = rest[idx : idx + n]
        data = json.loads(raw.decode("utf-8"))
        return findings_from_status_json(data)
    finally:
        sock.close()


def findings_from_status_json(data: Any) -> dict[str, Any]:
    """Map a decoded status ping object to asserted probe keys only."""

    out: dict[str, Any] = {}
    if not isinstance(data, dict):
        return out
    out["ready"] = True
    version = data.get("version")
    if isinstance(version, dict):
        name = str(version.get("name") or "").strip()
        if name:
            out["game_version"] = name
    players = data.get("players")
    if isinstance(players, dict) and "online" in players and players["online"] is not None:
        try:
            if isinstance(players["online"], bool):
                raise ValueError("bool")
            out["player_count"] = int(players["online"])
        except (TypeError, ValueError):
            pass
    return out


def cmd_status_probe() -> int:
    """Supervisor status_probe argv: JSON object, omitted keys mean no yield.

    Occupancy comes from the Java status ping (SLP). Do not open RCON here:
    each probe is a new process, so RCON would connect and disconnect every
    tick and flood the game log with client start/shutdown lines. SLP already
    asserts ``player_count`` and ``ready``.
    """

    directory = profile_dir()
    props = read_server_properties(directory)
    findings: dict[str, Any] = {}
    game_port = _bind_port(props, "server-port", "SERVER_PORT") or 25565
    try:
        status = minecraft_status_payload("127.0.0.1", game_port)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        status = {}
    sync_automodpack_identity(directory)
    if "ready" in status:
        findings["ready"] = True
        write_automodpack_fingerprint_file(directory)
    if "game_version" in status:
        findings["game_version"] = status["game_version"]
    if "player_count" in status:
        findings["player_count"] = status["player_count"]
    print(json.dumps(findings, separators=(",", ":")), flush=True)
    from golden_boot import apply_probe_findings

    apply_probe_findings(directory, findings)
    return 0


def _sweep_drop_junk(folder: Path) -> None:
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".PARTIAL") or name.endswith(".partial"):
            path.unlink(missing_ok=True)
            continue
        try:
            empty_jar = path.suffix.lower() == ".jar" and path.stat().st_size == 0
        except OSError:
            continue
        if empty_jar:
            path.unlink(missing_ok=True)


def fabric_launcher_jar(install: Path, directory: Path) -> Path | None:
    entry = helper_server_entry(install)
    if entry is None:
        return None
    linked = directory / entry.name
    if linked.exists():
        return linked
    if entry.exists():
        return entry
    return None


def cmd_write_copyparty_banner() -> int:
    """HTML banner on the mod-upload folder (Copyparty has no dots perm)."""

    uploaded = uploaded_mods_dir()
    uploaded.mkdir(parents=True, exist_ok=True)
    _sweep_drop_junk(uploaded)
    from publish_mod import sweep_uploaded_non_jars

    sweep_uploaded_non_jars(uploaded)
    (uploaded / ".prologue.html").write_text(
        """\
<div style="max-width:42rem;margin:1rem 0 1.25rem;padding:1rem 1.15rem;\
background:#241c12;color:#f2e6c9;border-left:4px solid #5aad32;\
font-family:sans-serif;line-height:1.45">
  <strong>Minecraft mods</strong>
  <p style="margin:0.6rem 0 0">This is the <em>upload</em> folder, not the
  running server&rsquo;s copy. Drop a <code>.jar</code> to add or replace
  a mod (same mod id replaces the last build even if the filename is
  different). Only <code>.jar</code> files are accepted. Delete a jar to
  take it off next restart (not AutoModpack).
  An empty folder runs vanilla. Fabric jars run Fabric, NeoForge jars
  run NeoForge, for this world&rsquo;s Minecraft version. Do not mix them.</p>
  <p style="margin:0.6rem 0 0">The game keeps the last proven snapshot
  until a new upload is ready to try. If anyone is playing, it
  waits until the last player leaves, then restarts. Then relaunch
  Minecraft if AutoModpack asks.</p>
  <p style="margin:0.6rem 0 0">After the server has started once, copy
  <code>AUTOMODPACK-FINGERPRINT.txt</code> (read-only) and paste it when
  the Minecraft client warns about mods. Every world shows the same
  fingerprint.</p>
</div>
""",
        encoding="utf-8",
    )
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    handlers = {
        "print-version": cmd_print_version,
        "install": cmd_install,
        "prepare-world": cmd_prepare_world,
        "run": cmd_run,
        "write-copyparty-banner": cmd_write_copyparty_banner,
        "status-probe": cmd_status_probe,
    }
    if cmd not in handlers:
        print(
            "Usage: haos_defaults.py print-version|install|prepare-world|run|"
            "write-copyparty-banner|status-probe",
            file=sys.stderr,
        )
        return 2
    try:
        return handlers[cmd]()
    except MinecraftPinError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"command failed: {exc}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
