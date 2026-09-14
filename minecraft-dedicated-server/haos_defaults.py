"""Minecraft add-on helpers: loader install, world profiles, Copyparty config.

Kept out of game-server-base so Fabric/NeoForge names stay in this folder.
"""

from __future__ import annotations

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
DEFAULT_MC = "1.21.1"
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


def options() -> dict[str, Any]:
    path = Path(os.environ.get("OPTIONS_FILE") or "/data/options.json")
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def env_or_option(key: str, default: str = "") -> str:
    env_key = key.upper()
    if os.environ.get(env_key):
        return str(os.environ[env_key]).strip()
    opt = options()
    if key in opt and str(opt.get(key) or "").strip():
        return str(opt[key]).strip()
    return default


def minecraft_version() -> str:
    raw = env_or_option("minecraft_version", DEFAULT_MC)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", raw):
        return DEFAULT_MC
    return raw


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
    return env_or_option("world_name", "FamilyWorld")


def profile_dir(name: str | None = None) -> Path:
    return worlds_dir() / (name or active_world_name())


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
    print(minecraft_version(), flush=True)
    return 0


def _run_helper(args: list[str]) -> None:
    cmd = [str(HELPER), *args]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def cmd_install() -> int:
    version = minecraft_version()
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    fabric = root / f"fabric-{version}"
    neoforge = root / f"neoforge-{version}"
    marker = root / ".loaders_ready"
    if (
        marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == version
        and fabric.is_dir()
        and neoforge.is_dir()
    ):
        if STARTER_JAR.is_file() and not (neoforge / "server.jar").exists():
            shutil.copy2(STARTER_JAR, neoforge / "server.jar")
        print(version, flush=True)
        return 0
    fabric.mkdir(parents=True, exist_ok=True)
    neoforge.mkdir(parents=True, exist_ok=True)
    _run_helper(
        [
            "install-fabric-loader",
            f"--minecraft-version={version}",
            f"--output-directory={fabric}",
        ]
    )
    _run_helper(
        [
            "install-neoforge",
            f"--minecraft-version={version}",
            f"--output-directory={neoforge}",
        ]
    )
    if STARTER_JAR.is_file():
        shutil.copy2(STARTER_JAR, neoforge / "server.jar")
    marker = root / ".loaders_ready"
    marker.write_text(f"{version}\n", encoding="utf-8")
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
    version = str(profile.get("minecraft_version") or minecraft_version())
    write_json(
        directory / "profile.json",
        {
            "loader": loader,
            "minecraft_version": version,
        },
    )
    (directory / "mods").mkdir(parents=True, exist_ok=True)
    (directory / "world").mkdir(parents=True, exist_ok=True)
    (directory / "config").mkdir(parents=True, exist_ok=True)
    eula = env_or_option("eula", "true").lower() in {"1", "true", "yes", "on"}
    (directory / "eula.txt").write_text(
        f"eula={'true' if eula else 'false'}\n", encoding="utf-8"
    )
    _write_server_properties(directory)
    _link_install(directory, loader, version)
    _seed_infrastructure(directory, loader, version)
    return 0


def _write_server_properties(directory: Path) -> None:
    motd = env_or_option("server_motd", "Family Minecraft")
    slots = env_or_option("server_slots", "8")
    online = env_or_option("online_mode", "true").lower()
    whitelist = env_or_option("white_list", "true").lower()
    port = os.environ.get("SERVER_PORT") or "25565"
    lines = [
        f"motd={motd}",
        f"max-players={slots}",
        f"online-mode={'true' if online in {'1', 'true', 'yes', 'on'} else 'false'}",
        f"white-list={'true' if whitelist in {'1', 'true', 'yes', 'on'} else 'false'}",
        f"server-port={port}",
        "server-ip=0.0.0.0",
        "level-name=world",
        "enable-status=true",
        "sync-chunk-writes=true",
    ]
    (directory / "server.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _link_install(directory: Path, loader: str, version: str) -> None:
    install = install_dir() / f"{loader}-{version}"
    if not install.is_dir():
        print(f"Install tree missing: {install}", file=sys.stderr)
        return
    for name in ("libraries", "versions", "fabric-server-launch.jar", "server.jar"):
        src = install / name
        dest = directory / name
        if dest.is_symlink() or dest.exists():
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            elif dest.is_dir() and name != "libraries":
                continue
        if src.exists():
            dest.symlink_to(src)
    unix_args = install / "unix_args.txt"
    if unix_args.is_file():
        dest = directory / "unix_args.txt"
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.symlink_to(unix_args)


def _github_json(url: str) -> Any:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)


