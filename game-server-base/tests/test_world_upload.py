#!/usr/bin/env python3
"""Tests for generic world upload restore (file vs directory kind)."""

from __future__ import annotations

import gzip
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.config import SupervisorConfig  # noqa: E402
from game_server.plugin import load_plugin  # noqa: E402
from game_server.supervisor import GameServerSupervisor  # noqa: E402
from game_server.world_save import (  # noqa: E402
    KIND_DIRECTORY,
    KIND_FILE,
    ActiveWorld,
    SCOPE_MISSING,
    SCOPE_NAMED_PATH,
    WorldUploadSpec,
    apply_world_upload,
    infer_world_kind,
    locate_active_world,
    replace_world_file,
    validate_world_upload,
    world_upload_accepts,
)

FIXTURE = ROOT / "tests" / "fixtures" / "example.game.yaml"


class WorldUploadTests(unittest.TestCase):
    def test_infer_kind_from_path_name_and_live_path(self) -> None:
        self.assertEqual(infer_world_kind("World.zip"), KIND_FILE)
        self.assertEqual(infer_world_kind("World"), KIND_DIRECTORY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "save.dat"
            file_path.write_bytes(b"abc")
            dir_path = root / "World"
            dir_path.mkdir()
            self.assertEqual(infer_world_kind(file_path), KIND_FILE)
            self.assertEqual(infer_world_kind(dir_path), KIND_DIRECTORY)

    def test_fixture_missing_prefers_zip_file_kind(self) -> None:
        plugin = load_plugin(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "world"
            data.mkdir()
            active = locate_active_world(
                plugin,
                {"world_name": "FamilyWorld", "data_dir": str(data)},
                data_dir=str(data),
            )
            self.assertEqual(active.scope, SCOPE_MISSING)
            self.assertTrue(str(active.path).endswith("FamilyWorld.zip"))
            self.assertEqual(active.kind, KIND_FILE)
            meta = world_upload_accepts(active)
            self.assertTrue(meta["uploadable"])
            self.assertEqual(meta["mode"], "replace_file")

    def test_apply_upload_replaces_file_world_as_is(self) -> None:
        """When the game save IS a zip file, the upload replaces that file."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            target = data / "saves" / "worlds" / "FamilyWorld.zip"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"OLD-WORLD-ZIP")
            # Alternate folder sibling that should be removed.
            sibling = data / "saves" / "worlds" / "FamilyWorld"
            sibling.mkdir()
            (sibling / "chunk.bin").write_bytes(b"old")

            upload = root / "incoming.zip"
            upload.write_bytes(b"NEW-WORLD-ZIP-BYTES")

            active = ActiveWorld(
                bytes=target.stat().st_size,
                path=str(target),
                label=target.name,
                scope=SCOPE_NAMED_PATH,
                sources=[str(target)],
                expected_paths=[str(target), str(sibling)],
                kind=KIND_FILE,
            )
            result = apply_world_upload(active, upload, data_dir=data)
            self.assertEqual(result["mode"], "replace_file")
            self.assertEqual(target.read_bytes(), b"NEW-WORLD-ZIP-BYTES")
            self.assertFalse(sibling.exists())

    def test_apply_upload_extracts_into_directory_world(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            target = data / "saves" / "worlds" / "FamilyWorld"
            target.mkdir(parents=True)
            (target / "old.bin").write_bytes(b"OLD")

            upload = root / "folder-world.zip"
            with zipfile.ZipFile(upload, "w") as zf:
                zf.writestr("FamilyWorld/level.dat", b"LEVEL")
                zf.writestr("FamilyWorld/region/a.bin", b"REGION")

            active = ActiveWorld(
                bytes=1,
                path=str(target),
                label="FamilyWorld",
                scope=SCOPE_NAMED_PATH,
                sources=[str(target)],
                expected_paths=[str(target)],
                kind=KIND_DIRECTORY,
            )
            result = apply_world_upload(active, upload, data_dir=data)
            self.assertEqual(result["mode"], "extract_zip_into_directory")
            self.assertFalse((target / "old.bin").exists())
            self.assertEqual((target / "level.dat").read_bytes(), b"LEVEL")
            self.assertEqual((target / "region" / "a.bin").read_bytes(), b"REGION")

    def test_directory_world_rejects_non_zip_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "World"
            target.mkdir()
            upload = root / "not-a-zip.bin"
            upload.write_bytes(b"nope")
            active = ActiveWorld(
                bytes=1,
                path=str(target),
                label="World",
                scope=SCOPE_NAMED_PATH,
                sources=[str(target)],
                expected_paths=[str(target)],
                kind=KIND_DIRECTORY,
            )
            with self.assertRaises(RuntimeError) as ctx:
                apply_world_upload(active, upload, data_dir=root)
            self.assertIn("folder world save", str(ctx.exception).lower())


def _gzip_bytes(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as fh:
        fh.write(payload)
    return buf.getvalue()


def _zip_bytes(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


_UPLOAD_SPEC = {
    "max_bytes": 128,
    "allowed_suffixes": [".sav"],
    "magic": "1f8b",
    "content": "gzip",
    "accept": ".sav,application/gzip",
    "hint": "Upload the single save file. A safety copy is taken first.",
    "reject": [
        {"suffix": ".bak", "error": "Rename the backup so it ends in .sav"},
        {"suffix": ".zip", "error": "Upload the single save file, not a zip"},
        {
            "magic": "504b",
            "error": "This is a zip archive. Upload the single save file.",
        },
    ],
    "errors": {
        "empty": "The uploaded file is empty.",
        "oversize": "That file is too large.",
        "suffix": "Upload a single .sav file.",
        "magic": "That file is not a gzip save.",
        "content": "That file is not a valid gzip save.",
        "damaged": "That file is incomplete or damaged.",
    },
}


def _spec() -> WorldUploadSpec:
    parsed = WorldUploadSpec.from_dict(_UPLOAD_SPEC)
    assert parsed is not None
    return parsed


class _Alive:
    def poll(self) -> None:
        return None


def _supervisor(tmp: Path, spec: WorldUploadSpec | None):
    plugin = load_plugin(FIXTURE)
    plugin.world_upload = spec
    world = tmp / "world"
    logs = tmp / "logs"
    game = tmp / "game"
    for path in (world, logs, game):
        path.mkdir()
    cfg = SupervisorConfig(
        drop_privileges=False,
        status_http_enabled=False,
        backup_enabled=False,
        ha_notifications=False,
        update_on_start=False,
        auto_update_interval_minutes=0,
        backup_min_source_bytes=1,
        min_free_disk_mb=1,
        crash_restart_delay_seconds=0,
        state_dir=str(tmp / "state"),
        install_dir=str(game),
        backup_dir=str(tmp / "backups"),
        steamcmd_dir=str(tmp / "steamcmd"),
        game_options={
            "data_dir": str(world),
            "logs_dir": str(logs),
            "world_name": "FamilyWorld",
        },
    )
    return GameServerSupervisor(plugin, cfg)


class WorldUploadValidationTests(unittest.TestCase):
    def test_spec_rejects_unknown_content_and_mixed_reject_rule(self) -> None:
        with self.assertRaises(ValueError):
            WorldUploadSpec.from_dict({"content": "custom"})
        with self.assertRaises(ValueError):
            WorldUploadSpec.from_dict(
                {"reject": [{"suffix": ".bak", "magic": "504b", "error": "no"}]}
            )

    def test_gzip_save_is_accepted_and_hint_overrides_ui(self) -> None:
        spec = _spec()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / "slot.SAV"
            upload.write_bytes(_gzip_bytes(b"tiles"))
            self.assertIsNone(validate_world_upload(spec, upload))
            active = ActiveWorld(
                bytes=1,
                path=str(root / "world.sav"),
                label="world.sav",
                scope=SCOPE_NAMED_PATH,
                sources=[],
                expected_paths=[],
                kind=KIND_FILE,
            )
            meta = world_upload_accepts(active, spec)
            self.assertEqual(meta["hint"], spec.hint)
            self.assertEqual(meta["accept"], spec.accept)

    def test_rejects_empty_oversize_suffix_zip_and_damaged_gzip(self) -> None:
        spec = _spec()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "empty.sav": (b"", "empty"),
                "big.sav": (_gzip_bytes(os.urandom(200)), "too large"),
                "notes.txt": (_gzip_bytes(b"tiles"), ".sav"),
                "bundle.zip": (_zip_bytes("a.sav", b"nested"), "not a zip"),
                "slot.sav.bak": (_gzip_bytes(b"tiles"), "rename the backup"),
                "slot.sav": (_zip_bytes("a.sav", b"nested"), "zip archive"),
                "plain.sav": (b"this is not gzip", "not a gzip"),
                "cut.sav": (_gzip_bytes(b"tiles-and-more")[:12], "incomplete"),
            }
            for name, (payload, needle) in cases.items():
                path = root / name
                path.write_bytes(payload)
                error = validate_world_upload(spec, path)
                self.assertIsNotNone(error, name)
                assert error is not None
                self.assertIn(needle, error.lower(), name)

    def test_replace_file_adopts_owner_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "saves"
            parent.mkdir()
            target = parent / "world.sav"
            target.write_bytes(b"OLD")
            upload = root / "incoming.sav"
            upload.write_bytes(b"NEW-SAVE")
            expected_uid = target.stat().st_uid
            expected_gid = target.stat().st_gid
            if os.geteuid() == 0:
                os.chown(target, 65534, 65534)
                expected_uid, expected_gid = 65534, 65534
            calls: list[tuple[int, int]] = []
            real_chown = os.chown

            def spy(path, uid, gid, *args, **kwargs):
                calls.append((uid, gid))
                return real_chown(path, uid, gid, *args, **kwargs)

            old_umask = os.umask(0o077)
            try:
                with patch("game_server.world_save.os.chown", spy):
                    replace_world_file(upload, target)
            finally:
                os.umask(old_umask)
            self.assertEqual(target.read_bytes(), b"NEW-SAVE")
            self.assertIn((expected_uid, expected_gid), calls)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            if os.geteuid() == 0:
                self.assertEqual(target.stat().st_uid, 65534)
                self.assertEqual(target.stat().st_gid, 65534)

    def test_declared_rules_reject_before_the_server_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root, _spec())
            live = root / "world" / "saves" / "worlds" / "FamilyWorld.zip"
            live.parent.mkdir(parents=True)
            live.write_bytes(b"LIVE-WORLD")
            upload = root / "bundle.zip"
            upload.write_bytes(_zip_bytes("a.sav", b"nested"))
            stops: list[str] = []
            supervisor.process.proc = _Alive()  # type: ignore[assignment]
            supervisor.process.stop = lambda timeout=None: stops.append("stop")  # type: ignore[method-assign]
            result = supervisor.request_world_upload(upload)
            self.assertFalse(result["ok"])
            self.assertIn("not a zip", result["error"].lower())
            self.assertEqual(stops, [])
            self.assertIsNone(supervisor._upload_pending)
            self.assertIsNone(supervisor._activity)
            self.assertIsNone(supervisor.last_restore_error)
            self.assertTrue(supervisor.process.running)
            self.assertFalse(supervisor.process.intentional_stop)
            self.assertEqual(live.read_bytes(), b"LIVE-WORLD")
            with self.assertRaises(RuntimeError):
                supervisor._apply_world_upload(upload)
            self.assertEqual(stops, [])
            self.assertEqual(live.read_bytes(), b"LIVE-WORLD")
            self.assertTrue(supervisor.process.running)

    def test_without_rules_a_non_gzip_upload_still_schedules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = _supervisor(root, None)
            upload = root / "incoming.bin"
            upload.write_bytes(b"not-a-gzip")
            stops: list[str] = []
            supervisor.process.stop = lambda timeout=None: stops.append("stop")  # type: ignore[method-assign]
            result = supervisor.request_world_upload(upload)
            self.assertTrue(result["ok"])
            self.assertEqual(stops, [])
            self.assertEqual(supervisor._upload_pending, upload)


if __name__ == "__main__":
    unittest.main()
