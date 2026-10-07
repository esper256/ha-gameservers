#!/usr/bin/env python3
"""Validate Home Assistant app config.yaml files against Supervisor rules.

Catches silent store failures where Supervisor skips an app during reload
because config validation failed (for example timeout > 300).
"""

from __future__ import annotations

import hashlib
import re
import struct
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RE_SLUG = re.compile(r"^(?!-)[a-z0-9_-]+(?<!-)$")
RE_WATCHDOG = re.compile(
    r"^(?:https?|\[PROTO:\w+\]|tcp):\/\/\[HOST\]:(\[PORT:\d+\]|\d+).*$"
)
ALLOWED_ARCH = {"aarch64", "amd64", "armhf", "armv7", "i386"}
CURRENT_ARCH = {"aarch64", "amd64"}
# Supervisor SCHEMA_APP_CONFIG: vol.Range(min=10, max=300)
TIMEOUT_MIN = 10
TIMEOUT_MAX = 300


def discover_configs(root: Path) -> list[Path]:
    configs: list[Path] = []
    for path in root.glob("**/config.yaml"):
        parts = path.parts
        if any(part.startswith(".") for part in parts):
            continue
        if "rootfs" in parts:
            continue
        configs.append(path)
    return sorted(configs)


def validate_config(path: Path) -> list[str]:
    errors: list[str] = []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return [f"{path}: root must be a mapping"]

    for key in ("name", "version", "slug", "description", "arch"):
        if key not in data:
            errors.append(f"{path}: missing required key '{key}'")

    slug = data.get("slug")
    if isinstance(slug, str) and not RE_SLUG.match(slug):
        errors.append(f"{path}: slug {slug!r} is not URI-friendly")

    arch = data.get("arch")
    if not isinstance(arch, list) or not arch:
        errors.append(f"{path}: arch must be a non-empty list")
    else:
        bad = [a for a in arch if a not in ALLOWED_ARCH]
        if bad:
            errors.append(f"{path}: unknown arch values: {bad}")
        deprecated = [a for a in arch if a not in CURRENT_ARCH]
        if deprecated:
            errors.append(
                f"{path}: deprecated arch values (recent HAOS may warn): {deprecated}"
            )

    if "timeout" in data:
        try:
            timeout = int(data["timeout"])
        except (TypeError, ValueError):
            errors.append(f"{path}: timeout must be an int")
        else:
            if timeout < TIMEOUT_MIN or timeout > TIMEOUT_MAX:
                errors.append(
                    f"{path}: timeout={timeout} outside Supervisor allowed "
                    f"range {TIMEOUT_MIN}-{TIMEOUT_MAX} (app would be hidden from store)"
                )

    watchdog = data.get("watchdog")
    if watchdog is not None and not RE_WATCHDOG.match(str(watchdog)):
        errors.append(f"{path}: watchdog URL does not match Supervisor pattern")

    options = data.get("options") or {}
    schema = data.get("schema") or {}
    if options and not isinstance(options, dict):
        errors.append(f"{path}: options must be a mapping")
    if schema not in (False, None) and not isinstance(schema, dict):
        errors.append(f"{path}: schema must be a mapping or false")
    if isinstance(options, dict) and isinstance(schema, dict):
        missing = sorted(set(options) - set(schema))
        if missing:
            errors.append(f"{path}: options missing from schema: {missing}")

    return errors


def _png_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


