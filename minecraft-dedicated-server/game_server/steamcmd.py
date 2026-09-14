"""SteamCMD install / update helpers with retries and build-id comparison."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .plugin import GamePlugin
from .privileges import chown_paths, make_preexec
from .steam_gate import get_gate
from .update_check import UpdateCheckResult

LOG = logging.getLogger("game_server.steamcmd")

__all__ = ["UpdateCheckResult", "SteamCMDError"]

MISSING_CONFIG_MARKERS = (
    "missing configuration",
    "missing app configuration",
)
MISSING_PERMISSION_MARKERS = (
    "missing file permissions",
)

# How long to wait for Steam to publish install config after login/app_info_update.
APP_INFO_READY_TIMEOUT_SECONDS = 90.0
APP_INFO_POLL_INTERVAL_SECONDS = 5.0

_STEAMCMD_VERSION_RE = re.compile(
    r"Steam Console Client.*?version\s+(\d+)", re.IGNORECASE
)
_steamcmd_version: str | None = None
_steamcmd_version_path: Path | None = None


class SteamCMDError(RuntimeError):
    pass


def configure_steamcmd_version_path(path: str | Path | None) -> None:
    """Load/persist the SteamCMD client version beside supervisor state."""

    global _steamcmd_version, _steamcmd_version_path
    if path is None:
        _steamcmd_version = None
        _steamcmd_version_path = None
        return
    _steamcmd_version_path = Path(path)
    try:
        if _steamcmd_version_path.is_file():
            text = _steamcmd_version_path.read_text(encoding="utf-8").strip()
            if text:
                _steamcmd_version = text
                return
    except OSError:
        LOG.debug("Could not read SteamCMD version file", exc_info=True)
    _steamcmd_version = None


def steamcmd_client_version() -> str | None:
    return _steamcmd_version


def remember_steamcmd_version(output: str) -> str | None:
    """Parse SteamCMD client version from process output and persist it."""

    global _steamcmd_version
    match = _STEAMCMD_VERSION_RE.search(output or "")
    if not match:
        return _steamcmd_version
    version = match.group(1)
    _steamcmd_version = version
    if _steamcmd_version_path is not None:
        try:
            _steamcmd_version_path.parent.mkdir(parents=True, exist_ok=True)
            _steamcmd_version_path.write_text(version + "\n", encoding="utf-8")
        except OSError:
            LOG.debug("Could not persist SteamCMD version", exc_info=True)
    return version


def _run_streaming(
    cmd: list[str],
    *,
    timeout: float,
    prefix: str = "[steamcmd]",
    env: dict[str, str] | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
    stop_event: threading.Event | None = None,
    on_line: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run a command, streaming stdout/stderr line-by-line into HA Logs."""

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        preexec_fn=make_preexec(run_uid, run_gid),
    )
    lines: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                text = raw.rstrip("\n")
                lines.append(text)
                LOG.info("%s %s", prefix, text)
                remember_steamcmd_version(text)
                if on_line is not None:
                    try:
                        on_line(text)
                    except Exception:  # noqa: BLE001
                        LOG.debug("steamcmd on_line callback failed", exc_info=True)
        finally:
            proc.stdout.close()

    reader = threading.Thread(target=_reader, name="steamcmd-stdout", daemon=True)
    reader.start()
    stopped = False
    try:
        deadline = time.time() + max(0.0, timeout)
        while True:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                LOG.warning("Stop requested; terminating SteamCMD")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                proc.wait(timeout=min(1.0, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        reader.join(timeout=30)
    combined = "\n".join(lines)
    remember_steamcmd_version(combined)
    if stopped:
        raise SteamCMDError("Stopped while SteamCMD was running")
    return int(proc.returncode or 0), combined


def steamcmd_bin(steamcmd_dir: str | Path) -> Path:
    path = Path(steamcmd_dir) / "steamcmd.sh"
    if not path.is_file():
        raise SteamCMDError(f"steamcmd.sh not found at {path}")
    return path


def looks_missing_configuration(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in MISSING_CONFIG_MARKERS)


def looks_missing_file_permissions(output: str) -> bool:
    text = (output or "").lower()
    return any(marker in text for marker in MISSING_PERMISSION_MARKERS)


def steam_home_dir() -> Path:
    raw = (os.environ.get("STEAM_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path("/data/steam-home")


def prepare_steam_env(
    install_dir: str | Path,
    *,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> dict[str, str]:
    """Point SteamCMD at a persistent HOME and ensure steamapps/logs exist."""

    install_dir = Path(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    steamapps = install_dir / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)

    home = steam_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    steam_root = home / "Steam"
    steam_root.mkdir(parents=True, exist_ok=True)
    logs = steam_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    # SteamCMD is picky about being able to write logs + install files as the
    # same user. When we drop to gameserver, make sure those trees match.
    if run_uid is not None and run_gid is not None and os.geteuid() == 0:
        owned = [install_dir, steamapps, home, steam_root, logs]
        chown_paths(run_uid, run_gid, owned)
        for path in owned:
            try:
                os.chmod(path, 0o755)
            except OSError:
                pass

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["STEAM_HOME"] = str(home)
    env.setdefault("LANG", os.environ.get("LANG") or "en_US.UTF-8")
    env.setdefault("LC_ALL", env["LANG"])
    return env


def manifest_path(install_dir: str | Path, app_id: int) -> Path | None:
    candidates = [
        Path(install_dir) / "steamapps" / f"appmanifest_{app_id}.acf",
        Path(install_dir).parent / "steamapps" / f"appmanifest_{app_id}.acf",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def read_local_build_id(install_dir: str | Path, app_id: int) -> str | None:
    meta = read_local_install_meta(install_dir, app_id)
    return meta.get("build_id")


def read_local_install_meta(
    install_dir: str | Path, app_id: int
) -> dict[str, str | int | None]:
    """Read local Steam appmanifest fields (build id + LastUpdated epoch)."""

    path = manifest_path(install_dir, app_id)
    if path is None:
        return {"build_id": None, "last_updated": None}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"build_id": None, "last_updated": None}
    build_match = re.search(r'"buildid"\s+"(\d+)"', text)
    updated_match = re.search(r'"LastUpdated"\s+"(\d+)"', text)
    last_updated: int | None = None
    if updated_match:
        try:
            last_updated = int(updated_match.group(1))
        except ValueError:
            last_updated = None
    return {
        "build_id": build_match.group(1) if build_match else None,
        "last_updated": last_updated,
    }


def parse_app_info_build_id(output: str, branch: str = "public") -> str | None:
    """Return build id from `app_info_print` output when install config is present."""

    if not output or looks_missing_configuration(output):
        return None
    branch_key = re.escape(branch or "public")
    branch_match = re.search(
        rf'"{branch_key}"\s*\{{.*? "buildid"\s+"(\d+)"',
        output,
        re.DOTALL | re.IGNORECASE,
    )
    if branch_match:
        return branch_match.group(1)
    # Some prints only expose a top-level/public buildid.
    match = re.search(r'"buildid"\s+"(\d+)"', output)
    return match.group(1) if match else None


def _interruptible_sleep(
    seconds: float, stop_event: threading.Event | None = None
) -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise SteamCMDError("Stopped while waiting")
        time.sleep(min(1.0, deadline - time.time()))


def _login_args(plugin: GamePlugin) -> list[str]:
    if plugin.steam_login == "anonymous":
        return ["+login", "anonymous"]
    args = ["+login", plugin.steam_login]
    if plugin.steam_password:
        args.append(plugin.steam_password)
    return args


def _app_info_cmd(steamcmd_dir: str | Path, plugin: GamePlugin) -> list[str]:
    """Probe app info with the same platform pin used for app_update."""

    cmd = [
        str(steamcmd_bin(steamcmd_dir)),
        "+@ShutdownOnFailedCommand",
        "1",
        "+@NoPromptForPassword",
        "1",
    ]
    platform = (plugin.steam_platform or "").strip().lower()
    if platform in {"windows", "linux", "macos"}:
        cmd.extend(["+@sSteamCmdForcePlatformType", platform])
    cmd.extend(_login_args(plugin))
    cmd.extend(
        [
            "+app_info_update",
            "1",
            "+app_info_print",
            str(plugin.steam_app_id),
            "+quit",
        ]
    )
    return cmd


def _build_app_update_cmd(
    steamcmd_dir: str | Path,
    install_dir: str | Path,
    plugin: GamePlugin,
    *,
    validate: bool,
    platform: str,
) -> list[str]:
    """Build SteamCMD argv. force_install_dir must come before login."""

    cmd = [
        str(steamcmd_bin(steamcmd_dir)),
        "+@ShutdownOnFailedCommand",
        "1",
        "+@NoPromptForPassword",
        "1",
    ]
    platform = (platform or "").strip().lower()
    if platform in {"windows", "linux", "macos"}:
        cmd.extend(["+@sSteamCmdForcePlatformType", platform])

    cmd.extend(["+force_install_dir", str(install_dir)])
    cmd.extend(_login_args(plugin))
    # Keep app info fresh in the same session as the install.
    cmd.extend(["+app_info_update", "1"])
    cmd.extend(["+app_update", str(plugin.steam_app_id)])
    if plugin.steam_branch and plugin.steam_branch != "public":
        cmd.extend(["-beta", plugin.steam_branch])
    if validate:
        cmd.append("validate")
    cmd.append("+quit")
    return cmd


def _install_succeeded(
    *,
    returncode: int,
    output: str,
    install_dir: Path,
    plugin: GamePlugin,
) -> bool:
    success_markers = (
        "Success! App '%s' fully installed" % plugin.steam_app_id,
        "Success! App '%s' already up to date" % plugin.steam_app_id,
    )
    if any(marker in output for marker in success_markers):
        return True
    if returncode == 0 and read_local_build_id(install_dir, plugin.steam_app_id):
        return True
    return returncode == 0 and server_installed(install_dir, plugin.install_marker)


def wait_for_app_info(
    steamcmd_dir: str | Path,
    plugin: GamePlugin,
    *,
    env: dict[str, str],
    stop_event: threading.Event | None = None,
    timeout_seconds: float = APP_INFO_READY_TIMEOUT_SECONDS,
    poll_interval_seconds: float = APP_INFO_POLL_INTERVAL_SECONDS,
    run_uid: int | None = None,
    run_gid: int | None = None,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Block until Steam has install config for the app (a parseable build id).

    This is a readiness wait, not an install retry. "Not ready yet" does not
    count as a Steam failure and does not start cooldowns.

    Do not clear the local Steam appcache here: that only forces another
    anonymous appinfo fetch and is not a reliable fix for readiness lag.
    """

    deadline = time.time() + timeout_seconds
    probe = 0
    cmd = _app_info_cmd(steamcmd_dir, plugin)
    while True:
        if stop_event is not None and stop_event.is_set():
            raise SteamCMDError("Stopped while waiting for Steam app info")
        probe += 1
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        LOG.info(
            "Waiting for Steam app info for app %s (probe %s, %.0fs left)",
            plugin.steam_app_id,
            probe,
            remaining,
        )
        try:
            _returncode, output = _run_streaming(
                cmd,
                timeout=min(180.0, max(30.0, remaining)),
                env=env,
                run_uid=run_uid,
                run_gid=run_gid,
                stop_event=stop_event,
                on_line=on_line,
            )
        except subprocess.TimeoutExpired as exc:
            raise SteamCMDError(
                f"Timed out probing Steam app info for {plugin.steam_app_id}"
            ) from exc

        build_id = parse_app_info_build_id(output, plugin.steam_branch)
        if build_id:
            LOG.info(
                "Steam app info ready for app %s (buildid=%s)",
                plugin.steam_app_id,
                build_id,
            )
            return build_id

        if time.time() + poll_interval_seconds >= deadline:
            break
        LOG.info(
            "Steam app info for app %s not ready yet; next probe in %.0fs",
            plugin.steam_app_id,
            poll_interval_seconds,
        )
        _interruptible_sleep(poll_interval_seconds, stop_event)

    raise SteamCMDError(
        f"Steam app info for app {plugin.steam_app_id} was not ready within "
        f"{int(timeout_seconds)}s (no install configuration / buildid yet)"
    )


def fetch_remote_build_id(
    steamcmd_dir: str | Path,
    plugin: GamePlugin,
    *,
    stop_event: threading.Event | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> str:
    """Return remote build id. Raises SteamCMDError on Steam/backend failure."""

    gate = get_gate()
    env = prepare_steam_env(
        Path(os.environ.get("INSTALL_DIR") or "/data/game"),
        run_uid=run_uid,
        run_gid=run_gid,
    )
    try:
        with gate.session("app_info", stop_event=stop_event):
            build_id = wait_for_app_info(
                steamcmd_dir,
                plugin,
                env=env,
                stop_event=stop_event,
                timeout_seconds=min(APP_INFO_READY_TIMEOUT_SECONDS, 60.0),
                poll_interval_seconds=APP_INFO_POLL_INTERVAL_SECONDS,
                run_uid=run_uid,
                run_gid=run_gid,
            )
    except InterruptedError as exc:
        raise SteamCMDError("Remote build id query cancelled by stop") from exc
    except SteamCMDError as exc:
        gate.note_failure(str(exc), kind="app_info")
        raise
    gate.note_success(kind="app_info")
    return build_id


def server_installed(install_dir: str | Path, marker_relative: str | None = None) -> bool:
    """Return True only when a real game install is present.

    Empty ``steamapps/`` scaffolding created before the first SteamCMD run must
    not count as an installed server.
    """

    root = Path(install_dir)
    if not root.is_dir():
        return False
    if marker_relative:
        return (root / marker_relative).exists()
    # Marker-less fallback: require a Steam appmanifest, not just any file.
    try:
        for path in root.rglob("appmanifest_*.acf"):
            if path.is_file():
                return True
    except OSError:
        return False
    return False


def install_or_update(
    steamcmd_dir: str | Path,
    install_dir: str | Path,
    plugin: GamePlugin,
    *,
    retries: int = 3,
    retry_delay_seconds: int | None = None,
    validate: bool | None = None,
    stop_event: threading.Event | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
    on_line: Callable[[str], None] | None = None,
) -> str | None:
    """Ensure Steam app info is ready, then run app_update.

    Returns new local build id. SteamCMD access is serialized through SteamGate.
    Prefer running SteamCMD as the same non-root user that owns ``/data/game``
    (``run_uid``/``run_gid``); Valve often reports "Missing file permissions"
    when root writes into a gameserver-owned tree (or the reverse).
    """

    gate = get_gate()
    retries = gate.clamp_retries(retries)
    install_dir = Path(install_dir)
    validate = plugin.validate_on_update if validate is None else validate
    env = prepare_steam_env(install_dir, run_uid=run_uid, run_gid=run_gid)
    platform = (plugin.steam_platform or "").strip().lower()
    if run_uid is not None:
        LOG.info(
            "SteamCMD will run as uid=%s gid=%s (HOME=%s)",
            run_uid,
            run_gid,
            env.get("HOME"),
        )

    last_error: Exception | None = None
    repaired_permissions = False
    for attempt in range(1, retries + 1):
        if stop_event is not None and stop_event.is_set():
            raise SteamCMDError("Stopped before SteamCMD install/update")

        LOG.info(
            "SteamCMD install/update attempt %s/%s for app %s",
            attempt,
            retries,
            plugin.steam_app_id,
        )
        combined = ""
        returncode = 1
        try:
            with gate.session("app_update", stop_event=stop_event):
                # Re-assert ownership/writability each attempt (volume remounts,
                # partial installs, and Steam-created roots can drift).
                env = prepare_steam_env(
                    install_dir, run_uid=run_uid, run_gid=run_gid
                )
                # Readiness gate: poll app_info_print until buildid exists.
                wait_for_app_info(
                    steamcmd_dir,
                    plugin,
                    env=env,
                    stop_event=stop_event,
                    run_uid=run_uid,
                    run_gid=run_gid,
                    on_line=on_line,
                )

                cmd = _build_app_update_cmd(
                    steamcmd_dir,
                    install_dir,
                    plugin,
                    validate=validate,
                    platform=platform,
                )
                LOG.info(
                    "Running app_update for app %s (platform=%s)",
                    plugin.steam_app_id,
                    platform or "native",
                )
                returncode, combined = _run_streaming(
                    cmd,
                    timeout=3600,
                    env=env,
                    run_uid=run_uid,
                    run_gid=run_gid,
                    stop_event=stop_event,
                    on_line=on_line,
                )
                if _install_succeeded(
                    returncode=returncode,
                    output=combined,
                    install_dir=install_dir,
                    plugin=plugin,
                ):
                    build_id = read_local_build_id(install_dir, plugin.steam_app_id)
                    LOG.info("SteamCMD succeeded (buildid=%s)", build_id or "unknown")
                    gate.note_success(kind="app_update")
                    return build_id

            LOG.warning(
                "SteamCMD attempt %s failed (exit %s). Tail:\n%s",
                attempt,
                returncode,
                "\n".join(combined.splitlines()[-40:]),
            )
            last_error = SteamCMDError(
                f"SteamCMD failed to install app {plugin.steam_app_id}"
            )
            if looks_missing_file_permissions(combined):
                last_error = SteamCMDError(
                    f"SteamCMD reported missing file permissions for app "
                    f"{plugin.steam_app_id} (HOME={env.get('HOME')}, "
                    f"install_dir={install_dir}, run_uid={run_uid})"
                )
                if not repaired_permissions:
                    LOG.warning(
                        "Repairing Steam/data directory ownership before retry"
                    )
                    env = prepare_steam_env(
                        install_dir, run_uid=run_uid, run_gid=run_gid
                    )
                    repaired_permissions = True
            if looks_missing_configuration(combined):
                # Keep the normal retry/backoff budget. Do not clear appcache:
                # that forces another anonymous Steam appinfo fetch and is not
                # a reliable fix for this SteamCMD quirk.
                last_error = SteamCMDError(
                    f"SteamCMD still reports missing configuration for app "
                    f"{plugin.steam_app_id} after app info readiness "
                    f"(platform={platform or 'native'})"
                )
            gate.note_failure(combined, kind="app_update")
        except InterruptedError as exc:
            raise SteamCMDError("Stopped while waiting for Steam gate") from exc
        except subprocess.TimeoutExpired as exc:
            LOG.warning("SteamCMD timed out on attempt %s", attempt)
            last_error = exc
            gate.note_failure("timeout", kind="app_update")
        except SteamCMDError as exc:
            LOG.warning("SteamCMD attempt %s failed: %s", attempt, exc)
            last_error = exc
            gate.note_failure(str(exc), kind="app_update")
            combined = str(exc)
        except OSError as exc:
            # SteamCMD process/filesystem problems — retryable via the gate.
            LOG.warning("SteamCMD attempt %s OS error: %s", attempt, exc)
            last_error = exc
            gate.note_failure(str(exc), kind="app_update")
            combined = str(exc)
        # Other exceptions (TypeError, AttributeError, etc.) propagate: those are
        # supervisor bugs, not Steam backend failures.

        if gate.looks_rate_limited(combined) or gate.cooldown_remaining() >= 600:
            LOG.error(
                "Aborting remaining SteamCMD retries due to cooldown/rate-limit "
                "(remaining=%.0fs)",
                gate.cooldown_remaining(),
            )
            break

        # Missing configuration / permissions are often transient or repaired by
        # cache clear + ownership fix above — keep using the normal retry budget
        # instead of hard-aborting after the first post-readiness failure.

        if attempt < retries:
            delay = gate.retry_delay_seconds(attempt)
            if retry_delay_seconds is not None:
                delay = max(delay, float(retry_delay_seconds))
            delay = max(delay, gate.cooldown_remaining())
            LOG.info("Backing off %.0fs before SteamCMD retry", delay)
            _interruptible_sleep(delay, stop_event)

    # Always raise on failure. Callers that already have an install may catch
    # SteamCMDError and keep serving the existing files — but must not treat
    # this as a successful update.
    if server_installed(install_dir, plugin.install_marker):
        raise SteamCMDError(
            f"SteamCMD failed to update app {plugin.steam_app_id} after "
            f"{retries} attempts (existing install left in place): {last_error}"
        )

    raise SteamCMDError(
        f"SteamCMD failed to install app {plugin.steam_app_id} after {retries} attempts: {last_error}"
    )


def update_available(
    steamcmd_dir: str | Path,
    install_dir: str | Path,
    plugin: GamePlugin,
    *,
    stop_event: threading.Event | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> UpdateCheckResult:
    local = read_local_build_id(install_dir, plugin.steam_app_id)
    try:
        remote = fetch_remote_build_id(
            steamcmd_dir,
            plugin,
            stop_event=stop_event,
            run_uid=run_uid,
            run_gid=run_gid,
        )
    except SteamCMDError as exc:
        LOG.warning("Steam update check failed: %s", exc)
        return UpdateCheckResult(
            update_available=False,
            local_build_id=local,
            remote_build_id=None,
            error=str(exc),
        )
    if local is None:
        return UpdateCheckResult(True, local, remote)
    return UpdateCheckResult(local != remote, local, remote)


def ensure_steamcmd(steamcmd_dir: str | Path) -> None:
    """Download SteamCMD if the directory is empty / missing steamcmd.sh."""

    steamcmd_dir = Path(steamcmd_dir)
    steamcmd_dir.mkdir(parents=True, exist_ok=True)
    marker = steamcmd_dir / "steamcmd.sh"
    if marker.is_file():
        return

    LOG.info("Downloading SteamCMD into %s", steamcmd_dir)
    import tarfile
    import urllib.request

    url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
    archive = steamcmd_dir / "steamcmd_linux.tar.gz"
    urllib.request.urlretrieve(url, archive)  # noqa: S310 - trusted Valve CDN
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(steamcmd_dir)
    archive.unlink(missing_ok=True)
    if not marker.is_file():
        raise SteamCMDError("SteamCMD download did not produce steamcmd.sh")
    os.chmod(marker, 0o755)
