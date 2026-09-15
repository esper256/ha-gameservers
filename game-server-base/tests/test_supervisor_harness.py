#!/usr/bin/env python3
"""In-process supervisor tests: restart debounce, empty-wait, probe, Copyparty."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.active_world import write_restart_request  # noqa: E402
from game_server.copyparty import CopypartySpec  # noqa: E402
from game_server.plugin import StatusProbeSpec, load_plugin  # noqa: E402
from game_server.supervisor import GameServerSupervisor  # noqa: E402

from test_review_findings import _supervisor  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "example.game.yaml"


def _run_supervisor(supervisor: GameServerSupervisor) -> threading.Thread:
    def runner() -> None:
        with patch("signal.signal"):
            supervisor.run()

    thread = threading.Thread(target=runner, name="supervisor-run", daemon=True)
    thread.start()
    return thread


def _stop_supervisor(supervisor: GameServerSupervisor, thread: threading.Thread) -> None:
    supervisor._stop.set()
    try:
        supervisor.process.stop(timeout=2)
    except Exception:
        pass
    thread.join(timeout=8)


class SupervisorHarnessTests(unittest.TestCase):
    def test_debounced_file_restart_stops_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            script = root / "game" / "sleep.py"
            script.write_text(
                "import time\ntime.sleep(30)\n",
                encoding="utf-8",
            )
            supervisor.plugin.executable = [sys.executable, str(script)]
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]
            supervisor.config.crash_restart_delay_seconds = 0
            thread = _run_supervisor(supervisor)
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count < 1:
                time.sleep(0.05)
            self.assertGreaterEqual(supervisor.process.start_count, 1)
            write_restart_request(
                supervisor.config.state_dir,
                reason="mod-publish",
                debounce_seconds=0.2,
            )
            deadline = time.time() + 6
            while time.time() < deadline and supervisor.process.start_count < 2:
                time.sleep(0.05)
            self.assertGreaterEqual(supervisor.process.start_count, 2)
            _stop_supervisor(supervisor, thread)

    def test_restart_when_empty_waits_for_probe_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            supervisor.plugin.restart_when_empty = True
            script = root / "game" / "sleep.py"
            script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            supervisor.plugin.executable = [sys.executable, str(script)]
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]
            probe = root / "game" / "probe.py"
            count_file = root / "count.txt"
            count_file.write_text("1\n", encoding="utf-8")
            probe.write_text(
                "import json, pathlib, sys\n"
                f"n = int(pathlib.Path({str(count_file)!r}).read_text().strip() or '0')\n"
                "print(json.dumps({'player_count': n, 'ready': True}))\n",
                encoding="utf-8",
            )
            supervisor.plugin.status_probe = StatusProbeSpec(
                argv=[sys.executable, str(probe)],
                interval_seconds=0.2,
            )
            thread = _run_supervisor(supervisor)
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count < 1:
                time.sleep(0.05)
            deadline = time.time() + 4
            while time.time() < deadline and supervisor.monitor.state.player_count != 1:
                time.sleep(0.05)
            self.assertEqual(supervisor.monitor.state.player_count, 1)
            starts = supervisor.process.start_count
            write_restart_request(
                supervisor.config.state_dir,
                reason="mod-publish",
                debounce_seconds=0,
            )
            time.sleep(0.6)
            self.assertEqual(supervisor.process.start_count, starts)
            count_file.write_text("0\n", encoding="utf-8")
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.process.start_count <= starts:
                time.sleep(0.05)
            self.assertGreater(supervisor.process.start_count, starts)
            _stop_supervisor(supervisor, thread)

    def test_copyparty_survives_game_exit_and_status_uses_mapped_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "copyparty"
            alive = root / "copyparty.alive"
            stub.write_text(
                "#!/usr/bin/env python3\n"
                "import signal, time, pathlib, sys\n"
                f"pathlib.Path({str(alive)!r}).write_text('1')\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "while True:\n"
                "    time.sleep(0.2)\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            data = root / "world"
            data.mkdir(exist_ok=True)
            supervisor.plugin.copyparty = CopypartySpec.from_dict(
                {"port": 8765, "root": "{data_dir}/drop"}
            )
            supervisor._publisher._spec = supervisor.plugin.copyparty
            supervisor._publisher._data_dir = str(data)
            supervisor._publisher._world_name = "World"
            script = root / "game" / "die.py"
            script.write_text("raise SystemExit(1)\n", encoding="utf-8")
            supervisor.plugin.executable = [sys.executable, str(script)]
            supervisor.plugin.hold_on_crash_loop = True
            supervisor.config.restart_on_crash = False
            supervisor.ensure_installed = lambda: None  # type: ignore[method-assign]
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
            with patch.dict(os.environ, env, clear=False):
                with patch(
                    "game_server.copyparty.fetch_addon_network",
                    return_value={"8765/tcp": 19999},
                ):
                    thread = _run_supervisor(supervisor)
                    deadline = time.time() + 5
                    while time.time() < deadline and not alive.is_file():
                        time.sleep(0.05)
                    self.assertTrue(alive.is_file())
                    deadline = time.time() + 4
                    while time.time() < deadline and supervisor.process.start_count < 1:
                        time.sleep(0.05)
                    time.sleep(0.4)
                    status = supervisor.status()
                    self.assertEqual((status.get("copyparty") or {}).get("port"), 19999)
                    _stop_supervisor(supervisor, thread)

    def test_example_plugin_unchanged(self) -> None:
        plugin = load_plugin(FIXTURE)
        self.assertIsNone(plugin.copyparty)


if __name__ == "__main__":
    unittest.main()
