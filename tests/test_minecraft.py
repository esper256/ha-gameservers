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

from game_server.config import load_config  # noqa: E402
from game_server.plugin import load_plugin  # noqa: E402
from game_server.version import SUPERVISOR_VERSION  # noqa: E402
import haos_defaults  # noqa: E402
import mod_scan  # noqa: E402
import publish_mod  # noqa: E402


def _write_ha_pin(tmp: Path, version: str = "1.21.1", **extra: object) -> Path:
    options = tmp / "options.json"
    payload: dict[str, object] = {"minecraft_version": version}
    payload.update(extra)
    options.write_text(json.dumps(payload), encoding="utf-8")
    os.environ["OPTIONS_FILE"] = str(options)
    return options


def _jar(path: Path, *, fabric: bool = True, mod_id: str = "cool_creepers") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        os.chmod(path, 0o644)
        path.unlink()
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
        field = plugin.world_create.fields[0]
        self.assertEqual(field.id, "minecraft_version")
        self.assertEqual(field.kind, "select")
        self.assertEqual(field.default, haos_defaults.DEFAULT_MINECRAFT_VERSION)
        values = [item.value for item in field.options]
        self.assertEqual(
            values,
            [
                "26.2",
                "26.1.2",
                "1.21.11",
                "1.21.10",
                "1.21.8",
                "1.21.5",
                "1.21.4",
                "1.21.3",
                "1.21.1",
                "1.21",
                "1.20.6",
                "1.20.4",
                "1.20.2",
                "1.20.1",
            ],
        )
        self.assertTrue(all(isinstance(item, str) for item in values))
        self.assertIn(field.default, values)
        self.assertNotIn("mod_loader", PLUGIN.read_text(encoding="utf-8"))
        self.assertEqual(plugin.world_catalog.caption_json_path, "caption")
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
        self.assertNotIn("minecraft_version", cfg.get("options") or {})
        self.assertNotIn("neoforge_version", cfg.get("options") or {})
        self.assertNotIn("fabric_loader_version", cfg.get("options") or {})
        self.assertNotIn("minecraft_version", cfg.get("schema") or {})
        self.assertNotIn("neoforge_version", cfg.get("schema") or {})
        self.assertNotIn("fabric_loader_version", cfg.get("schema") or {})
        self.assertNotIn("white_list", cfg.get("options") or {})
        self.assertNotIn("white_list", cfg.get("schema") or {})
        self.assertNotIn("WHITE_LIST", plugin.env_options)
        self.assertEqual(
            plugin.env_options,
            [
                "JAVA_OPTS",
                "MINECRAFT_VERSION",
                "PUBLISHER_PASSWORD",
                "SERVER_MOTD",
                "EULA",
                "ONLINE_MODE",
            ],
        )
        self.assertTrue(plugin.restart_when_empty)
        assert plugin.copyparty is not None
        self.assertEqual(plugin.copyparty.port, 8765)
        self.assertEqual(
            plugin.copyparty.root, "{data_dir}/{world_name}/uploaded_mods"
        )
        self.assertIn("--guard-upload", plugin.copyparty.before_upload)
        self.assertEqual(
            plugin.copyparty.count_exclude,
            [haos_defaults.AUTOMODPACK_FINGERPRINT_NAME],
        )
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

    def test_run_sh_does_not_copy_the_pin(self) -> None:
        runsh = (MC / "run.sh").read_text(encoding="utf-8")
        defaults = (MC / "haos_defaults.py").read_text(encoding="utf-8")
        golden = (MC / "golden_boot.py").read_text(encoding="utf-8")
        self.assertIn("chmod a+r", runsh)
        self.assertIn("OPTIONS_FILE", runsh)
        self.assertNotIn("publish-pin", runsh)
        self.assertNotIn("Exported MINECRAFT_VERSION", runsh)
        self.assertNotIn("supervisor/addons/self/info", defaults)
        self.assertNotIn("minecraft_pin.json", defaults)
        self.assertNotIn("last_attempt_ha_version", golden)
        self.assertNotIn("attempt_state.json", golden)

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
        for key in (
            "DATA_DIR",
            "STATE_DIR",
            "MOD_PUBLISHER_DIR",
            "OPTIONS_FILE",
            "MINECRAFT_VERSION",
        ):
            os.environ.pop(key, None)

    def test_inspect_and_replace_by_mod_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "fabric"}),
                encoding="utf-8",
            )
            _write_ha_pin(root)
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
            _write_ha_pin(root)
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

    def test_publish_allows_a_different_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps(
                    {"loader": "neoforge", "minecraft_version": "1.21.1"}
                ),
                encoding="utf-8",
            )
            _write_ha_pin(root)
            publisher = root / "publisher"
            incoming = publisher / "incoming"
            incoming.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(root / "worlds")
            os.environ["STATE_DIR"] = str(root / "state")
            os.environ["MOD_PUBLISHER_DIR"] = str(publisher)
            Path(os.environ["STATE_DIR"]).mkdir(exist_ok=True)
            jar = incoming / "nope.jar"
            _jar(jar, fabric=True)
            self.assertEqual(publish_mod.publish(jar), 0)
            self.assertTrue((worlds / "uploaded_mods" / "cool_creepers.jar").is_file())
            self.assertFalse((publisher / "quarantine" / "nope.jar").exists())

    def test_rejects_wrong_minecraft_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worlds = root / "worlds" / "World"
            worlds.mkdir(parents=True)
            (worlds / "profile.json").write_text(
                json.dumps({"loader": "neoforge"}),
                encoding="utf-8",
            )
            _write_ha_pin(root, "1.21.1")
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
            self.assertIn("exec ", hook)
            self.assertNotIn("while IFS=", hook)

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
                banner = (worlds / "uploaded_mods" / ".prologue.html").read_text(
                    encoding="utf-8"
                )
                self.assertIn("AUTOMODPACK-FINGERPRINT.txt", banner)
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
            fingerprint = uploaded / "AUTOMODPACK-FINGERPRINT.txt"
            fingerprint.write_text("fingerprint\n", encoding="utf-8")
            self.assertEqual(publish_mod.guard_delete(fingerprint), 2)
            self.assertEqual(publish_mod.guard_upload(fingerprint), 2)

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
                json.dumps({"loader": "neoforge"}),
                encoding="utf-8",
            )
            _write_ha_pin(root)
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
                json.dumps({"loader": "fabric"}),
                encoding="utf-8",
            )
            _write_ha_pin(root)
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
                json.dumps({"loader": "neoforge"}),
                encoding="utf-8",
            )
            _write_ha_pin(root)
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

    def test_xiu_stdin_without_argv_publishes_and_requests_restart(self) -> None:
        """Copyparty xiu invokes the idle hook with paths on stdin, not argv."""

        from game_server.active_world import restart_request_path
        from game_server.copyparty import CopypartyPublisher, CopypartySpec

        pub = str(MC / "publish_mod.py")
        py = sys.executable
        spec = CopypartySpec.from_dict(
            {
                "port": 8765,
                "root": "{data_dir}/{world_name}/uploaded_mods",
                "after_idle_upload": [py, pub],
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
                json.dumps({"loader": "neoforge"}),
                encoding="utf-8",
            )
            _write_ha_pin(root)
            publisher = CopypartyPublisher(
                spec,
                state_dir=str(root / "state"),
                data_dir=str(data),
                options={"publisher_password": "secret", "world_name": "World"},
                world_name="World",
            )
            publisher._write_config()
            hook = root / "state" / "copyparty" / "on-upload.sh"
            drop = uploaded / "xaerominimap-neoforge-1.21.11-26.5.0.jar"
            _jar(drop, fabric=False, mod_id="xaerominimap")
            hook_env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [str(MC), str(BASE), os.environ.get("PYTHONPATH", "")]
                ),
            }
            published = subprocess.run(
                [str(hook)],
                input=str(drop).encode("utf-8"),
                check=False,
                env=hook_env,
                capture_output=True,
            )
            self.assertEqual(
                published.returncode,
                0,
                published.stderr.decode() + published.stdout.decode(),
            )
            self.assertTrue((uploaded / "xaerominimap.jar").is_file())
            self.assertFalse(drop.exists())
            self.assertTrue(restart_request_path(root / "state").is_file())
            self.assertIn(b"Published xaerominimap", published.stdout)

    def test_xiu_json_stdin_publishes(self) -> None:
        drop = Path("/tmp/does-not-matter")
        payload = json.dumps(
            [
                {
                    "ap": "/data/worlds/World/uploaded_mods/xaero.jar",
                    "sz": 12,
                    "wark": "abc",
                }
            ]
        )
        paths = publish_mod.paths_from_xiu_payload(payload)
        self.assertEqual(
            [p.as_posix() for p in paths],
            ["/data/worlds/World/uploaded_mods/xaero.jar"],
        )
        self.assertEqual(
            [p.as_posix() for p in publish_mod.paths_from_xiu_payload(str(drop))],
            [str(drop)],
        )
        self.assertEqual(publish_mod.paths_from_xiu_payload(""), [])


class HaVersionPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self._seed = patch.object(haos_defaults, "_seed_infrastructure", return_value=None)
        self._seed.start()

    def tearDown(self) -> None:
        self._seed.stop()
        for key in (
            "DATA_DIR",
            "STATE_DIR",
            "INSTALL_DIR",
            "MINECRAFT_VERSION",
            "JAVA_OPTS",
            "OPTIONS_FILE",
            "SUPERVISOR_TOKEN",
            "EULA",
            "ONLINE_MODE",
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
        os.environ["JAVA_OPTS"] = "-Xms32M"
        _write_ha_pin(tmp, version)
        Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
        for ver in ("1.21.1", "1.21.11"):
            for loader in ("neoforge", "vanilla", "fabric"):
                inst = installs / f"{loader}-{ver}"
                inst.mkdir(parents=True)
                (inst / "server.jar").write_bytes(b"starter")
                (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                if loader == "fabric":
                    (inst / ".install.env").write_text(
                        "SERVER=server.jar\n", encoding="utf-8"
                    )
        (world / "profile.json").write_text(
            json.dumps({"loader": "neoforge"}),
            encoding="utf-8",
        )
        uploaded = world / "uploaded_mods"
        uploaded.mkdir(parents=True)
        (uploaded / "cool_creepers.jar").write_bytes(b"mod")
        os.chmod(uploaded / "cool_creepers.jar", 0o444)
        return world

    def test_every_ha_option_keeps_json_false_and_zero(self) -> None:
        import yaml

        cfg = yaml.safe_load(
            (MC / "config.yaml").read_text(encoding="utf-8")
        )
        schema = cfg["schema"]
        payload: dict[str, object] = dict(cfg["options"])
        bool_keys = [key for key, spec in schema.items() if spec == "bool"]
        int_keys = [
            key
            for key, spec in schema.items()
            if str(spec).startswith("int")
        ]
        for key in bool_keys:
            payload[key] = False
        for key in int_keys:
            payload[key] = 0
        payload["minecraft_version"] = "1.21.1"
        payload["server_motd"] = "Pinned MOTD"
        payload["publisher_password"] = "secret-pass"
        payload["java_opts"] = "-Xms1G -Xmx2G"
        payload["backup_retention"] = "minimal"
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps(payload), encoding="utf-8")
            os.environ["OPTIONS_FILE"] = str(options)
            for leftover in ("ONLINE_MODE", "EULA", "SERVER_MOTD"):
                os.environ[leftover] = "FromEnv"
            try:
                for key in bool_keys:
                    self.assertEqual(
                        haos_defaults.env_or_option(key, "true"),
                        "false",
                        key,
                    )
                self.assertEqual(haos_defaults.env_or_option("server_slots", "8"), "0")
                self.assertEqual(
                    haos_defaults.env_or_option("server_motd", "A Minecraft Server"),
                    "Pinned MOTD",
                )
                for leftover in ("ONLINE_MODE", "EULA", "SERVER_MOTD"):
                    os.environ.pop(leftover, None)
                loaded = load_config()
                for key in bool_keys:
                    if hasattr(loaded, key):
                        self.assertFalse(getattr(loaded, key), key)
                for key in int_keys:
                    if hasattr(loaded, key):
                        self.assertEqual(getattr(loaded, key), 0, key)
                self.assertEqual(loaded.backup_retention, "minimal")
            finally:
                for leftover in ("ONLINE_MODE", "EULA", "SERVER_MOTD"):
                    os.environ.pop(leftover, None)

    def test_env_or_option_honors_json_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_ha_pin(Path(tmp), "1.21.1", online_mode=False)
            self.assertEqual(
                haos_defaults.env_or_option("online_mode", "true"), "false"
            )

    def test_prepare_world_disables_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _write_ha_pin(Path(tmp), "1.21.1", white_list=True)
            with patch.object(haos_defaults, "_seed_infrastructure", return_value=None):
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            props = (world / "server.properties").read_text(encoding="utf-8")
            self.assertIn("white-list=false", props)
            self.assertIn("enforce-whitelist=false", props)
            self.assertNotIn("white-list=true", props)

    def test_prepare_world_honors_eula_and_online_mode_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _write_ha_pin(Path(tmp), "1.21.1", eula=False, online_mode=False)
            os.environ["EULA"] = "true"
            os.environ["ONLINE_MODE"] = "true"
            with patch.object(haos_defaults, "_seed_infrastructure", return_value=None):
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            os.environ.pop("EULA", None)
            os.environ.pop("ONLINE_MODE", None)
            self.assertEqual(
                (world / "eula.txt").read_text(encoding="utf-8").strip(),
                "eula=false",
            )
            props = (world / "server.properties").read_text(encoding="utf-8")
            self.assertIn("online-mode=false", props)

    def test_prepare_pins_legacy_configuration_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.11")
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["loader"], "neoforge")
            self.assertEqual(profile["minecraft_version"], "1.21.11")
            self.assertEqual(profile["caption"], "1.21.11 · NeoForge")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(
                haos_defaults.current_install(world), ("neoforge", "1.21.11")
            )

    def test_addon_update_does_not_change_a_world_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            (world / "profile.json").write_text(
                json.dumps(
                    {
                        "loader": "neoforge",
                        "minecraft_version": "1.21.1",
                        "caption": "1.21.1 · NeoForge",
                    }
                ),
                encoding="utf-8",
            )
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            _write_ha_pin(Path(tmp), "1.21.11")
            os.environ["MINECRAFT_VERSION"] = "26.2"
            self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.1")
            self.assertEqual(profile["loader"], "neoforge")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(
                haos_defaults.current_install(world), ("neoforge", "1.21.1")
            )

    def test_loader_switch_clears_and_reseeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            (uploaded / "automodpack.jar").write_bytes(b"old-seed")
            os.chmod(uploaded / "automodpack.jar", 0o444)
            self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            _jar(uploaded / "cool_creepers.jar", fabric=True)
            with patch.object(haos_defaults, "_seed_infrastructure") as seed:
                self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.1")
            self.assertEqual(profile["loader"], "fabric")
            self.assertEqual(profile["caption"], "1.21.1 · Fabric")
            self.assertFalse((uploaded / "automodpack.jar").exists())
            seed.assert_called_once()
            self.assertEqual(seed.call_args.args[1], "fabric")
            self.assertEqual(seed.call_args.args[2], "1.21.1")

    def test_existing_install_beats_a_newer_configuration_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            (world / "profile.json").write_text("{}", encoding="utf-8")
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            haos_defaults._link_install(world, "neoforge", "1.21.1")
            _write_ha_pin(Path(tmp), "1.21.11")
            os.environ["MINECRAFT_VERSION"] = "26.2"
            self.assertEqual(haos_defaults.resolve_world_version(world), "1.21.1")
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.1")
            _write_ha_pin(Path(tmp), "26.2")
            self.assertEqual(haos_defaults.resolve_world_version(world), "1.21.1")

    def test_legacy_options_pin_used_only_without_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            (world / "profile.json").unlink()
            _write_ha_pin(Path(tmp), "1.21.11")
            os.environ["MINECRAFT_VERSION"] = "1.21.1"
            self.assertEqual(haos_defaults.resolve_world_version(world), "1.21.11")

    def test_compose_env_used_when_options_have_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "worlds" / "World"
            world.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(Path(tmp) / "worlds")
            os.environ["OPTIONS_FILE"] = str(Path(tmp) / "options.json")
            os.environ["MINECRAFT_VERSION"] = "1.21.11"
            self.assertEqual(haos_defaults.resolve_world_version(world), "1.21.11")

    def test_new_world_without_a_pin_uses_the_picker_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "worlds" / "World"
            world.mkdir(parents=True)
            os.environ["DATA_DIR"] = str(Path(tmp) / "worlds")
            os.environ["OPTIONS_FILE"] = str(Path(tmp) / "missing-options.json")
            os.environ.pop("MINECRAFT_VERSION", None)
            self.assertEqual(
                haos_defaults.resolve_world_version(world),
                haos_defaults.DEFAULT_MINECRAFT_VERSION,
            )

    def test_world_create_version_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            (world / "profile.json").unlink()
            (world / "world_create.json").write_text(
                json.dumps({"minecraft_version": "26.2"}),
                encoding="utf-8",
            )
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            # 26.2 tree is not in _env; detection still records the pin before boot.
            self.assertEqual(haos_defaults.cmd_prepare_world(), 0)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "26.2")
            self.assertFalse((world / "world_create.json").exists())

    def test_unreadable_options_fail_when_migration_needs_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "worlds" / "World"
            world.mkdir(parents=True)
            options = Path(tmp) / "options.json"
            options.write_text("{", encoding="utf-8")
            os.environ["DATA_DIR"] = str(Path(tmp) / "worlds")
            os.environ["OPTIONS_FILE"] = str(options)
            os.environ["MINECRAFT_VERSION"] = "1.21.11"
            with self.assertRaises(haos_defaults.MinecraftPinError) as raised:
                haos_defaults.resolve_world_version(world)
            self.assertIn(str(options), str(raised.exception))

    def test_stored_version_ignores_a_broken_options_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "worlds" / "World"
            world.mkdir(parents=True)
            (world / "profile.json").write_text(
                json.dumps({"minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            options = Path(tmp) / "options.json"
            options.write_text("{", encoding="utf-8")
            os.environ["DATA_DIR"] = str(Path(tmp) / "worlds")
            os.environ["OPTIONS_FILE"] = str(options)
            self.assertEqual(haos_defaults.resolve_world_version(world), "1.21.1")

    def test_print_version_is_a_stable_package_marker(self) -> None:
        from io import StringIO

        captured = StringIO()
        stdout = StringIO()
        with patch.object(sys, "stderr", captured), patch.object(sys, "stdout", stdout):
            self.assertEqual(haos_defaults.cmd_print_version(), 0)
        self.assertIn("pinned on each world", captured.getvalue())
        self.assertEqual(stdout.getvalue().strip(), haos_defaults.PACKAGE_VERSION)

    def test_cmd_install_only_writes_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._env(Path(tmp), "1.21.11")
            root = Path(os.environ["INSTALL_DIR"])
            with patch.object(haos_defaults, "_run_helper") as helper:
                self.assertEqual(haos_defaults.cmd_install(), 0)
            helper.assert_not_called()
            self.assertEqual(
                (root / ".loaders_ready").read_text(encoding="utf-8").strip(),
                haos_defaults.PACKAGE_VERSION,
            )

    def test_missing_tree_runs_loader_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.11")
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            shutil.rmtree(Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.11")

            def _install(loader: str, version: str) -> bool:
                inst = Path(os.environ["INSTALL_DIR"]) / f"{loader}-{version}"
                inst.mkdir(parents=True)
                (inst / "server.jar").write_bytes(b"starter")
                (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                return True

            with patch.object(
                haos_defaults, "ensure_loader_install", side_effect=_install
            ) as install:
                cmd = haos_defaults.prepare_game_command()
            install.assert_called_once_with("neoforge", "1.21.11")
            self.assertIsNotNone(cmd)
            self.assertTrue((world / "server.jar").exists())

    def test_missing_install_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _jar(world / "uploaded_mods" / "cool_creepers.jar", fabric=False)
            shutil.rmtree(Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.1")
            with patch.object(haos_defaults, "ensure_loader_install", return_value=False):
                cmd = haos_defaults.prepare_game_command()
            self.assertIsNone(cmd)

    def test_helper_argv_is_latest_loader(self) -> None:
        fabric = Path("/data/installs/fabric-1.21.11")
        fabric_args = haos_defaults.fabric_install_args(fabric, mc_version="1.21.11")
        self.assertTrue(all(not a.startswith("--loader-version=") for a in fabric_args))
        self.assertIn("--minecraft-version=1.21.11", fabric_args)
        neo = haos_defaults.neoforge_install_args(
            Path("/data/installs/neoforge-1.21.11"), mc_version="1.21.11"
        )
        self.assertIn("--neoforge-version=latest", neo)

    def test_loader_switch_on_next_boot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            self.assertIsNotNone(haos_defaults.prepare_game_command())
            self.assertEqual(
                haos_defaults.current_install(world), ("neoforge", "1.21.1")
            )
            uploaded.joinpath("cool_creepers.jar").unlink()
            _jar(uploaded / "cool_creepers.jar", fabric=True)
            self.assertIsNotNone(haos_defaults.prepare_game_command())
            self.assertEqual(
                haos_defaults.current_install(world), ("fabric", "1.21.1")
            )
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.1")
            self.assertEqual(profile["loader"], "fabric")

    def test_empty_folder_is_vanilla_and_ignores_injected_jars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            (uploaded / "cool_creepers.jar").unlink()
            (uploaded / "automodpack.jar").write_bytes(b"seed")
            (uploaded / "fabric-api.jar").write_bytes(b"api")
            (uploaded / "AUTOMODPACK-FINGERPRINT.txt").write_text("fp\n", encoding="utf-8")
            (uploaded / "libraries.jar").write_bytes(b"not-a-zip")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(haos_defaults.current_install(world), ("vanilla", "1.21.1"))
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["loader"], "vanilla")
            self.assertEqual(profile["caption"], "1.21.1 · Vanilla")
            self.assertFalse((uploaded / "automodpack.jar").exists())

    def test_mixed_mods_refuse_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            (uploaded / "cool_creepers.jar").unlink()
            _jar(uploaded / "fabric_mod.jar", fabric=True, mod_id="fabric_mod")
            _jar(uploaded / "neo_mod.jar", fabric=False, mod_id="neo_mod")
            buf = io.StringIO()
            with patch.object(sys, "stderr", buf):
                cmd = haos_defaults.prepare_game_command()
            self.assertIsNone(cmd)
            text = buf.getvalue()
            self.assertIn("fabric_mod.jar", text)
            self.assertIn("neo_mod.jar", text)
            self.assertIn("Fabric and NeoForge", text)
            profile = json.loads((world / "profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["minecraft_version"], "1.21.1")

    def test_incompatible_mod_refuses_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            uploaded = world / "uploaded_mods"
            (uploaded / "cool_creepers.jar").unlink()
            jar = uploaded / "jade.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr(
                    "META-INF/neoforge.mods.toml",
                    'modId="jade"\n'
                    '[[dependencies.jade]]\nmodId="neoforge"\nversionRange="[21,)"\n'
                    '[[dependencies.jade]]\nmodId="minecraft"\n'
                    'versionRange="[1.21.11,1.22)"\n',
                )
            buf = io.StringIO()
            with patch.object(sys, "stderr", buf):
                self.assertIsNone(haos_defaults.prepare_game_command())
            text = buf.getvalue()
            self.assertIn("jade.jar", text)
            self.assertIn("1.21.1", text)
            self.assertIn("[1.21.11,1.22)", text)

    def test_parse_install_ref_keeps_legacy_loader_pin(self) -> None:
        self.assertEqual(
            haos_defaults.parse_install_ref(Path("/data/installs/neoforge-1.21.11")),
            ("neoforge", "1.21.11"),
        )
        self.assertEqual(
            haos_defaults.parse_install_ref(
                Path("/data/installs/neoforge-1.21.11-beta")
            ),
            ("neoforge", "1.21.11-beta"),
        )
        self.assertEqual(
            haos_defaults.parse_install_ref(Path("/data/installs/vanilla-1.21.1")),
            ("vanilla", "1.21.1"),
        )
        self.assertEqual(
            haos_defaults.parse_install_ref(Path("/data/installs/vanilla-26.2")),
            ("vanilla", "26.2"),
        )


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
            self.assertNotIn("automodpack_fingerprint", payload)


_TEST_AUTOMODPACK_CERT = """\
-----BEGIN CERTIFICATE-----
MIIDFzCCAf+gAwIBAgIUD8APV0kbxy9RkDm9s4Vy0tyjne4wDQYJKoZIhvcNAQEL
BQAwGzEZMBcGA1UEAwwQYXV0b21vZHBhY2stdGVzdDAeFw0yNjA5MTYxNDQzNDZa
Fw0zNjA5MTMxNDQzNDZaMBsxGTAXBgNVBAMMEGF1dG9tb2RwYWNrLXRlc3QwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDS8N31L5jFvjX6HOE24hxK02Rs
J2gV25On9wM3sRiGjycHORfetzFCT4JDDJW8H99cvqTZAP+2S82o65UupnBysntI
5BZg8sTdOpYHtxZik7B9q+n+PqYdEyUeTRmpAHoSebJ+xn3dl9zQ4B1/XbQ2l+Us
enpoIf30NQnYQNEQzPaMA/37pw+Utjsnqa/qD61kNRmnR5grl6Ewg2eun6LVIvcB
2R0jRt9URNS4DAysaXC29NhG9gnLTY/ErlC4j1hxRWGQex1uDDYU7+vLy+GxrpPc
wXr7/7joPUm9pPCeZvVkyiwWAidbkrMHPYz01fQcdRy6bxhqtGCfEY9NhNdVAgMB
AAGjUzBRMB0GA1UdDgQWBBS4V0nIK7ejHV454e6Wf6ZqW05OtzAfBgNVHSMEGDAW
gBS4V0nIK7ejHV454e6Wf6ZqW05OtzAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3
DQEBCwUAA4IBAQB+qb2kiAQcHSlLdjEDySEYb7LJnEss3LNm6NyoWs5hB4ZkWx1T
PMRMZkZtEdd0v6K2TbrmwbhCoSLZwKT+KxxNwXKPSyzkqHf1/k53HHGEcGzC6WrD
vyF31/1DXGEdfd+RkqPMXxTYsaB2b6IzJRRhj4wlbQLF+MnU4s4V2o/6/LGEWXK5
P/A47b8RvJQ77DebfuiB5yvZqvv1R8DCY8cH+1RLQkPGoz6ntcN8B4wufEfQDSAA
v7PjkSG53pvhJugQ4O+GFwUPrFz39Vdxr4za+qox4dMejHaHJS3V+BRLT4Iox8Gy
tVOL/twF9BTNS1II6Zfn+OlfSdENNZoYSF/W
-----END CERTIFICATE-----
"""

_TEST_AUTOMODPACK_FINGERPRINT = (
    "17:E0:5C:B7:95:78:39:2C:AA:79:A3:F0:2E:5E:40:2F:"
    "C0:F5:2E:80:7E:C8:70:43:E4:AD:31:3D:8A:02:C0:D9"
)


def _write_automodpack_cert(world: Path) -> Path:
    cert = world / "automodpack" / ".private" / "cert.crt"
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_text(_TEST_AUTOMODPACK_CERT, encoding="utf-8")
    return cert


class AutoModpackFingerprintTests(unittest.TestCase):
    def test_fingerprint_matches_openssl_style_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            _write_automodpack_cert(world)
            self.assertEqual(
                haos_defaults.automodpack_tls_fingerprint(world),
                _TEST_AUTOMODPACK_FINGERPRINT,
            )

    def test_missing_cert_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            world.mkdir()
            self.assertIsNone(haos_defaults.automodpack_tls_fingerprint(world))
            self.assertIsNone(haos_defaults.write_automodpack_fingerprint_file(world))
            uploaded = world / "uploaded_mods"
            self.assertFalse(
                (uploaded / haos_defaults.AUTOMODPACK_FINGERPRINT_NAME).exists()
            )

    def test_writes_sealed_copyparty_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp) / "World"
            _write_automodpack_cert(world)
            dest = haos_defaults.write_automodpack_fingerprint_file(world)
            assert dest is not None
            self.assertEqual(dest.name, "AUTOMODPACK-FINGERPRINT.txt")
            text = dest.read_text(encoding="utf-8")
            self.assertIn(_TEST_AUTOMODPACK_FINGERPRINT, text)
            self.assertIn("Paste this when the Minecraft client warns", text)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o444)
            inode = dest.stat().st_ino
            again = haos_defaults.write_automodpack_fingerprint_file(world)
            self.assertEqual(again, dest)
            self.assertEqual(dest.stat().st_ino, inode)

    def test_sealed_jars_ignore_fingerprint_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uploaded = Path(tmp)
            (uploaded / "cool_creepers.jar").write_bytes(b"mod")
            (uploaded / "AUTOMODPACK-FINGERPRINT.txt").write_text(
                "fp\n", encoding="utf-8"
            )
            jars = haos_defaults.sealed_jars(uploaded)
            self.assertEqual([path.name for path in jars], ["cool_creepers.jar"])

    def test_prepare_game_command_writes_fingerprint_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _write_automodpack_cert(world)
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            dest = world / "uploaded_mods" / "AUTOMODPACK-FINGERPRINT.txt"
            self.assertTrue(dest.is_file())
            self.assertIn(_TEST_AUTOMODPACK_FINGERPRINT, dest.read_text(encoding="utf-8"))

    def test_status_probe_writes_file_after_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _write_automodpack_cert(world)
            buf = io.StringIO()
            with patch.object(
                haos_defaults,
                "minecraft_status_payload",
                return_value={"ready": True, "player_count": 0, "game_version": "1.21.1"},
            ):
                with patch("sys.stdout", buf):
                    self.assertEqual(haos_defaults.cmd_status_probe(), 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("ready"))
            self.assertNotIn("automodpack_fingerprint", payload)
            dest = world / "uploaded_mods" / "AUTOMODPACK-FINGERPRINT.txt"
            self.assertTrue(dest.is_file())
            self.assertIn(_TEST_AUTOMODPACK_FINGERPRINT, dest.read_text(encoding="utf-8"))

    def test_status_probe_skips_write_until_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp), "1.21.1")
            _write_automodpack_cert(world)
            buf = io.StringIO()
            with patch.object(haos_defaults, "minecraft_status_payload", return_value={}):
                with patch("sys.stdout", buf):
                    self.assertEqual(haos_defaults.cmd_status_probe(), 0)
            dest = world / "uploaded_mods" / "AUTOMODPACK-FINGERPRINT.txt"
            self.assertFalse(dest.exists())

    def test_minecraft_copyparty_skips_fingerprint_in_file_count(self) -> None:
        """Uploads count uses the base helper and Minecraft's plugin exclude list."""

        from game_server.copyparty import CopypartyPublisher, count_visible_files

        plugin = load_plugin(PLUGIN)
        assert plugin.copyparty is not None
        fingerprint = haos_defaults.AUTOMODPACK_FINGERPRINT_NAME
        self.assertEqual(plugin.copyparty.count_exclude, [fingerprint])
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            data = folder / "worlds"
            drop = data / "World" / "uploaded_mods"
            drop.mkdir(parents=True)
            (drop / "cool_creepers.jar").write_bytes(b"mod")
            (drop / fingerprint).write_text("fp\n", encoding="utf-8")
            (drop / ".prologue.html").write_text("banner\n", encoding="utf-8")
            self.assertEqual(count_visible_files(drop), 2)
            self.assertEqual(count_visible_files(drop, plugin.copyparty.count_exclude), 1)
            publisher = CopypartyPublisher(
                plugin.copyparty,
                state_dir=str(folder / "state"),
                data_dir=str(data),
                options={"world_name": "World"},
                world_name="World",
            )
            status = publisher.ui_status()
            assert status is not None
            self.assertEqual(status["file_count"], 1)

    def _env(self, tmp: Path, version: str = "1.21.1") -> Path:
        worlds = tmp / "worlds"
        world = worlds / "World"
        world.mkdir(parents=True)
        installs = tmp / "installs"
        os.environ["DATA_DIR"] = str(worlds)
        os.environ["STATE_DIR"] = str(tmp / "state")
        os.environ["INSTALL_DIR"] = str(installs)
        os.environ["JAVA_OPTS"] = "-Xms32M"
        _write_ha_pin(tmp, version)
        Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
        for loader in ("neoforge", "vanilla", "fabric"):
            inst = installs / f"{loader}-{version}"
            inst.mkdir(parents=True)
            (inst / "server.jar").write_bytes(b"starter")
            (inst / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            if loader == "fabric":
                (inst / ".install.env").write_text(
                    "SERVER=server.jar\n", encoding="utf-8"
                )
        (world / "profile.json").write_text(
            json.dumps({"loader": "neoforge"}),
            encoding="utf-8",
        )
        uploaded = world / "uploaded_mods"
        uploaded.mkdir(parents=True)
        (uploaded / "cool_creepers.jar").write_bytes(b"mod")
        os.chmod(uploaded / "cool_creepers.jar", 0o444)
        return world


def _fabric_jar(
    path: Path,
    *,
    mod_id: str = "cool_creepers",
    minecraft: str | list[str] | None = None,
    quilt: bool = False,
    neoforge: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    depends: dict[str, object] = {}
    if minecraft is not None:
        depends["minecraft"] = minecraft
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": mod_id,
                    "version": "1.0.0",
                    "name": "Cool Creepers",
                    "depends": depends,
                }
            ),
        )
        if quilt:
            zf.writestr("quilt.mod.json", "{}")
        if neoforge:
            zf.writestr(
                "META-INF/neoforge.mods.toml",
                f'modId="{mod_id}"\n',
            )


def _toml_jar(path: Path, body: str, *, name: str = "META-INF/mods.toml") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, body)


