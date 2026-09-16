#!/usr/bin/env python3
"""Copyparty plugin: port + live mods-directory root, not generic sidecars."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.copyparty import (  # noqa: E402
    CopypartyPublisher,
    CopypartySpec,
    copyparty_lan_port,
)
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


class CopypartyLanPortTests(unittest.TestCase):
    def test_falls_back_to_container_port(self) -> None:
        self.assertEqual(copyparty_lan_port(8765, None), 8765)
        self.assertEqual(copyparty_lan_port(8765, {}), 8765)
        self.assertEqual(copyparty_lan_port(8765, {"25565/tcp": 25565}), 8765)

    def test_uses_ha_network_host_mapping(self) -> None:
        self.assertEqual(copyparty_lan_port(8765, {"8765/tcp": 19999}), 19999)
        self.assertEqual(copyparty_lan_port(8765, {"8765": "19999"}), 19999)

    def test_disabled_mapping_is_none(self) -> None:
        self.assertIsNone(copyparty_lan_port(8765, {"8765/tcp": None}))
        self.assertIsNone(copyparty_lan_port(8765, {"8765/tcp": False}))
        self.assertIsNone(copyparty_lan_port(8765, {"8765/tcp": 0}))


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
            leftover_txt = world_mods / "empty.dat"
            leftover_txt.write_bytes(b"")
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
            self.assertIn("mods: secret", text)
            self.assertFalse(leftover.exists())
            self.assertFalse(empty.exists())
            self.assertFalse(leftover_txt.exists())
            upload = (root / "state" / "copyparty" / "on-upload.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("python3", upload)
            self.assertIn("/opt/publish_mod.py", upload)
            self.assertIn("exec ", upload)
            self.assertNotIn("while IFS=", upload)

    def test_idle_hook_forwards_xiu_stdin_without_trailing_newline(self) -> None:
        """Copyparty xiu: no argv, b'\\n'.join(paths) on stdin (no final newline)."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "worlds"
            (data / "FamilyWorld" / "mods").mkdir(parents=True)
            seen = root / "seen.txt"
            spec = CopypartySpec.from_dict(
                {
                    "port": 8765,
                    "root": "{data_dir}/{world_name}/mods",
                    "after_idle_upload": [
                        sys.executable,
                        "-c",
                        "import sys; open(sys.argv[1], 'w').write(sys.stdin.read())",
                        str(seen),
                    ],
                }
            )
            publisher = CopypartyPublisher(
                spec,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"world_name": "FamilyWorld"},
                world_name="FamilyWorld",
            )
            publisher._write_config()
            hook = root / "state" / "copyparty" / "on-upload.sh"
            path = "/data/worlds/World/uploaded_mods/xaero.jar"
            result = subprocess.run(
                [str(hook)],
                input=path.encode("utf-8"),
                check=False,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(seen.read_text(encoding="utf-8"), path)

    def test_ui_status_counts_visible_files(self) -> None:
        from game_server.copyparty import count_visible_files

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cool_creepers.jar").write_bytes(b"jar")
            (folder / "jade.jar").write_bytes(b"jar")
            (folder / ".prologue.html").write_text("hi", encoding="utf-8")
            (folder / "skip.jar.PARTIAL").write_bytes(b"x")
            (folder / "nested").mkdir()
            self.assertEqual(count_visible_files(folder), 2)
            self.assertEqual(count_visible_files(folder / "missing"), 0)
            spec = CopypartySpec.from_dict(
                {"port": 8765, "root": "{data_dir}/{world_name}/uploaded_mods"}
            )
            data = folder / "worlds"
            drop = data / "FamilyWorld" / "uploaded_mods"
            drop.mkdir(parents=True)
            (drop / "cool_creepers.jar").write_bytes(b"jar")
            publisher = CopypartyPublisher(
                spec,
                state_dir=str(folder / "state"),
                data_dir=str(data),
                options={"world_name": "FamilyWorld"},
                world_name="FamilyWorld",
            )
            self.assertEqual(
                publisher.ui_status(),
                {"port": 8765, "file_count": 1},
            )
            from unittest.mock import patch

            with patch(
                "game_server.copyparty.fetch_addon_network",
                return_value={"8765/tcp": 19999},
            ):
                publisher._network_cache = None
                self.assertEqual(
                    publisher.ui_status(),
                    {"port": 19999, "file_count": 1},
                )
            with patch(
                "game_server.copyparty.fetch_addon_network",
                return_value={"8765/tcp": None},
            ):
                publisher._network_cache = None
                self.assertIsNone(publisher.ui_status())
            self.assertIsNone(
                CopypartyPublisher(
                    None,
                    state_dir=str(folder / "state"),
                    data_dir=str(data),
                    options={},
                    world_name="FamilyWorld",
                ).ui_status()
            )

    def test_example_plugin_has_no_copyparty(self) -> None:
        plugin = load_plugin(FIXTURE)
        self.assertIsNone(plugin.copyparty)
        self.assertFalse(plugin.restart_when_empty)
        self.assertIsNone(plugin.status_probe)


if __name__ == "__main__":
    unittest.main()
