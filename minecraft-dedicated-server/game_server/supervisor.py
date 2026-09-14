"""Main supervisor loop: install, run, monitor, update, backup, status."""

from __future__ import annotations

import logging
import signal
import tarfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import package_install, steamcmd
from .backup import BackupManager, EMPTY_WORLD
from .config import SupervisorConfig, load_config
from .disk import ensure_free_mb
from .lifecycle import LIFECYCLE_HEALTHY
from .log_bridge import configure_logging
from .log_tools import LogToolbox
from .machine_id import ensure_machine_id
from .monitor import LogMonitor
from .notify import Notifier
from .operator_action import read_operator_action
from .package_install import PackageInstallError
from .plugin import GamePlugin, load_plugin, resolve_plugin_path
from .privileges import prepare_owned_paths
from .process_manager import ProcessManager
from .status_http import StatusServer
from .steam_gate import configure_gate
from .steamcmd import SteamCMDError
from .version import app_version, supervisor_version
from .active_world import (
    apply_active_world_to_options,
    consume_restart_request,
    load_active_world,
    save_active_world,
    try_sync_ha_addon_option,
)
from .world_catalog import (
    expected_world_path,
    list_catalog_worlds,
    validate_create_fields,
    validate_world_name,
    write_world_create_payload,
)
from .world_save import (
    apply_world_upload,
    backup_sources_for,
    locate_active_world,
    prepare_world_download,
    world_save_is_downloadable,
    world_upload_accepts,
)

LOG = logging.getLogger("game_server.supervisor")

