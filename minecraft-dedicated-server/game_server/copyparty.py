"""Optional Copyparty file-drop on a live mods (or similar) directory.

Games opt in from game.yaml with a port and a path template. The supervisor
owns the process so it survives a game crash. Hook argv stay in the game
layer (validate, rename, protect).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .active_world import fetch_addon_network
from .world_save import expand_world_path_template

try:
    from haos_defaults import is_automodpack_fingerprint_name
except ImportError:  # other games using this supervisor copy
    def is_automodpack_fingerprint_name(name: str) -> bool:
        return False

LOG = logging.getLogger("game_server.copyparty")


def _coerce_argv(raw: Any) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Copyparty hook argv must be a list")
    return [str(x) for x in raw if str(x).strip()]


@dataclass
class CopypartySpec:
    root: str
    port: int = 8765
    password_option: str = "publisher_password"
    before_upload: list[str] = field(default_factory=list)
    after_idle_upload: list[str] = field(default_factory=list)
    before_delete: list[str] = field(default_factory=list)
    after_delete: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> CopypartySpec | None:
        if not data:
            return None
        if not isinstance(data, dict):
            raise ValueError("copyparty must be an object")
        root = str(data.get("root") or "").strip()
        if not root:
            raise ValueError("copyparty.root is required")
        port_raw = data.get("port", 8765)
        try:
            port = int(port_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"copyparty.port must be an integer, not {port_raw!r}") from exc
        if port < 1 or port > 65535:
            raise ValueError(f"copyparty.port out of range: {port}")
        return cls(
            root=root,
            port=port,
            password_option=str(data.get("password_option") or "publisher_password").strip()
            or "publisher_password",
            before_upload=_coerce_argv(data.get("before_upload")),
            after_idle_upload=_coerce_argv(data.get("after_idle_upload")),
            before_delete=_coerce_argv(data.get("before_delete")),
            after_delete=_coerce_argv(data.get("after_delete")),
        )


def copyparty_lan_port(
    container_port: int, network: Mapping[str, Any] | None
) -> int | None:
    """Host port for the Ingress Uploads card.

    Copyparty still binds ``container_port`` inside the add-on. Home Assistant
    Network remaps that to a host port. ``None`` means the mapping is disabled.
    When Supervisor did not return a map, fall back to the container port.
    """

    port = int(container_port)
    if network is None:
        return port
    raw: Any = None
    found = False
    for key in (f"{port}/tcp", str(port)):
        if key in network:
            raw = network[key]
            found = True
            break
    if not found:
        return port
    if raw is None or raw is False or raw == "":
        return None
    try:
        mapped = int(raw)
    except (TypeError, ValueError):
        return port
    if mapped < 1 or mapped > 65535:
        return None
    return mapped


class CopypartyPublisher:
    """Write a slim Copyparty config and keep the process running."""

    def __init__(
        self,
        spec: CopypartySpec | None,
        *,
        state_dir: str,
        data_dir: str,
        options: Mapping[str, Any],
        world_name: str,
    ) -> None:
        self._spec = spec
        self._state_dir = Path(state_dir)
        self._data_dir = data_dir
        self._options = dict(options)
        self._world_name = world_name
        self._proc: subprocess.Popen[str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._network_cache: tuple[float, Mapping[str, Any] | None] | None = None

    def set_world(self, world_name: str, options: Mapping[str, Any]) -> None:
        self._world_name = world_name
        self._options = dict(options)

    def start(self) -> None:
        if self._spec is None:
            return
        self._spawn()
        self._thread = threading.Thread(
            target=self._reaper, name="copyparty", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            _terminate(proc)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def reload_for_world(self, world_name: str, options: Mapping[str, Any]) -> None:
        if self._spec is None:
            return
        self.set_world(world_name, options)
        with self._lock:
            old = self._proc
            self._proc = None
        if old is not None:
            _terminate(old)
        if not self._stop.is_set():
            self._spawn()

    def _conf_dir(self) -> Path:
        path = self._state_dir / "copyparty"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def expanded_root(self) -> Path | None:
        if self._spec is None:
            return None
        expanded = expand_world_path_template(
            self._spec.root,
            data_dir=self._data_dir,
            world_name=self._world_name,
            options=self._options,
        )
        if not expanded:
            return None
        return Path(expanded)

    def ui_status(self) -> dict[str, Any] | None:
        """Ingress card payload, or None when this game did not opt into Copyparty."""

        if self._spec is None:
            return None
        host_port = copyparty_lan_port(self._spec.port, self._addon_network())
        if host_port is None:
            return None
        root = self.expanded_root()
        return {
            "port": int(host_port),
            "file_count": count_visible_files(root) if root is not None else 0,
        }

    def _addon_network(self) -> Mapping[str, Any] | None:
        now = time.monotonic()
        cached = self._network_cache
        if cached is not None and now - cached[0] < 30:
            return cached[1]
        network = fetch_addon_network()
        self._network_cache = (now, network)
        return network

    def _root_path(self) -> Path:
        path = self.expanded_root()
        if path is None:
            raise RuntimeError("copyparty.root did not expand (empty option?)")
        path.mkdir(parents=True, exist_ok=True)
        _sweep_partials(path)
        return path

    def _password(self) -> str:
        assert self._spec is not None
        key = self._spec.password_option
        env_key = key.upper()
        if os.environ.get(env_key):
            return str(os.environ[env_key]).strip() or "upload"
        raw = self._options.get(key)
        text = str(raw or "").strip()
        return text or "upload"

    def _write_hook(self, name: str, argv: list[str], *, idle: bool) -> Path | None:
        if not argv:
            return None
        quoted = " ".join(_shell_quote(part) for part in argv)
        path = self._conf_dir() / name
        if idle:
            # Copyparty xiu never passes argv. It writes joined absolute paths
            # on stdin via b"\n".join(paths) — no trailing newline — so a
            # `while read` loop drops the only (or last) file and publish never
            # runs. Forward stdin to the program; tests may still pass a path.
            body = f"#!/bin/sh\nexec {quoted} \"$@\"\n"
        else:
            body = f"#!/bin/sh\nexec {quoted} \"$1\"\n"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _write_config(self) -> Path:
        assert self._spec is not None
        spec = self._spec
        conf_dir = self._conf_dir()
        root = self._root_path()
        before_up = self._write_hook("on-upload-guard.sh", spec.before_upload, idle=False)
        after_up = self._write_hook("on-upload.sh", spec.after_idle_upload, idle=True)
        before_del = self._write_hook("on-delete-guard.sh", spec.before_delete, idle=False)
        after_del = self._write_hook("on-delete.sh", spec.after_delete, idle=False)
        flags = ["e2dsa"]
        if before_up is not None:
            flags.append(f"xbu: c,{before_up}")
        if after_up is not None:
            flags.append(f"xiu: i2,{after_up}")
        if before_del is not None:
            flags.append(f"xbd: c,{before_del}")
        if after_del is not None:
            flags.append(f"xad: {after_del}")
        flag_block = "\n".join(f"    {line}" for line in flags)
        conf = conf_dir / "copyparty.conf"
        conf.write_text(
            f"""\
