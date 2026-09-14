#!/usr/bin/env python3
"""Characterization tests for adversarial-review findings.

These tests prove the currently observed (buggy) behavior. They are not the
desired contract. When a finding is fixed, the matching test should fail and
be inverted to the safe behavior.

Classification of the full review list lives in the PR that introduced this
file. Only the possibly-serious items are encoded here.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.backup import BackupManager  # noqa: E402
from game_server.config import SupervisorConfig  # noqa: E402
from game_server.plugin import load_plugin  # noqa: E402
from game_server.process_manager import ProcessManager  # noqa: E402
from game_server.supervisor import GameServerSupervisor  # noqa: E402
from game_server.world_save import (  # noqa: E402
    KIND_DIRECTORY,
    ActiveWorld,
    SCOPE_NAMED_PATH,
    apply_world_upload,
    write_world_backup,
)

FIXTURE = ROOT / "tests" / "fixtures" / "example.game.yaml"
SECRET = "s3cret-pass-not-for-logs"


def _directory_world(tmp: Path, *, nested: bool = False) -> tuple[Path, Path, ActiveWorld]:
    data = tmp / "data"
    target = data / "saves" / "worlds" / "FamilyWorld"
    target.mkdir(parents=True)
    if nested:
        payload = target / "worlds" / "save.dat"
        payload.parent.mkdir()
        payload.write_bytes(b"LIVE-WORLD")
    else:
        (target / "keep.dat").write_bytes(b"LIVE-WORLD")
    active = ActiveWorld(
        bytes=1,
        path=str(target),
        label="FamilyWorld",
        scope=SCOPE_NAMED_PATH,
        sources=[str(target)],
        expected_paths=[str(target)],
        kind=KIND_DIRECTORY,
    )
    return data, target, active


def _supervisor(tmp: Path, *, extra_options: dict | None = None) -> GameServerSupervisor:
    plugin = load_plugin(FIXTURE)
    world = tmp / "world"
    logs = tmp / "logs"
    game = tmp / "game"
    world.mkdir()
    logs.mkdir()
    game.mkdir()
    options = {
        "data_dir": str(world),
        "logs_dir": str(logs),
        "world_name": "FamilyWorld",
    }
    if extra_options:
        options.update(extra_options)
    cfg = SupervisorConfig(
        drop_privileges=False,
        status_http_enabled=False,
        backup_enabled=False,
        ha_notifications=False,
        update_on_start=False,
        auto_update_interval_minutes=0,
        backup_on_update=True,
        backup_min_source_bytes=1,
        crash_restart_delay_seconds=1,
        state_dir=str(tmp / "state"),
        install_dir=str(game),
        backup_dir=str(tmp / "backups"),
        steamcmd_dir=str(tmp / "steamcmd"),
        game_options=options,
    )
    plugin.data_dir = str(world)
    plugin.logs_dir = str(logs)
    plugin.working_dir = str(game)
    plugin.executable = [sys.executable, "-c", "raise SystemExit(0)"]
    return GameServerSupervisor(plugin, cfg)


class FailedFolderRestoreTests(unittest.TestCase):
    """Finding 1: failed folder restores wipe the live world, then restart."""

    def test_empty_zip_rejects_after_deleting_live_folder_contents(self) -> None:
        """Empty ZIP is a valid archive with no members.

        Desired: refuse before mutating the live directory (extract into staging).
        Today: contents are deleted, then extraction raises.
        """

        with tempfile.TemporaryDirectory() as tmp:
            data, target, active = _directory_world(Path(tmp))
            upload = Path(tmp) / "empty.zip"
            with zipfile.ZipFile(upload, "w"):
                pass
            with self.assertRaises(RuntimeError) as ctx:
                apply_world_upload(active, upload, data_dir=data)
            self.assertIn("no files", str(ctx.exception).lower())
            self.assertTrue(target.is_dir())
            self.assertFalse(
                (target / "keep.dat").exists(),
                "live keep.dat should still be missing until this wipe-before-validate is fixed",
            )
            self.assertEqual(list(target.iterdir()), [])

    def test_failed_upload_restarts_on_emptied_world_without_rollback(self) -> None:
        """Supervisor creates a safety copy, then does not restore it on failure."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            world_dir = root / "world" / "saves" / "worlds" / "FamilyWorld"
            world_dir.mkdir(parents=True)
            (world_dir / "keep.dat").write_bytes(b"LIVE-WORLD")
            upload = root / "empty.zip"
            with zipfile.ZipFile(upload, "w"):
                pass
            supervisor.process.start = lambda reason="boot": None  # type: ignore[method-assign]
            supervisor.process.stop = lambda timeout=None: None  # type: ignore[method-assign]
            with self.assertRaises(RuntimeError):
                supervisor._apply_world_upload(upload)
            self.assertIsNotNone(supervisor.last_restore_error)
            self.assertFalse((world_dir / "keep.dat").exists())
            safeties = list((root / "backups").glob("pre-restore-*"))
            self.assertEqual(len(safeties), 1)
            # Safety copy still holds the old world; live dir was not rolled back.
            with zipfile.ZipFile(safeties[0]) as zf:
                self.assertIn("keep.dat", zf.namelist())


