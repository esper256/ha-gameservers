"""Minecraft add-on helpers: loader install, world profiles, Copyparty banner.

Kept out of game-server-base so Fabric/NeoForge names stay in this folder.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

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
    if key in opt and str(opt.get(key) or "").strip():
        return str(opt[key]).strip()
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


def minecraft_pin_source() -> tuple[str, str]:
    """Desired pin: HA options.json. Compose env only if that file is absent."""

    path = options_path()
    if path.is_file():
        data, err = _read_options_file()
        if err:
            raise MinecraftPinError(f"Cannot read Minecraft pin from {path}: {err}")
        version = _normalize_mc_version(data.get("minecraft_version"))
        if not version:
            raise MinecraftPinError(
                f"minecraft_version missing or invalid in {path}: "
                f"{data.get('minecraft_version')!r}"
            )
        return version, str(path)
    env = _normalize_mc_version(os.environ.get("MINECRAFT_VERSION"))
    if env:
        return env, "MINECRAFT_VERSION (no options.json)"
    raise MinecraftPinError(
        f"No Minecraft pin: {path} is missing and MINECRAFT_VERSION is unset"
    )


def minecraft_version() -> str:
    """HA Configuration pin from options.json (compose env if that file is absent)."""

    return minecraft_pin_source()[0]


def explain_minecraft_pin() -> str:
    version, source = minecraft_pin_source()
    return f"Minecraft pin {version} from {source}"


def _loader_pin_from_options(key: str, *, kind: str) -> tuple[str, str]:
    """Loader pin from options.json only. Missing/empty is latest."""

    path = options_path()
    if not path.is_file():
        return "latest", f"default latest (no {path.name})"
    data, err = _read_options_file()
    if err:
        raise MinecraftPinError(f"Cannot read {key} from {path}: {err}")
    if key not in data:
        return "latest", f"default latest ({path})"
    raw = data.get(key)
    if str(raw or "").strip() == "":
        return "latest", f"default latest ({path})"
    pin = _normalize_loader_pin(raw, kind=kind)
    if not pin:
        raise MinecraftPinError(f"{key} missing or invalid in {path}: {raw!r}")
    return pin, str(path)


def neoforge_version() -> str:
    """HA NeoForge pin: latest, beta, or an exact id (example 21.11.10-beta)."""

    return _loader_pin_from_options("neoforge_version", kind="neoforge")[0]


def fabric_loader_version() -> str:
    """HA Fabric loader pin: latest, or an exact loader id."""

    return _loader_pin_from_options("fabric_loader_version", kind="fabric")[0]


def loader_pin(loader: str) -> str:
    if loader == "fabric":
        return fabric_loader_version()
    return neoforge_version()


def desired_install_id(loader: str, mc_version: str | None = None) -> str:
    """Folder suffix after ``{loader}-``: MC version, plus pin when not latest."""

    mc = mc_version if mc_version is not None else minecraft_version()
    pin = loader_pin(loader)
    if pin == "latest":
        return mc
    return f"{mc}-{pin}"


def describe_loader_install(
    loader: str, mc_version: str, pin: str | None = None
) -> str:
    """Human label: Minecraft 1.21.11 (neoforge beta), not 1.21.11-beta."""

    pin = loader_pin(loader) if pin is None else pin
    if pin == "latest":
        return f"Minecraft {mc_version} ({loader})"
    return f"Minecraft {mc_version} ({loader} {pin})"


def unavailable_loader_message(loader: str, mc_version: str, pin: str) -> str:
    """Explain a missing loader channel without dumping the helper argv."""

    label = "NeoForge" if loader == "neoforge" else "Fabric loader"
    if pin == "beta":
        return (
            f"No {label} beta for Minecraft {mc_version}. "
            f"Use latest, an exact {label} id, or a Minecraft version that "
            f"publishes a {label} beta (for example 1.21.11)."
        )
    return f"Could not install {label} {pin} for Minecraft {mc_version}."


def fallback_install_ref(
    directory: Path, *, loader: str, mc_version: str, pin: str
) -> tuple[str, str] | None:
    """Last proven tree, else an already-installed latest tree for this MC."""

    from golden_boot import load_golden_install

    golden = load_golden_install(directory)
    if golden is not None and install_tree_ready(golden[0], golden[1]):
        return golden
    if pin != "latest" and install_tree_ready(loader, mc_version):
        return loader, mc_version
    current = current_install(directory)
    if current is not None and install_tree_ready(current[0], current[1]):
        return current
    return None


def explain_loader_pins() -> str:
    neo, neo_src = _loader_pin_from_options("neoforge_version", kind="neoforge")
    fabric, fabric_src = _loader_pin_from_options(
        "fabric_loader_version", kind="fabric"
    )
    return (
        f"NeoForge pin {neo} from {neo_src}; "
        f"Fabric loader pin {fabric} from {fabric_src}"
    )


def loaders_ready_token() -> str:
    return (
        f"{minecraft_version()} "
        f"fabric={fabric_loader_version()} "
        f"neoforge={neoforge_version()}"
    )


def loaders_ready_matches(marker_text: str) -> bool:
    text = (marker_text or "").strip()
    if text == loaders_ready_token():
        return True
    mc = minecraft_version()
    if (
        text == mc
        and fabric_loader_version() == "latest"
        and neoforge_version() == "latest"
    ):
        return True
    return False


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
    print(explain_minecraft_pin(), file=sys.stderr, flush=True)
    print(explain_loader_pins(), file=sys.stderr, flush=True)
    print(minecraft_version(), flush=True)
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


def fabric_install_args(output: Path, *, mc_version: str, pin: str) -> list[str]:
    args = [
        "install-fabric-loader",
        f"--minecraft-version={mc_version}",
        f"--output-directory={output}",
        f"--results-file={_results_file(output)}",
    ]
    if pin != "latest":
        args.append(f"--loader-version={pin}")
    return args


def neoforge_install_args(output: Path, *, mc_version: str, pin: str) -> list[str]:
    return [
        "install-neoforge",
        f"--minecraft-version={mc_version}",
        f"--neoforge-version={pin}",
        f"--output-directory={output}",
        f"--results-file={_results_file(output)}",
    ]


def cmd_install() -> int:
    version = minecraft_version()
    fabric_pin = fabric_loader_version()
    neo_pin = neoforge_version()
    print(explain_minecraft_pin(), flush=True)
    print(explain_loader_pins(), flush=True)
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    fabric = install_tree("fabric", desired_install_id("fabric", version))
    neoforge = install_tree("neoforge", desired_install_id("neoforge", version))
    marker = root / ".loaders_ready"
    marker_text = ""
    if marker.is_file():
        try:
            marker_text = marker.read_text(encoding="utf-8")
        except OSError:
            marker_text = ""
    if (
        loaders_ready_matches(marker_text)
        and fabric.is_dir()
        and neoforge.is_dir()
        and _results_file(fabric).is_file()
        and _results_file(neoforge).is_file()
    ):
        if STARTER_JAR.is_file() and not (neoforge / "server.jar").exists():
            shutil.copy2(STARTER_JAR, neoforge / "server.jar")
        print(version, flush=True)
        return 0
    fabric.mkdir(parents=True, exist_ok=True)
    neoforge.mkdir(parents=True, exist_ok=True)
    _run_helper(fabric_install_args(fabric, mc_version=version, pin=fabric_pin))
    _run_helper(neoforge_install_args(neoforge, mc_version=version, pin=neo_pin))
    if STARTER_JAR.is_file():
        shutil.copy2(STARTER_JAR, neoforge / "server.jar")
    marker.write_text(f"{loaders_ready_token()}\n", encoding="utf-8")
    print(version, flush=True)
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


def cmd_prepare_world() -> int:
    directory = profile_dir()
    directory.mkdir(parents=True, exist_ok=True)
    created = _consume_world_create(directory)
    profile = read_profile(directory)
    loader = str(
        created.get("mod_loader")
        or profile.get("loader")
        or "neoforge"
    ).strip().lower()
    if loader not in {"neoforge", "fabric"}:
        loader = "neoforge"
    version = minecraft_version()
    install_id = desired_install_id(loader, version)
    print(explain_minecraft_pin(), flush=True)
    print(explain_loader_pins(), flush=True)
    write_json(directory / "profile.json", {"loader": loader})
    (directory / "world").mkdir(parents=True, exist_ok=True)
    (directory / "config").mkdir(parents=True, exist_ok=True)
    migrate_legacy_mods(directory)
    uploaded_mods_dir(directory).mkdir(parents=True, exist_ok=True)
    eula = env_or_option("eula", "true").lower() in {"1", "true", "yes", "on"}
    (directory / "eula.txt").write_text(
        f"eula={'true' if eula else 'false'}\n", encoding="utf-8"
    )
    _write_server_properties(directory)
    current = current_install(directory)
    if current is not None and current != (loader, install_id):
        _clear_seeded_infrastructure(directory)
    _seed_infrastructure(directory, loader, version)
    cmd_write_copyparty_banner()
    return 0


def _write_server_properties(directory: Path) -> None:
    motd = env_or_option("server_motd", "A Minecraft Server")
    slots = env_or_option("server_slots", "8")
    online = env_or_option("online_mode", "true").lower()
    whitelist = env_or_option("white_list", "true").lower()
    port = os.environ.get("SERVER_PORT") or ""
    rcon_port = os.environ.get("RCON_PORT") or ""
    password = _ensure_rcon_password()
    lines = [
        f"motd={motd}",
        f"max-players={slots}",
        f"online-mode={'true' if online in {'1', 'true', 'yes', 'on'} else 'false'}",
        f"white-list={'true' if whitelist in {'1', 'true', 'yes', 'on'} else 'false'}",
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
    """Read loader + install id (``neoforge-1.21.1`` or ``neoforge-1.21.11-beta``)."""

    name = path.name
    for loader in ("neoforge", "fabric"):
        prefix = f"{loader}-"
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix) :]
        mc, sep, pin = rest.partition("-")
        if not _normalize_mc_version(mc):
            continue
        if not sep:
            return loader, mc
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
    return (install / "server.jar").exists() or (install / "run.sh").exists()


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
    profile = read_profile(directory)
    loader = str(profile.get("loader") or "neoforge").lower()
    if loader not in {"neoforge", "fabric"}:
        loader = "neoforge"
    ha_version = minecraft_version()
    print(explain_minecraft_pin(), flush=True)
    print(explain_loader_pins(), flush=True)
    mode = choose_boot_mode(directory, ha_version=ha_version, loader=loader)
    golden_ref = load_golden_install(directory) if mode == "golden" else None
    if mode == "golden" and golden_ref is None:
        mode = "attempt"
    if mode == "golden":
        assert golden_ref is not None
        golden_loader, golden_version = golden_ref
        if not install_tree_ready(golden_loader, golden_version):
            print(
                f"Golden install missing ({install_tree(golden_loader, golden_version)}); "
                f"attempting Minecraft {ha_version} ({loader})",
                file=sys.stderr,
            )
            mode = "attempt"
            golden_ref = None
    if mode == "golden":
        assert golden_ref is not None
        loader, version = golden_ref
        print(
            f"Boot mode=golden requested={ha_version} launching={version} ({loader})",
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
        pin = loader_pin(loader)
        version = desired_install_id(loader, ha_version)
        print(
            f"Boot mode=attempt launching {describe_loader_install(loader, ha_version, pin)}",
            flush=True,
        )
        used_golden_fallback = False
        if not install_tree_ready(loader, version):
            print(
                f"Installing {describe_loader_install(loader, ha_version, pin)} "
                f"into {install_dir()}…",
                file=sys.stderr,
            )
            try:
                cmd_install()
            except (OSError, subprocess.CalledProcessError) as exc:
                print(
                    unavailable_loader_message(loader, ha_version, pin),
                    file=sys.stderr,
                )
                print(
                    f"Install failed for {describe_loader_install(loader, ha_version, pin)}",
                    file=sys.stderr,
                )
                fallback = fallback_install_ref(
                    directory, loader=loader, mc_version=ha_version, pin=pin
                )
                if fallback is None:
                    print(str(exc), file=sys.stderr)
                    return None
                if load_golden_install(directory) == fallback:
                    loader, version = fallback
                    print(
                        f"Keeping last proven snapshot {version} ({loader})",
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
                    used_golden_fallback = True
                else:
                    loader, version = fallback
                    print(
                        f"Launching existing {version} ({loader}) for Minecraft {ha_version}",
                        flush=True,
                    )
        if used_golden_fallback:
            pass
        elif not install_tree_ready(loader, version):
            print(
                f"Install tree missing for {describe_loader_install(loader, ha_version, pin)}: "
                f"{install_tree(loader, version)}",
                file=sys.stderr,
            )
            return None
        elif should_restage(directory, loader=loader, version=version):
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
            print("Missing NeoForge server.jar", file=sys.stderr)
            return None
        cmd += ["-jar", str(starter), "nogui"]
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
    if "ready" in status:
        findings["ready"] = True
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
    (uploaded / ".prologue.html").write_text(
        """\
<div style="max-width:42rem;margin:1rem 0 1.25rem;padding:1rem 1.15rem;\
background:#241c12;color:#f2e6c9;border-left:4px solid #5aad32;\
font-family:sans-serif;line-height:1.45">
  <strong>Minecraft mods</strong>
  <p style="margin:0.6rem 0 0">This is the <em>upload</em> folder, not the
  running server&rsquo;s copy. Drop a <code>.jar</code> to add or replace
  a mod (same mod id replaces the last build even if the filename is
  different). Delete a jar to take it off next restart (not AutoModpack).
  Use a build for this world&rsquo;s Minecraft version and loader
  (the pin on Configuration, Fabric or NeoForge per world).</p>
  <p style="margin:0.6rem 0 0">The game keeps the last proven snapshot
  until a new pin or upload is ready to try. If anyone is playing, it
  waits until the last player leaves, then restarts. Then relaunch
  Minecraft if AutoModpack asks.</p>
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