[global]
  p: {spec.port}
  e2dsa
  no-crt
  dotpart
  hist: {conf_dir / "cphist"}
  name: Mods
  doctitle: Mods
  no-thumb
  no-acode
  no-zip
  no-lifetime
  unpost: 0
  unp-who: 0
  ui-nombar
  ui-nosrvi
  ui-notree
  ui-nolbar
  ui-noctxb
  ui-norepl

[accounts]
  mods: {self._password()}

[/]
  {root}
  accs:
    rw: mods
    d: mods
  flags:
{flag_block}
""",
            encoding="utf-8",
        )
        return conf

    def _spawn(self) -> None:
        binary = shutil.which("copyparty")
        if not binary:
            LOG.error("copyparty binary not on PATH; file-drop page disabled")
            return
        conf = self._write_config()
        LOG.info("Starting Copyparty on TCP %s root=%s", self._spec.port, self._root_path())
        proc = subprocess.Popen(  # noqa: S603
            [binary, "-c", str(conf)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with self._lock:
            self._proc = proc
        threading.Thread(target=_drain, args=(proc,), daemon=True).start()

    def _reaper(self) -> None:
        while not self._stop.wait(2):
            with self._lock:
                proc = self._proc
            if proc is None or proc.poll() is None:
                continue
            LOG.warning("Copyparty exited with %s", proc.returncode)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            if not self._stop.is_set():
                time.sleep(1)
                if not self._stop.is_set():
                    self._spawn()


def _shell_quote(part: str) -> str:
    if part.replace("_", "").replace("-", "").replace("/", "").replace(".", "").isalnum():
        return part
    return "'" + part.replace("'", "'\\''") + "'"


def count_visible_files(folder: Path | None) -> int:
    """Regular files in the drop root, skipping dots, PARTIAL names, and the fingerprint txt."""

    if folder is None or not folder.is_dir():
        return 0
    total = 0
    try:
        children = list(folder.iterdir())
    except OSError:
        return 0
    for path in children:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        name = path.name
        if name.startswith("."):
            continue
        lower = name.lower()
        if lower.endswith(".partial"):
            continue
        if is_automodpack_fingerprint_name(name):
            continue
        total += 1
    return total


def _sweep_partials(folder: Path) -> None:
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name
        try:
            if name.endswith(".PARTIAL") or name.endswith(".partial"):
                path.unlink(missing_ok=True)
                continue
            if path.stat().st_size == 0:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _drain(proc: subprocess.Popen[str]) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        text = line.rstrip("\n")
        if text:
            LOG.info("[copyparty] %s", text)
