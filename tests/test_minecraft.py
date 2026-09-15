#!/usr/bin/env python3
"""Minecraft add-on plugin and publish-mod checks."""

from __future__ import annotations

import json
import os
import shutil
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
        self.assertTrue(plugin.hold_on_crash_loop)
        self.assertTrue(plugin.restart_when_empty)
        assert plugin.copyparty is not None
        self.assertEqual(plugin.copyparty.port, 8765)
        self.assertEqual(
            plugin.copyparty.root, "{data_dir}/{world_name}/uploaded_mods"
        )
        self.assertIn("--guard-upload", plugin.copyparty.before_upload)
        assert plugin.status_probe is not None
        self.assertIn("status-probe", plugin.status_probe.argv)

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
            for word in ("minecraft", "neoforge", "automodpack"):
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
            dest = worlds / "uploaded_mods" / "cool_creepers.jar"
            self.assertTrue(dest.is_file())
            self.assertFalse(first.exists())
            self.assertEqual(publish_mod.publish(second), 0)
            hist = list((publisher / "history" / "cool_creepers").glob("*/artifact.jar"))
            self.assertEqual(len(hist), 1)
            self.assertTrue(dest.is_file())
            self.assertEqual(
                publish_mod.inspect_jar(dest)["mod_id"], "cool_creepers"
            )

    def test_publish_from_stdin_skips_partial(self) -> None:
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
            Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
            jar = incoming / "jade.jar"
            partial = incoming / "jade.jar.PARTIAL"
            _jar(jar, fabric=False, mod_id="jade")
            _jar(partial, fabric=False, mod_id="jade")
            self.assertEqual(publish_mod.publish_paths([partial, jar]), 0)
            dest = worlds / "uploaded_mods" / "jade.jar"
            self.assertTrue(dest.is_file())
            self.assertFalse(jar.exists())
            self.assertTrue(partial.is_file())

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

    def test_rejects_wrong_minecraft_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "FamilyWorld"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            publisher = root / "publisher"
            incoming = publisher / "incoming"
            incoming.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(publisher)
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            jar = incoming / "Jade-1.21.11-NeoForge-21.1.7.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr(
                    "META-INF/neoforge.mods.toml",
                    'modId="jade"\nside="BOTH"\n'
                    '[[dependencies.jade]]\nmodId="minecraft"\n'
                    'versionRange="[1.21.11]"\n',
                )
            self.assertEqual(publish_mod.publish(jar), 1)
            self.assertTrue(
                (publisher / "quarantine" / "Jade-1.21.11-NeoForge-21.1.7.jar").is_file()
            )
            self.assertFalse((worlds / "uploaded_mods" / "jade.jar").exists())

    def test_copyparty_config_uses_upload_hook(self) -> None:
        from game_server.copyparty import CopypartyPublisher
        from game_server.plugin import load_plugin

        plugin = load_plugin(PLUGIN)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "worlds"
            (data / "FamilyWorld" / "uploaded_mods").mkdir(parents=True)
            os.environ["DATA_DIR"] = str(data)
            publisher = CopypartyPublisher(
                plugin.copyparty,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"publisher_password": "secret", "world_name": "FamilyWorld"},
                world_name="FamilyWorld",
            )
            conf = publisher._write_config().read_text(encoding="utf-8")
            self.assertIn("xiu:", conf)
            self.assertIn("i2,", conf)
            self.assertNotIn("xau:", conf)
            self.assertIn("[/]", conf)
            self.assertNotIn("[/mods]", conf)
            self.assertIn("xbd:", conf)
            self.assertIn("xbu:", conf)
            self.assertIn("e2dsa", conf)
            self.assertIn("dotpart", conf)
            self.assertIn("ui-nombar", conf)
            self.assertIn("no-thumb", conf)
            self.assertIn("unpost: 0", conf)
            self.assertNotIn("ui-noacci", conf)
            self.assertNotIn("ui-nonav", conf)
            hook = (root / "state" / "copyparty" / "on-upload.sh").read_text(
                encoding="utf-8"
            )
            self.assertIn("publish_mod.py", hook)

    def test_copyparty_banner_on_upload_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "FamilyWorld"
            worlds.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            try:
                self.assertEqual(haos_defaults.cmd_write_copyparty_banner(), 0)
                self.assertTrue(
                    (worlds / "uploaded_mods" / ".prologue.html").is_file()
                )
            finally:
                os.environ.pop("DATA_DIR", None)
                os.environ.pop("STATE_DIR", None)

    def test_guard_delete_protects_automodpack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uploaded = root / "worlds" / "FamilyWorld" / "uploaded_mods"
            uploaded.mkdir(parents=True)
            protected = uploaded / "automodpack.jar"
            _jar(protected, fabric=False, mod_id="automodpack")
            kid = uploaded / "cool_creepers.jar"
            _jar(kid, fabric=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            self.assertEqual(publish_mod.guard_delete(protected), 2)
            self.assertEqual(publish_mod.guard_delete(kid), 0)
            self.assertEqual(publish_mod.guard_upload(kid), 2)
            fresh = uploaded / "cool-creepers-2.jar"
            self.assertEqual(publish_mod.guard_upload(fresh), 0)

    def test_atomic_replace_uses_new_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "cool_creepers.jar"
            dest.write_bytes(b"old-jar-bytes")
            in_place_ino = dest.stat().st_ino
            other = root / "other.jar"
            other.write_bytes(b"copy2-payload")
            shutil.copy2(other, dest)
            self.assertEqual(dest.stat().st_ino, in_place_ino)
            incoming = root / "incoming.jar"
            incoming.write_bytes(b"new-jar-bytes-xxxx")
            haos_defaults.install_atomic(incoming, dest)
            self.assertNotEqual(dest.stat().st_ino, in_place_ino)
            self.assertEqual(dest.read_bytes(), b"new-jar-bytes-xxxx")
            self.assertEqual(dest.stat().st_mode & 0o777, 0o444)

    def test_stage_snapshot_survives_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "FamilyWorld"
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            src = uploaded / "cool_creepers.jar"
            src.write_bytes(b"sealed-payload")
            os.chmod(src, 0o444)
            haos_defaults.stage_mod_snapshot(world)
            staged = world / "mods" / "cool_creepers.jar"
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), b"sealed-payload")
            src.unlink()
            self.assertEqual(staged.read_bytes(), b"sealed-payload")
            uploaded.joinpath("cool_creepers.jar").write_bytes(b"next-generation")
            os.chmod(uploaded / "cool_creepers.jar", 0o444)
            haos_defaults.stage_mod_snapshot(world)
            prev = world / "mods.prev" / "cool_creepers.jar"
            self.assertTrue(prev.is_file())
            self.assertEqual(prev.read_bytes(), b"sealed-payload")
            self.assertEqual(staged.read_bytes(), b"next-generation")


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


class StatusProbeTests(unittest.TestCase):
    def test_list_response_and_status_json_omit(self) -> None:
        self.assertEqual(
            haos_defaults.parse_java_list_response(
                "There are 2 of a max of 8 players online: Ada, Bob"
            ),
            2,
        )
        self.assertEqual(
            haos_defaults.parse_java_list_response(
                "There are 0 of a max of 8 players online:"
            ),
            0,
        )
        self.assertIsNone(haos_defaults.parse_java_list_response(""))
        missing = haos_defaults.findings_from_status_json(
            {"version": {"name": "1.21.1"}, "players": {"max": 8}}
        )
        self.assertEqual(missing.get("ready"), True)
        self.assertEqual(missing.get("game_version"), "1.21.1")
        self.assertNotIn("player_count", missing)
        zero = haos_defaults.findings_from_status_json({"players": {"online": 0}})
        self.assertEqual(zero.get("player_count"), 0)
        self.assertEqual(
            haos_defaults._bind_port({"server-port": "25566"}, "server-port", "SERVER_PORT"),
            25566,
        )
        self.assertIsNone(haos_defaults._bind_port({}, "server-port", "MISSING_PORT"))


if __name__ == "__main__":
    unittest.main()