class GameServerSupervisor:
    def __init__(
        self,
        plugin: GamePlugin,
        config: SupervisorConfig,
    ) -> None:
        self.plugin = plugin
        self.config = config
        self.started_at = time.time()
        self._stop = threading.Event()
        # Update / restore state machine (main loop + Ingress actions):
        #
        #   schedule_update → _update_pending
        #     ├─ wait until _can_apply_update()
        #     │    • window: _update_bypass_window OR within configured hours
        #     │    • empty: update_when_empty_only unless _update_ignore_players
        #     │      (force UI) or _empty_wait_expired() (max wait → ignore players)
        #     │    • Steam gate / disk / _update_not_before backoff
        #     └─ _apply_update() clears pending flags
        #   version mismatch → _urgent_update_check* (probe before stop)
        #   restore/upload → _restore_pending / _upload_pending (separate lock)
        self._update_pending = False
        self._update_reason: str | None = None
        self._update_bypass_window = False
        self._update_ignore_players = False
        self._update_pending_since: float | None = None
        self._update_not_before = 0.0
        self._apply_failures = 0
        self._update_lock = threading.Lock()
        self._urgent_update_check = False
        self._urgent_update_check_bypass_window = False
        self._urgent_update_check_reason: str | None = None
        self._restore_pending: str | None = None
        self._upload_pending: Path | None = None
        self._restore_lock = threading.Lock()
        self.last_restore_error: str | None = None
        self.last_restore_at: float | None = None
        # Short-lived activity while a long operation holds the main loop.
        self._activity: str | None = None
        self.local_build_id: str | None = None
        self.remote_build_id: str | None = None
        self.last_update_check_at: float | None = None
        self.last_update_applied_at: float | None = None
        self.last_update_error: str | None = None
        self.update_check_count = 0
        self.update_apply_count = 0
        self.steam_gate = configure_gate(config.state_dir)
        steamcmd.configure_steamcmd_version_path(
            Path(config.state_dir) / "steamcmd_version.txt"
        )

        data_dir = str(config.game_options.get("data_dir") or plugin.data_dir)
        logs_dir = str(config.game_options.get("logs_dir") or plugin.logs_dir)
        self.plugin.data_dir = data_dir
        self.plugin.logs_dir = logs_dir
        # Persist Steam game files on the data volume by default.
        if not config.install_dir:
            config.install_dir = "/data/game"
        self.plugin.working_dir = str(
            config.game_options.get("working_dir") or config.install_dir
        )
        # HA/Docker: steam_branch and/or package release_channel (stable/experimental).
        self.plugin.apply_install_channel_options(config.game_options)
        stored_world = load_active_world(config.state_dir)
        apply_active_world_to_options(config.game_options, stored_world)

        Path(config.state_dir).mkdir(parents=True, exist_ok=True)
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        Path(config.install_dir).mkdir(parents=True, exist_ok=True)
        Path(config.backup_dir).mkdir(parents=True, exist_ok=True)
        # Before drop_privileges: persist a Linux machine-id under /data and
        # project it onto /etc/machine-id when the overlay is writable. Do not
        # write /sys or request extra HA privileges for this.
        ensure_machine_id(state_dir=config.state_dir)

        self.run_ids = None
        if config.drop_privileges:
            self.run_ids = prepare_owned_paths(
                config.run_as_user,
                [
                    config.install_dir,
                    data_dir,
                    logs_dir,
                    config.backup_dir,
                    config.state_dir,
                    config.steamcmd_dir,
                    steamcmd.steam_home_dir(),
                ],
            )

        self.notifier = Notifier(
            config.state_dir,
            enabled=config.ha_notifications,
            notification_id_prefix=plugin.name.lower().replace(" ", "_"),
        )
        self.monitor = LogMonitor(
            plugin,
            logs_dir,
            on_version_mismatch=self._on_version_mismatch,
        )
        self.log_tools = LogToolbox(
            plugin,
            logs_dir,
            config.state_dir,
            recent_lines_provider=lambda: list(self.monitor.state.recent_lines),
        )
        self.process = ProcessManager(
            plugin,
            config,
            on_line=self.monitor.ingest_stdout_line,
            run_uid=self.run_ids[0] if self.run_ids else None,
            run_gid=self.run_ids[1] if self.run_ids else None,
        )
        # Backups prefer the active world artifact (by kind): copy a file save
        # as-is, zip a folder save. backup_paths remain the fallback / legacy
        # tar.gz restore roots.
        backup_sources = backup_sources_for(plugin, data_dir)
        world_data_dir = data_dir or plugin.data_dir
        self.backups = BackupManager(
            config.backup_dir,
            backup_sources,
            world_locator=lambda: locate_active_world(
                plugin,
                config.game_options,
                data_dir=world_data_dir,
            ),
            data_dir=world_data_dir,
            interval_minutes=config.backup_interval_minutes,
            enabled=config.backup_enabled,
            retention=config.retention(),
            min_source_bytes=max(config.backup_min_source_bytes, plugin.min_backup_bytes),
            min_free_disk_mb=config.min_free_disk_mb,
            max_backoff_minutes=config.backup_max_backoff_minutes,
        )
        self.backups.set_failure_callback(self._on_backup_failure)
        self.backups.set_pre_backup_callback(self._flush_before_backup)
        self.status_server: StatusServer | None = None
        self._update_thread: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._restart_pending = False
        self._restart_reason: str | None = None
        self._restart_not_before = 0.0
        self._restart_lock = threading.Lock()

    def _package_extra_env(self) -> dict[str, str]:
        """Env for plugin command installers (dirs + optional release channel)."""

        env = {
            "INSTALL_DIR": self.config.install_dir,
            "STATE_DIR": self.config.state_dir,
        }
        channel = str(self.config.game_options.get("release_channel") or "").strip()
        if channel:
            env["RELEASE_CHANNEL"] = channel
        return env

    def capture_logs(self, reason: str = "manual") -> dict[str, Any]:
        return self.log_tools.capture(reason=reason, status=self.status())

    def _on_backup_failure(self, reason: str) -> None:
        self.notifier.notify(
            "backup_failed",
            f"{self.plugin.name}: backup failed",
            (
                f"{reason}\n"
                f"Consecutive failures: {self.backups.consecutive_failures}. "
                "The next attempt backs off automatically."
            ),
        )

    def _flush_before_backup(self) -> None:
        commands = list(self.plugin.pre_backup_stdin_commands)
        if not commands or not self.process.running:
            return
        if self.process.send_stdin_commands(commands):
            time.sleep(2)

    def _world_option_key(self) -> str:
        spec = self.plugin.world_save
        if spec is not None and spec.world_name_option:
            return spec.world_name_option
        return "world_name"

    def _active_world_name(self) -> str:
        return str(self.config.game_options.get(self._world_option_key()) or "").strip()

    def world_save_download(self) -> dict[str, Any] | None:
        """Prepare the active world save for Ingress download (file or zip)."""

        active = locate_active_world(
            self.plugin,
            self.config.game_options,
            data_dir=self.plugin.data_dir,
        )
        prepared = prepare_world_download(active, data_dir=self.plugin.data_dir)
        if prepared is None:
            return None
        return {
            "path": str(prepared.path),
            "filename": prepared.filename,
            "content_type": prepared.content_type,
            "cleanup_path": (
                str(prepared.cleanup_path) if prepared.cleanup_path else None
            ),
        }

    def request_restore(self, archive_name: str) -> dict[str, Any]:
        """Schedule a world restore or empty-world reset from Ingress."""

        empty = str(archive_name or "").strip() == EMPTY_WORLD
        archive_path: Path | None = None
        if not empty:
            archive_path = self.backups.resolve_archive(archive_name)
            if archive_path is None:
                return {
                    "ok": False,
                    "error": f"Backup not found or invalid name: {archive_name}",
                }
        with self._restore_lock:
            if self._restore_pending or self._upload_pending is not None:
                return {
                    "ok": False,
                    "error": "A restore is already pending",
                }
            if self._update_pending:
                return {
                    "ok": False,
                    "error": "An update is already pending; wait for it to finish",
                }
            self._restore_pending = EMPTY_WORLD if empty else archive_path.name
        if empty:
            LOG.warning("Empty-world reset scheduled (wipe live world)")
            return {
                "ok": True,
                "archive": None,
                "empty": True,
                "restore_pending": True,
            }
        assert archive_path is not None
        LOG.warning("World restore scheduled from archive %s", archive_path.name)
        return {
            "ok": True,
            "archive": archive_path.name,
            "empty": False,
            "restore_pending": True,
        }

    def request_world_upload(self, staged_path: str | Path) -> dict[str, Any]:
        """Schedule replacing the active world from a staged upload file."""

        staged = Path(staged_path)
        if not staged.is_file() or staged.stat().st_size < 1:
            return {"ok": False, "error": "uploaded world file is missing or empty"}
        active = locate_active_world(
            self.plugin,
            self.config.game_options,
            data_dir=self.plugin.data_dir,
        )
        meta = world_upload_accepts(active)
        if not meta["uploadable"]:
            return {"ok": False, "error": str(meta["hint"])}
        with self._restore_lock:
            if self._restore_pending or self._upload_pending is not None:
                return {"ok": False, "error": "A restore is already pending"}
            if self._update_pending:
                return {
                    "ok": False,
                    "error": "An update is already pending; wait for it to finish",
                }
            self._upload_pending = staged
        LOG.warning(
            "World upload restore scheduled (%s bytes, kind=%s, mode=%s)",
            staged.stat().st_size,
            meta["kind"],
            meta["mode"],
        )
        return {
            "ok": True,
            "kind": meta["kind"],
            "mode": meta["mode"],
            "restore_pending": True,
            "upload_pending": True,
        }

    def _create_pre_restore_safety(self) -> Path | None:
        """Safety-copy the live world before any wipe. Required when data exists."""

        if not self.backups.sources_have_any_data():
            LOG.info("No world data present; skipping pre-restore safety copy")
            return None
        # Bypass min_source_bytes and defer retention prune until after wipe.
        safety = self.backups.create_safety_backup(reason="safety")
        if safety is None:
            raise RuntimeError(
                self.backups.last_error
                or self.backups.last_skip_reason
                or "could not create pre-restore safety backup"
            )
        LOG.info("Pre-restore safety copy saved as %s", safety.name)
        return safety

    def _apply_world_upload(self, staged: Path) -> None:
        """Stop → safety backup → apply upload to active world → restart."""

        LOG.info("Applying world upload from %s", staged)
        self._activity = "restoring"
        safety: Path | None = None
        result: dict[str, Any] | None = None
        try:
            if self.process.running:
                self.process.stop()
            try:
                safety = self._create_pre_restore_safety()
                active = locate_active_world(
                    self.plugin,
                    self.config.game_options,
                    data_dir=self.plugin.data_dir,
                )
                result = apply_world_upload(
                    active, staged, data_dir=self.plugin.data_dir
                )
                self.backups.apply_retention()
                self.last_restore_at = time.time()
                self.last_restore_error = None
            except Exception as exc:
                self.last_restore_error = str(exc)
                LOG.exception("World upload restore failed")
                self.notifier.notify(
                    "restore_failed",
                    f"{self.plugin.name}: world upload failed",
                    str(exc),
                    force=True,
                )
                if not self.process.running and not self._stop.is_set():
                    try:
                        self.monitor.reset_session()
                        self.process.start(reason="restore_failed")
                    except OSError:
                        LOG.exception(
                            "Failed restarting server after world upload failure"
                        )
                raise
            finally:
                staged.unlink(missing_ok=True)

            assert result is not None
            self.monitor.reset_session()
            self.process.start(reason="restore")
            safety_note = (
                f"Previous world kept as {safety.name}."
                if safety is not None
                else "No prior world data to keep."
            )
            self.notifier.notify(
                "restored",
                f"{self.plugin.name}: world upload restored",
                f"Applied upload to {result.get('path')}. {safety_note}",
                force=True,
            )
            LOG.info("World upload restore complete: %s", result)
        finally:
            self._activity = None

    def _apply_restore(self, archive_name: str) -> None:
        empty = archive_name == EMPTY_WORLD
        archive = None if empty else self.backups.resolve_archive(archive_name)
        if not empty and archive is None:
            raise FileNotFoundError(f"backup archive not found: {archive_name}")

        if empty:
            LOG.info("Applying empty-world reset (clear live world files)")
        else:
            assert archive is not None
            LOG.info("Applying world restore from %s", archive.name)
        self._activity = "restoring"
        try:
            if self.process.running:
                self.process.stop()

            try:
                safety = self._create_pre_restore_safety()
                if empty:
                    result = self.backups.clear_world_sources(
                        prior_safety_backup=safety
                    )
                else:
                    assert archive is not None
                    result = self.backups.restore_archive(
                        archive, prior_safety_backup=safety
                    )
                # Retention only after the live world is safely archived + replaced.
                self.backups.apply_retention()
                self.last_restore_at = time.time()
                self.last_restore_error = None
            except Exception as exc:
                self.last_restore_error = str(exc)
                LOG.exception("World restore failed")
                self.notifier.notify(
                    "restore_failed",
                    f"{self.plugin.name}: world restore failed",
                    str(exc),
                    force=True,
                )
                if not self.process.running and not self._stop.is_set():
                    try:
                        self.monitor.reset_session()
                        self.process.start(reason="restore_failed")
                    except OSError:
                        LOG.exception("Failed restarting server after restore failure")
                raise

            self.monitor.reset_session()
            self.process.start(reason="restore")
            if empty:
                safety_note = (
                    f"Previous world kept as {safety.name}."
                    if safety is not None
                    else "No prior world data to keep."
                )
                self.notifier.notify(
                    "restored",
                    f"{self.plugin.name}: empty world reset",
                    f"Live world cleared for a fresh start. {safety_note}",
                    force=True,
                )
            else:
                assert archive is not None
                safety_note = (
                    f"Previous world kept as {safety.name}."
                    if safety is not None
                    else "No prior world data to keep."
                )
                self.notifier.notify(
                    "restored",
                    f"{self.plugin.name}: world restored",
                    f"Restored {archive.name}. {safety_note}",
                    force=True,
                )
            LOG.info("World restore complete: %s", result)
        finally:
            self._activity = None

    def lifecycle(self) -> str:
        """Return a single phase a person can reason about.

        Values: stopping, stopped, restoring, installing, updating, running,
        waiting (operator action or update queued), failed (crash loop / left
        down), starting.
        """

        if self._stop.is_set():
            return "stopping" if self.process.running else "stopped"
        if read_operator_action(self.config.state_dir):
            return "waiting"
        if self._activity in ("installing", "updating", "restoring", "restarting"):
            return self._activity
        if self._restore_pending or self._upload_pending is not None:
            return "restoring"
        if self._restart_pending:
            return "waiting"
        if self.process.running:
            return "running"
        if self._update_pending:
            return "waiting"
        if (
            self.process.start_count > 0
            and not self.process.intentional_stop
            and not self.process.can_restart_after_crash()
        ):
            return "failed"
        if self.process.intentional_stop and self.process.start_count > 0:
            return "stopped"
        return "starting"

    def health(self) -> dict[str, Any]:
        """Cheap snapshot for /healthz (no disk/manifest scans)."""

        phase = self.lifecycle()
        return {
            "lifecycle": phase,
            "running": self.process.running,
            "ok": phase in LIFECYCLE_HEALTHY,
        }

    def status(self) -> dict[str, Any]:
        monitor = self.monitor.state.to_dict()
        monitor["recent_lines"] = list(self.monitor.state.recent_lines)
        pattern_report = self.monitor.pattern_report()
        disk_ok, free = ensure_free_mb(self.config.backup_dir, self.config.min_free_disk_mb)
        waits_for_empty_server = (
            "yes"
            if self.monitor.player_tracking_enabled
            else "no_player_tracking"
        )
        player_tracking_mode = self.monitor.player_tracking_mode
        if self.plugin.uses_package_install and self.plugin.package_install is not None:
            install_meta = {
                "build_id": package_install.read_local_version(
                    self.config.install_dir, self.plugin.package_install
                ),
                "last_updated": None,
            }
        else:
            assert self.plugin.steam_app_id is not None
            install_meta = steamcmd.read_local_install_meta(
                self.config.install_dir, self.plugin.steam_app_id
            )
        # Prefer on-disk build id / package version for display.
        local_build = install_meta.get("build_id") or self.local_build_id
        active_world = locate_active_world(
            self.plugin,
            self.config.game_options,
            data_dir=self.plugin.data_dir,
        )
        world_size = active_world.to_dict()
        world_size["downloadable"] = world_save_is_downloadable(
            active_world, data_dir=self.plugin.data_dir
        )
        world_size.update(world_upload_accepts(active_world))
        phase = self.lifecycle()
        action = read_operator_action(self.config.state_dir)
        return {
            "game": self.plugin.name,
            "app_version": app_version(),
            "supervisor_version": supervisor_version(),
            "steam_app_id": self.plugin.steam_app_id,
            "install_method": (
                "package" if self.plugin.uses_package_install else "steamcmd"
            ),
            "steam_branch": self.plugin.steam_branch,
            "release_channel": (
                str(self.config.game_options.get("release_channel") or "stable")
                if self.plugin.uses_package_install
                else None
            ),
            "running": self.process.running,
            "lifecycle": phase,
            # Narrower than the old "not stopped" meaning; prefer ``lifecycle``.
            "starting": phase == "starting",
            "supervisor_uptime_seconds": int(time.time() - self.started_at),
            "game_uptime_seconds": (
                int(time.time() - self.process.last_started_at)
                if self.process.running and self.process.last_started_at
                else 0
            ),
            "restart_count": self.process.restart_count,
            "last_start_reason": self.process.last_start_reason,
            "crash_count": self.process.crash_count,
            "local_build_id": str(local_build) if local_build else None,
            "steamcmd_version": steamcmd.steamcmd_client_version(),
            "game_version": self.monitor.state.game_version,
            "remote_build_id": self.remote_build_id,
            "install_last_updated_at": install_meta.get("last_updated"),
            "world_save": world_size,
            "worlds": {
                "active": self._active_world_name(),
                "option_key": self._world_option_key(),
                "catalog": [
                    item.to_dict()
                    for item in list_catalog_worlds(
                        self.plugin.world_catalog,
                        data_dir=self.plugin.data_dir,
                        options=self.config.game_options,
                        active_name=self._active_world_name(),
                    )
                ],
                "create_fields": (
                    self.plugin.world_create.to_list()
                    if self.plugin.world_create is not None
                    else []
                ),
            },
            "update_pending": self._update_pending,
            "update_reason": self._update_reason,
            "update_pending_since": self._update_pending_since,
            "update_empty_max_wait_hours": self.config.update_empty_max_wait_hours,
            "update_ignore_players": self._update_ignore_players,
            "last_update_check_at": self.last_update_check_at,
            "last_update_applied_at": self.last_update_applied_at,
            "last_update_error": self.last_update_error,
            "update_check_count": self.update_check_count,
            "update_apply_count": self.update_apply_count,
            "update_apply_failures": self._apply_failures,
            "update_not_before": self._update_not_before or None,
            "auto_update_interval_minutes": self.config.auto_update_interval_minutes,
            "auto_update_check_hour": self.config.auto_update_check_hour,
            "install_dir": self.config.install_dir,
            "restore_pending": self._restore_pending,
            "restart_pending": self._restart_pending,
            "restart_reason": self._restart_reason,
            "last_restore_at": self.last_restore_at,
            "last_restore_error": self.last_restore_error,
            # Plain-language status for the UI (avoid "gating" jargon).
            "waits_for_empty_server": waits_for_empty_server,
            "player_tracking_mode": player_tracking_mode,
            "debug_mode": bool(self.config.debug_mode),
            "steam_gate": self.steam_gate.to_dict(),
            "disk": {
                "ok": disk_ok,
                "free_mb": free,
                "min_free_disk_mb": self.config.min_free_disk_mb,
            },
            "monitor": monitor,
            "log_patterns": pattern_report,
            "process": self.process.to_dict(),
            "backups": self.backups.to_dict(),
            "log_captures": self.log_tools.list_captures(),
            "operator_action": action,
        }

    def _publish_status(self) -> None:
        status = self.status()
        self.notifier.write_status(status)

    def _status_loop(self) -> None:
        while not self._stop.wait(15):
            try:
                self._publish_status()
            except OSError:
                LOG.exception("Failed writing status.json")
            # Other exceptions are supervisor bugs — let the status thread die
            # so the failure is obvious in logs rather than hidden every 15s.

    def _on_version_mismatch(self, line: str) -> None:
        # Only invoked for active patterns (dry-run candidates never call this).
        # Do NOT stop the game yet — ask Steam whether a newer build exists first.
        # Main loop runs the check, and only then may request_update → orderly
        # stop (plugin stop_stdin_commands + stop_timeout) → SteamCMD → restart.
        try:
            self.capture_logs("version_mismatch")
        except OSError:
            LOG.exception("Failed capturing logs on version mismatch")
        self.notifier.notify(
            "version_mismatch",
            f"{self.plugin.name}: client version mismatch",
            f"A client was rejected for a version problem. Checking Steam for a "
            f"newer build before any restart.\n{line}",
        )
        if not self.config.update_on_version_mismatch:
            return
        LOG.warning(
            "Client version mismatch; queuing Steam update check before any "
            "restart: %s",
            line,
        )
        with self._update_lock:
            self._urgent_update_check = True
            self._urgent_update_check_bypass_window = True
            self._urgent_update_check_reason = "version_mismatch"

    def request_update(
        self,
        reason: str,
        bypass_window: bool = False,
        *,
        ignore_players: bool = False,
    ) -> None:
        with self._update_lock:
            if not self._update_pending:
                self._update_pending_since = time.time()
            self._update_pending = True
            self._update_reason = reason
            self._update_bypass_window = self._update_bypass_window or bypass_window
            self._update_ignore_players = (
                self._update_ignore_players or ignore_players
            )
        LOG.info(
            "Update requested (%s)%s%s",
            reason,
            " [bypass window]" if bypass_window else "",
            " [may interrupt players]" if ignore_players else "",
        )

    def force_update_now(self) -> dict[str, Any]:
        """Schedule an update from the web UI, even if players are online.

        SteamCMD games respect a long Steam cooldown so a button mash cannot
        hammer Valve. Package-install games skip the Steam gate.
        """

        if not self.plugin.uses_package_install:
            cooldown = self.steam_gate.cooldown_remaining()
            if cooldown >= 600:
                return {
                    "ok": False,
                    "error": (
                        f"Steam is cooling down for about {int(cooldown)}s after a "
                        "recent failure or rate limit. Try again later."
                    ),
                    "cooldown_seconds": int(cooldown),
                }
        # Clear soft "try later" from a previous apply failure; the Steam gate
        # still enforces spacing / hard cooldowns for SteamCMD games.
        self._update_not_before = 0.0
        self.request_update(
            reason="manual",
            bypass_window=True,
            ignore_players=True,
        )
        online = self._players_online()
        source = "the package download" if self.plugin.uses_package_install else "Steam"
        return {
            "ok": True,
            "message": (
                f"Update scheduled. The game server will stop, update from {source}, "
                "and restart. Anyone playing will be disconnected."
            ),
            "update_pending": True,
            "players_online": online,
        }

    def request_restart(
        self,
        reason: str = "manual",
        *,
        debounce_seconds: float = 0,
        ignore_players: bool = True,
    ) -> dict[str, Any]:
        """Schedule a game-process restart without exiting the supervisor."""

        if self._update_pending:
            return {
                "ok": False,
                "error": "An update is already pending; wait for it to finish",
            }
        with self._restore_lock:
            if self._restore_pending or self._upload_pending is not None:
                return {
                    "ok": False,
                    "error": "A restore is already pending; wait for it to finish",
                }
        delay = max(0.0, float(debounce_seconds or 0))
        with self._restart_lock:
            self._restart_pending = True
            self._restart_reason = reason
            not_before = time.time() + delay
            if delay > 0:
                self._restart_not_before = max(self._restart_not_before, not_before)
            else:
                self._restart_not_before = 0.0
        LOG.info(
            "Game restart requested (%s)%s",
            reason,
            f" in {delay:.0f}s" if delay else "",
        )
        online = self._players_online()
        return {
            "ok": True,
            "message": (
                "Restart scheduled. The game server will stop and start again. "
                "Anyone playing will be disconnected."
                if ignore_players
                else "Restart scheduled."
            ),
            "restart_pending": True,
            "players_online": online,
        }

    def _apply_restart(self) -> None:
        reason = self._restart_reason or "manual"
        LOG.info("Restarting game process (%s)", reason)
        self._activity = "restarting"
        try:
            if self.process.running:
                self.process.stop()
            if self._stop.is_set():
                return
            self.monitor.reset_session()
            self.process.start(reason=f"restart:{reason}")
        finally:
            self._activity = None
            with self._restart_lock:
                self._restart_pending = False
                self._restart_reason = None
                self._restart_not_before = 0.0

    def _select_world(self, name: str) -> None:
        key = self._world_option_key()
        self.config.game_options[key] = name
        save_active_world(
            self.config.state_dir, option_key=key, value=name
        )
        try_sync_ha_addon_option(key, name)

    def request_world_switch(self, name: str) -> dict[str, Any]:
        try:
            world_name = validate_world_name(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        current = self._active_world_name()
        if world_name == current:
            return {
                "ok": True,
                "message": f"{world_name} is already the active world",
                "world": world_name,
            }
        self._select_world(world_name)
        result = self.request_restart(reason="world-switch", debounce_seconds=0)
        if not result.get("ok"):
            return result
        result["world"] = world_name
        result["message"] = (
            f"Switching to {world_name}. The game server will restart."
        )
        return result

    def request_world_create(
        self, name: str, fields: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            world_name = validate_world_name(name)
            extra = validate_create_fields(self.plugin.world_create, fields)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        paths = list(self.plugin.world_save.paths) if self.plugin.world_save else []
        options = dict(self.config.game_options)
        options[self._world_option_key()] = world_name
        expected = expected_world_path(
            paths,
            data_dir=self.plugin.data_dir,
            world_name=world_name,
            options=options,
        )
        write_world_create_payload(expected_path=expected, fields=extra)
        if expected:
            Path(expected).parent.mkdir(parents=True, exist_ok=True)
        self._select_world(world_name)
        result = self.request_restart(reason="world-create", debounce_seconds=0)
        if not result.get("ok"):
            return result
        result["world"] = world_name
        result["created"] = True
        result["message"] = (
            f"Creating world {world_name}. The game server will restart."
        )
        return result

    def _within_update_window(self) -> bool:
        start = self.config.update_window_start_hour
        end = self.config.update_window_end_hour
        if start is None or end is None:
            return True
        hour = datetime.now().hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def _players_online(self) -> int | None:
        """Return player count when known; None when log tracking cannot tell."""
        if not self.monitor.player_tracking_enabled:
            return None
        state = self.monitor.state
        if not state.players_known and not state.players:
            # Active patterns exist but nothing observed yet — treat as unknown
            # until we see a join/leave/count signal (safer than assuming 0).
            return None
        if state.players:
            return len(state.players)
        if state.player_count is None:
            return None
        return int(state.player_count)

    def _empty_wait_expired(self) -> bool:
        """True when a pending update has waited long enough to interrupt players."""

        max_hours = int(self.config.update_empty_max_wait_hours or 0)
        if max_hours <= 0 or self._update_pending_since is None:
            return False
        return (time.time() - self._update_pending_since) >= max_hours * 3600

    def _can_apply_update(self) -> bool:
        if time.time() < self._update_not_before:
            return False
        if (
            not self.plugin.uses_package_install
            and self.steam_gate.seconds_until_next_call() > 0
        ):
            return False
        if not self._update_bypass_window and not self._within_update_window():
            return False
        if self.config.update_when_empty_only and not self._update_ignore_players:
            online = self._players_online()
            if online is None:
                # Do not block updates forever when player tracking is not
                # available yet. Status explains that restarts may interrupt players.
                LOG.debug(
                    "Player tracking unavailable; allowing update without empty check"
                )
            elif online > 0:
                if self._empty_wait_expired():
                    LOG.warning(
                        "Update pending for >= %sh with players still online; "
                        "applying anyway (update_empty_max_wait_hours)",
                        self.config.update_empty_max_wait_hours,
                    )
                    with self._update_lock:
                        self._update_ignore_players = True
                else:
                    return False
        ok, _free = ensure_free_mb(self.config.install_dir, self.config.min_free_disk_mb)
        return ok

    def _steamcmd_identity(self) -> tuple[int | None, int | None]:
        """UID/GID SteamCMD should run as (same owner as /data/game)."""

        if self.run_ids:
            return self.run_ids[0], self.run_ids[1]
        return None, None

    def ensure_installed(self) -> None:
        if not self.plugin.uses_package_install:
            steamcmd.ensure_steamcmd(self.config.steamcmd_dir)
            # Re-apply ownership after SteamCMD bootstrap files appear.
            if self.config.drop_privileges:
                self.run_ids = prepare_owned_paths(
                    self.config.run_as_user,
                    [
                        self.config.steamcmd_dir,
                        self.config.install_dir,
                        steamcmd.steam_home_dir(),
                    ],
                ) or self.run_ids
        had_install = steamcmd.server_installed(
            self.config.install_dir, self.plugin.install_marker
        )
        if not had_install or self.config.update_on_start:
            source = (
                "package download"
                if self.plugin.uses_package_install
                else "SteamCMD"
            )
            LOG.info(
                "Installing/updating game server via %s into %s",
                source,
                self.config.install_dir,
            )
            self._activity = "installing"
            run_uid, run_gid = self._steamcmd_identity()
            try:
                if self.plugin.uses_package_install:
                    # Version compare inside install_or_update; no force on boot.
                    self.local_build_id = package_install.install_or_update(
                        self.config.install_dir,
                        self.plugin,
                        force=False,
                        stop_event=self._stop,
                        run_uid=run_uid,
                        run_gid=run_gid,
                        extra_env=self._package_extra_env(),
                    )
                else:
                    self.local_build_id = steamcmd.install_or_update(
                        self.config.steamcmd_dir,
                        self.config.install_dir,
                        self.plugin,
                        retries=self.config.steamcmd_retries,
                        retry_delay_seconds=self.config.steamcmd_retry_delay_seconds,
                        stop_event=self._stop,
                        run_uid=run_uid,
                        run_gid=run_gid,
                    )
                self.last_update_applied_at = time.time()
                self.last_update_check_at = self.last_update_applied_at
                self.update_apply_count += 1
                self.last_update_error = None
                self._apply_failures = 0
            except (SteamCMDError, PackageInstallError) as exc:
                if self._stop.is_set():
                    LOG.info(
                        "%s stopped during install (shutdown); not a failed install: %s",
                        (
                            "package install"
                            if self.plugin.uses_package_install
                            else "SteamCMD"
                        ),
                        exc,
                    )
                    return
                self.last_update_error = str(exc)
                self.last_update_check_at = time.time()
                label = (
                    "package install"
                    if self.plugin.uses_package_install
                    else "SteamCMD"
                )
                self.notifier.notify(
                    "steamcmd_failed",
                    f"{self.plugin.name}: {label} failed",
                    str(exc),
                    force=True,
                )
                if had_install:
                    # Backend problem on restart: keep serving the existing
                    # build instead of refusing to start the whole app.
                    if (
                        self.plugin.uses_package_install
                        and self.plugin.package_install is not None
                    ):
                        self.local_build_id = package_install.read_local_version(
                            self.config.install_dir, self.plugin.package_install
                        )
                    else:
                        self.local_build_id = steamcmd.read_local_build_id(
                            self.config.install_dir, self.plugin.steam_app_id
                        )
                    LOG.error(
                        "%s update-on-start failed; continuing with existing "
                        "install (version=%s): %s",
                        label,
                        self.local_build_id or "unknown",
                        exc,
                    )
                else:
                    raise
            finally:
                self._activity = None
            if self.config.drop_privileges:
                paths = [self.config.install_dir]
                if not self.plugin.uses_package_install:
                    paths.extend(
                        [self.config.steamcmd_dir, steamcmd.steam_home_dir()]
                    )
                prepare_owned_paths(self.config.run_as_user, paths)
        else:
            if (
                self.plugin.uses_package_install
                and self.plugin.package_install is not None
            ):
                self.local_build_id = package_install.read_local_version(
                    self.config.install_dir, self.plugin.package_install
                )
            else:
                self.local_build_id = steamcmd.read_local_build_id(
                    self.config.install_dir, self.plugin.steam_app_id
                )

    def _schedule_update_retry(self, exc: Exception) -> None:
        self._apply_failures += 1
        delay = self.steam_gate.apply_failure_delay_seconds(self._apply_failures)
        delay = max(delay, self.steam_gate.cooldown_remaining())
        self._update_not_before = time.time() + delay
        self.last_update_error = str(exc)
        LOG.warning(
            "Update apply failure #%s; next attempt not before %.0fs (error=%s)",
            self._apply_failures,
            delay,
            exc,
        )
        if self._apply_failures >= self.steam_gate.policy.max_apply_failures:
            self.notifier.notify(
                "update_failed",
                f"{self.plugin.name}: update paused (Steam backoff)",
                (
                    f"Failed {self._apply_failures} times. Cooling down "
                    f"{int(delay)}s before trying Steam again.\n{exc}"
                ),
                force=True,
            )

    def _restart_existing_after_update_failure(self) -> None:
        """Bring the current install back up after a failed update attempt."""

        if self.process.running or self._stop.is_set():
            return
        try:
            self.monitor.reset_session()
            self.process.start(reason="update_failed")
        except OSError:
            LOG.exception("Failed restarting server after update failure")

    def _apply_update(self) -> None:
        reason = self._update_reason or "requested"
        LOG.info("Applying update (%s)", reason)
        self._activity = "updating"
        try:
            if self.config.backup_on_update:
                # Graceful stop first so the world flush happens before backup.
                if self.process.running:
                    self.process.stop()
                try:
                    outcome = self.backups.create_backup_result(
                        reason="pre-update", allow_tiny=True
                    )
                except (OSError, tarfile.TarError) as exc:
                    # Do not mutate the install when we could not snapshot the world.
                    LOG.exception("Pre-update backup failed; aborting update")
                    self.notifier.notify(
                        "backup_failed",
                        f"{self.plugin.name}: pre-update backup failed",
                        f"Update aborted until a world backup succeeds.\n{exc}",
                        force=True,
                    )
                    self._schedule_update_retry(exc)
                    self._restart_existing_after_update_failure()
                    raise
                if outcome.status == "failed":
                    reason_text = outcome.reason or "pre-update backup failed"
                    LOG.error("Pre-update backup failed; aborting update: %s", reason_text)
                    self.notifier.notify(
                        "backup_failed",
                        f"{self.plugin.name}: pre-update backup failed",
                        f"Update aborted until a world backup succeeds.\n{reason_text}",
                        force=True,
                    )
                    exc = RuntimeError(reason_text)
                    self._schedule_update_retry(exc)
                    self._restart_existing_after_update_failure()
                    raise exc

            if self.process.running:
                self.process.stop()

            run_uid, run_gid = self._steamcmd_identity()
            try:
                if self.plugin.uses_package_install:
                    self.local_build_id = package_install.install_or_update(
                        self.config.install_dir,
                        self.plugin,
                        force=True,
                        stop_event=self._stop,
                        run_uid=run_uid,
                        run_gid=run_gid,
                        extra_env=self._package_extra_env(),
                    )
                else:
                    self.local_build_id = steamcmd.install_or_update(
                        self.config.steamcmd_dir,
                        self.config.install_dir,
                        self.plugin,
                        retries=self.config.steamcmd_retries,
                        retry_delay_seconds=self.config.steamcmd_retry_delay_seconds,
                        stop_event=self._stop,
                        run_uid=run_uid,
                        run_gid=run_gid,
                    )
                if self.config.drop_privileges:
                    paths = [self.config.install_dir]
                    if not self.plugin.uses_package_install:
                        paths.extend(
                            [self.config.steamcmd_dir, steamcmd.steam_home_dir()]
                        )
                    self.run_ids = (
                        prepare_owned_paths(self.config.run_as_user, paths)
                        or self.run_ids
                    )
                self.last_update_applied_at = time.time()
                self.update_apply_count += 1
                self.last_update_error = None
                self._apply_failures = 0
                self._update_not_before = 0.0
            except (SteamCMDError, PackageInstallError) as exc:
                if self._stop.is_set():
                    LOG.info("Update interrupted by shutdown: %s", exc)
                    return
                try:
                    self.capture_logs("update_failed")
                except OSError:
                    LOG.exception("Failed capturing logs after update failure")
                self._schedule_update_retry(exc)
                self._restart_existing_after_update_failure()
                raise
            except Exception as exc:
                # Unexpected supervisor bug after stopping the game: still try to
                # recover the server, schedule backoff so we don't tight-loop, then
                # propagate so the failure is visible.
                self._schedule_update_retry(exc)
                self._restart_existing_after_update_failure()
                raise

            with self._update_lock:
                self._update_pending = False
                self._update_reason = None
                self._update_bypass_window = False
                self._update_ignore_players = False
                self._update_pending_since = None
            self.monitor.reset_session()
            self.process.start(reason="update")
            self.notifier.notify(
                "updated",
                f"{self.plugin.name}: updated",
                f"Now running {self.local_build_id or 'unknown'} (reason: {reason})",
                force=True,
            )
        finally:
            self._activity = None

    @staticmethod
    def _seconds_until_local_hour(hour: int, *, now: datetime | None = None) -> float:
        """Seconds until the next local ``hour:00:00`` (always in the future)."""

        current = now or datetime.now()
        target_hour = max(0, min(23, int(hour)))
        target = current.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target <= current:
            target = target + timedelta(days=1)
        return max(1.0, (target - current).total_seconds())

    def _seconds_until_next_update_check(self) -> float:
        """How long to sleep before the next Steam "newer build?" probe."""

        minutes = self.steam_gate.clamp_check_interval_minutes(
            self.config.auto_update_interval_minutes
        )
        if minutes <= 0:
            return 0.0
        check_hour = self.config.auto_update_check_hour
        if check_hour is not None:
            # Prefer a once-daily wall-clock check (default 05:00 local) so we do
            # not poll Steam every few minutes as an anonymous client.
            return self._seconds_until_local_hour(check_hour)
        return float(max(0, minutes) * 60)

    def _probe_steam_for_update(
        self,
        *,
        reason: str,
        bypass_window: bool,
    ) -> bool | None:
        """Ask whether a newer build/package version exists.

        Returns:
            True — newer build found; apply was scheduled (still not stopped here)
            False — up to date or check failed; do not restart
            None — deferred for Steam cooldown/spacing; caller may retry
        """

        if not self.plugin.uses_package_install:
            cooldown = self.steam_gate.seconds_until_next_call()
            if cooldown > 0:
                LOG.info(
                    "Deferring Steam update check (%s); Steam cooldown %.0fs remaining",
                    reason,
                    cooldown,
                )
                return None

        self.update_check_count += 1
        self.last_update_check_at = time.time()
        if self.plugin.uses_package_install:
            result = package_install.update_available(
                self.config.install_dir,
                self.plugin,
                extra_env=self._package_extra_env(),
            )
        else:
            run_uid, run_gid = self._steamcmd_identity()
            result = steamcmd.update_available(
                self.config.steamcmd_dir,
                self.config.install_dir,
                self.plugin,
                stop_event=self._stop,
                run_uid=run_uid,
                run_gid=run_gid,
            )
        self.local_build_id = result.local_build_id or self.local_build_id
        self.remote_build_id = result.remote_build_id
        if not result.check_ok:
            self.last_update_error = result.error
            LOG.warning(
                "Update check unavailable (local=%s): %s",
                result.local_build_id or "unknown",
                result.error,
            )
            self.notifier.notify(
                "update_check_failed",
                f"{self.plugin.name}: update check failed",
                str(result.error),
            )
            return False
        if result.update_available:
            LOG.info(
                "Remote update available (local=%s remote=%s); scheduling apply (%s)",
                result.local_build_id,
                result.remote_build_id,
                reason,
            )
            self.request_update(reason=reason, bypass_window=bypass_window)
            self.last_update_error = None
            return True

        LOG.info(
            "Game is up to date (version=%s); not restarting (%s)",
            result.local_build_id or result.remote_build_id or "unknown",
            reason,
        )
        self.last_update_error = None
        if reason == "version_mismatch":
            source = (
                "the package source"
                if self.plugin.uses_package_install
                else "Steam"
            )
            self.notifier.notify(
                "version_mismatch_no_update",
                f"{self.plugin.name}: version mismatch, no update available",
                (
                    "A client was rejected for a version problem, but "
                    f"{source} reports the install is already current "
                    f"(version {result.local_build_id or 'unknown'}). "
                    "Not stopping the server."
                ),
            )
        return False

    def _run_urgent_update_check(self) -> None:
        """Handle version-mismatch Steam probes from the main loop."""

        with self._update_lock:
            if not self._urgent_update_check:
                return
            reason = self._urgent_update_check_reason or "version_mismatch"
            bypass_window = self._urgent_update_check_bypass_window

        outcome = self._probe_steam_for_update(
            reason=reason, bypass_window=bypass_window
        )
        if outcome is None:
            # Cooldown — keep the urgent flag and retry on the next main-loop pass.
            return
        with self._update_lock:
            self._urgent_update_check = False
            self._urgent_update_check_reason = None
            self._urgent_update_check_bypass_window = False

    def _update_checker_loop(self) -> None:
        minutes = self.steam_gate.clamp_check_interval_minutes(
            self.config.auto_update_interval_minutes
        )
        source = (
            "package"
            if self.plugin.uses_package_install
            else "Steam"
        )
        if minutes <= 0:
            LOG.info("Periodic %s update checks disabled", source)
            return
        check_hour = self.config.auto_update_check_hour
        if check_hour is not None:
            if self.plugin.uses_package_install:
                LOG.info(
                    "Checking package source for updates once daily at local %02d:00",
                    check_hour,
                )
            else:
                LOG.info(
                    "Checking Steam for updates once daily at local %02d:00 "
                    "(Steam spacing %.0fs, max retries %s)",
                    check_hour,
                    self.steam_gate.policy.min_interval_seconds,
                    self.steam_gate.policy.max_retries,
                )
        else:
            if self.plugin.uses_package_install:
                LOG.info(
                    "Checking package source for updates every %s minutes",
                    minutes,
                )
            else:
                LOG.info(
                    "Checking Steam for updates every %s minutes "
                    "(Steam spacing %.0fs, max retries %s)",
                    minutes,
                    self.steam_gate.policy.min_interval_seconds,
                    self.steam_gate.policy.max_retries,
                )
        while True:
            wait_for = self._seconds_until_next_update_check()
            if wait_for <= 0:
                return
            if check_hour is not None:
                LOG.info(
                    "Next %s update check in %.0fs (daily at local %02d:00)",
                    source,
                    wait_for,
                    check_hour,
                )
            if self._stop.wait(wait_for):
                return
            self._probe_steam_for_update(reason="steam_build", bypass_window=False)

    def run(self) -> int:
        def _signal_handler(signum: int, _frame: Any) -> None:
            # HA/Docker stop: SIGTERM, then SIGKILL after add-on ``timeout`` (≤300s).
            # Start the game graceful stop immediately so stdin stop commands
            # can use that budget even if the main loop is blocked in wait()/SteamCMD.
            LOG.info("Received signal %s; shutting down", signum)
            self._stop.set()
            try:
                self.process.stop()
            except Exception:  # noqa: BLE001
                LOG.exception("Error while stopping game process on signal")

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

        if self.config.status_http_enabled:
            self.status_server = StatusServer(
                self.config.status_http_host,
                self.config.status_http_port,
                self.status,
                health_provider=self.health,
                game_name=self.plugin.name,
                ui_theme=self.plugin.ui_theme,
                log_toolbox=self.log_tools,
                capture_callback=self.capture_logs,
                update_callback=self.force_update_now,
                restore_callback=self.request_restore,
                upload_callback=self.request_world_upload,
                upload_staging_dir=self.config.backup_dir,
                world_download_callback=self.world_save_download,
                restart_callback=self.request_restart,
                world_switch_callback=self.request_world_switch,
                world_create_callback=self.request_world_create,
            )
            self.status_server.start()

        self._status_thread = threading.Thread(
            target=self._status_loop, name="status-writer", daemon=True
        )
        self._status_thread.start()

        self.ensure_installed()
        if self._stop.is_set():
            LOG.info("Shutdown requested during install; exiting without starting the game")
        else:
            self.monitor.start()
            self.backups.start()
            self.process.start(reason="boot")
            self._publish_status()

            self._update_thread = threading.Thread(
                target=self._update_checker_loop, name="update-checker", daemon=True
            )
            self._update_thread.start()

        while not self._stop.is_set():
            if self._restore_pending:
                archive_name = self._restore_pending
                try:
                    self._apply_restore(archive_name)
                except Exception:
                    LOG.exception("World restore from %s failed", archive_name)
                finally:
                    with self._restore_lock:
                        if self._restore_pending == archive_name:
                            self._restore_pending = None

            if self._upload_pending is not None:
                staged = self._upload_pending
                try:
                    self._apply_world_upload(staged)
                except Exception:
                    LOG.exception("World upload restore from %s failed", staged)
                finally:
                    with self._restore_lock:
                        if self._upload_pending == staged:
                            self._upload_pending = None
                    staged.unlink(missing_ok=True)

            file_restart = consume_restart_request(self.config.state_dir)
            if file_restart is not None:
                debounce = file_restart.get("debounce_seconds") or 0
                reason = str(file_restart.get("reason") or "script")
                self.request_restart(reason=reason, debounce_seconds=float(debounce))

            if self._restart_pending and time.time() >= self._restart_not_before:
                try:
                    self._apply_restart()
                except Exception:
                    LOG.exception("Game process restart failed")

            if self._urgent_update_check:
                try:
                    self._run_urgent_update_check()
                except Exception:
                    LOG.exception("Urgent Steam update check failed")

            if self._update_pending and self._can_apply_update():
                try:
                    self._apply_update()
                except SteamCMDError:
                    LOG.exception("Steam update failed; backing off")
                    # Backoff + server recovery already handled in _apply_update.
                except Exception:
                    LOG.exception(
                        "Unexpected error applying update; backing off. "
                        "This is likely a supervisor bug."
                    )
                    # _apply_update schedules backoff for unexpected errors too.

            code = self.process.wait(timeout=2)
            if code is None:
                continue
            if self._stop.is_set():
                break
            if (
                self._restore_pending
                or self._upload_pending is not None
                or self._restart_pending
            ):
                continue
            if self.process.intentional_stop:
                break
            try:
                self.capture_logs("crash")
            except OSError:
                LOG.exception("Failed capturing logs after crash")
            self.notifier.notify(
                "crash",
                f"{self.plugin.name}: server crashed",
                f"Exit code {code}. Restarts this hour: {self.process.crashes_in_last_hour()}",
                force=True,
            )
            if self.process.can_restart_after_crash():
                delay = self.config.crash_restart_delay_seconds
                LOG.warning("Restarting after crash in %ss", delay)
                time.sleep(delay)
                if self._stop.is_set():
                    break
                self.monitor.reset_session()
                self.process.start(reason="crash")
            else:
                LOG.error("Not restarting after crash (limit reached or disabled)")
                self.notifier.notify(
                    "crash_loop",
                    f"{self.plugin.name}: crash loop",
                    "Restart limit reached; supervisor is leaving the server stopped.",
                    force=True,
                )
                break

        self.monitor.stop()
        self.backups.stop()
        if self.status_server:
            self.status_server.stop()
        # Idempotent if the signal handler already stopped the game.
        self.process.stop()
        self._publish_status()
        # Exit 0 on intentional stop so HA/Docker do not treat SIGTERM as failure.
        if self._stop.is_set() or self.process.intentional_stop:
            return 0
        return self.process.last_exit_code or 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generic Steam game server supervisor")
    parser.add_argument(
        "--plugin",
        default=None,
        help="Path to game plugin YAML/JSON (or set GAME_PLUGIN)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (default INFO, or LOG_LEVEL env)",
    )
    args = parser.parse_args(argv)

    level_name = (
        args.log_level or __import__("os").environ.get("LOG_LEVEL") or "INFO"
    ).upper()
    configure_logging(getattr(logging, level_name, logging.INFO))

    version = app_version()
    LOG.info("============================================================")
    LOG.info("Home Assistant app version: %s", version)
    LOG.info("Supervisor version: %s", supervisor_version())
    LOG.info("============================================================")

    plugin_path = resolve_plugin_path(args.plugin)
    LOG.info("Loading game plugin from %s", plugin_path)
    plugin = load_plugin(plugin_path)
    # Game option env keys come from the plugin (arg_map / settings_map /
    # templates / env_options) — not a hardcoded list in config.py.
    config = load_config(game_env_keys=plugin.docker_env_keys())
    if not config.install_dir:
        config.install_dir = "/data/game"

    if plugin.uses_package_install:
        LOG.info(
            "Home Assistant Logs tab = this container's stdout: supervisor events, "
            "[game] process output, [game-log] file-only lines, [package] downloads"
        )
    else:
        LOG.info(
            "Home Assistant Logs tab = this container's stdout: supervisor events, "
            "[game] process output, [game-log] file-only lines, [steamcmd] updates"
        )
    LOG.info(
        "Ingress status UI listens on port %s (HA OPEN WEB UI; host port not required)",
        config.status_http_port,
    )
    if plugin.uses_package_install:
        LOG.info(
            "Starting supervisor for %s (package_install, app_version=%s, install_dir=%s)",
            plugin.name,
            version,
            config.install_dir,
        )
    else:
        LOG.info(
            "Starting supervisor for %s (appid=%s, app_version=%s, install_dir=%s)",
            plugin.name,
            plugin.steam_app_id,
            version,
            config.install_dir,
        )
    supervisor = GameServerSupervisor(plugin, config)
    return supervisor.run()
