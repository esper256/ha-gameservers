#!/usr/bin/env python3
"""Regression tests for the adversarial-review restore / backup / crash bugs.

Password-in-argv is a nitpick: the same secret is already on the HA
Configuration screen immediately before Logs / Open Web UI.
"""

from __future__ import annotations

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

from game_server.backup import BACKUP_FAILED, BackupManager  # noqa: E402
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


def _supervisor(tmp: Path) -> GameServerSupervisor:
    plugin = load_plugin(FIXTURE)
    world = tmp / "world"
    logs = tmp / "logs"
    game = tmp / "game"
    world.mkdir()
    logs.mkdir()
    game.mkdir()
    cfg = SupervisorConfig(
        drop_privileges=False,
        status_http_enabled=False,
        backup_enabled=False,
        ha_notifications=False,
        update_on_start=False,
        auto_update_interval_minutes=0,
        backup_on_update=True,
        backup_min_source_bytes=1,
        crash_restart_delay_seconds=0,
        state_dir=str(tmp / "state"),
        install_dir=str(game),
        backup_dir=str(tmp / "backups"),
        steamcmd_dir=str(tmp / "steamcmd"),
        game_options={
            "data_dir": str(world),
            "logs_dir": str(logs),
            "world_name": "FamilyWorld",
        },
    )
    plugin.data_dir = str(world)
    plugin.logs_dir = str(logs)
    plugin.working_dir = str(game)
    plugin.executable = [sys.executable, "-c", "raise SystemExit(0)"]
    return GameServerSupervisor(plugin, cfg)


class FailedFolderRestoreTests(unittest.TestCase):
    def test_empty_zip_rejects_without_deleting_live_folder_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data, target, active = _directory_world(Path(tmp))
            upload = Path(tmp) / "empty.zip"
            with zipfile.ZipFile(upload, "w"):
                pass
            with self.assertRaises(RuntimeError) as ctx:
                apply_world_upload(active, upload, data_dir=data)
            self.assertIn("no files", str(ctx.exception).lower())
            self.assertEqual((target / "keep.dat").read_bytes(), b"LIVE-WORLD")

    def test_failed_upload_leaves_live_world_and_keeps_safety_copy(self) -> None:
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
            self.assertEqual((world_dir / "keep.dat").read_bytes(), b"LIVE-WORLD")
            safeties = list((root / "backups").glob("pre-restore-*"))
            self.assertEqual(len(safeties), 1)
            with zipfile.ZipFile(safeties[0]) as zf:
                self.assertIn("keep.dat", zf.namelist())


class FolderBackupRoundTripTests(unittest.TestCase):
    def test_internal_folder_backup_keeps_single_child_directory(self) -> None:
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
            self.assertEqual(
                (target / "worlds" / "save.dat").read_bytes(), b"LIVE-WORLD"
            )
            self.assertFalse((target / "save.dat").exists())

    def test_user_zip_of_world_folder_still_strips_matching_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data, target, active = _directory_world(Path(tmp))
            upload = Path(tmp) / "wrapped.zip"
            with zipfile.ZipFile(upload, "w") as zf:
                zf.writestr("FamilyWorld/level.dat", b"LEVEL")
            apply_world_upload(active, upload, data_dir=data)
            self.assertEqual((target / "level.dat").read_bytes(), b"LEVEL")
            self.assertFalse((target / "FamilyWorld").exists())
            self.assertFalse((target / "keep.dat").exists())


class UpdateWithoutBackupTests(unittest.TestCase):
    def test_apply_update_aborts_when_backup_reports_insufficient_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            save = root / "world" / "saves" / "worlds" / "FamilyWorld.zip"
            save.parent.mkdir(parents=True)
            save.write_bytes(b"WORLD-BYTES" * 32)
            supervisor.backups.min_free_disk_mb = 10**9
            outcome = supervisor.backups.create_backup_result(reason="pre-update")
            self.assertEqual(outcome.status, BACKUP_FAILED)
            self.assertIn("insufficient disk", (outcome.reason or "").lower())

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
                with self.assertRaises(RuntimeError):
                    supervisor._apply_update()
            self.assertEqual(installs, [])
            self.assertIn("insufficient disk", (supervisor.last_update_error or "").lower())


class CrashRecoveryDuringDeferredUpdateTests(unittest.TestCase):
    def test_unexpected_exit_zero_consumes_restart_budget(self) -> None:
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
            self.assertEqual(mgr.crash_count, 1)

    def test_deferred_update_after_exit_crash_restarts_without_busy_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            marker = root / "state" / "first-boot"
            script = root / "game" / "fake_server.py"
            script.write_text(
                "import pathlib, sys, time\n"
                f"marker = pathlib.Path({str(marker)!r})\n"
                "if marker.exists():\n"
                "    time.sleep(60)\n"
                "else:\n"
                "    marker.write_text('1')\n"
                "    sys.exit(1)\n",
                encoding="utf-8",
            )
            supervisor.plugin.executable = [sys.executable, str(script)]
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
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count < 2:
                time.sleep(0.05)
            supervisor._stop.set()
            try:
                supervisor.process.stop(timeout=2)
            except Exception:
                pass
            thread.join(timeout=8)
            self.assertGreaterEqual(supervisor.process.start_count, 2)
            self.assertLess(wait_calls, 40, f"busy-looped: {wait_calls} waits")

    def test_crash_loop_exits_supervisor_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            supervisor.config.restart_on_crash = False
            script = root / "game" / "die.py"
            script.write_text("raise SystemExit(1)\n", encoding="utf-8")
            supervisor.plugin.executable = [sys.executable, str(script)]
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]

            def runner() -> None:
                with patch("signal.signal"):
                    supervisor.run()

            thread = threading.Thread(target=runner, name="supervisor-run", daemon=True)
            thread.start()
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
            self.assertFalse(supervisor.health()["ok"])

    def test_hold_on_crash_loop_keeps_supervisor_for_operator_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            supervisor.config.restart_on_crash = False
            supervisor.plugin.hold_on_crash_loop = True
            script = root / "game" / "die.py"
            script.write_text("raise SystemExit(1)\n", encoding="utf-8")
            supervisor.plugin.executable = [sys.executable, str(script)]
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]

            def runner() -> None:
                with patch("signal.signal"):
                    supervisor.run()

            thread = threading.Thread(target=runner, name="supervisor-run", daemon=True)
            thread.start()
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count < 1:
                time.sleep(0.05)
            time.sleep(0.4)
            self.assertTrue(thread.is_alive(), "supervisor exited after the game crashed")
            self.assertEqual(supervisor.lifecycle(), "failed")
            self.assertFalse(supervisor.health()["ok"])
            supervisor.request_restart(reason="operator")
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count < 2:
                time.sleep(0.05)
            self.assertGreaterEqual(supervisor.process.start_count, 2)
            supervisor._stop.set()
            try:
                supervisor.process.stop(timeout=2)
            except Exception:
                pass
            thread.join(timeout=8)


class BackupResultTests(unittest.TestCase):
    def test_create_backup_result_failed_for_disk_pressure(self) -> None:
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
            outcome = mgr.create_backup_result(reason="pre-update")
            self.assertEqual(outcome.status, BACKUP_FAILED)
            self.assertIsNone(mgr.create_backup(reason="pre-update"))
            self.assertIn("insufficient disk", (mgr.last_error or "").lower())


if __name__ == "__main__":
    unittest.main()
