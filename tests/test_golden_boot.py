#!/usr/bin/env python3
"""Golden boot: stock ready, extra mods need a player, unproven crash falls back."""

from __future__ import annotations

import json
import os
import shutil
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

# Flip when restore is wired: stock ready promotes; extra/pin/loader need a
# player; unproven crash boots golden_mods without rewriting uploaded_mods.
GOLDEN_ROLLBACK_IMPLEMENTED = True


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


def _has_golden(world: Path) -> bool:
    if not (world / "golden_mods").is_dir():
        return False
    link = world / "golden_install"
    if link.exists() or link.is_symlink():
        try:
            name = link.resolve().name
        except OSError:
            name = ""
        if "neoforge-" in name or "fabric-" in name:
            return True
    meta = world / "golden.json"
    if not meta.is_file():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if not str(data.get("minecraft_version") or "").strip():
        return False
    if not str(data.get("loader") or "").strip():
        return False
    return True


def _write_ha_pin(tmp: Path, version: str) -> Path:
    options = tmp / "options.json"
    options.write_text(json.dumps({"minecraft_version": version}), encoding="utf-8")
    os.environ["OPTIONS_FILE"] = str(options)
    return options


def _boot_mode(world: Path) -> str:
    path = world / "boot.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("mode") or "") if isinstance(data, dict) else ""


