#!/usr/bin/env python3
"""Core Keeper game-layer checks (plugin + stable Game ID)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "core-keeper-dedicated-server" / "games" / "game.yaml"
DEFAULTS = ROOT / "core-keeper-dedicated-server" / "haos_defaults.py"
CONFIG = ROOT / "core-keeper-dedicated-server" / "config.yaml"
WRAPPER = ROOT / "core-keeper-dedicated-server" / "launch_wrapper.sh"
RUNSH = ROOT / "core-keeper-dedicated-server" / "run.sh"

sys.path.insert(0, str(ROOT / "game-server-base"))
from game_server.plugin import load_plugin  # noqa: E402
from game_server.process_manager import ProcessManager  # noqa: E402
from game_server.config import SupervisorConfig  # noqa: E402
from game_server.world_save import locate_active_world  # noqa: E402
from game_server.log_tools import LogToolbox  # noqa: E402
from game_server.monitor import LogMonitor  # noqa: E402


def _load_defaults():
    spec = importlib.util.spec_from_file_location("ck_haos_defaults", DEFAULTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoreKeeperPluginTests(unittest.TestCase):
    def test_plugin_is_steamcmd_core_keeper(self) -> None:
        plugin = load_plugin(PLUGIN)
        self.assertEqual(plugin.name, "Core Keeper")
        self.assertEqual(plugin.steam_app_id, 1963720)
        self.assertEqual(plugin.install_marker, "CoreKeeperServer_Data")
        self.assertEqual(plugin.executable, ["/opt/launch_wrapper.sh"])
        self.assertEqual(plugin.log_patterns.ready, ["Listening on ip:"])
        self.assertEqual(
            plugin.log_patterns.player_join,
            [r"\[userid:(?P<player>\d+)\] player \S+ connected"],
        )
        self.assertEqual(
            plugin.log_patterns.player_leave,
            [r"Disconnected from userid:(?P<player>\d+)"],
        )
        self.assertEqual(plugin.log_patterns.player_count, [])
        self.assertEqual(plugin.log_patterns.players_empty, [])
        self.assertEqual(
            plugin.log_patterns.version_mismatch,
            ["RpcSystem received bad protocol version"],
        )
        self.assertEqual(
            plugin.log_patterns.game_version,
            [r"Game version:\s+(?P<version>\S+)"],
        )
        self.assertIn("ready", plugin.log_pattern_candidates)
        self.assertIn("server_port", plugin.arg_map)
        self.assertEqual(plugin.arg_map["server_port"], "-port")
        self.assertEqual(plugin.arg_map["server_password"], "-password")
        self.assertIn("-datapath", plugin.argv_prefix)
        self.assertIn("{data_dir}", plugin.argv_prefix)
        self.assertEqual(plugin.env_options, ["ADMIN_STEAM_IDS"])

    def test_world_save_slot_zero(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "world"
            worlds = data_dir / "worlds"
            worlds.mkdir(parents=True)
            save = worlds / "0.world.gzip"
            save.write_bytes(b"fake-world" * 200)
            other = worlds / "3.world.gzip"
            other.write_bytes(b"other-world" * 200)
            active = locate_active_world(
                plugin,
                {"world_index": 0, "data_dir": str(data_dir)},
                data_dir=str(data_dir),
            )
            self.assertEqual(active.path, str(save))
            self.assertEqual(active.kind, "file")
            self.assertEqual(active.label, "0.world.gzip")
            slotted = locate_active_world(
                plugin,
                {"world_index": 3, "data_dir": str(data_dir)},
                data_dir=str(data_dir),
            )
            self.assertEqual(slotted.path, str(other))
            self.assertEqual(slotted.label, "3.world.gzip")

    def test_cli_keeps_numeric_zero_from_env_strings(self) -> None:
        plugin = load_plugin(PLUGIN)
        cfg = SupervisorConfig(
            drop_privileges=False,
            status_http_enabled=False,
            backup_enabled=False,
            ha_notifications=False,
            game_options={
                "world_name": "World",
                "world_index": "0",
                "world_mode": "0",
                "world_seed": "",
                "game_id": "abcdeFGHIJ12345KLMN",
                "server_password": "JoinPass123",
                "server_port": "7778",
                "server_slots": "8",
                "admin_steam_ids": "76561198012345678",
                "data_dir": "/data/world",
                "logs_dir": "/data/logs",
            },
        )
        cmd = ProcessManager(plugin, cfg).build_command()
        self.assertEqual(cmd[0], "/opt/launch_wrapper.sh")
        self.assertIn("-world", cmd)
        self.assertEqual(cmd[cmd.index("-world") + 1], "0")
        self.assertEqual(cmd[cmd.index("-worldmode") + 1], "0")
        self.assertEqual(cmd[cmd.index("-gameid") + 1], "abcdeFGHIJ12345KLMN")
        self.assertEqual(cmd[cmd.index("-password") + 1], "JoinPass123")
        self.assertEqual(cmd[cmd.index("-port") + 1], "7778")
        self.assertEqual(cmd[cmd.index("-maxplayers") + 1], "8")
        self.assertNotIn("76561198012345678", cmd)
        self.assertNotIn("-admin", cmd)

    def test_ha_config_publishes_direct_connect_port(self) -> None:
        import yaml

        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(data["ports"], {"7778/udp": 7778})
        self.assertFalse(data.get("host_network"))
        self.assertEqual(data["schema"]["world_index"], "int(0,29)")
        self.assertEqual(data["schema"]["server_password"], "password")
        self.assertEqual(data["schema"]["admin_steam_ids"], "str?")
        self.assertEqual(data["options"]["admin_steam_ids"], "")
        self.assertEqual(data["slug"], "core_keeper_dedicated_server")
        self.assertEqual(data["arch"], ["amd64"])

    def test_launch_wrapper_is_executable(self) -> None:
        self.assertTrue(WRAPPER.is_file())
        self.assertTrue(os.access(WRAPPER, os.X_OK), f"{WRAPPER} must be chmod +x")
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("GameInfo.txt", text)
        self.assertIn("Xvfb", text)
        self.assertIn("steamclient.so", text)
        self.assertNotIn("no IP:port", text)
        self.assertIn("both work at once", text)

    def test_run_sh_merges_admins_and_describes_mixed_join(self) -> None:
        text = RUNSH.read_text(encoding="utf-8")
        self.assertIn("haos_defaults.py ensure-admins", text)
        self.assertIn("Both are on", text)
        self.assertIn("Game ID joiners need no forward", text)


class CoreKeeperHaosDefaultsTests(unittest.TestCase):
    def test_default_game_id_stable_per_install(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            first = mod.default_game_id(state_dir=state)
            second = mod.default_game_id(state_dir=state)
            self.assertEqual(first, second)
            self.assertEqual(len(first), 20)
            self.assertTrue(set(first) <= set(mod._GAME_ID_ALPHABET))
            # Official ARGUMENTS.txt: Game ID may not include Y, y, x, 0, or O.
            self.assertTrue(set(mod._GAME_ID_ALPHABET).isdisjoint(set("Yyx0O")))
            self.assertTrue((state / "instance_salt").is_file())

    def test_different_installs_get_different_ids(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            id_a = mod.default_game_id(state_dir=a)
            id_b = mod.default_game_id(state_dir=b)
            if id_a == id_b:
                (Path(b) / "instance_salt").unlink()
                id_b = mod.default_game_id(state_dir=b)
            self.assertNotEqual(id_a, id_b)

    def test_resolve_prefers_options_then_env(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = root / "options.json"
            options.write_text('{"game_id": "PinnedJoinCode12345"}', encoding="utf-8")
            self.assertEqual(
                mod.resolve_game_id(
                    options_file=options,
                    state_dir=root / "state",
                    install_dir=root / "game",
                    data_dir=root / "world",
                    environ={},
                ),
                "PinnedJoinCode12345",
            )
            self.assertEqual(
                mod.resolve_game_id(
                    options_file=options,
                    state_dir=root / "state",
                    install_dir=root / "game",
                    data_dir=root / "world",
                    environ={"GAME_ID": "FromEnvJoinCode1234"},
                ),
                "FromEnvJoinCode1234",
            )
            options.write_text('{"game_id": ""}', encoding="utf-8")
            generated = mod.resolve_game_id(
                options_file=options,
                state_dir=root / "state",
                install_dir=root / "game",
                data_dir=root / "world",
                environ={},
            )
            self.assertEqual(len(generated), 20)

    def test_invalid_pinned_id_does_not_pass_through(self) -> None:
        mod = _load_defaults()
        self.assertTrue(mod.is_valid_game_id("abcdeFGHIJ12345KLMN"))
        self.assertFalse(mod.is_valid_game_id("short"))
        self.assertFalse(mod.is_valid_game_id("YYYYYYYYYYYYYYY"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = root / "options.json"
            options.write_text('{"game_id": "not-valid"}', encoding="utf-8")
            game = root / "game"
            game.mkdir()
            (game / "GameID.txt").write_text("PersistedJoinCode123", encoding="utf-8")
            resolved = mod.resolve_game_id(
                options_file=options,
                state_dir=root / "state",
                install_dir=game,
                data_dir=root / "world",
                environ={},
            )
            self.assertEqual(resolved, "PersistedJoinCode123")

    def test_recovers_id_from_server_config_if_salt_missing(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            (world / "ServerConfig.json").write_text(
                '{"gameId": "FromConfigJoinCode1"}',
                encoding="utf-8",
            )
            resolved = mod.resolve_game_id(
                options_file=root / "missing.json",
                state_dir=root / "state",
                install_dir=root / "game",
                data_dir=world,
                environ={},
            )
            self.assertEqual(resolved, "FromConfigJoinCode1")

    def test_default_password_stable_and_distinct_from_game_id(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            first = mod.default_server_password(state_dir=state)
            second = mod.default_server_password(state_dir=state)
            game_id = mod.default_game_id(state_dir=state)
            self.assertEqual(first, second)
            self.assertEqual(len(first), 16)
            self.assertNotEqual(first, game_id)
            self.assertTrue(mod.is_valid_server_password(first))
            self.assertFalse(mod.is_valid_server_password(""))
            self.assertFalse(mod.is_valid_server_password("x" * 29))

    def test_resolve_password_prefers_options_then_env(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = root / "options.json"
            options.write_text(
                '{"server_password": "PinnedJoinPassword1"}', encoding="utf-8"
            )
            self.assertEqual(
                mod.resolve_server_password(
                    options_file=options,
                    state_dir=root / "state",
                    environ={},
                ),
                "PinnedJoinPassword1",
            )
            self.assertEqual(
                mod.resolve_server_password(
                    options_file=options,
                    state_dir=root / "state",
                    environ={"SERVER_PASSWORD": "FromEnvPassword12"},
                ),
                "FromEnvPassword12",
            )
            options.write_text('{"server_password": ""}', encoding="utf-8")
            generated = mod.resolve_server_password(
                options_file=options,
                state_dir=root / "state",
                environ={},
            )
            self.assertEqual(len(generated), 16)


class CoreKeeperAdminTests(unittest.TestCase):
    def test_parse_steam_ids_skips_invalid(self) -> None:
        mod = _load_defaults()
        self.assertTrue(mod.is_valid_steam64("76561197968471340"))
        self.assertTrue(mod.is_valid_steam64(str(mod._STEAM64_INDIVIDUAL_BASE)))
        self.assertTrue(mod.is_valid_steam64(str(mod._STEAM64_INDIVIDUAL_MAX)))
        self.assertTrue(mod.is_valid_steam64("76561200000000000"))  # past 7656119 prefix
        self.assertFalse(mod.is_valid_steam64(str(mod._STEAM64_INDIVIDUAL_BASE - 1)))
        self.assertFalse(mod.is_valid_steam64(str(mod._STEAM64_INDIVIDUAL_MAX + 1)))
        self.assertFalse(mod.is_valid_steam64("76561190000000000"))  # 17 digits, below base
        self.assertFalse(mod.is_valid_steam64("7656119"))
        self.assertFalse(mod.is_valid_steam64("steam:123"))
        self.assertFalse(mod.is_valid_steam64("STEAM_0:1:12345"))
        self.assertFalse(mod.is_valid_steam64("[U:1:12345]"))
        self.assertEqual(
            mod.parse_admin_steam_ids(
                "76561198012345678, 76561198087654321;76561198012345678"
            ),
            [76561198012345678, 76561198087654321],
        )
        self.assertEqual(mod.parse_admin_steam_ids("not-a-steam-id, 123"), [])
        self.assertEqual(mod.parse_admin_steam_ids(""), [])

    def test_blank_option_does_not_write_admins_file(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            options = root / "options.json"
            options.write_text('{"admin_steam_ids": ""}', encoding="utf-8")
            written = mod.ensure_admins(
                options_file=options,
                data_dir=world,
                environ={},
            )
            self.assertIsNone(written)
            self.assertFalse((world / "Admins.json").exists())

    def test_blank_option_does_not_wipe_existing_admins(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            existing = {
                "adminList": [
                    {
                        "index": 1,
                        "privileges": 2,
                        "name": "FirstJoiner",
                        "steamId": 76561198011111111,
                    }
                ]
            }
            admins = world / "Admins.json"
            admins.write_text(json.dumps(existing), encoding="utf-8")
            mod.ensure_admins(
                options_file=root / "missing.json",
                data_dir=world,
                environ={},
            )
            data = json.loads(admins.read_text(encoding="utf-8"))
            self.assertEqual(data["adminList"][0]["steamId"], 76561198011111111)
            self.assertEqual(len(data["adminList"]), 1)

    def test_merge_adds_without_deleting_in_game_admin(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            admins = world / "Admins.json"
            admins.write_text(
                json.dumps(
                    {
                        "adminList": [
                            {
                                "index": 1,
                                "privileges": 1,
                                "name": "GuestStar",
                                "steamId": 76561198011111111,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            options = root / "options.json"
            options.write_text(
                '{"admin_steam_ids": "76561198022222222"}', encoding="utf-8"
            )
            written = mod.ensure_admins(
                options_file=options,
                data_dir=world,
                environ={},
            )
            self.assertEqual(written, admins)
            data = json.loads(admins.read_text(encoding="utf-8"))
            by_id = {int(e["steamId"]): e for e in data["adminList"]}
            self.assertEqual(set(by_id), {76561198011111111, 76561198022222222})
            self.assertEqual(by_id[76561198011111111]["name"], "GuestStar")
            self.assertEqual(by_id[76561198011111111]["privileges"], 1)
            self.assertEqual(by_id[76561198022222222]["privileges"], 2)
            self.assertEqual(by_id[76561198022222222]["index"], 2)

    def test_existing_pinned_id_is_promoted_to_full_admin(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            admins = world / "Admins.json"
            admins.write_text(
                json.dumps(
                    {
                        "adminList": [
                            {
                                "index": 3,
                                "privileges": 1,
                                "name": "Household",
                                "steamId": "76561198033333333",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            written = mod.ensure_admins(
                options_file=root / "missing.json",
                data_dir=world,
                environ={"ADMIN_STEAM_IDS": "76561198033333333"},
            )
            self.assertEqual(written, admins)
            data = json.loads(admins.read_text(encoding="utf-8"))
            self.assertEqual(len(data["adminList"]), 1)
            self.assertEqual(data["adminList"][0]["privileges"], 2)
            self.assertEqual(data["adminList"][0]["steamId"], 76561198033333333)
            self.assertEqual(data["adminList"][0]["name"], "Household")

    def test_resolve_admins_prefers_env_over_options(self) -> None:
        mod = _load_defaults()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = root / "options.json"
            options.write_text(
                '{"admin_steam_ids": "76561198044444444"}', encoding="utf-8"
            )
            self.assertEqual(
                mod.resolve_admin_steam_ids(options_file=options, environ={}),
                [76561198044444444],
            )
            self.assertEqual(
                mod.resolve_admin_steam_ids(
                    options_file=options,
                    environ={"ADMIN_STEAM_IDS": "76561198055555555"},
                ),
                [76561198055555555],
            )


# Live client-too-old session captured from HA Logs (prefix stripped).
# Unity then dumps RPC/component hashes; those headers also mention
# "bad protocol version" but must not re-fire the active mismatch pattern.
_CK_PROTOCOL_MISMATCH_LOG = """\
Accepted connection from 76561197968471340 with result OK awaiting authentication
Connected to userid:3784111641
Authentication message was wrong length: 23
Successful authentication from userid: 76561197968471340
timescale = 1
[ServerWorld] RpcSystem received bad protocol version from NetworkConnection[id0,v2]
Local protocol: NetCode=1 Game=8 RpcCollection=1468618670998561951 ComponentCollection=0
Remote protocol: NetCode=1 Game=6 RpcCollection=14788448070397539380 ComponentCollection=0
RPC List (for above 'bad protocol version' error): 37
RpcHash[0] = null
Component serializer data (for above 'bad protocol version' error): 257
ComponentHash[0] = Type: null GhostFieldHash: 0 SnapshotSize: 0 ChangeMaskBits: 0 SendToOwner: 3
Disconnected from userid:3784111641 with reason App_Min
"""


class CoreKeeperLogPatternTests(unittest.TestCase):
    def test_protocol_mismatch_fires_once_dump_headers_do_not(self) -> None:
        plugin = load_plugin(PLUGIN)
        triggered: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp, on_version_mismatch=triggered.append)
            for line in _CK_PROTOCOL_MISMATCH_LOG.splitlines():
                mon.ingest_stdout_line(line)
            self.assertEqual(mon.state.version_mismatch_count, 1)
            self.assertEqual(len(triggered), 1)
            self.assertIn("RpcSystem received bad protocol version", triggered[0])
            self.assertNotIn("App_Min", triggered[0])
            self.assertIsNone(mon.state.game_version)
            self.assertEqual(mon.state.players, set())
            # Leave without a matching in-world join is an unknown identity;
            # presence mode records that a leave was seen.
            self.assertTrue(mon.state.players_known)

    def test_suggest_returns_join_leave_examples_not_app_min_as_mismatch(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            state = Path(tmp) / "state"
            logs.mkdir()
            state.mkdir()
            (logs / "server.log").write_text(
                _CK_PROTOCOL_MISMATCH_LOG, encoding="utf-8"
            )
            box = LogToolbox(plugin, logs, state, recent_lines_provider=lambda: [])
            report = box.suggest(lines=200)
            mismatch = (report.get("configured") or {}).get("version_mismatch")
            self.assertTrue(mismatch)
            self.assertEqual(
                mismatch["patterns"],
                ["RpcSystem received bad protocol version"],
            )
            mismatch_text = "\n".join(mismatch.get("examples") or [])
            self.assertIn("RpcSystem received bad protocol version", mismatch_text)
            self.assertNotIn("App_Min", mismatch_text)
            self.assertNotIn("RPC List", mismatch_text)
            join = (report.get("configured") or {}).get("player_join")
            self.assertTrue(join)
            self.assertEqual(join["hits"], 0)
            leave = (report.get("configured") or {}).get("player_leave")
            self.assertTrue(leave)
            leave_text = "\n".join(leave.get("examples") or [])
            self.assertIn("Disconnected from userid:", leave_text)
            self.assertIn("App_Min", leave_text)


# Live debug-mode boot + join (HA Logs prefix stripped; GameInfo secrets redacted).
# Listening is when the UDP port is bound. Started session is later public-IP
# print. App_Min here is a normal leave, not a version mismatch.
_CK_BOOT_AND_JOIN_LOG = """\
Listening on ip:0.0.0.0:7778
ConvertAuthoringDataRoutine complete. Process was successful.
Decompressed brotli compressed world file: 406599 bytes -> 5907452 bytes
Deserialize world finishied
successfully loaded world file into SerializeWorld
found 250 chunks in serialized world
SteamNet Bug: [#1 UDP steamid:76561197968471340@192.168.1.10:39261] We are in state 4 and have been waiting 20.0s to be cleaned up.  Did you forget to call CloseConnection()?
Accepted connection from 76561197968471340 with result InvalidState awaiting authentication
Disconnected from userid:1707773013 with reason Misc_Timeout
Accepted connection from 76561197968471340 with result OK awaiting authentication
Connected to userid:373251987
failed get internal IP
Started session with info: 203.0.113.1;7778;REDACTED;REDACTED
Accepted connection from 76561197968471340 with result OK awaiting authentication
Connected to userid:2158889800
Authentication message was wrong length: 23
Successful authentication from userid: 76561197968471340
timescale = 1
Accepted connect RPC
spawning player with character data size 20842
Enabling player entity
[userid:2158889800] player Frizz connected islocalplayer=False
[userid:2158889800] is using new name Frizz
Started game for new connection
Disconnected from userid:2158889800 with reason App_Min
Removed endpoint.
player disconnect
timescale = 0
Disconnected from userid:373251987 with reason AppException_Max
Waiting for reconnection.
"""


class CoreKeeperBootJoinPatternTests(unittest.TestCase):
    def test_ready_is_listening_not_late_session_line(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            mon.ingest_stdout_line("ECSManager: converting authoring data.")
            self.assertFalse(mon.state.ready)
            mon.ingest_stdout_line("Listening on ip:0.0.0.0:7778")
            self.assertTrue(mon.state.ready)
            mon.ingest_stdout_line(
                "Started session with info: 203.0.113.1;7778;REDACTED;REDACTED"
            )
            self.assertTrue(mon.state.ready)

    def test_normal_leave_app_min_is_not_version_mismatch(self) -> None:
        plugin = load_plugin(PLUGIN)
        triggered: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp, on_version_mismatch=triggered.append)
            for line in _CK_BOOT_AND_JOIN_LOG.splitlines():
                mon.ingest_stdout_line(line)
            self.assertTrue(mon.state.ready)
            self.assertEqual(mon.state.version_mismatch_count, 0)
            self.assertEqual(triggered, [])
            self.assertEqual(mon.state.players, set())
            self.assertTrue(mon.state.players_known)

    def test_in_world_join_and_leave_share_userid(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            mon = LogMonitor(plugin, tmp)
            mon.ingest_stdout_line(
                "Accepted connection from 76561197968471340 with result OK "
                "awaiting authentication"
            )
            mon.ingest_stdout_line("Connected to userid:641048955")
            mon.ingest_stdout_line(
                "Successful authentication from userid: 76561197968471340"
            )
            self.assertEqual(mon.state.players, set())
            mon.ingest_stdout_line(
                "[userid:641048955] player Frizz connected islocalplayer=False"
            )
            self.assertEqual(mon.state.players, {"641048955"})
            mon.ingest_stdout_line(
                "Disconnected from userid:641048955 with reason App_Min"
            )
            self.assertEqual(mon.state.players, set())
            self.assertEqual(mon.state.player_count, 0)

    def test_suggest_boot_join_examples_are_configured(self) -> None:
        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            state = Path(tmp) / "state"
            logs.mkdir()
            state.mkdir()
            (logs / "server.log").write_text(_CK_BOOT_AND_JOIN_LOG, encoding="utf-8")
            box = LogToolbox(plugin, logs, state, recent_lines_provider=lambda: [])
            report = box.suggest(lines=200)
            ready = (report.get("configured") or {}).get("ready")
            self.assertTrue(ready)
            self.assertEqual(ready["patterns"], ["Listening on ip:"])
            self.assertTrue(
                any("Listening on ip:" in line for line in ready.get("examples") or [])
            )
            join = (report.get("configured") or {}).get("player_join")
            self.assertTrue(join)
            join_text = "\n".join(join.get("examples") or [])
            self.assertIn("player Frizz connected", join_text)
            self.assertNotIn("InvalidState", join_text)
            leave = (report.get("configured") or {}).get("player_leave")
            self.assertTrue(leave)
            leave_text = "\n".join(leave.get("examples") or [])
            self.assertIn("App_Min", leave_text)
            self.assertIn("AppException_Max", leave_text)
            mismatch = (report.get("configured") or {}).get("version_mismatch")
            self.assertTrue(mismatch)
            self.assertEqual(mismatch["hits"], 0)
            self.assertEqual(mismatch["examples"], [])


if __name__ == "__main__":
    unittest.main()