def _seed_infrastructure(directory: Path, loader: str, version: str) -> None:
    mods = directory / "mods"
    if any(mods.glob("automodpack*.jar")):
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
            _download(str(url), mods / "automodpack.jar")
            print(f"Seeded AutoModpack from {url}", flush=True)
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"Could not seed AutoModpack: {exc}", file=sys.stderr)
    if loader == "fabric" and not any(mods.glob("fabric-api*.jar")):
        try:
            query = (
                "https://api.modrinth.com/v2/project/P7dR8mSH/version"
                f'?game_versions=["{version}"]&loaders=["fabric"]'
            )
            data = _modrinth(query)
            if isinstance(data, list) and data:
                files = data[0].get("files") or []
                if files:
                    _download(str(files[0]["url"]), mods / "fabric-api.jar")
        except (OSError, json.JSONDecodeError, TimeoutError, KeyError) as exc:
            print(f"Could not seed Fabric API: {exc}", file=sys.stderr)


def _modrinth(url: str) -> Any:
    req = Request(url, headers={"User-Agent": "haos-minecraft-addon"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def cmd_run() -> int:
    directory = profile_dir()
    profile = read_profile(directory)
    loader = str(profile.get("loader") or "neoforge").lower()
    version = str(profile.get("minecraft_version") or minecraft_version())
    _link_install(directory, loader, version)
    java_opts = env_or_option("java_opts", "-Xms2G -Xmx4G")
    os.chdir(directory)
    cmd = ["java", *java_opts.split()]
    if loader == "fabric":
        launch = directory / "fabric-server-launch.jar"
        if not launch.exists():
            print("Missing fabric-server-launch.jar", file=sys.stderr)
            return 1
        cmd += ["-jar", str(launch), "nogui"]
    else:
        starter = directory / "server.jar"
        if not starter.exists():
            print("Missing NeoForge server.jar", file=sys.stderr)
            return 1
        cmd += ["-jar", str(starter), "nogui"]
    os.execvp(cmd[0], cmd)
    return 1


def cmd_write_copyparty_config() -> int:
    password = env_or_option("publisher_password", "family")
    port = env_or_option("publisher_port", os.environ.get("PUBLISHER_PORT") or "8765")
    root = Path(os.environ.get("MOD_PUBLISHER_DIR") or "/data/mod-publisher")
    incoming = root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    hook = root / "on-upload.sh"
    hook.write_text(
        '#!/bin/sh\nexec python3 /opt/publish_mod.py "$1"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    # Keep the config out of the incoming share so kids cannot edit it.
    # {{p}} is Copyparty's uploaded-file path placeholder.
    (root / "copyparty.conf").write_text(
        f"""\
[global]
  p: {port}
  no-crt
  hist: {root / "cphist"}

[accounts]
  kids: {password}

[/]
  {incoming}
  accs:
    rw: kids
  xbu: {hook} {{p}}
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
        "write-copyparty-config": cmd_write_copyparty_config,
    }
    if cmd not in handlers:
        print(
            "Usage: haos_defaults.py print-version|install|prepare-world|run|"
            "write-copyparty-config",
            file=sys.stderr,
        )
        return 2
    try:
        return handlers[cmd]()
    except subprocess.CalledProcessError as exc:
        print(f"command failed: {exc}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
