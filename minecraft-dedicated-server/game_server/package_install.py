"""Non-SteamCMD install/update for games that declare ``package_install``.

Kinds:

- ``http_archive`` — GET a version JSON document, download a tarball, extract
- ``command`` — run plugin argv to print a version and to install/update

No game names or URLs live here — plugins supply version/download templates
or argv. Command installers may block on a human (device-code login) and
write ``operator_action.json`` under ``STATE_DIR`` for the Ingress card.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .privileges import make_preexec
from .update_check import UpdateCheckResult

LOG = logging.getLogger("game_server.package")

LineCallback = Callable[[str], None]


class _PackagePlugin(Protocol):
    install_marker: str
    package_install: Any


_SUPPORTED_KINDS = frozenset({"http_archive", "command"})
VERSION_FILENAME = ".package_version"


class PackageInstallError(RuntimeError):
    pass


def _marker_installed(install_dir: str | Path, marker_relative: str) -> bool:
    return (Path(install_dir) / marker_relative).exists()


@dataclass
class PackageInstallSpec:
    """Declarative install source from ``game.yaml``."""

    kind: str
    version_url: str = ""
    version_json_path: str = ""
    download_url: str = ""
    strip_components: int = 1
    version_filename: str = VERSION_FILENAME
    version_argv: list[str] = field(default_factory=list)
    install_argv: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "PackageInstallSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("package_install must be a mapping when provided")
        kind = str(data.get("kind") or "").strip().lower()
        if kind not in _SUPPORTED_KINDS:
            raise ValueError(
                f"Unsupported package_install.kind {kind!r}; "
                "expected http_archive or command"
            )
        version_filename = str(data.get("version_filename") or VERSION_FILENAME).strip()
        if not version_filename:
            version_filename = VERSION_FILENAME
        if kind == "command":
            version_argv = _coerce_argv(data.get("version_argv"), "version_argv")
            install_argv = _coerce_argv(data.get("install_argv"), "install_argv")
            if not version_argv or not install_argv:
                raise ValueError(
                    "package_install.kind command requires version_argv and install_argv"
                )
            return cls(
                kind=kind,
                version_argv=version_argv,
                install_argv=install_argv,
                version_filename=version_filename,
            )
        version_url = str(data.get("version_url") or "").strip()
        version_json_path = str(data.get("version_json_path") or "").strip()
        download_url = str(data.get("download_url") or "").strip()
        if not version_url or not version_json_path or not download_url:
            raise ValueError(
                "package_install requires version_url, version_json_path, and download_url"
            )
        strip = int(data.get("strip_components") or 1)
        if strip < 0:
            raise ValueError("package_install.strip_components must be >= 0")
        return cls(
            kind=kind,
            version_url=version_url,
            version_json_path=version_json_path,
            download_url=download_url,
            strip_components=strip,
            version_filename=version_filename,
        )


def _coerce_argv(raw: Any, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"package_install.{field_name} must be a non-empty list")
    argv = [str(x) for x in raw if str(x).strip()]
    if not argv:
        raise ValueError(f"package_install.{field_name} must be a non-empty list")
    return argv


def _json_path(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            raise PackageInstallError(
                f"version_json_path {dotted!r} not found in version response"
            )
        cur = cur[part]
    return cur


def fetch_remote_version(
    spec: PackageInstallSpec,
    *,
    timeout: float = 60.0,
    install_dir: str | Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    on_line: LineCallback | None = None,
    stop_event: threading.Event | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> str:
    """Return the remote version string (JSON path or command stdout)."""

    if spec.kind == "command":
        cwd = Path(install_dir) if install_dir is not None else Path.cwd()
        cwd.mkdir(parents=True, exist_ok=True)
        output = _run_argv(
            spec.version_argv,
            cwd=cwd,
            extra_env=extra_env,
            timeout=timeout,
            on_line=on_line,
            stop_event=stop_event,
            run_uid=run_uid,
            run_gid=run_gid,
            label="version",
        )
        version = _version_from_command_output(output)
        if not version:
            raise PackageInstallError("version_argv printed an empty version")
        return version

    try:
        request = urllib.request.Request(
            spec.version_url,
            headers={"User-Agent": "hassio-game-server/1"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PackageInstallError(
            f"Failed fetching version from {spec.version_url}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageInstallError(
            f"Invalid JSON from version URL {spec.version_url}: {exc}"
        ) from exc
    value = _json_path(payload, spec.version_json_path)
    version = str(value or "").strip()
    if not version:
        raise PackageInstallError(
            f"Empty version at {spec.version_json_path!r} from {spec.version_url}"
        )
    return version


def _version_from_command_output(output: str) -> str:
    """Last non-empty line of command stdout is the version string."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def version_path(install_dir: str | Path, spec: PackageInstallSpec) -> Path:
    return Path(install_dir) / spec.version_filename


