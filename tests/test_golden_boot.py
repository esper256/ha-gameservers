#!/usr/bin/env python3
"""Golden boot: stock ready, extra mods need a player, unproven crash falls back."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MC = ROOT / "minecraft-dedicated-server"
import sys

sys.path.insert(0, str(MC))
sys.path.insert(0, str(ROOT / "game-server-base"))

import golden_boot  # noqa: E402
import haos_defaults  # noqa: E402
import publish_mod  # noqa: E402


def _jar(path: Path, *, fabric: bool = True, mod_id: str = "cool_creepers") -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        if fabric:
            zf.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "id": mod_id,
                        "version": "0.0.1",
                        "name": mod_id,
                        "environment": "*",
                    }
                ),
            )
        else:
            zf.writestr(
                "META-INF/neoforge.mods.toml",
                f'modId="{mod_id}"\nside="BOTH"\n',
            )


def _fake_neoforge(installs: Path, version: str) -> None:
    inst = installs / f"neoforge-{version}"
    inst.mkdir(parents=True)
    (inst / "server.jar").write_bytes(b"starter")
    (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")


class GoldenBootTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "DATA_DIR",
            "STATE_DIR",
            "INSTALL_DIR",
            "MINECRAFT_VERSION",
            "JAVA_OPTS",
        ):
            os.environ.pop(key, None)

    def _env(self, tmp: Path, version: str = "1.21.1") -> Path:
        worlds = tmp / "worlds"
        world = worlds / "World"
        world.mkdir(parents=True)
        installs = tmp / "installs"
        os.environ["DATA_DIR"] = str(worlds)
        os.environ["STATE_DIR"] = str(tmp / "state")
        os.environ["INSTALL_DIR"] = str(installs)
        os.environ["MINECRAFT_VERSION"] = version
        os.environ["JAVA_OPTS"] = "-Xms32M"
        Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
        _fake_neoforge(installs, "1.21.1")
        _fake_neoforge(installs, "1.21.11")
        (world / "profile.json").write_text(
            json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
            encoding="utf-8",
        )
        return world

    def test_ha_pin_is_attempted_after_first_boot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.11")
            with patch.object(haos_defaults, "_seed_infrastructure", return_value=None):
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.11")
            self.assertEqual(profile["loader"], "neoforge")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertTrue((world / "server.jar").exists())
            self.assertTrue((Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.1").is_dir())
            session = golden_boot.load_boot_session(world)
            self.assertEqual(session.get("mode"), "attempt")
            self.assertEqual(session.get("minecraft_version"), "1.21.11")

    def test_stock_ready_promotes_without_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            golden_boot.apply_probe_findings(world, {"ready": True})
            self.assertTrue(golden_boot.has_golden(world))
            meta = golden_boot.load_golden_meta(world)
            assert meta is not None
            self.assertEqual(meta["minecraft_version"], "1.21.1")
            self.assertTrue(meta.get("stock"))

    def test_extra_jar_ready_without_player_does_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            haos_defaults.prepare_game_command()
            golden_boot.apply_probe_findings(world, {"ready": True})
            self.assertFalse(golden_boot.has_golden(world))
            golden_boot.apply_probe_findings(world, {"ready": True, "player_count": 1})
            self.assertTrue(golden_boot.has_golden(world))
            self.assertFalse(json.loads((world / "golden.json").read_text()).get("stock"))

    def test_unproven_crash_boots_golden_without_rewriting_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            golden_boot.apply_probe_findings(world, {"ready": True})
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            golden_boot.mark_attempt_request(world)
            haos_defaults.prepare_game_command()
            self.assertTrue((world / "mods" / "cool_creepers.jar").is_file())
            self.assertEqual(golden_boot.load_boot_session(world).get("mode"), "attempt")
            self.assertFalse(golden_boot.load_boot_session(world).get("proven"))
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(golden_boot.load_boot_session(world).get("mode"), "golden")
            self.assertTrue((uploaded / "cool_creepers.jar").is_file())
            self.assertFalse((world / "mods" / "cool_creepers.jar").exists())

    def test_empty_restart_after_fallback_does_not_restage_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            golden_boot.apply_probe_findings(world, {"ready": True})
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            golden_boot.mark_attempt_request(world)
            haos_defaults.prepare_game_command()
            haos_defaults.prepare_game_command()
            haos_defaults.prepare_game_command()
            self.assertEqual(golden_boot.load_boot_session(world).get("mode"), "golden")
            self.assertTrue((uploaded / "cool_creepers.jar").is_file())
            self.assertFalse((world / "mods" / "cool_creepers.jar").exists())

    def test_copyparty_change_starts_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            golden_boot.apply_probe_findings(world, {"ready": True})
            haos_defaults.prepare_game_command()
            self.assertEqual(golden_boot.load_boot_session(world).get("mode"), "golden")
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            golden_boot.mark_attempt_request(world)
            haos_defaults.prepare_game_command()
            self.assertEqual(golden_boot.load_boot_session(world).get("mode"), "attempt")
            self.assertTrue((world / "mods" / "cool_creepers.jar").is_file())

    def test_missing_install_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.99.9")
            os.environ["MINECRAFT_VERSION"] = "1.99.9"
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNone(cmd)

    def test_publish_marks_attempt_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            os.environ["MOD_PUBLISHER_DIR"] = str(Path(tmp) / "pub")
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir()
            incoming = Path(tmp) / "drop.jar"
            _jar(incoming, fabric=False)
            self.assertEqual(publish_mod.publish(incoming), 0)
            self.assertTrue((world / "attempt.request").is_file())


if __name__ == "__main__":
    unittest.main()
