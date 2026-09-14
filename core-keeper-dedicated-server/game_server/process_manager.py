"""Launch and supervise the game server process with crash restarts."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .config import SupervisorConfig, format_option_value
from .launch_prepare import launch_options, prepare_launch, render_template
from .log_bridge import STDOUT_DEDUPER, strip_ansi
from .plugin import GamePlugin
from .privileges import make_preexec

LOG = logging.getLogger("game_server.process")


def _render_argv_token(token: str, options: dict, bool_style: str) -> str:
    """Expand ``{option_key}`` templates; return empty string to omit the token."""

    return render_template(str(token), options, bool_style)


class ProcessManager:
    def __init__(
        self,
        plugin: GamePlugin,
        config: SupervisorConfig,
        on_line: Callable[[str], None] | None = None,
        *,
        run_uid: int | None = None,
        run_gid: int | None = None,
    ) -> None:
        self.plugin = plugin
        self.config = config
        self.on_line = on_line
        self.run_uid = run_uid
        self.run_gid = run_gid
        self.proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self.start_count = 0
        self.restart_count = 0
        self.crash_count = 0
        self.intentional_stop = False
        self.last_exit_code: int | None = None
        self.last_started_at: float | None = None
        self.last_stopped_at: float | None = None
        self.last_start_reason: str | None = None
        self._crash_times: deque[float] = deque(maxlen=100)
        self._reader: threading.Thread | None = None
        self.on_line_error: str | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def build_command(self) -> list[str]:
        cmd = list(self.plugin.executable)
        java_env = self.plugin.java_opts_env or "JAVA_OPTS"
        java_opts = str(
            self.config.game_options.get("java_opts")
            or os.environ.get(java_env)
            or ""
        )
        if java_opts and cmd and cmd[0] == "java":
            opts = [part for part in java_opts.split() if part]
            cmd = [cmd[0], *opts, *cmd[1:]]

        options = launch_options(
            self.plugin,
            self.config.game_options,
            working_dir=self.config.game_options.get("working_dir")
            or self.config.install_dir
            or self.plugin.working_dir,
            install_dir=self.config.install_dir,
        )

        for token in self.plugin.argv_prefix:
            rendered = _render_argv_token(token, options, self.plugin.bool_style)
            if rendered == "":
                continue
            cmd.append(rendered)

        for option_key, flag in self.plugin.arg_map.items():
            if option_key not in options:
                continue
            value = options[option_key]
            if value is None or value == "":
                continue
            rendered = format_option_value(value, self.plugin.bool_style)
            if flag.endswith("="):
                cmd.append(f"{flag}{rendered}")
            else:
                cmd.extend([flag, rendered])

        settings_flag = (self.plugin.settings_flag or "").strip()
        if settings_flag and (self.plugin.fixed_settings or self.plugin.settings_map):
            pairs: list[str] = []
            for setting_name, raw in self.plugin.fixed_settings.items():
                rendered = _render_argv_token(raw, options, self.plugin.bool_style)
                if rendered == "":
                    continue
                pairs.extend([str(setting_name), rendered])
            for option_key, setting_name in self.plugin.settings_map.items():
                if option_key not in options:
                    continue
                value = options[option_key]
                if value is None or value == "":
                    continue
                pairs.extend(
                    [
                        str(setting_name),
                        format_option_value(value, self.plugin.bool_style),
                    ]
                )
            if pairs:
                cmd.append(settings_flag)
                cmd.extend(pairs)
        return cmd

    def start(self, reason: str = "boot") -> None:
        with self._lock:
            if self.running:
                return
            self.intentional_stop = False
            workdir = Path(
                self.config.game_options.get("working_dir")
                or self.config.install_dir
                or self.plugin.working_dir
            )
            workdir.mkdir(parents=True, exist_ok=True)
            Path(self.plugin.data_dir).mkdir(parents=True, exist_ok=True)
            Path(self.plugin.logs_dir).mkdir(parents=True, exist_ok=True)

            # Rewrite plugin config files and create a missing world before launch.
            prepare_launch(
                self.plugin,
                self.config.game_options,
                working_dir=workdir,
                install_dir=self.config.install_dir,
                run_uid=self.run_uid,
                run_gid=self.run_gid,
            )

            cmd = self.build_command()
            env = os.environ.copy()
            env.update(self.plugin.env)
            if self.start_count > 0:
                self.restart_count += 1
            self.start_count += 1
            self.last_start_reason = reason
            LOG.info(
                "Starting server (%s): %s (cwd=%s, uid=%s)",
                reason,
                " ".join(cmd),
                workdir,
                self.run_uid,
            )
            # Keep stdin open even when we have no stop commands. Some headless
            # servers (Factorio) log a scary "Got EOF on stdin" Error and close
            # their console reader when stdin is /dev/null — hosting still works,
            # but PIPE avoids the false alarm. Stop commands write to this pipe.
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=make_preexec(self.run_uid, self.run_gid),
            )
            self.last_started_at = time.time()
            self._reader = threading.Thread(
                target=self._read_output, name="game-stdout", daemon=True
            )
            self._reader.start()

    def _read_output(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            text = strip_ansi(line.rstrip("\n"))
            # Prefer process stdout over a later file-log echo of the same line.
            if STDOUT_DEDUPER.remember_if_new(text):
                LOG.info("[game] %s", text)
            if self.on_line:
                try:
                    self.on_line(text)
                except Exception as exc:  # noqa: BLE001
                    # Keep reading stdout (game logs must not stop), but do not
                    # keep invoking a broken callback forever.
                    self.on_line_error = str(exc)
                    LOG.exception(
                        "on_line callback failed; disabling further line callbacks"
                    )
                    self.on_line = None

    def stop(self, timeout: float | None = None) -> None:
        """Stop the game process as gracefully as the timeout allows.

        Sequence (all within ``timeout``):
        1. Send plugin ``stop_stdin_commands`` when configured
        2. Wait for a voluntary exit
        3. Escalate to SIGTERM, then SIGKILL only if still running

        Home Assistant uses the add-on ``timeout`` (max 300s) as the Docker
        stop grace period. Keep this game-stop budget below that so supervisor
        cleanup after the game exits can still finish before SIGKILL.
        """

        timeout = (
            float(timeout)
            if timeout is not None
            else float(
                self.config.stop_timeout_seconds
                or self.plugin.stop_timeout_seconds
                or 60
            )
        )
        timeout = max(5.0, timeout)
        with self._lock:
            self.intentional_stop = True
            proc = self.proc
        if proc is None:
            return
        if proc.poll() is not None:
            self.last_exit_code = proc.returncode
            self.last_stopped_at = time.time()
            with self._lock:
                self.proc = None
            return

        deadline = time.time() + timeout
        has_stdin_stop = bool(self.plugin.stop_stdin_commands)
        kill_budget = min(10.0, max(3.0, timeout * 0.05))
        if has_stdin_stop:
            # Spend most of the budget waiting for stdin stop commands. Escalate
            # late so HA's Docker stop grace (add-on timeout, ≤300s) is not burned
            # on SIGKILL.
            term_budget = min(30.0, max(5.0, timeout * 0.12))
            escalate_budget = term_budget + kill_budget
            if escalate_budget > timeout * 0.45:
                escalate_budget = max(3.0, timeout * 0.4)
                term_budget = escalate_budget * 0.7
                kill_budget = escalate_budget - term_budget
            graceful_deadline = deadline - escalate_budget
        else:
            # No console quit path — SIGTERM immediately and keep most of the
            # budget for save-on-signal games to finish writing.
            term_budget = max(5.0, timeout - kill_budget - 1.0)
            graceful_deadline = time.time()

        LOG.info(
            "Stopping server pid=%s gracefully (timeout=%.0fs%s)",
            proc.pid,
            timeout,
            "" if has_stdin_stop else ", signal-first",
        )
        if has_stdin_stop and proc.stdin:
            try:
                for command in self.plugin.stop_stdin_commands:
                    LOG.info("Sending stop command via stdin: %s", command)
                    proc.stdin.write(command + "\n")
                    proc.stdin.flush()
                    # Brief pause so save can start before exit is sent.
                    time.sleep(1)
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            except (BrokenPipeError, OSError):
                LOG.warning("Failed writing stop commands to stdin", exc_info=True)

        # Prefer voluntary exit after stdin commands (no signal yet).
        remaining = max(0.0, graceful_deadline - time.time())
        try:
            if remaining > 0:
                proc.wait(timeout=remaining)
            elif proc.poll() is None:
                raise subprocess.TimeoutExpired(proc.args, 0)
        except subprocess.TimeoutExpired:
            LOG.info(
                "Sending SIGTERM%s",
                " after graceful wait" if has_stdin_stop else " (no stdin stop commands)",
            )
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            term_wait = max(0.5, min(term_budget, deadline - time.time() - kill_budget))
            try:
                proc.wait(timeout=term_wait)
            except subprocess.TimeoutExpired:
                LOG.warning("Server did not exit after SIGTERM; killing")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=max(0.5, min(kill_budget, deadline - time.time())))
                except subprocess.TimeoutExpired:
                    LOG.error("Server did not exit after SIGKILL")
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

        self.last_exit_code = proc.returncode
        self.last_stopped_at = time.time()
        with self._lock:
            self.proc = None

    def wait(self, timeout: float | None = None) -> int | None:
        proc = self.proc
        if proc is None:
            # Do not re-report last_exit_code (that busy-loops the supervisor).
            if timeout and timeout > 0:
                time.sleep(timeout)
            return None
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self.last_exit_code = code
        self.last_stopped_at = time.time()
        if not self.intentional_stop and code is not None:
            self.crash_count += 1
            self._crash_times.append(time.time())
            LOG.error("Server exited unexpectedly with code %s", code)
        with self._lock:
            if self.proc is proc:
                self.proc = None
        return code

    def crashes_in_last_hour(self) -> int:
        cutoff = time.time() - 3600
        return sum(1 for ts in self._crash_times if ts >= cutoff)

    def can_restart_after_crash(self) -> bool:
        if not self.config.restart_on_crash:
            return False
        return self.crashes_in_last_hour() < self.config.max_crash_restarts_per_hour

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "start_count": self.start_count,
            "restart_count": self.restart_count,
            "last_start_reason": self.last_start_reason,
            "crash_count": self.crash_count,
            "crashes_last_hour": self.crashes_in_last_hour(),
            "last_exit_code": self.last_exit_code,
            "last_started_at": self.last_started_at,
            "last_stopped_at": self.last_stopped_at,
            "run_uid": self.run_uid,
            "command": self.build_command(),
            "on_line_error": self.on_line_error,
        }