def read_local_version(install_dir: str | Path, spec: PackageInstallSpec) -> str | None:
    path = version_path(install_dir, spec)
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            return text or None
    except OSError:
        LOG.debug("Could not read package version file", exc_info=True)
    return None


def write_local_version(
    install_dir: str | Path, spec: PackageInstallSpec, version: str
) -> None:
    path = version_path(install_dir, spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(version.strip() + "\n", encoding="utf-8")
    tmp.replace(path)


def download_url_for(spec: PackageInstallSpec, version: str) -> str:
    if "{version}" not in spec.download_url:
        return spec.download_url
    return spec.download_url.replace("{version}", version)


def _emit(on_line: LineCallback | None, message: str) -> None:
    LOG.info("[package] %s", message)
    if on_line is not None:
        try:
            on_line(message)
        except Exception:  # noqa: BLE001
            LOG.debug("package on_line callback failed", exc_info=True)


def _command_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        for key, value in extra_env.items():
            if key:
                env[str(key)] = str(value)
    return env


def _run_argv(
    argv: list[str],
    *,
    cwd: Path,
    extra_env: Mapping[str, str] | None,
    timeout: float,
    on_line: LineCallback | None,
    stop_event: threading.Event | None,
    run_uid: int | None,
    run_gid: int | None,
    label: str,
) -> str:
    """Run argv, stream stdout to logs, return combined output. Raises on non-zero."""

    _emit(on_line, f"Running {label}: {' '.join(argv)}")
    preexec = make_preexec(run_uid, run_gid)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=_command_env(extra_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            preexec_fn=preexec,
        )
    except OSError as exc:
        raise PackageInstallError(f"Failed starting {label} command: {exc}") from exc

    chunks: list[str] = []
    started = time.monotonic()
    stopped = False
    timed_out = False

    def _reader() -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                chunks.append(raw)
                _emit(on_line, raw.rstrip("\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass

    # Reader thread so stop_event is noticed during a silent OAuth wait
    # (blocking readline() used to hold SIGTERM until the next downloader line).
    reader = threading.Thread(
        target=_reader, name=f"package-{label}-stdout", daemon=True
    )
    reader.start()
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                LOG.info("Stop requested; terminating %s command", label)
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                break
            if timeout > 0 and (time.monotonic() - started) > timeout:
                timed_out = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                break
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=30)

    if stopped:
        raise PackageInstallError(f"Stopped while running {label} command")
    if timed_out:
        raise PackageInstallError(
            f"{label} command timed out after {timeout:.0f}s"
        )
    output = "".join(chunks)
    if proc.returncode != 0:
        raise PackageInstallError(
            f"{label} command exited {proc.returncode}"
        )
    return output


def _download_file(
    url: str,
    dest: Path,
    *,
    timeout: float,
    on_line: LineCallback | None,
    stop_event: threading.Event | None,
) -> None:
    _emit(on_line, f"Downloading {url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "hassio-game-server/1"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            total = resp.headers.get("Content-Length")
            total_n = int(total) if total and str(total).isdigit() else None
            wrote = 0
            last_report = 0
            with dest.open("wb") as out:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise PackageInstallError("Stopped while downloading package")
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    wrote += len(chunk)
                    if total_n and wrote - last_report >= max(total_n // 10, 1):
                        pct = int(wrote * 100 / total_n)
                        _emit(on_line, f"Download progress {pct}% ({wrote}/{total_n} bytes)")
                        last_report = wrote
    except PackageInstallError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PackageInstallError(f"Download failed from {url}: {exc}") from exc
    _emit(on_line, f"Download complete ({dest.stat().st_size} bytes)")


def _safe_members(tar: tarfile.TarFile, strip: int) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        parts = Path(member.name).parts
        if strip and len(parts) <= strip:
            continue
        if strip:
            member.name = str(Path(*parts[strip:]))
        # Block path escape.
        target = Path(member.name)
        if target.is_absolute() or ".." in target.parts:
            raise PackageInstallError(f"Refusing unsafe archive path: {member.name}")
        members.append(member)
    return members


def _extract_archive(
    archive: Path,
    install_dir: Path,
    *,
    strip_components: int,
    on_line: LineCallback | None,
) -> None:
    """Extract archive and replace ``install_dir`` contents (no merge).

    Merging left deleted upstream files in place (e.g. Factorio 2.0→2.1 moved
    ``quality/.../recycling.lua`` into ``recycler/``; a merge kept the stale
    file and the game crashed). Always swap in a clean tree instead.
    """

    _emit(on_line, f"Extracting archive into {install_dir} (clean replace)")
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    # Staging + backup live beside install_dir so renames stay on one filesystem.
    with tempfile.TemporaryDirectory(
        prefix="pkg-extract-", dir=str(install_dir.parent)
    ) as tmp:
        staging = Path(tmp) / "root"
        staging.mkdir()
        try:
            with tarfile.open(archive, mode="r:*") as tar:
                members = _safe_members(tar, strip_components)
                # filter='data' is the Python 3.12+ safe default; ignore on older.
                try:
                    tar.extractall(path=staging, members=members, filter="data")
                except TypeError:
                    tar.extractall(path=staging, members=members)
        except (tarfile.TarError, OSError) as exc:
            raise PackageInstallError(f"Failed extracting archive: {exc}") from exc

        backup = Path(str(install_dir) + ".replace-old")
        if backup.exists():
            shutil.rmtree(backup)
        replaced = False
        try:
            if install_dir.exists():
                install_dir.rename(backup)
                replaced = True
            staging.rename(install_dir)
        except OSError as exc:
            # Best-effort rollback if the swap failed mid-way.
            if not install_dir.exists() and backup.exists():
                try:
                    backup.rename(install_dir)
                except OSError:
                    LOG.warning(
                        "Failed restoring install dir after extract error",
                        exc_info=True,
                    )
            raise PackageInstallError(
                f"Failed replacing install tree at {install_dir}: {exc}"
            ) from exc
        if replaced and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    _emit(on_line, "Extract complete")


def _chown_tree(root: Path, run_uid: int, run_gid: int | None) -> None:
    if not hasattr(os, "chown"):
        return
    try:
        for path in root.rglob("*"):
            os.chown(path, run_uid, run_gid if run_gid is not None else -1)
        os.chown(root, run_uid, run_gid if run_gid is not None else -1)
    except OSError:
        LOG.debug("chown after package install failed", exc_info=True)


def install_or_update(
    install_dir: str | Path,
    plugin: _PackagePlugin,
    *,
    force: bool = False,
    timeout: float = 1800.0,
    stop_event: threading.Event | None = None,
    on_line: LineCallback | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    """Install or update from package_install. Returns the installed version."""

    spec = plugin.package_install
    if spec is None:
        raise PackageInstallError("plugin has no package_install configuration")
    root = Path(install_dir)
    root.mkdir(parents=True, exist_ok=True)
    local = read_local_version(root, spec)
    installed = _marker_installed(root, plugin.install_marker)

    if spec.kind == "command":
        return _install_or_update_command(
            root,
            plugin,
            spec,
            force=force,
            local=local,
            installed=installed,
            stop_event=stop_event,
            on_line=on_line,
            run_uid=run_uid,
            run_gid=run_gid,
            extra_env=extra_env,
        )

    remote = fetch_remote_version(spec, timeout=min(60.0, timeout))
    if installed and local == remote and not force:
        _emit(on_line, f"Already up to date (version {remote})")
        return remote

    _emit(
        on_line,
        f"Installing package version {remote}"
        + (f" (was {local})" if local else " (fresh install)"),
    )
    url = download_url_for(spec, remote)
    with tempfile.TemporaryDirectory(prefix="pkg-dl-", dir=str(root.parent)) as tmp:
        archive = Path(tmp) / "package.tar"
        _download_file(
            url,
            archive,
            timeout=timeout,
            on_line=on_line,
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            raise PackageInstallError("Stopped before extract")
        _extract_archive(
            archive,
            root,
            strip_components=spec.strip_components,
            on_line=on_line,
        )

    if not _marker_installed(root, plugin.install_marker):
        raise PackageInstallError(
            f"Package extract finished but install marker missing: {plugin.install_marker}"
        )
    write_local_version(root, spec, remote)
    if run_uid is not None:
        _chown_tree(root, run_uid, run_gid)
    _emit(on_line, f"Installed version {remote}")
    return remote


def _install_or_update_command(
    root: Path,
    plugin: _PackagePlugin,
    spec: PackageInstallSpec,
    *,
    force: bool,
    local: str | None,
    installed: bool,
    stop_event: threading.Event | None,
    on_line: LineCallback | None,
    run_uid: int | None,
    run_gid: int | None,
    extra_env: Mapping[str, str] | None,
) -> str:
    """Command-kind install (plugin argv). HTTP archive stays on install_or_update."""

    env = dict(extra_env or {})
    env.setdefault("INSTALL_DIR", str(root))
    remote: str | None = None
    if installed and not force:
        remote = fetch_remote_version(
            spec,
            timeout=60.0,
            install_dir=root,
            extra_env=env,
            on_line=on_line,
            stop_event=stop_event,
            run_uid=run_uid,
            run_gid=run_gid,
        )
        if local == remote:
            _emit(on_line, f"Already up to date (version {remote})")
            return remote

    _emit(
        on_line,
        f"Installing package version {remote or 'unknown'}"
        + (f" (was {local})" if local else " (fresh install)"),
    )
    # Device-code login + large payloads: honor stop_event, no wall clock.
    _run_argv(
        spec.install_argv,
        cwd=root,
        extra_env=env,
        timeout=0,
        on_line=on_line,
        stop_event=stop_event,
        run_uid=run_uid,
        run_gid=run_gid,
        label="install",
    )
    remote = fetch_remote_version(
        spec,
        timeout=60.0,
        install_dir=root,
        extra_env=env,
        on_line=on_line,
        stop_event=stop_event,
        run_uid=run_uid,
        run_gid=run_gid,
    )
    if not _marker_installed(root, plugin.install_marker):
        raise PackageInstallError(
            f"Package install finished but install marker missing: {plugin.install_marker}"
        )
    if not remote:
        raise PackageInstallError("Installer finished without a version string")
    write_local_version(root, spec, remote)
    if run_uid is not None:
        _chown_tree(root, run_uid, run_gid)
    _emit(on_line, f"Installed version {remote}")
    return remote


def update_available(
    install_dir: str | Path,
    plugin: _PackagePlugin,
    *,
    timeout: float = 60.0,
    extra_env: Mapping[str, str] | None = None,
) -> UpdateCheckResult:
    """Compare local package version to remote (same shape as Steam checks)."""

    spec = plugin.package_install
    if spec is None:
        return UpdateCheckResult(
            update_available=False,
            local_build_id=None,
            remote_build_id=None,
            error="no package_install configured",
        )
    local = read_local_version(install_dir, spec)
    try:
        if spec.kind == "command":
            env = dict(extra_env or {})
            env.setdefault("INSTALL_DIR", str(install_dir))
            remote = fetch_remote_version(
                spec,
                timeout=timeout,
                install_dir=install_dir,
                extra_env=env,
            )
        else:
            remote = fetch_remote_version(spec, timeout=timeout)
    except PackageInstallError as exc:
        return UpdateCheckResult(
            update_available=False,
            local_build_id=local,
            remote_build_id=None,
            error=str(exc),
        )
    return UpdateCheckResult(
        update_available=bool(remote) and remote != local,
        local_build_id=local,
        remote_build_id=remote,
        error=None,
    )
