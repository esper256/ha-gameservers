#!/usr/bin/env python3
"""Minecraft add-on plugin and publish-mod checks."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "minecraft-dedicated-server" / "games" / "game.yaml"
MC = ROOT / "minecraft-dedicated-server"
BASE = ROOT / "game-server-base"

sys.path.insert(0, str(MC))
sys.path.insert(0, str(BASE))

from game_server.plugin import load_plugin  # noqa: E402
from game_server.version import SUPERVISOR_VERSION  # noqa: E402
import haos_defaults  # noqa: E402
import publish_mod  # noqa: E402


def _jar(path: Path, *, fabric: bool = True, mod_id: str = "cool_creepers") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        if fabric:
            zf.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "id": mod_id,
                        "version": "0.0.1",
                        "name": "Cool Creepers",
                        "environment": "*",
                    }
                ),
            )
        else:
            zf.writestr(
                "META-INF/neoforge.mods.toml",
                f'modId="{mod_id}"\nside="BOTH"\n',
            )


class MinecraftPluginTests(unittest.TestCase):
    def test_plugin_shape(self) -> None:
        plugin = load_plugin(PLUGIN)
        self.assertEqual(plugin.name, "Minecraft")
        self.assertTrue(plugin.uses_package_install)
        self.assertEqual(plugin.executable, ["/opt/launch_wrapper.sh"])
        self.assertEqual(plugin.pre_backup_stdin_commands, ["save-all flush"])
        self.assertIsNotNone(plugin.world_catalog)
        self.assertIsNotNone(plugin.world_create)
        self.assertEqual(plugin.world_create.fields[0].id, "mod_loader")
        self.assertEqual(plugin.ui_theme.get("accent"), "#5aad32")

    def test_config_version_matches_supervisor(self) -> None:
        import yaml

        data = yaml.safe_load(
            (ROOT / "minecraft-dedicated-server" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(str(data["version"]).startswith(SUPERVISOR_VERSION + "."))

    def test_dockerfile_is_not_itzg_image(self) -> None:
        text = (MC / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("itzg/minecraft-server", text.lower())
        self.assertIn("mc-image-helper", text)
        self.assertIn("server-starter.jar", text)

    def test_no_minecraft_in_supervisor(self) -> None:
        hits = []
        for path in BASE.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lower = text.lower()
            for word in ("minecraft", "neoforge", "automodpack", "copyparty"):
                if word in lower:
                    hits.append(f"{path.relative_to(ROOT)}:{word}")
        self.assertEqual(hits, [], f"Minecraft leaked into game-server-base: {hits}")


class PublishModTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in ("DATA_DIR", "STATE_DIR", "MOD_PUBLISHER_DIR"):
            os.environ.pop(key, None)

    def test_inspect_and_replace_by_mod_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "FamilyWorld"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "fabric", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            publisher = root / "publisher"
            incoming = publisher / "incoming"
            incoming.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(publisher)
            Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
            first = incoming / "cool-creepers-final.jar"
            second = incoming / "cool-creepers-0.0.3.jar"
            _jar(first, fabric=True)
            _jar(second, fabric=True)
            self.assertEqual(publish_mod.publish(first), 0)
            dest = worlds / "mods" / "cool_creepers.jar"
            self.assertTrue(dest.is_file())
            self.assertFalse(first.exists())
            self.assertEqual(publish_mod.publish(second), 0)
            hist = list((publisher / "history" / "cool_creepers").glob("*/artifact.jar"))
            self.assertEqual(len(hist), 1)
            self.assertTrue(dest.is_file())
            self.assertEqual(
                publish_mod.inspect_jar(dest)["mod_id"], "cool_creepers"
            )

    def test_rejects_wrong_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "FamilyWorld"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "neoforge"}), encoding="utf-8"
            )
            publisher = root / "publisher"
            incoming = publisher / "incoming"
            incoming.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(publisher)
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            jar = incoming / "nope.jar"
            _jar(jar, fabric=True)
            self.assertEqual(publish_mod.publish(jar), 1)
            self.assertTrue((publisher / "quarantine" / "nope.jar").is_file())

    def test_copyparty_config_uses_upload_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["MOD_PUBLISHER_DIR"] = str(root)
            os.environ["PUBLISHER_PASSWORD"] = "secret"
            try:
                self.assertEqual(haos_defaults.cmd_write_copyparty_config(), 0)
            finally:
                os.environ.pop("PUBLISHER_PASSWORD", None)
            conf = (root / "copyparty.conf").read_text(encoding="utf-8")
            self.assertIn("xau:", conf)
            self.assertIn("flags:", conf)
            self.assertIn("e2dsa", conf)
            self.assertNotIn("xbu:", conf)
            self.assertNotIn("{p}", conf)
            self.assertTrue((root / "on-upload.sh").is_file())


class LaunchLinkTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("INSTALL_DIR", None)

    def test_neoforge_links_run_script_and_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "installs" / "neoforge-1.21.1"
            libraries = install / "libraries" / "net" / "neoforged"
            libraries.mkdir(parents=True)
            (install / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (install / "user_jvm_args.txt").write_text("-Xmx1G\n", encoding="utf-8")
            (install / "server.jar").write_bytes(b"starter")
            (install / ".install.env").write_text("SERVER=run.sh\n", encoding="utf-8")
            world = root / "worlds" / "FamilyWorld"
            world.mkdir(parents=True)
            os.environ["INSTALL_DIR"] = str(root / "installs")
            haos_defaults._link_install(world, "neoforge", "1.21.1")
            self.assertTrue((world / "run.sh").exists())
            self.assertTrue((world / "user_jvm_args.txt").exists())
            self.assertTrue((world / "libraries").exists())
            self.assertTrue((world / "server.jar").exists())

    def test_fabric_uses_results_file_launcher_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jar_name = "fabric-server-mc.1.21.1-loader.0.16.9-launcher.1.0.1.jar"
            install = root / "installs" / "fabric-1.21.1"
            install.mkdir(parents=True)
            (install / jar_name).write_bytes(b"fabric")
            (install / ".install.env").write_text(f"SERVER={jar_name}\n", encoding="utf-8")
            world = root / "worlds" / "Creative"
            world.mkdir(parents=True)
            os.environ["INSTALL_DIR"] = str(root / "installs")
            haos_defaults._link_install(world, "fabric", "1.21.1")
            self.assertTrue((world / jar_name).exists())
            self.assertFalse((world / "fabric-server-launch.jar").exists())
            found = haos_defaults.fabric_launcher_jar(install, world)
            self.assertEqual(found, world / jar_name)


if __name__ == "__main__":
    unittest.main()
