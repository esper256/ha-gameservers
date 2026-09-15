#!/usr/bin/env python3
"""Minecraft add-on plugin and publish-mod checks."""

from __future__ import annotations

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

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
        self.assertFalse(hasattr(plugin, "hold_on_crash_loop"))
        self.assertNotIn("hold_on_crash_loop", PLUGIN.read_text(encoding="utf-8"))
        import yaml

        cfg = yaml.safe_load(
            (ROOT / "minecraft-dedicated-server" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("healthz", str(cfg.get("watchdog") or ""))
        self.assertTrue(plugin.restart_when_empty)
        assert plugin.copyparty is not None
        self.assertEqual(plugin.copyparty.port, 8765)
        self.assertEqual(
            plugin.copyparty.root, "{data_dir}/{world_name}/uploaded_mods"
        )
        self.assertIn("--guard-upload", plugin.copyparty.before_upload)
        assert plugin.status_probe is not None
        self.assertIn("status-probe", plugin.status_probe.argv)
        self.assertEqual(plugin.player_tracking_mode, "count")
        self.assertFalse(plugin.log_patterns.player_count)

    def test_ingress_uses_probe_count_not_last_join(self) -> None:
        from game_server.status_http import _ui_view

        plugin = load_plugin(PLUGIN)
        view = _ui_view(
            {
                "running": True,
                "lifecycle": "running",
                "debug_mode": False,
                "player_tracking_mode": plugin.player_tracking_mode,
                "status_probe": plugin.status_probe is not None,
                "log_patterns": {
                    "player_tracking_enabled": True,
                    "patterns": [
                        {
                            "mode": "active",
                            "category": "player_join",
                            "pattern": plugin.log_patterns.player_join[0],
                            "hits": 1,
                        },
                        {
                            "mode": "active",
                            "category": "player_leave",
                            "pattern": plugin.log_patterns.player_leave[0],
                            "hits": 0,
                        },
                    ],
                },
                "monitor": {
                    "players_known": True,
                    "player_count": 2,
                    "players_present": True,
                },
            },
            plugin.name,
        )
        self.assertEqual(view["players_label"], "Number of players")
        self.assertEqual(view["players"], "2")
        self.assertEqual(view["players_hint"], "Live count from the game")
        self.assertFalse(view["players_card_hidden"])

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
            worlds = root / "worlds" / "World"
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
            self.assertEqual(publish_mod.rollback("cool_creepers"), 0)
            self.assertTrue(dest.is_file())

    def test_publish_paths_skips_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
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
            worlds = root / "worlds" / "World"
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
            worlds = root / "worlds" / "World"
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
            (data / "World" / "uploaded_mods").mkdir(parents=True)
            os.environ["DATA_DIR"] = str(data)
            publisher = CopypartyPublisher(
                plugin.copyparty,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"publisher_password": "secret", "world_name": "World"},
                world_name="World",
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
            worlds = root / "worlds" / "World"
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
            uploaded = root / "worlds" / "World" / "uploaded_mods"
            uploaded.mkdir(parents=True)
            protected = uploaded / "automodpack.jar"
            _jar(protected, fabric=False, mod_id="automodpack")
            jar = uploaded / "cool_creepers.jar"
            _jar(jar, fabric=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            self.assertEqual(publish_mod.guard_delete(protected), 2)
            self.assertEqual(publish_mod.guard_delete(jar), 0)
            self.assertEqual(publish_mod.guard_upload(jar), 2)
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
            world = Path(tmp) / "World"
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
            self.assertFalse((world / "mods.prev").exists())
            self.assertFalse((world / "mods.release").exists())
            self.assertEqual(
                (world / "mods" / "cool_creepers.jar").read_bytes(), b"next-generation"
            )

    def test_stage_skips_vanished_upload_jars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            gone = uploaded / "cool_creepers.jar"
            gone.write_bytes(b"sealed-payload")
            os.chmod(gone, 0o444)
            kept = uploaded / "jade.jar"
            kept.write_bytes(b"kept-payload")
            os.chmod(kept, 0o444)
            real_sealed = haos_defaults.sealed_jars

            def vanish_then_list(folder: Path) -> list[Path]:
                jars = real_sealed(folder)
                gone.unlink()
                return jars

            haos_defaults.sealed_jars = vanish_then_list  # type: ignore[method-assign]
            try:
                haos_defaults.stage_mod_snapshot(world)
            finally:
                haos_defaults.sealed_jars = real_sealed  # type: ignore[method-assign]
            self.assertFalse((world / "mods" / "cool_creepers.jar").exists())
            self.assertEqual((world / "mods" / "jade.jar").read_bytes(), b"kept-payload")

    def test_stage_clears_leftover_mods_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            uploaded = world / "uploaded_mods"
            nxt = world / "mods.next"
            nxt.mkdir(parents=True)
            (nxt / "stale.jar").write_bytes(b"stale")
            uploaded.mkdir(parents=True)
            src = uploaded / "cool_creepers.jar"
            src.write_bytes(b"fresh")
            os.chmod(src, 0o444)
            haos_defaults.stage_mod_snapshot(world)
            self.assertFalse(nxt.exists())
            self.assertEqual((world / "mods" / "cool_creepers.jar").read_bytes(), b"fresh")
            self.assertFalse((world / "mods" / "stale.jar").exists())
            leftover = world / "mods.prev"
            leftover.mkdir()
            (leftover / "old.jar").write_bytes(b"old")
            haos_defaults.stage_mod_snapshot(world)
            self.assertFalse(leftover.exists())

    def test_hardlink_stage_isolates_replaced_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            src = uploaded / "cool_creepers.jar"
            src.write_bytes(b"sealed-payload")
            os.chmod(src, 0o444)
            with patch.object(haos_defaults, "_try_reflink", return_value=False):
                haos_defaults.stage_mod_snapshot(world)
            staged = world / "mods" / "cool_creepers.jar"
            self.assertEqual(staged.stat().st_ino, src.stat().st_ino)
            src.unlink()
            src.write_bytes(b"truncated-or-replaced")
            self.assertEqual(staged.read_bytes(), b"sealed-payload")

    def test_stage_copy_fallback_isolates_truncate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            src = uploaded / "cool_creepers.jar"
            src.write_bytes(b"sealed-payload")
            os.chmod(src, 0o444)

            def boom(*_args: object, **_kwargs: object) -> None:
                raise OSError(errno.EXDEV, "cross-device")

            with patch.object(haos_defaults, "_try_reflink", return_value=False):
                with patch.object(os, "link", side_effect=boom):
                    haos_defaults.stage_mod_snapshot(world)
            staged = world / "mods" / "cool_creepers.jar"
            os.chmod(src, 0o644)
            with open(src, "wb") as handle:
                handle.truncate(0)
            self.assertEqual(staged.read_bytes(), b"sealed-payload")

    def test_rollback_prunes_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            publisher = root / "publisher"
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(publisher)
            Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
            keep = publish_mod.HISTORY_KEEP
            publish_mod.HISTORY_KEEP = 2
            try:
                for i in range(4):
                    drop = root / f"drop-{i}.jar"
                    _jar(drop, fabric=False, mod_id="jade")
                    self.assertEqual(publish_mod.publish(drop), 0)
                hist = list((publisher / "history" / "jade").glob("*/artifact.jar"))
                self.assertEqual(len(hist), 2)
                dest = worlds / "uploaded_mods" / "jade.jar"
                self.assertEqual(publish_mod.rollback("jade"), 0)
                self.assertTrue(dest.is_file())
            finally:
                publish_mod.HISTORY_KEEP = keep

    def test_client_only_jar_goes_to_host_modpack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "fabric", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(root / "publisher")
            Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir(parents=True, exist_ok=True)
            drop = root / "minimap.jar"
            with zipfile.ZipFile(drop, "w") as zf:
                zf.writestr(
                    "fabric.mod.json",
                    json.dumps(
                        {
                            "id": "minimap",
                            "version": "0.0.1",
                            "name": "minimap",
                            "environment": "client",
                        }
                    ),
                )
            self.assertEqual(publish_mod.publish(drop), 0)
            packed = (
                worlds
                / "automodpack"
                / "host-modpack"
                / "main"
                / "mods"
                / "minimap.jar"
            )
            self.assertTrue(packed.is_file())
            self.assertFalse((worlds / "uploaded_mods" / "minimap.jar").exists())

    def test_generated_hooks_guard_publish_and_delete(self) -> None:
        from game_server.active_world import restart_request_path
        from game_server.copyparty import CopypartyPublisher, CopypartySpec

        pub = str(MC / "publish_mod.py")
        py = sys.executable
        spec = CopypartySpec.from_dict(
            {
                "port": 8765,
                "root": "{data_dir}/{world_name}/uploaded_mods",
                "before_upload": [py, pub, "--guard-upload"],
                "after_idle_upload": [py, pub],
                "before_delete": [py, pub, "--guard-delete"],
                "after_delete": [py, pub, "--after-delete"],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "worlds"
            uploaded = data / "World" / "uploaded_mods"
            uploaded.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(data)
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(root / "publisher")
            Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir(parents=True, exist_ok=True)
            (data / "World" / "profile.json").write_text(
                json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            publisher = CopypartyPublisher(
                spec,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"publisher_password": "secret", "world_name": "World"},
                world_name="World",
            )
            publisher._write_config()
            hooks = root / "state" / "copyparty"
            hook_env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [str(MC), str(BASE), os.environ.get("PYTHONPATH", "")]
                ),
            }
            protected = uploaded / "automodpack.jar"
            _jar(protected, fabric=False, mod_id="automodpack")
            deny = subprocess.run(
                [str(hooks / "on-upload-guard.sh"), str(protected)],
                check=False,
                env=hook_env,
            )
            self.assertNotEqual(deny.returncode, 0)
            drop = uploaded / "cool-creepers-1.jar"
            allow = subprocess.run(
                [str(hooks / "on-upload-guard.sh"), str(drop)],
                check=False,
                env=hook_env,
            )
            self.assertEqual(allow.returncode, 0)
            _jar(drop, fabric=False)
            published = subprocess.run(
                [str(hooks / "on-upload.sh"), str(drop)],
                check=False,
                env=hook_env,
            )
            self.assertEqual(published.returncode, 0)
            canonical = uploaded / "cool_creepers.jar"
            self.assertTrue(canonical.is_file())
            self.assertFalse(drop.exists())
            extra = uploaded / "cool_creepers.jar"
            gone = subprocess.run(
                [str(hooks / "on-delete.sh"), str(extra)],
                check=False,
                env=hook_env,
            )
            self.assertEqual(gone.returncode, 0)
            self.assertTrue(restart_request_path(root / "state").is_file())


class HaVersionPinTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "DATA_DIR",
            "STATE_DIR",
            "INSTALL_DIR",
            "MINECRAFT_VERSION",
            "JAVA_OPTS",
            "OPTIONS_FILE",
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
        for ver in ("1.21.1", "1.21.11"):
            inst = installs / f"neoforge-{ver}"
            inst.mkdir(parents=True)
            (inst / "server.jar").write_bytes(b"starter")
            (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (world / "profile.json").write_text(
            json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
            encoding="utf-8",
        )
        uploaded = world / "uploaded_mods"
        uploaded.mkdir(parents=True)
        (uploaded / "cool_creepers.jar").write_bytes(b"mod")
        os.chmod(uploaded / "cool_creepers.jar", 0o444)
        return world

    def test_prepare_and_run_attempt_the_ha_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.11")
            with patch.object(haos_defaults, "_seed_infrastructure", return_value=None):
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.11")
            self.assertEqual(profile["loader"], "neoforge")
            self.assertFalse((world / "mods").exists())
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertTrue((world / "server.jar").exists())
            self.assertTrue((Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.1").is_dir())
            self.assertTrue((Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.11").is_dir())
            self.assertEqual((world / "mods" / "cool_creepers.jar").read_bytes(), b"mod")

    def test_prepare_world_reseeds_infra_when_pin_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            (uploaded / "automodpack.jar").write_bytes(b"old-seed")
            os.chmod(uploaded / "automodpack.jar", 0o444)
            os.environ["MINECRAFT_VERSION"] = "1.21.11"
            with patch.object(haos_defaults, "_seed_infrastructure") as seed:
                with patch.object(haos_defaults, "_link_install", return_value=None):
                    self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.11")
            self.assertFalse((uploaded / "automodpack.jar").exists())
            seed.assert_called_once()
            self.assertEqual(seed.call_args.args[1], "neoforge")
            self.assertEqual(seed.call_args.args[2], "1.21.11")

    def test_options_json_pin_beats_stale_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            (world / "attempt_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_ha_version": "1.21.1",
                        "last_attempt_loader": "neoforge",
                    }
                ),
                encoding="utf-8",
            )
            (world / "golden.json").write_text(
                json.dumps(
                    {
                        "loader": "neoforge",
                        "minecraft_version": "1.21.1",
                        "stock": True,
                    }
                ),
                encoding="utf-8",
            )
            (world / "golden_mods").mkdir()
            options = Path(tmp) / "options.json"
            options.write_text(
                json.dumps({"minecraft_version": "1.21.11"}), encoding="utf-8"
            )
            os.environ["OPTIONS_FILE"] = str(options)
            os.environ["MINECRAFT_VERSION"] = "1.21.1"
            self.assertEqual(haos_defaults.minecraft_version(), "1.21.11")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            session = json.loads((world / "boot.json").read_text(encoding="utf-8"))
            self.assertEqual(session.get("mode"), "attempt")
            self.assertEqual(session.get("minecraft_version"), "1.21.11")
            self.assertTrue((world / "server.jar").is_symlink() or (world / "server.jar").exists())
            target = (world / "server.jar").resolve()
            self.assertIn("neoforge-1.21.11", str(target))

    def test_missing_pin_tree_runs_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            os.environ["MINECRAFT_VERSION"] = "1.21.11"
            shutil.rmtree(Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.11")

            def _install() -> int:
                inst = Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.11"
                inst.mkdir(parents=True)
                (inst / "server.jar").write_bytes(b"starter")
                (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                return 0

            with patch.object(haos_defaults, "cmd_install", side_effect=_install) as install:
                cmd = haos_defaults.prepare_game_command()
            install.assert_called_once()
            self.assertIsNotNone(cmd)
            self.assertTrue((world / "server.jar").exists())

    def test_unchanged_pin_keeps_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            with patch.object(haos_defaults, "_seed_infrastructure", return_value=None):
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["loader"], "neoforge")
            self.assertEqual(profile["minecraft_version"], "1.21.1")

    def test_missing_install_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._env(Path(tmp), "1.99.9")
            os.environ["MINECRAFT_VERSION"] = "1.99.9"
            with patch.object(haos_defaults, "cmd_install", return_value=0) as install:
                cmd = haos_defaults.prepare_game_command()
            install.assert_called_once()
            self.assertIsNone(cmd)


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
            world = root / "worlds" / "World"
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
    def tearDown(self) -> None:
        for key in ("DATA_DIR", "STATE_DIR", "SERVER_PORT"):
            os.environ.pop(key, None)

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

    def test_probe_uses_status_ping_not_rcon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATA_DIR"] = str(Path(tmp) / "worlds")
            os.environ["STATE_DIR"] = str(Path(tmp) / "state")
            os.environ["SERVER_PORT"] = "25565"
            Path(os.environ["STATE_DIR"]).mkdir(parents=True)
            (Path(os.environ["DATA_DIR"]) / "World").mkdir(parents=True)
            buf = io.StringIO()
            with patch.object(
                haos_defaults,
                "minecraft_status_payload",
                return_value={"ready": True, "player_count": 2, "game_version": "1.21.1"},
            ):
                with patch.object(haos_defaults, "rcon_command") as rcon:
                    with patch("sys.stdout", buf):
                        self.assertEqual(haos_defaults.cmd_status_probe(), 0)
            rcon.assert_not_called()
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("player_count"), 2)
            self.assertTrue(payload.get("ready"))


if __name__ == "__main__":
    unittest.main()
