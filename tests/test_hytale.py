#!/usr/bin/env python3
"""Hytale game-layer checks (plugin, names, device-code scrape, config merge)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hytale-dedicated-server" / "games" / "game.yaml"
DEFAULTS = ROOT / "hytale-dedicated-server" / "haos_defaults.py"
CONFIG = ROOT / "hytale-dedicated-server" / "config.yaml"
WRAPPER = ROOT / "hytale-dedicated-server" / "launch_wrapper.sh"
RUNSH = ROOT / "hytale-dedicated-server" / "run.sh"
BASE = ROOT / "game-server-base"

sys.path.insert(0, str(BASE))
from game_server.plugin import load_plugin  # noqa: E402
from game_server.process_manager import ProcessManager  # noqa: E402
from game_server.config import SupervisorConfig  # noqa: E402
from game_server.log_tools import LogToolbox  # noqa: E402
from game_server.monitor import LogMonitor  # noqa: E402
from game_server.world_save import locate_active_world  # noqa: E402
from game_server.version import SUPERVISOR_VERSION  # noqa: E402


def _load_defaults():
    spec = importlib.util.spec_from_file_location("hytale_haos_defaults", DEFAULTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HytalePluginTests(unittest.TestCase):
    def test_plugin_is_command_package_install(self) -> None:
        plugin = load_plugin(PLUGIN)
        self.assertEqual(plugin.name, "Hytale")
        self.assertIsNone(plugin.steam_app_id)
        self.assertTrue(plugin.uses_package_install)
        assert plugin.package_install is not None
        self.assertEqual(plugin.package_install.kind, "command")
        self.assertEqual(
            plugin.package_install.version_argv,
            ["python3", "/opt/haos_defaults.py", "print-version"],
        )
        self.assertEqual(
            plugin.package_install.install_argv,
            ["python3", "/opt/haos_defaults.py", "install"],
        )
        self.assertEqual(plugin.install_marker, "Assets.zip")
        self.assertEqual(plugin.executable, ["/opt/launch_wrapper.sh"])
        self.assertEqual(
            plugin.log_patterns.ready,
            [r"\[ServerManager\|P\] Listening on /"],
        )
        self.assertEqual(
            plugin.log_patterns.game_version,
            [
                r"\[HytaleServer\] Booting up HytaleServer - Version: "
                r"(?P<version>\d+(?:\.\d+)+)"
            ],
        )
        self.assertEqual(
            plugin.log_patterns.player_join,
            [r"\[World\|[^\]]+\] Player '(?P<player>[^']+)' joined world"],
        )
        self.assertEqual(
            plugin.log_patterns.player_leave,
            [
                r"\[Hytale\] [0-9a-fA-F-]{36} - (?P<player>.+?) "
                r"at QuicConnectionAddress\{"
            ],
        )
        self.assertEqual(plugin.log_patterns.player_count, [])
        self.assertEqual(plugin.log_patterns.players_empty, [])
        self.assertEqual(plugin.log_patterns.version_mismatch, [])
        self.assertIn("ready", plugin.log_pattern_candidates)
        self.assertEqual(plugin.player_tracking_mode, "presence")
        self.assertEqual(plugin.ui_theme.get("accent"), "#8ee04a")
        self.assertEqual(plugin.env.get("HYTALE_DISABLE_UPDATES"), "1")
        self.assertEqual(plugin.env_options, ["JAVA_OPTS"])
        self.assertEqual(plugin.stop_stdin_commands, ["/stop"])
        self.assertIn("--bind", plugin.argv_prefix)
        self.assertIn("0.0.0.0:{server_port}", plugin.argv_prefix)
        self.assertEqual(plugin.arg_map, {})

    def test_cli_binds_all_interfaces_on_container_port(self) -> None:
        plugin = load_plugin(PLUGIN)
        cfg = SupervisorConfig(
            drop_privileges=False,
            status_http_enabled=False,
            backup_enabled=False,
            ha_notifications=False,
            game_options={
                "server_port": "5520",
                "data_dir": "/data/world",
                "logs_dir": "/data/logs",
                "java_opts": "-Xms2G -Xmx4G",
            },
        )
        cmd = ProcessManager(plugin, cfg).build_command()
        self.assertEqual(cmd[0], "/opt/launch_wrapper.sh")
        self.assertIn("--bind", cmd)
        self.assertEqual(cmd[cmd.index("--bind") + 1], "0.0.0.0:5520")
        self.assertNotIn("-Xmx4G", cmd)

    def test_world_save_is_universe_folder(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "world"
            universe = data_dir / "universe"
            universe.mkdir(parents=True)
            (universe / "players").mkdir()
            (universe / "players" / "one.dat").write_bytes(b"player" * 200)
            active = locate_active_world(
                plugin,
                {"data_dir": str(data_dir)},
                data_dir=str(data_dir),
            )
            self.assertEqual(active.path, str(universe))
            self.assertEqual(active.kind, "directory")
            self.assertEqual(active.label, "universe")

    def test_ha_config_publishes_quic_udp_port(self) -> None:
        import yaml

        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(data["ports"], {"5520/udp": 5520})
        self.assertFalse(data.get("host_network"))
        self.assertEqual(data["timeout"], 300)
        self.assertEqual(data["slug"], "hytale_dedicated_server")
        self.assertEqual(data["arch"], ["amd64"])
        self.assertTrue(str(data["version"]).startswith(SUPERVISOR_VERSION + "."))
        self.assertEqual(data["schema"]["release_channel"], "list(release|pre-release)")
        self.assertEqual(data["schema"]["server_password"], "password")

    def test_launch_wrapper_and_run_sh(self) -> None:
        self.assertTrue(WRAPPER.is_file())
        self.assertTrue(os.access(WRAPPER, os.X_OK), f"{WRAPPER} must be chmod +x")
        self.assertTrue(os.access(RUNSH, os.X_OK), f"{RUNSH} must be chmod +x")
        wrapper = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("haos_defaults.py run", wrapper)
        runsh = RUNSH.read_text(encoding="utf-8")
        self.assertIn("export SERVER_PORT=5520", runsh)
        self.assertIn("ensure-machine-id", runsh)
        self.assertIn("print-name", runsh)
        self.assertIn("do not use host_network", runsh.lower())

    def test_docker_env_keys_include_java_and_port(self) -> None:
        plugin = load_plugin(PLUGIN)
        keys = plugin.docker_env_keys()
        self.assertIn("JAVA_OPTS", keys)
        self.assertIn("SERVER_PORT", keys)
        self.assertIn("RELEASE_CHANNEL", keys)


class HytaleHaosDefaultsTests(unittest.TestCase):
    def test_default_server_name_stable_per_install(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            first = mod.default_server_name(state_dir=state)
            second = mod.default_server_name(state_dir=state)
            self.assertEqual(first, second)
            self.assertRegex(first, r"^HAOS Hytale \d{4}$")
            self.assertTrue((state / "instance_salt").is_file())

    def test_different_installs_get_different_digits(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            name_a = mod.default_server_name(state_dir=a)
            name_b = mod.default_server_name(state_dir=b)
            if name_a == name_b:
                (Path(b) / "instance_salt").unlink()
                name_b = mod.default_server_name(state_dir=b)
            self.assertNotEqual(name_a, name_b)

    def test_resolve_prefers_options_then_env(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = root / "options.json"
            options.write_text('{"server_name": "My Server"}', encoding="utf-8")
            self.assertEqual(
                mod.resolve_server_name(
                    options_file=options,
                    state_dir=root / "state",
                    environ={},
                ),
                "My Server",
            )
            self.assertEqual(
                mod.resolve_server_name(
                    options_file=options,
                    state_dir=root / "state",
                    environ={"SERVER_NAME": "From Env"},
                ),
                "From Env",
            )
            options.write_text('{"server_name": ""}', encoding="utf-8")
            generated = mod.resolve_server_name(
                options_file=options,
                state_dir=root / "state",
                environ={},
            )
            self.assertRegex(generated, r"^HAOS Hytale \d{4}$")

    def test_scrape_official_device_login_lines(self) -> None:
        mod = _load_defaults()
        url, code = mod.scrape_device_login("Visit: https://accounts.hytale.com/device")
        self.assertEqual(url, "https://accounts.hytale.com/device")
        self.assertEqual(code, "")
        url, code = mod.scrape_device_login("Enter code: ABCD-1234")
        self.assertEqual(code, "ABCD-1234")
        url, code = mod.scrape_device_login(
            "Or visit: https://accounts.hytale.com/device?user_code=ABCD-1234"
        )
        self.assertEqual(
            url, "https://accounts.hytale.com/device?user_code=ABCD-1234"
        )
        self.assertEqual(code, "ABCD-1234")
        url, code = mod.scrape_device_login("no login here")
        self.assertEqual((url, code), ("", ""))
        url, code = mod.scrape_device_login("javascript:alert(1)")
        self.assertEqual((url, code), ("", ""))
        url, code = mod.scrape_device_login(
            "[SessionServiceClient] Session Service client initialized for: "
            "https://sessions.hytale.com"
        )
        self.assertEqual((url, code), ("", ""))

    def test_scrape_strips_java_ansi_reset_from_device_code(self) -> None:
        """Java /auth colors the code; ESC[m must not ride into user_code."""

        mod = _load_defaults()
        dirty = (
            "https://oauth.accounts.hytale.com/oauth2/device/verify"
            "?user_code=KuFxp9fw\x1b[m"
        )
        url, code = mod.scrape_device_login(f"Or visit: {dirty}")
        self.assertEqual(code, "KuFxp9fw")
        self.assertEqual(
            url,
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=KuFxp9fw",
        )
        self.assertNotIn("\x1b", url)
        url, code = mod.scrape_device_login("Enter code: \x1b[33mKuFxp9fw\x1b[m")
        self.assertEqual(code, "KuFxp9fw")
        url, code = mod.scrape_device_login("Authorization code: KuFxp9fw\x1b[m")
        self.assertEqual(code, "KuFxp9fw")
        cleaned = mod._url_with_code(dirty, "KuFxp9fw")
        self.assertEqual(
            cleaned,
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=KuFxp9fw",
        )

    def test_coalesce_keeps_complete_url_from_official_downloader_block(self) -> None:
        """Official CLI prints verification_uri_complete, then a bare fallback URL.

        Last-URL-wins used to keep the bare page, so Ingress told people to paste
        the device code even after the complete link already carried it. Mixing
        that device code with Hytale's emailed login OTP then fails as invalid.
        """

        mod = _load_defaults()
        lines = [
            "Please visit the following URL to authenticate:",
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=GLrYHNyp",
            "Or visit the following URL and enter the code:",
            "https://oauth.accounts.hytale.com/oauth2/device/verify",
            "Authorization code: GLrYHNyp",
        ]
        url, code = "", ""
        last_url = ""
        for line in lines:
            new_url, new_code = mod.scrape_device_login(line)
            if new_url:
                last_url = new_url
            url, code = mod.coalesce_device_login(url, code, new_url, new_code)
        complete = (
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=GLrYHNyp"
        )
        self.assertEqual(
            last_url,
            "https://oauth.accounts.hytale.com/oauth2/device/verify",
            "naive last-URL-wins still sees the bare fallback last",
        )
        self.assertEqual(url, complete)
        self.assertEqual(code, "GLrYHNyp")
        self.assertTrue(mod._url_has_user_code(url))

    def test_url_with_code_attaches_when_cli_omits_query(self) -> None:
        mod = _load_defaults()
        attached = mod._url_with_code(
            "https://oauth.accounts.hytale.com/oauth2/device/verify",
            "GLrYHNyp",
        )
        self.assertEqual(
            attached,
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=GLrYHNyp",
        )
        already = (
            "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=GLrYHNyp"
        )
        self.assertEqual(mod._url_with_code(already, "GLrYHNyp"), already)

    def test_signin_detail_separates_device_code_from_email_otp(self) -> None:
        mod = _load_defaults()
        for text in (
            mod.SIGNIN_DETAIL,
            mod.DOWNLOAD_SIGNIN_DETAIL,
            mod.SERVER_SIGNIN_DETAIL,
            mod.TIMEOUT_RETRY_DETAIL,
        ):
            self.assertLessEqual(len(text), 400)
            lowered = text.lower()
            self.assertIn("device", lowered)
            self.assertIn("10 minutes", lowered)
            self.assertNotIn("enter the code", lowered)
        self.assertIn("email", mod.SIGNIN_DETAIL.lower())
        self.assertIn("authorize a device", mod.SIGNIN_DETAIL.lower())

    def test_token_wait_timeout_line_matches_official_downloader(self) -> None:
        mod = _load_defaults()
        line = "2026/08/27 14:16:41 error obtaining token: context deadline exceeded"
        self.assertTrue(mod.token_wait_timed_out_line(line))
        self.assertFalse(mod.token_wait_timed_out_line("Authorization code: GLrYHNyp"))
        self.assertFalse(
            mod.token_wait_timed_out_line("authentication successful! Mode: OAUTH_DEVICE")
        )

    def test_signin_finished_line_matches_downloader_and_java(self) -> None:
        mod = _load_defaults()
        self.assertTrue(
            mod.signin_finished_line(
                'downloading latest ("release" patchline) to "game.zip"'
            )
        )
        self.assertTrue(
            mod.signin_finished_line(
                'successfully downloaded "release" patchline (version 2026.01.17-4b0f30090)'
            )
        )
        self.assertTrue(
            mod.signin_finished_line("Authentication successful! Mode: OAUTH_DEVICE")
        )
        self.assertTrue(
            mod.signin_finished_line("Authentication successful! Mode: OAUTH_STORE")
        )
        self.assertTrue(
            mod.signin_finished_line("Session restored from stored credentials")
        )
        self.assertFalse(
            mod.signin_finished_line("Loaded encrypted credentials from auth.enc")
        )
        self.assertFalse(
            mod.signin_finished_line(
                "[ServerAuthManager] No server tokens configured. "
                "Use /auth login to authenticate, or provide tokens via CLI/environment."
            )
        )
        self.assertFalse(mod.signin_finished_line("Authorization code: GLrYHNyp"))
        self.assertFalse(
            mod.signin_finished_line(
                "2026/08/27 14:16:41 error obtaining token: context deadline exceeded"
            )
        )

    def test_signin_log_lines_put_url_on_its_own_line(self) -> None:
        mod = _load_defaults()
        url = "https://oauth.accounts.hytale.com/oauth2/device/verify?user_code=GLrYHNyp"
        lines = mod.signin_log_lines(url, "GLrYHNyp")
        self.assertIn("Sign-in from HA Logs: open this URL in a browser", lines)
        self.assertIn(url, lines)
        self.assertTrue(any("GLrYHNyp" in line for line in lines))
        self.assertTrue(any("email" in line.lower() for line in lines))

    def test_merge_server_config_keeps_unknown_keys(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "ServerName": "old",
                        "CustomModKey": 3,
                        "Defaults": {"World": "oldworld", "Keep": True},
                        "Update": {"Enabled": True, "Channel": "release"},
                    }
                ),
                encoding="utf-8",
            )
            mod.merge_server_config(
                path,
                server_name="HAOS Hytale 0001",
                motd="hi",
                password="secret",
                max_players=12,
                world_name="default",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["ServerName"], "HAOS Hytale 0001")
            self.assertEqual(data["MOTD"], "hi")
            self.assertEqual(data["Password"], "secret")
            self.assertEqual(data["MaxPlayers"], 12)
            self.assertEqual(data["CustomModKey"], 3)
            self.assertEqual(data["Defaults"]["World"], "default")
            self.assertTrue(data["Defaults"]["Keep"])
            self.assertFalse(data["Update"]["Enabled"])
            self.assertEqual(data["Update"]["Channel"], "release")

    def test_write_and_clear_operator_action(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            mod.write_operator_action(
                title="Sign in required",
                url="https://accounts.hytale.com/device?user_code=WXYZ-9999",
                code="WXYZ-9999",
                detail="Open a new tab",
                steps=[{"label": "Download files", "state": "active"}],
                state_dir=state,
            )
            payload = json.loads((state / "operator_action.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["code"], "WXYZ-9999")
            self.assertEqual(payload["url"].startswith("https://"), True)
            mod.clear_operator_action(state_dir=state)
            self.assertFalse((state / "operator_action.json").exists())

    def test_channel_and_patchline(self) -> None:
        mod = _load_defaults()
        self.assertEqual(mod._channel({"RELEASE_CHANNEL": "pre-release"}), "pre-release")
        self.assertEqual(mod._channel({"RELEASE_CHANNEL": "preview"}), "pre-release")
        self.assertEqual(mod._channel({}), "release")
        self.assertEqual(mod._patchline_args("pre-release"), ["-patchline", "pre-release"])
        self.assertEqual(mod._patchline_args("release"), [])

    def test_extract_zip_strips_single_wrapper_dir(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "game.zip"
            dest = root / "install"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("wrapper/Assets.zip", b"assets")
                zf.writestr("wrapper/Server/HytaleServer.jar", b"jar")
            mod._extract_zip(archive, dest)
            self.assertTrue((dest / "Assets.zip").is_file())
            self.assertTrue((dest / "Server" / "HytaleServer.jar").is_file())

    def test_build_java_command_uses_assets_and_optional_aot(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "game"
            server = install / "Server"
            server.mkdir(parents=True)
            (server / "HytaleServer.jar").write_bytes(b"jar")
            (install / "Assets.zip").write_bytes(b"assets")
            (server / "HytaleServer.aot").write_bytes(b"aot")
            env_old = os.environ.get("INSTALL_DIR")
            opts_old = os.environ.get("JAVA_OPTS")
            try:
                os.environ["INSTALL_DIR"] = str(install)
                os.environ["JAVA_OPTS"] = "-Xms2G -Xmx4G"
                cmd = mod.build_java_command(["--bind", "0.0.0.0:5520"])
            finally:
                if env_old is None:
                    os.environ.pop("INSTALL_DIR", None)
                else:
                    os.environ["INSTALL_DIR"] = env_old
                if opts_old is None:
                    os.environ.pop("JAVA_OPTS", None)
                else:
                    os.environ["JAVA_OPTS"] = opts_old
            self.assertEqual(cmd[0], "java")
            self.assertIn("-Xms2G", cmd)
            self.assertIn("-Xmx4G", cmd)
            self.assertTrue(any(part.startswith("-XX:AOTCache=") for part in cmd))
            self.assertIn("-jar", cmd)
            self.assertIn("--assets", cmd)
            self.assertEqual(cmd[cmd.index("--bind") + 1], "0.0.0.0:5520")

    def test_needs_auth_and_persisted_lines(self) -> None:
        mod = _load_defaults()
        # Boot always prints this before Encrypted restore — not a real need-auth.
        self.assertFalse(
            mod.needs_server_auth_line(
                "[ServerAuthManager] No server tokens configured. "
                "Use /auth login to authenticate, or provide tokens via CLI/environment."
            )
        )
        self.assertTrue(
            mod.needs_server_auth_line(
                "Server session token not available, cannot request auth grant"
            )
        )
        self.assertFalse(
            mod.needs_server_auth_line(
                "Authentication successful! Mode: OAUTH_DEVICE"
            )
        )
        self.assertTrue(
            mod.credentials_persisted_line(
                "Loaded encrypted credentials from auth.enc"
            )
        )
        self.assertTrue(
            mod.credentials_persisted_line(
                "Credential storage changed to: Encrypted"
            )
        )
        self.assertFalse(mod.credentials_persisted_line("No server tokens configured"))
        self.assertTrue(
            mod.auth_store_ready_line(
                "[ServerAuthManager] Auth credential store: Encrypted"
            )
        )
        self.assertFalse(
            mod.auth_store_ready_line(
                "[EncryptedAuthCredentialStore] Loaded encrypted credentials from auth.enc"
            )
        )
        self.assertTrue(
            mod.session_ok_line(
                "[ServerAuthManager] Found stored credentials, attempting to restore session..."
            )
        )
        self.assertTrue(
            mod.session_ok_line(
                "[ServerAuthManager] Authentication successful! Mode: OAUTH_STORE"
            )
        )
        self.assertTrue(
            mod.session_ok_line(
                "[ServerAuthManager] Session restored from stored credentials"
            )
        )
        self.assertFalse(
            mod.session_ok_line("Loaded encrypted credentials from auth.enc")
        )
        self.assertTrue(
            mod.console_ready_line(
                "[ServerAuthManager] No server tokens configured. "
                "Use /auth login to authenticate, or provide tokens via CLI/environment."
            )
        )
        self.assertTrue(
            mod.console_ready_line(
                "[ConsoleModule|P] Setup console with type: dumb-color"
            )
        )

    def test_live_boot_lines_drive_card_not_auth_enc(self) -> None:
        """Returning-server boot: no inject on missing-tokens; success clears."""

        mod = _load_defaults()
        boot = [
            "[ServerAuthManager] No server tokens configured. "
            "Use /auth login to authenticate, or provide tokens via CLI/environment.",
            "[EncryptedAuthCredentialStore] Loaded encrypted credentials from auth.enc",
            "[ServerAuthManager] Auth credential store: Encrypted",
            "[ServerAuthManager] Found stored credentials, attempting to restore session...",
            "[ServerAuthManager] Authentication successful! Mode: OAUTH_STORE",
            "[ServerAuthManager] Session restored from stored credentials",
        ]
        self.assertTrue(mod.console_ready_line(boot[0]))
        self.assertFalse(mod.needs_server_auth_line(boot[0]))
        self.assertFalse(mod.signin_finished_line(boot[0]))
        self.assertTrue(mod.credentials_persisted_line(boot[1]))
        self.assertFalse(mod.signin_finished_line(boot[1]))
        self.assertTrue(mod.auth_store_ready_line(boot[2]))
        self.assertFalse(mod.signin_finished_line(boot[2]))
        self.assertTrue(mod.session_ok_line(boot[3]))
        self.assertTrue(mod.signin_finished_line(boot[4]))
        self.assertTrue(mod.signin_finished_line(boot[5]))
        url, code = mod.scrape_device_login(
            "Session Service client initialized for: https://sessions.hytale.com"
        )
        self.assertEqual((url, code), ("", ""))
        url, code = mod.scrape_device_login(
            "Visit: https://oauth.accounts.hytale.com/oauth2/device/verify"
        )
        self.assertEqual(
            url, "https://oauth.accounts.hytale.com/oauth2/device/verify"
        )

    def test_ensure_machine_id_persists_and_copies(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            etc = root / "etc" / "machine-id"
            dbus = root / "dbus" / "machine-id"
            first = mod.ensure_machine_id(
                state_dir=state, etc_path=etc, dbus_path=dbus
            )
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertEqual((state / "machine-id").read_text(encoding="utf-8").strip(), first)
            self.assertEqual(etc.read_text(encoding="utf-8").strip(), first)
            self.assertEqual(dbus.read_text(encoding="utf-8").strip(), first)
            etc.chmod(0o644)
            etc.write_text("ffffffffffffffffffffffffffffffff\n", encoding="utf-8")
            second = mod.ensure_machine_id(
                state_dir=state, etc_path=etc, dbus_path=dbus
            )
            self.assertEqual(second, first)
            self.assertEqual(etc.read_text(encoding="utf-8").strip(), first)

    def test_ensure_machine_id_overwrites_writable_image_id(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            etc = root / "etc" / "machine-id"
            etc.parent.mkdir()
            baked = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            etc.write_text(baked + "\n", encoding="utf-8")
            got = mod.ensure_machine_id(
                state_dir=root / "state", etc_path=etc, dbus_path=root / "dbus" / "machine-id"
            )
            self.assertNotEqual(got, baked)
            self.assertEqual(etc.read_text(encoding="utf-8").strip(), got)


# Live boot + join/leave from HA Logs (2026-08-27). ready is ServerManager
# Listening (port bind), not "Universe ready!". Join/leave share TheFrizz.
# WorldGenerator 0.0.0 is not the game version.
_HYTALE_BOOT_JOIN_LOG = """\
[2026/08/27 23:30:28   INFO]      [ServerAuthManager] No server tokens configured. Use /auth login to authenticate, or provide tokens via CLI/environment.
[2026/08/27 23:30:30   INFO]           [HytaleServer] Booting up HytaleServer - Version: 0.6.1, Revision: 5097cd9e1099a0af639b359b453e4b117fe9f2a0
[2026/08/27 23:30:42   INFO]               [ServerManager|P] Listening on /[0:0:0:0:0:0:0:0]:5520 and took 6ms 396us 931ns
[2026/08/27 23:30:42   INFO]               [ServerManager|P] Listening on /0.0.0.0:5520 and took 1ms 465us 433ns
[2026/08/27 23:30:42   INFO]               [ServerManager|P] Listening on /[0:0:0:0:0:0:0:1]:5520 and took 3ms 67us 228ns
[2026/08/27 23:30:42   INFO]                [WorldGenerator] - [  0] Hytale:Hytale:0.0.0 - [/data/game/Assets.zip]
[2026/08/27 23:30:42   INFO]                  [HytaleServer] Universe ready!
[2026/08/27 23:31:07   INFO]                        [Hytale] Starting authenticated flow from QuicConnectionAddress{connId=2e049ca4ab53ae7349ae988b25beb4c2eaeb2bc8} (/192.168.11.106:44804, streamId=0)
[2026/08/27 23:31:08   INFO]              [HandshakeHandler] Mutual authentication complete for TheFrizz (14a2663f-4c89-4da6-9440-2d9d66b92c30) from QuicConnectionAddress{connId=2e049ca4ab53ae7349ae988b25beb4c2eaeb2bc8} (/192.168.11.106:44804, streamId=0)
[2026/08/27 23:31:13   INFO] [World|default] Player 'TheFrizz' joined world 'default' at location ( 2.795E+2  1.260E+2 -5.350E+1) (14a2663f-4c89-4da6-9440-2d9d66b92c30)
[2026/08/27 23:32:08   INFO]          [Hytale] 14a2663f-4c89-4da6-9440-2d9d66b92c30 - TheFrizz at QuicConnectionAddress{connId=2e049ca4ab53ae7349ae988b25beb4c2eaeb2bc8} (/192.168.11.106:44804, streamId=0) left with reason: Disconnect - PlayerLeave
[2026/08/27 23:32:08   INFO]    [Objectives|P] Checking objectives for disconnecting player 'TheFrizz' (14a2663f-4c89-4da6-9440-2d9d66b92c30)
"""


class HytaleLogPatternTests(unittest.TestCase):
    def test_ready_is_listening_not_universe_ready(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            mon.ingest_stdout_line(
                "[HytaleServer] Booting up HytaleServer - Version: 0.6.1, Revision: abc"
            )
            self.assertFalse(mon.state.ready)
            mon.ingest_stdout_line("[HytaleServer] Universe ready!")
            self.assertFalse(mon.state.ready)
            mon.ingest_stdout_line(
                "[ServerManager|P] Listening on /0.0.0.0:5520 and took 1ms"
            )
            self.assertTrue(mon.state.ready)

    def test_game_version_ignores_worldgen_zero(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            mon.ingest_stdout_line(
                "[WorldGenerator] - [  0] Hytale:Hytale:0.0.0 - [/data/game/Assets.zip]"
            )
            self.assertIsNone(mon.state.game_version)
            mon.ingest_stdout_line(
                "[HytaleServer] Booting up HytaleServer - Version: 0.6.1, "
                "Revision: 5097cd9e1099a0af639b359b453e4b117fe9f2a0"
            )
            self.assertEqual(mon.state.game_version, "0.6.1")

    def test_in_world_join_and_leave_share_display_name(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            mon.ingest_stdout_line(
                "[Hytale] Starting authenticated flow from QuicConnectionAddress"
                "{connId=abc} (/192.168.11.106:44804, streamId=0)"
            )
            mon.ingest_stdout_line(
                "[HandshakeHandler] Mutual authentication complete for TheFrizz "
                "(14a2663f-4c89-4da6-9440-2d9d66b92c30)"
            )
            self.assertEqual(mon.state.players, set())
            mon.ingest_stdout_line(
                "[World|default] Player 'TheFrizz' joined world 'default' at "
                "location ( 2.795E+2  1.260E+2 -5.350E+1) "
                "(14a2663f-4c89-4da6-9440-2d9d66b92c30)"
            )
            self.assertEqual(mon.state.players, {"TheFrizz"})
            mon.ingest_stdout_line(
                "[Hytale] 14a2663f-4c89-4da6-9440-2d9d66b92c30 - TheFrizz at "
                "QuicConnectionAddress{connId=abc} (/192.168.11.106:44804, "
                "streamId=0) left with reason: Disconnect - PlayerLeave"
            )
            self.assertEqual(mon.state.players, set())
            self.assertEqual(mon.state.player_count, 0)

    def test_live_boot_log_does_not_treat_handshake_as_join(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            for line in _HYTALE_BOOT_JOIN_LOG.splitlines():
                mon.ingest_stdout_line(line)
            self.assertTrue(mon.state.ready)
            self.assertEqual(mon.state.game_version, "0.6.1")
            self.assertEqual(mon.state.players, set())
            self.assertTrue(mon.state.players_known)
            self.assertEqual(mon.state.version_mismatch_count, 0)

    def test_suggest_boot_join_examples_are_configured(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            state = Path(tmp) / "state"
            logs.mkdir()
            state.mkdir()
            (logs / "server.log").write_text(_HYTALE_BOOT_JOIN_LOG, encoding="utf-8")
            box = LogToolbox(plugin, logs, state, recent_lines_provider=lambda: [])
            report = box.suggest(lines=200)
            ready = (report.get("configured") or {}).get("ready")
            self.assertTrue(ready)
            self.assertTrue(
                any("Listening on /" in line for line in ready.get("examples") or [])
            )
            join = (report.get("configured") or {}).get("player_join")
            self.assertTrue(join)
            join_text = "\n".join(join.get("examples") or [])
            self.assertIn("Player 'TheFrizz' joined world", join_text)
            self.assertNotIn("Starting authenticated flow", join_text)
            leave = (report.get("configured") or {}).get("player_leave")
            self.assertTrue(leave)
            leave_text = "\n".join(leave.get("examples") or [])
            self.assertIn("left with reason", leave_text)
            self.assertNotIn("Checking objectives", leave_text)
            version = (report.get("configured") or {}).get("game_version")
            self.assertTrue(version)
            version_text = "\n".join(version.get("examples") or [])
            self.assertIn("Version: 0.6.1", version_text)
            self.assertNotIn("0.0.0", version_text)


class HytaleIdentityIsolationTests(unittest.TestCase):
    def test_game_server_base_has_no_hytale_nouns(self) -> None:
        hits: list[str] = []
        for path in BASE.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "hytale" in text.lower():
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"Hytale leaked into game-server-base: {hits}")


if __name__ == "__main__":
    unittest.main()
