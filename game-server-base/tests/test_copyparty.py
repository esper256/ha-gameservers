#!/usr/bin/env python3
"""Copyparty plugin: port + live mods-directory root, not generic sidecars."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.copyparty import CopypartyPublisher, CopypartySpec  # noqa: E402
from game_server.plugin import load_plugin  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "example.game.yaml"


class CopypartySpecTests(unittest.TestCase):
    def test_missing_is_none(self) -> None:
        self.assertIsNone(CopypartySpec.from_dict(None))
        self.assertIsNone(CopypartySpec.from_dict({}))

    def test_requires_root_and_port(self) -> None:
        with self.assertRaises(ValueError):
            CopypartySpec.from_dict({"port": 8765})
        with self.assertRaises(ValueError):
            CopypartySpec.from_dict({"root": "{data_dir}/mods", "port": "nope"})
        spec = CopypartySpec.from_dict(
            {
                "port": 9001,
                "root": "{data_dir}/{world_name}/mods",
                "password_option": "publisher_password",
                "after_idle_upload": ["python3", "/opt/publish.py"],
            }
        )
        assert spec is not None
        self.assertEqual(spec.port, 9001)
        self.assertEqual(spec.root, "{data_dir}/{world_name}/mods")
        self.assertEqual(spec.after_idle_upload, ["python3", "/opt/publish.py"])


class CopypartyPublisherTests(unittest.TestCase):
    def test_writes_site_root_on_live_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "worlds"
            world_mods = data / "FamilyWorld" / "mods"
            world_mods.mkdir(parents=True)
            leftover = world_mods / "broke.jar.PARTIAL"
            leftover.write_bytes(b"")
            empty = world_mods / "empty.jar"
            empty.write_bytes(b"")
            spec = CopypartySpec.from_dict(
                {
                    "port": 8765,
                    "root": "{data_dir}/{world_name}/mods",
                    "password_option": "publisher_password",
                    "before_upload": ["python3", "/opt/publish_mod.py", "--guard-upload"],
                    "after_idle_upload": ["python3", "/opt/publish_mod.py"],
                    "before_delete": ["python3", "/opt/publish_mod.py", "--guard-delete"],
                    "after_delete": ["python3", "/opt/publish_mod.py", "--after-delete"],
                }
            )
            publisher = CopypartyPublisher(
                spec,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"publisher_password": "secret", "world_name": "FamilyWorld"},
                world_name="FamilyWorld",
            )
            conf_path = publisher._write_config()
            text = conf_path.read_text(encoding="utf-8")
            self.assertIn("p: 8765", text)
            self.assertIn("[/]", text)
            self.assertNotIn("[/mods]", text)
            self.assertIn(str(world_mods), text)
            self.assertIn("xiu: i2,", text)
            self.assertIn("xbu: c,", text)
            self.assertIn("xbd: c,", text)
            self.assertIn("xad: ", text)
            self.assertNotIn("xau:", text)
            self.assertIn("e2dsa", text)
            self.assertIn("dotpart", text)
            self.assertIn("ui-nombar", text)
            self.assertIn("kids: secret", text)
            self.assertFalse(leftover.exists())
            self.assertFalse(empty.exists())
            upload = (root / "state" / "copyparty" / "on-upload.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("python3", upload)
            self.assertIn("/opt/publish_mod.py", upload)

    def test_example_plugin_has_no_copyparty(self) -> None:
        plugin = load_plugin(FIXTURE)
        self.assertIsNone(plugin.copyparty)
        self.assertFalse(plugin.hold_on_crash_loop)
        self.assertFalse(plugin.restart_when_empty)
        self.assertIsNone(plugin.status_probe)


if __name__ == "__main__":
    unittest.main()