class FolderBackupRoundTripTests(unittest.TestCase):
    """Finding 2: sole top-level directory is stripped on extract."""

    def test_internal_folder_backup_flattens_single_child_directory(self) -> None:
        """Supervisor zips folder contents; restore reuses upload extract.

        Desired: internal backups round-trip byte-for-byte, including a sole
        child directory such as worlds/save.dat.
        Today: extract strips that wrapper, so save.dat lands at the world root.
        """

        with tempfile.TemporaryDirectory() as tmp:
            data, target, active = _directory_world(Path(tmp), nested=True)
            archive = Path(tmp) / "backup.zip"
            write_world_backup(target, KIND_DIRECTORY, archive)
            with zipfile.ZipFile(archive) as zf:
                self.assertIn("worlds/save.dat", zf.namelist())
            for child in list(target.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            apply_world_upload(active, archive, data_dir=data)
            self.assertTrue(
                (target / "save.dat").is_file(),
                "today the worlds/ prefix is stripped",
            )
            self.assertEqual((target / "save.dat").read_bytes(), b"LIVE-WORLD")
            self.assertFalse((target / "worlds" / "save.dat").exists())


class UpdateWithoutBackupTests(unittest.TestCase):
    """Finding 3: create_backup() None is treated as success on the update path."""

    def test_apply_update_installs_after_backup_reports_insufficient_disk(self) -> None:
        """Desired: abort the installer when world data could not be snapshotted."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            save = root / "world" / "saves" / "worlds" / "FamilyWorld.zip"
            save.parent.mkdir(parents=True)
            save.write_bytes(b"WORLD-BYTES" * 32)
            supervisor.backups.min_free_disk_mb = 10**9
            backup = supervisor.backups.create_backup(reason="pre-update")
            self.assertIsNone(backup)
            self.assertIn("insufficient disk", (supervisor.backups.last_error or "").lower())

            supervisor.process.start = lambda reason="boot": None  # type: ignore[method-assign]
            supervisor.process.stop = lambda timeout=None: None  # type: ignore[method-assign]
            installs: list[str] = []

            def fake_install(*_args: object, **_kwargs: object) -> str:
                installs.append("called")
                return "build-1"

            with patch(
                "game_server.supervisor.steamcmd.install_or_update",
                side_effect=fake_install,
            ):
                supervisor._apply_update()
            self.assertEqual(
                installs,
                ["called"],
                "installer ran even though pre-update backup returned None",
            )


class CrashRecoveryDuringDeferredUpdateTests(unittest.TestCase):
    """Finding 4: pending-but-blocked update skips crash handling and busy-loops."""

    def test_unexpected_exit_zero_does_not_consume_restart_budget(self) -> None:
        plugin = load_plugin(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "world").mkdir()
            (root / "logs").mkdir()
            (root / "game").mkdir()
            cfg = SupervisorConfig(
                drop_privileges=False,
                status_http_enabled=False,
                backup_enabled=False,
                ha_notifications=False,
                state_dir=str(root / "state"),
                install_dir=str(root / "game"),
                backup_dir=str(root / "backups"),
                steamcmd_dir=str(root / "steamcmd"),
                game_options={
                    "data_dir": str(root / "world"),
                    "logs_dir": str(root / "logs"),
                },
            )
            plugin.executable = [sys.executable, "-c", "raise SystemExit(0)"]
            plugin.working_dir = str(root / "game")
            plugin.data_dir = str(root / "world")
            plugin.logs_dir = str(root / "logs")
            mgr = ProcessManager(plugin, cfg)
            mgr.start(reason="boot")
            code = mgr.wait(timeout=5)
            self.assertEqual(code, 0)
            self.assertFalse(mgr.intentional_stop)
            self.assertEqual(
                mgr.crash_count,
                0,
                "exit 0 is ignored by the restart budget today",
            )

    def test_deferred_update_after_exit_spins_without_crash_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            supervisor.plugin.executable = [
                sys.executable,
                "-c",
                "raise SystemExit(1)",
            ]
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]
            supervisor._update_pending = True
            supervisor._update_not_before = time.time() + 10_000
            wait_calls = 0
            original_wait = supervisor.process.wait

            def counting_wait(timeout: float | None = None) -> int | None:
                nonlocal wait_calls
                wait_calls += 1
                return original_wait(timeout)

            supervisor.process.wait = counting_wait  # type: ignore[method-assign]

            def runner() -> None:
                with patch("signal.signal"):
                    supervisor.run()

            thread = threading.Thread(target=runner, name="supervisor-run", daemon=True)
            thread.start()
            time.sleep(0.2)
            supervisor._stop.set()
            thread.join(timeout=5)
            self.assertTrue(wait_calls > 50, f"expected a tight loop, got {wait_calls} waits")
            self.assertEqual(
                supervisor.process.start_count,
                1,
                "crash restart did not run while an update was queued but blocked",
            )


class PasswordLeakTests(unittest.TestCase):
    """Finding 5: launch argv (including passwords) is logged, status'd, and captured."""

    def test_status_and_capture_include_unredacted_server_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root, extra_options={"server_password": SECRET})
            supervisor.plugin.arg_map["server_password"] = "-password"
            cmd = supervisor.process.build_command()
            self.assertIn(SECRET, cmd)
            status = supervisor.status()
            dumped = json.dumps(status, default=str)
            self.assertIn(SECRET, dumped)
            capture = supervisor.capture_logs("manual")
            status_file = Path(capture["path"]) / "status.json"
            self.assertIn(SECRET, status_file.read_text(encoding="utf-8"))

    def test_startup_log_prints_full_command_with_password(self) -> None:
        plugin = load_plugin(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "world").mkdir()
            (root / "logs").mkdir()
            (root / "game").mkdir()
            cfg = SupervisorConfig(
                drop_privileges=False,
                status_http_enabled=False,
                backup_enabled=False,
                ha_notifications=False,
                state_dir=str(root / "state"),
                install_dir=str(root / "game"),
                backup_dir=str(root / "backups"),
                steamcmd_dir=str(root / "steamcmd"),
                game_options={
                    "data_dir": str(root / "world"),
                    "logs_dir": str(root / "logs"),
                    "server_password": SECRET,
                },
            )
            plugin.arg_map["server_password"] = "-password"
            plugin.executable = [sys.executable, "-c", "raise SystemExit(0)"]
            plugin.working_dir = str(root / "game")
            plugin.data_dir = str(root / "world")
            plugin.logs_dir = str(root / "logs")
            mgr = ProcessManager(plugin, cfg)
            with self.assertLogs("game_server.process", level="INFO") as cm:
                mgr.start(reason="boot")
                mgr.wait(timeout=5)
            joined = "\n".join(cm.output)
            self.assertIn(SECRET, joined)


class BackupNoneVsExceptionTests(unittest.TestCase):
    """Helpers that make finding 3 less ambiguous at the BackupManager layer."""

    def test_create_backup_returns_none_for_disk_pressure_not_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            (world / "save.dat").write_bytes(b"WORLD" * 64)
            mgr = BackupManager(
                root / "backups",
                [world],
                min_source_bytes=1,
                min_free_disk_mb=10**9,
            )
            self.assertIsNone(mgr.create_backup(reason="pre-update"))
            self.assertIn("insufficient disk", (mgr.last_error or "").lower())


if __name__ == "__main__":
    unittest.main()