class ModScanTests(unittest.TestCase):
    def test_fabric_neoforge_empty_and_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(mod_scan.scan_mods(folder, "1.21.1").loader, "vanilla")
            _fabric_jar(folder / "cool.jar")
            fabric = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(fabric.loader, "fabric")
            self.assertEqual(fabric.errors, [])
            (folder / "cool.jar").unlink()
            _toml_jar(
                folder / "neo.jar",
                'modId="jade"\n[[dependencies.jade]]\nmodId="neoforge"\n'
                'versionRange="[21,)"\n[[dependencies.jade]]\nmodId="minecraft"\n'
                'versionRange="[1.21.1,1.22)"\n',
                name="META-INF/neoforge.mods.toml",
            )
            neo = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(neo.loader, "neoforge")
            (folder / "neo.jar").unlink()
            (folder / "libraries.jar").write_bytes(b"not-a-zip")
            with zipfile.ZipFile(folder / "api.jar", "w") as zf:
                zf.writestr("com/example/Lib.class", b"\x00")
            empty = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(empty.loader, "vanilla")
            self.assertEqual(empty.errors, [])
            self.assertTrue(any("libraries.jar" in item for item in empty.warnings))
            self.assertTrue(any("api.jar" in item for item in empty.warnings))

    def test_injected_jars_do_not_choose_a_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "automodpack-mc1.21.1-fabric-4.0.6.jar").write_bytes(b"seed")
            (folder / "fabric-api.jar").write_bytes(b"api")
            (folder / "AUTOMODPACK-FINGERPRINT.txt").write_text("fp\n", encoding="utf-8")
            _fabric_jar(folder / "hidden.jar", mod_id="automodpack")
            self.assertEqual(mod_scan.scan_mods(folder, "1.21.1").loader, "vanilla")

    def test_mixed_loaders_name_both_jars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _fabric_jar(folder / "fabric_mod.jar", mod_id="fabric_mod")
            _toml_jar(
                folder / "neo_mod.jar",
                'modId="neo_mod"\n',
                name="META-INF/neoforge.mods.toml",
            )
            scan = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(scan.loader, "")
            text = "\n".join(scan.errors)
            self.assertIn("fabric_mod.jar", text)
            self.assertIn("neo_mod.jar", text)
            self.assertIn("Fabric and NeoForge", text)

    def test_legacy_mods_toml_forge_neoforge_and_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _toml_jar(
                folder / "old_neo.jar",
                'modId="old_neo"\n[[dependencies.old_neo]]\nmodId="neoforge"\n'
                'versionRange="[21,)"\n',
            )
            self.assertEqual(mod_scan.scan_mods(folder, "1.21.1").loader, "neoforge")
            (folder / "old_neo.jar").unlink()
            _toml_jar(
                folder / "forge_mod.jar",
                'modId="forge_mod"\n[[dependencies.forge_mod]]\nmodId="forge"\n'
                'versionRange="[47,)"\n',
            )
            forge = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(forge.loader, "")
            self.assertIn("Forge", "\n".join(forge.errors))
            (folder / "forge_mod.jar").unlink()
            _toml_jar(folder / "mystery.jar", 'modId="mystery"\n')
            mystery = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(mystery.loader, "vanilla")
            self.assertEqual(mystery.errors, [])
            self.assertTrue(any("mystery.jar" in item for item in mystery.warnings))

    def test_quilt_only_is_refused_and_fabric_plus_quilt_is_fabric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with zipfile.ZipFile(folder / "quilt_only.jar", "w") as zf:
                zf.writestr("quilt.mod.json", '{"id":"quilt_only"}')
            quilt = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(quilt.loader, "")
            self.assertIn("Quilt", "\n".join(quilt.errors))
            (folder / "quilt_only.jar").unlink()
            _fabric_jar(folder / "both.jar", quilt=True)
            self.assertEqual(mod_scan.scan_mods(folder, "1.21.1").loader, "fabric")

    def test_one_jar_with_both_metadata_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _fabric_jar(folder / "both.jar", neoforge=True)
            scan = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(scan.loader, "")
            self.assertIn("both.jar", "\n".join(scan.errors))

    def test_fabric_and_neoforge_version_ranges(self) -> None:
        self.assertTrue(mod_scan.fabric_predicate_matches(">=1.21 <1.22", "1.21.1"))
        self.assertFalse(mod_scan.fabric_predicate_matches(">=1.21 <1.22", "1.22"))
        self.assertTrue(mod_scan.fabric_predicate_matches("1.21.x", "1.21.11"))
        self.assertFalse(mod_scan.fabric_predicate_matches("1.21.x", "1.20.1"))
        self.assertTrue(mod_scan.fabric_predicate_matches("*", "26.2"))
        self.assertTrue(mod_scan.fabric_predicate_matches("", "1.21.1"))
        self.assertIsNone(mod_scan.fabric_predicate_matches("^1.21.1", "1.21.1"))
        self.assertIsNone(mod_scan.fabric_predicate_matches("not-a-range", "1.21.1"))
        self.assertTrue(mod_scan.fabric_predicate_matches("1.21.11 || 1.21.1", "1.21.1"))
        self.assertTrue(mod_scan.maven_range_matches("[1.21,1.22)", "1.21.11"))
        self.assertFalse(mod_scan.maven_range_matches("[1.21,1.22)", "1.22"))
        self.assertTrue(mod_scan.maven_range_matches("[1.21.11]", "1.21.11"))
        self.assertFalse(mod_scan.maven_range_matches("[1.21.11]", "1.21.1"))
        self.assertTrue(mod_scan.maven_range_matches("[1.21.1,)", "1.21.10"))
        self.assertEqual(mod_scan._cmp_versions("1.21.10", "1.21.9"), 1)
        self.assertEqual(mod_scan._cmp_versions("1.21", "1.21.0"), 0)
        self.assertIsNone(mod_scan.maven_range_matches("garbage", "1.21.1"))

    def test_incompatible_range_blocks_and_unparseable_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _fabric_jar(folder / "future.jar", minecraft=">=1.21.11")
            blocked = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(blocked.loader, "")
            text = "\n".join(blocked.errors)
            self.assertIn("future.jar", text)
            self.assertIn("cool_creepers", text)
            self.assertIn(">=1.21.11", text)
            (folder / "future.jar").unlink()
            _fabric_jar(folder / "caret.jar", minecraft="^1.21.1")
            warned = mod_scan.scan_mods(folder, "1.21.1")
            self.assertEqual(warned.loader, "fabric")
            self.assertEqual(warned.errors, [])
            self.assertTrue(any("caret.jar" in item for item in warned.warnings))
            (folder / "caret.jar").unlink()
            _fabric_jar(folder / "open.jar")
            self.assertEqual(mod_scan.scan_mods(folder, "1.21.1").loader, "fabric")


if __name__ == "__main__":
    unittest.main()