class HaAppConfigTests(unittest.TestCase):
    def test_game_server_base_is_not_an_ha_app(self) -> None:
        base_configs = [
            p for p in discover_configs(ROOT) if "game-server-base" in p.parts
        ]
        self.assertEqual(
            base_configs,
            [],
            "game-server-base must not contain config.yaml "
            f"(found: {base_configs})",
        )

    def test_installable_app_configs_pass_supervisor_rules(self) -> None:
        configs = [
            p for p in discover_configs(ROOT) if "game-server-base" not in p.parts
        ]
        self.assertTrue(configs, "expected at least one installable HA app config.yaml")
        errors: list[str] = []
        for path in configs:
            errors.extend(validate_config(path))
        self.assertEqual(errors, [], "\n".join(errors))

    def test_addon_versions_follow_supervisor_major_minor(self) -> None:
        """HA app version is supervisor major.minor plus a game-specific patch."""

        import sys

        sys.path.insert(0, str(ROOT / "game-server-base"))
        from game_server.version import SUPERVISOR_VERSION  # noqa: E402

        self.assertRegex(SUPERVISOR_VERSION, r"^\d+\.\d+$")
        prefix = SUPERVISOR_VERSION + "."
        configs = [
            p for p in discover_configs(ROOT) if "game-server-base" not in p.parts
        ]
        errors: list[str] = []
        for path in configs:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            version = str((data or {}).get("version") or "")
            parts = version.split(".")
            if not version.startswith(prefix) or len(parts) != 3:
                errors.append(
                    f"{path}: version {version!r} must be "
                    f"{SUPERVISOR_VERSION}.<game_patch> "
                    f"(e.g. {SUPERVISOR_VERSION}.0)"
                )
            elif not all(part.isdigit() for part in parts):
                errors.append(f"{path}: version {version!r} must be numeric")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_installable_apps_have_store_images(self) -> None:
        configs = [
            p for p in discover_configs(ROOT) if "game-server-base" not in p.parts
        ]
        errors: list[str] = []
        for path in configs:
            icon = path.parent / "icon.png"
            logo = path.parent / "logo.png"
            if not icon.is_file():
                errors.append(f"{path.parent}: missing icon.png (HA store tile)")
            else:
                size = _png_size(icon)
                if size is None:
                    errors.append(f"{icon}: not a PNG")
                elif size != (128, 128):
                    errors.append(
                        f"{icon}: expected 128x128 HA store tile (got {size[0]}x{size[1]})"
                    )
            if not logo.is_file():
                errors.append(f"{path.parent}: missing logo.png (HA Info header)")
            else:
                size = _png_size(logo)
                if size is None:
                    errors.append(f"{logo}: not a PNG")
                elif size != (250, 100):
                    errors.append(
                        f"{logo}: expected 250x100 HA Info header (got {size[0]}x{size[1]})"
                    )
        self.assertEqual(errors, [], "\n".join(errors))

    def test_store_icons_are_unique_per_game(self) -> None:
        """Copying another game folder leaves its icon.png behind; hashes must differ."""
        configs = [
            p for p in discover_configs(ROOT) if "game-server-base" not in p.parts
        ]
        hashes: dict[bytes, str] = {}
        errors: list[str] = []
        for path in configs:
            icon = path.parent / "icon.png"
            if not icon.is_file():
                continue
            digest = hashlib.sha256(icon.read_bytes()).digest()
            other = hashes.get(digest)
            if other:
                errors.append(
                    f"{path.parent.name} icon.png is identical to {other} "
                    "(replace leftover art from the folder you copied)"
                )
            else:
                hashes[digest] = path.parent.name
        self.assertEqual(errors, [], "\n".join(errors))

    def test_compose_addons_dockerignore_excludes_data(self) -> None:
        dockerfiles = sorted(ROOT.glob("*-dedicated-server/Dockerfile"))
        self.assertTrue(dockerfiles, "expected game add-on Dockerfiles")
        errors: list[str] = []
        for dockerfile in dockerfiles:
            ignore = dockerfile.parent / ".dockerignore"
            if not ignore.is_file():
                errors.append(f"{dockerfile.parent.name}: missing .dockerignore")
                continue
            lines = {
                line.strip()
                for line in ignore.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
            if "data/" not in lines and "data" not in lines:
                errors.append(
                    f"{ignore}: must exclude data/ so compose build context "
                    "does not upload the Steam/game install"
                )
        self.assertEqual(errors, [], "\n".join(errors))

    def test_game_yaml_env_options_are_identifiers(self) -> None:
        """A bad indent must not merge several env keys into one string."""

        plugins = sorted(ROOT.glob("*-dedicated-server/games/game.yaml"))
        self.assertTrue(plugins, "expected game plugins")
        ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        errors: list[str] = []
        for path in plugins:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = data.get("env_options")
            if raw is None:
                continue
            if not isinstance(raw, list):
                errors.append(f"{path}: env_options must be a list")
                continue
            for item in raw:
                if not isinstance(item, str) or ident.fullmatch(item) is None:
                    errors.append(
                        f"{path}: env_options entry {item!r} is not a plain identifier"
                    )
        self.assertEqual(errors, [], "\n".join(errors))

    def test_ingress_theme_accents_are_distinct(self) -> None:
        """Each game's Ingress accent should differ so store UIs don't look cloned."""
        plugins = sorted(ROOT.glob("*-dedicated-server/games/game.yaml"))
        self.assertTrue(plugins, "expected game plugins")
        accents: dict[str, str] = {}
        errors: list[str] = []
        for path in plugins:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            theme = data.get("ui_theme") or {}
            accent = str(theme.get("accent") or "").strip().lower()
            if not accent:
                errors.append(f"{path}: ui_theme.accent is required")
                continue
            other = accents.get(accent)
            if other:
                errors.append(
                    f"{path.parent.parent.name} accent {accent} matches {other}"
                )
            else:
                accents[accent] = path.parent.parent.name
        self.assertEqual(errors, [], "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
