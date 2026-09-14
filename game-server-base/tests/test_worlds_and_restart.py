#!/usr/bin/env python3
"""World catalog, active-world persistence, and game-process restart."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.active_world import (  # noqa: E402
    apply_active_world_to_options,
    consume_restart_request,
    load_active_world,
    save_active_world,
    write_restart_request,
)
from game_server.plugin import load_plugin  # noqa: E402
from game_server.world_catalog import (  # noqa: E402
    WorldCatalogSpec,
    WorldCreateSpec,
    list_catalog_worlds,
    validate_create_fields,
    validate_world_name,
    write_world_create_payload,
)

FIXTURE = ROOT / "tests" / "fixtures" / "example.game.yaml"


class ActiveWorldTests(unittest.TestCase):
    def test_round_trip_and_options_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            save_active_world(tmp, option_key="world_name", value="Alpha")
            loaded = load_active_world(tmp)
            assert loaded is not None
            self.assertEqual(loaded.option_key, "world_name")
            self.assertEqual(loaded.value, "Alpha")
            options = {"world_name": "FamilyWorld"}
            apply_active_world_to_options(options, loaded)
            self.assertEqual(options["world_name"], "Alpha")

    def test_restart_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_restart_request(tmp, reason="publish", debounce_seconds=20)
            payload = consume_restart_request(tmp)
            self.assertEqual(payload["reason"], "publish")
            self.assertEqual(payload["debounce_seconds"], 20)
            self.assertIsNone(consume_restart_request(tmp))


class WorldCatalogTests(unittest.TestCase):
    def test_glob_stem_and_caption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saves = Path(tmp) / "saves"
            saves.mkdir()
            (saves / "Alpha.zip").write_bytes(b"x")
            (saves / "Beta.zip").write_bytes(b"y")
            (saves / "profile.json").write_text(
                json.dumps({"loader": "alpha"}), encoding="utf-8"
            )
            spec = WorldCatalogSpec.from_dict(
                {
                    "glob": str(saves / "*.zip"),
                    "name_from": "stem",
                    "caption_file": "profile.json",
                    "caption_json_path": "loader",
                }
            )
            entries = list_catalog_worlds(
                spec, data_dir=tmp, options={}, active_name="Alpha"
            )
            names = [item.name for item in entries]
            self.assertEqual(names[0], "Alpha")
            self.assertTrue(entries[0].active)
            captions = {item.name: item.caption for item in entries}
            self.assertEqual(captions["Alpha"], "alpha")

    def test_create_fields_select_validation(self) -> None:
        spec = WorldCreateSpec.from_dict(
            {
                "fields": [
                    {
                        "id": "flavor",
                        "kind": "select",
                        "label": "Flavor",
                        "default": "plain",
                        "options": [
                            {"value": "plain", "label": "Plain"},
                            {"value": "spicy", "label": "Spicy"},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            validate_create_fields(spec, {}),
            {"flavor": "plain"},
        )
        self.assertEqual(
            validate_create_fields(spec, {"flavor": "spicy"}),
            {"flavor": "spicy"},
        )
        with self.assertRaises(ValueError):
            validate_create_fields(spec, {"flavor": "other"})
        with self.assertRaises(ValueError):
            validate_create_fields(spec, {"nope": "x"})
        with self.assertRaises(ValueError):
            validate_world_name("bad name")
        self.assertEqual(validate_world_name("Family-1"), "Family-1")

    def test_write_world_create_payload_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "worlds" / "NewWorld"
            path = write_world_create_payload(
                expected_path=str(dest),
                fields={"flavor": "spicy"},
            )
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["flavor"], "spicy")

    def test_example_plugin_loads_without_catalog(self) -> None:
        plugin = load_plugin(FIXTURE)
        self.assertIsNone(plugin.world_catalog)
        self.assertIsNone(plugin.world_create)
        self.assertEqual(plugin.pre_backup_stdin_commands, [])


class IngressHtmlTests(unittest.TestCase):
    def test_create_world_js_uses_field_key_variable(self) -> None:
        from game_server.status_http import HTML_PAGE

        self.assertIn("fields[{{key}}] = el.value", HTML_PAGE)
        self.assertNotIn("fields[{key}] = el.value", HTML_PAGE)


class RestartSupervisorTests(unittest.TestCase):
    def test_request_restart_sets_pending(self) -> None:
        from game_server.config import SupervisorConfig
        from game_server.supervisor import GameServerSupervisor

        plugin = load_plugin(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SupervisorConfig(
                install_dir=str(Path(tmp) / "game"),
                state_dir=str(Path(tmp) / "state"),
                backup_dir=str(Path(tmp) / "backups"),
                status_http_enabled=False,
                ha_notifications=False,
                backup_enabled=False,
                drop_privileges=False,
                update_on_start=False,
                auto_update_interval_minutes=0,
                game_options={
                    "world_name": "TestWorld",
                    "data_dir": str(Path(tmp) / "world"),
                    "logs_dir": str(Path(tmp) / "logs"),
                },
            )
            supervisor = GameServerSupervisor(plugin, cfg)
            result = supervisor.request_restart("publish", debounce_seconds=30)
            self.assertTrue(result["ok"])
            self.assertTrue(supervisor._restart_pending)
            self.assertGreater(supervisor._restart_not_before, time.time())
            immediate = supervisor.request_restart("publish", debounce_seconds=0)
            self.assertTrue(immediate["ok"])
            self.assertEqual(supervisor._restart_not_before, 0.0)


if __name__ == "__main__":
    unittest.main()