@unittest.skipUnless(
    GOLDEN_ROLLBACK_IMPLEMENTED,
    "golden restore is a later Minecraft-layer pass",
)
class GoldenBootContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "DATA_DIR",
            "STATE_DIR",
            "INSTALL_DIR",
            "MINECRAFT_VERSION",
            "JAVA_OPTS",
            "MOD_PUBLISHER_DIR",
            "SERVER_PORT",
            "OPTIONS_FILE",
            "SUPERVISOR_TOKEN",
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
        os.environ["SERVER_PORT"] = "25565"
        _write_ha_pin(tmp, version)
        Path(os.environ["STATE_DIR"]).mkdir(parents=True, exist_ok=True)
        _fake_neoforge(installs, "1.21.1")
        _fake_neoforge(installs, "1.21.11")
        (world / "profile.json").write_text(
            json.dumps({"loader": "neoforge"}),
            encoding="utf-8",
        )
        return world

    def _probe(self, *, ready: bool = False, player_count: int | None = None) -> None:
        status: dict[str, object] = {}
        if ready:
            status["ready"] = True
            status["game_version"] = "1.21.1"
        if player_count is not None:
            status["player_count"] = player_count
        with patch.object(haos_defaults, "minecraft_status_payload", return_value=status):
            with patch.object(haos_defaults, "read_server_properties", return_value={}):
                haos_defaults.cmd_status_probe()

    def test_stock_ready_promotes_without_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            self.assertTrue(_has_golden(world))
            meta = json.loads((world / "golden.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["minecraft_version"], "1.21.1")
            self.assertTrue(meta.get("stock"))

    def test_extra_jar_ready_without_player_does_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            self.assertFalse(_has_golden(world))
            self._probe(ready=True, player_count=1)
            self.assertTrue(_has_golden(world))
            self.assertFalse(
                json.loads((world / "golden.json").read_text(encoding="utf-8")).get("stock")
            )

    def test_pin_change_stock_ready_promotes_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            self.assertTrue(_has_golden(world))
            _write_ha_pin(Path(tmp), "1.21.11")
            haos_defaults.prepare_game_command()
            self.assertEqual(haos_defaults.current_install(world), ("neoforge", "1.21.11"))
            self._probe(ready=True)
            self.assertTrue((world / "golden_install").resolve().name.endswith("1.21.11"))

    def test_unproven_crash_boots_golden_without_rewriting_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            os.environ["MOD_PUBLISHER_DIR"] = str(Path(tmp) / "pub")
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir()
            drop = Path(tmp) / "drop.jar"
            _jar(drop, fabric=False)
            self.assertEqual(publish_mod.publish(drop), 0)
            haos_defaults.prepare_game_command()
            self.assertTrue((world / "mods" / "cool_creepers.jar").is_file())
            self.assertEqual(_boot_mode(world), "attempt")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(_boot_mode(world), "golden")
            self.assertTrue((uploaded / "cool_creepers.jar").is_file())
            self.assertFalse((world / "mods" / "cool_creepers.jar").exists())
            self.assertTrue((Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.1").is_dir())
            self.assertTrue((Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.11").is_dir())

    def test_empty_restart_after_fallback_does_not_restage_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            os.environ["MOD_PUBLISHER_DIR"] = str(Path(tmp) / "pub")
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir()
            drop = Path(tmp) / "drop.jar"
            _jar(drop, fabric=False)
            publish_mod.publish(drop)
            haos_defaults.prepare_game_command()
            haos_defaults.prepare_game_command()
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "golden")
            self.assertTrue((uploaded / "cool_creepers.jar").is_file())
            self.assertFalse((world / "mods" / "cool_creepers.jar").exists())

    def test_copyparty_change_starts_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "golden")
            os.environ["MOD_PUBLISHER_DIR"] = str(Path(tmp) / "pub")
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir()
            drop = Path(tmp) / "drop.jar"
            _jar(drop, fabric=False)
            publish_mod.publish(drop)
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            self.assertTrue((world / "mods" / "cool_creepers.jar").is_file())

    def test_ha_pin_edit_starts_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "golden")
            _write_ha_pin(Path(tmp), "1.21.11")
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            self.assertTrue((world / "server.jar").exists())

    def test_ha_options_json_pin_beats_stale_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "golden")
            options = Path(tmp) / "options.json"
            options.write_text(
                json.dumps({"minecraft_version": "1.21.11", "server_motd": "New pin"}),
                encoding="utf-8",
            )
            os.environ["OPTIONS_FILE"] = str(options)
            os.environ["MINECRAFT_VERSION"] = "1.21.1"
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            session = json.loads((world / "boot.json").read_text(encoding="utf-8"))
            self.assertEqual(session["minecraft_version"], "1.21.11")

    def test_ha_pin_retries_after_unproven_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            self.assertTrue(_has_golden(world))
            _write_ha_pin(Path(tmp), "1.21.11")
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "golden")
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            session = json.loads((world / "boot.json").read_text(encoding="utf-8"))
            self.assertEqual(session["minecraft_version"], "1.21.11")

    def test_publish_marks_attempt_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            os.environ["MOD_PUBLISHER_DIR"] = str(Path(tmp) / "pub")
            Path(os.environ["MOD_PUBLISHER_DIR"]).mkdir()
            incoming = Path(tmp) / "drop.jar"
            _jar(incoming, fabric=False)
            self.assertEqual(publish_mod.publish(incoming), 0)
            self.assertTrue((world / "attempt.request").is_file())

    def test_golden_preserves_jvm_snapshot_not_later_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            uploaded = world / "uploaded_mods"
            uploaded.mkdir(parents=True)
            _jar(uploaded / "cool_creepers.jar", fabric=False)
            haos_defaults.prepare_game_command()
            self.assertTrue((world / "mods" / "cool_creepers.jar").is_file())
            _jar(uploaded / "jade.jar", fabric=False, mod_id="jade")
            self._probe(ready=True, player_count=1)
            self.assertTrue(_has_golden(world))
            self.assertTrue((world / "golden_mods" / "cool_creepers.jar").is_file())
            self.assertFalse((world / "golden_mods" / "jade.jar").exists())
            self.assertTrue((uploaded / "jade.jar").is_file())
            self.assertFalse((world / "mods.prev").exists())

    def test_missing_golden_install_falls_back_to_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._env(Path(tmp))
            (world / "uploaded_mods").mkdir(parents=True)
            haos_defaults.prepare_game_command()
            self._probe(ready=True)
            self.assertTrue(_has_golden(world))
            _write_ha_pin(Path(tmp), "1.21.11")
            haos_defaults.prepare_game_command()
            self.assertEqual(_boot_mode(world), "attempt")
            shutil.rmtree(Path(os.environ["INSTALL_DIR"]) / "neoforge-1.21.1")
            cmd = haos_defaults.prepare_game_command()
            self.assertIsNotNone(cmd)
            self.assertEqual(_boot_mode(world), "attempt")
            self.assertTrue((world / "server.jar").exists())

    def test_restage_uses_install_link_not_saved_pin_copies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            installs = root / "installs"
            os.environ["INSTALL_DIR"] = str(installs)
            _fake_neoforge(installs, "1.21.1")
            _fake_neoforge(installs, "1.21.11")
            (world / "golden_mods").mkdir()
            (world / "mods").mkdir()
            (world / "uploaded_mods").mkdir()
            (world / "golden.json").write_text(
                json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            (world / "attempt_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_ha_version": "1.21.1",
                        "last_attempt_loader": "neoforge",
                    }
                ),
                encoding="utf-8",
            )
            haos_defaults._link_install(world, "neoforge", "1.21.1")
            self.assertTrue(
                golden_boot.should_restage(world, loader="neoforge", version="1.21.11")
            )
            self.assertFalse(
                golden_boot.should_restage(world, loader="neoforge", version="1.21.1")
            )
            self.assertEqual(
                golden_boot.choose_boot_mode(world, ha_version="1.21.11", loader="neoforge"),
                "attempt",
            )

    def test_crash_falls_back_then_retries_when_install_link_is_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = root / "world"
            world.mkdir()
            installs = root / "installs"
            os.environ["INSTALL_DIR"] = str(installs)
            _fake_neoforge(installs, "1.21.1")
            _fake_neoforge(installs, "1.21.11")
            (world / "golden_mods").mkdir()
            (world / "mods").mkdir()
            (world / "uploaded_mods").mkdir()
            (world / "golden.json").write_text(
                json.dumps({"loader": "neoforge", "minecraft_version": "1.21.1"}),
                encoding="utf-8",
            )
            golden_boot.write_golden_install(world, loader="neoforge", version="1.21.1")
            golden_boot.write_boot_session(
                world,
                mode="attempt",
                loader="neoforge",
                minecraft_version="1.21.11",
                stock=True,
            )
            self.assertEqual(
                golden_boot.choose_boot_mode(
                    world, ha_version="1.21.11", loader="neoforge"
                ),
                "golden",
            )
            golden_boot.write_boot_session(
                world,
                mode="golden",
                loader="neoforge",
                minecraft_version="1.21.1",
                stock=True,
                proven=True,
            )
            haos_defaults._link_install(world, "neoforge", "1.21.1")
            self.assertEqual(
                golden_boot.choose_boot_mode(
                    world, ha_version="1.21.11", loader="neoforge"
                ),
                "attempt",
            )
            self.assertTrue(
                golden_boot.should_restage(world, loader="neoforge", version="1.21.11")
            )

    def test_minecraft_only_state_no_supervisor_golden_api(self) -> None:
        base = ROOT / "game-server-base"
        hits = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "golden_mods" in text or "golden.json" in text:
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])

    def test_restored_golden_crashloop_fails_healthz_contract(self) -> None:
        plugin = (MC / "games" / "game.yaml").read_text(encoding="utf-8")
        cfg = (MC / "config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("hold_on_crash_loop", plugin)
        self.assertIn("healthz", cfg)


if __name__ == "__main__":
    unittest.main()
