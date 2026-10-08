"""Detect a world's mod loader from jars in its upload folder.

Fabric, NeoForge, and vanilla are decided here. Quilt-only and Forge jars
are refused. Library jars and unreadable metadata do not choose a loader.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FINGERPRINT_NAME = "automodpack-fingerprint.txt"
INJECTED_MOD_IDS = frozenset({"automodpack", "fabric-api", "fabricloader"})
_INJECTED_PREFIXES = ("automodpack", "fabric-api")

_MODID_TOML_RE = re.compile(
    r'^\s*(?:modId|modid)\s*=\s*"([^"]+)"',
    re.IGNORECASE | re.MULTILINE,
)
_ENV_TOML_RE = re.compile(
    r'^\s*(?:side|displayTest)\s*=\s*"([^"]+)"',
    re.IGNORECASE | re.MULTILINE,
)
_MAVEN_RANGE_RE = re.compile(
    r"(?P<lbrk>[\[\(])\s*(?P<lo>[^,\s\[\]\(\)]*)\s*,\s*(?P<hi>[^,\s\[\]\(\)]*)\s*(?P<hbrk>[\]\)])"
    r"|"
    r"(?P<obrk>[\[\(])\s*(?P<one>[^,\s\[\]\(\)]+)\s*(?P<ebrk>[\]\)])"
)


@dataclass
class ModHit:
    """One upload jar. ``loader`` is fabric, neoforge, forge, quilt, mixed, library, or unknown."""

    name: str
    loader: str
    mod_id: str = ""
    environment: str = "*"
    version: str = ""
    display_name: str = ""
    minecraft_spec: str = ""
    specs: tuple[str, ...] = ()
    detail: str = ""


@dataclass
class ScanResult:
    loader: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hits: list[ModHit] = field(default_factory=list)


def is_injected_filename(name: str) -> bool:
    """True for jars the add-on itself places in the upload folder."""

    stem = Path(name).stem.lower()
    if stem.startswith(_INJECTED_PREFIXES):
        return True
    return stem in INJECTED_MOD_IDS


def _read_json(zf: zipfile.ZipFile, name: str) -> dict[str, Any] | None:
    try:
        with zf.open(name) as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        with zf.open(name) as handle:
            return handle.read().decode("utf-8")
    except (KeyError, UnicodeDecodeError, OSError):
        return None


def toml_dep_ranges(toml: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"\[\[dependencies", toml, flags=re.IGNORECASE):
        mod = re.search(r'modId\s*=\s*"([^"]+)"', chunk, re.IGNORECASE)
        rng = re.search(r'versionRange\s*=\s*"([^"]+)"', chunk, re.IGNORECASE)
        if mod and rng:
            out[mod.group(1).strip().lower()] = rng.group(1).strip()
    return out


def _fabric_specs(fabric: dict[str, Any]) -> tuple[str, ...]:
    depends = fabric.get("depends")
    if not isinstance(depends, dict) or "minecraft" not in depends:
        return ()
    raw = depends.get("minecraft")
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    text = str(raw or "").strip()
    return (text,) if text else ()


def _toml_mod_id(toml: str) -> str:
    match = _MODID_TOML_RE.search(toml)
    return match.group(1).strip() if match else ""


def _toml_environment(toml: str) -> str:
    match = _ENV_TOML_RE.search(toml)
    return match.group(1).strip() if match else "*"


def classify_jar(path: Path) -> ModHit:
    """Classify one jar. Unreadable metadata is ``unknown`` (does not choose a loader)."""

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            fabric = _read_json(zf, "fabric.mod.json") if "fabric.mod.json" in names else None
            quilt = "quilt.mod.json" in names
            neo_toml = (
                _read_text(zf, "META-INF/neoforge.mods.toml")
                if "META-INF/neoforge.mods.toml" in names
                else None
            )
            mods_toml = (
                _read_text(zf, "META-INF/mods.toml")
                if "META-INF/mods.toml" in names
                else None
            )
    except (zipfile.BadZipFile, OSError) as exc:
        return ModHit(
            name=path.name,
            loader="unknown",
            detail=f"unreadable jar ({exc}); it will not choose the loader",
        )

    neo_hit = False
    forge_hit = False
    ambiguous = False
    neo_id = ""
    neo_env = "*"
    neo_spec = ""
    if neo_toml:
        neo_hit = True
        deps = toml_dep_ranges(neo_toml)
        neo_id = _toml_mod_id(neo_toml)
        neo_env = _toml_environment(neo_toml)
        neo_spec = deps.get("minecraft") or ""
    elif mods_toml:
        deps = toml_dep_ranges(mods_toml)
        neo_id = _toml_mod_id(mods_toml)
        neo_env = _toml_environment(mods_toml)
        neo_spec = deps.get("minecraft") or ""
        if "neoforge" in deps:
            neo_hit = True
        elif "forge" in deps:
            forge_hit = True
        else:
            ambiguous = True

    if fabric and (neo_hit or forge_hit):
        mod_id = str(fabric.get("id") or neo_id or "").strip()
        return ModHit(
            name=path.name,
            loader="mixed",
            mod_id=mod_id,
            display_name=str(fabric.get("name") or mod_id),
            detail="contains both Fabric and NeoForge metadata",
        )

    if fabric:
        mod_id = str(fabric.get("id") or "").strip()
        specs = _fabric_specs(fabric)
        return ModHit(
            name=path.name,
            loader="fabric",
            mod_id=mod_id,
            environment=str(fabric.get("environment") or "*").strip() or "*",
            version=str(fabric.get("version") or ""),
            display_name=str(fabric.get("name") or mod_id),
            minecraft_spec=", ".join(specs),
            specs=specs,
        )

    if quilt:
        return ModHit(
            name=path.name,
            loader="quilt",
            detail="Quilt mod (quilt.mod.json) without Fabric metadata",
        )

    if neo_hit:
        specs = (neo_spec,) if neo_spec else ()
        return ModHit(
            name=path.name,
            loader="neoforge",
            mod_id=neo_id,
            environment=neo_env or "*",
            display_name=neo_id,
            minecraft_spec=neo_spec,
            specs=specs,
        )

    if forge_hit:
        return ModHit(
            name=path.name,
            loader="forge",
            mod_id=neo_id,
            environment=neo_env or "*",
            display_name=neo_id,
            minecraft_spec=neo_spec,
            detail="Forge mod (META-INF/mods.toml depends on forge)",
        )

    if ambiguous:
        return ModHit(
            name=path.name,
            loader="unknown",
            mod_id=neo_id,
            minecraft_spec=neo_spec,
            detail=(
                "META-INF/mods.toml does not say Forge or NeoForge; "
                "it will not choose the loader"
            ),
        )

    return ModHit(
        name=path.name,
        loader="library",
        detail="no Fabric or NeoForge metadata; it will not choose the loader",
    )


def _version_parts(text: str) -> tuple[int, ...] | None:
    raw = (text or "").strip()
    if not raw or not re.fullmatch(r"\d+(?:\.\d+)*", raw):
        return None
    return tuple(int(part) for part in raw.split("."))


def _cmp_versions(left: str, right: str) -> int | None:
    a = _version_parts(left)
    b = _version_parts(right)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _cmp_op(op: str, world: str, bound: str) -> bool | None:
    cmp = _cmp_versions(world, bound)
    if cmp is None:
        return None
    if op == ">=":
        return cmp >= 0
    if op == "<=":
        return cmp <= 0
    if op == ">":
        return cmp > 0
    if op == "<":
        return cmp < 0
    if op == "=":
        return cmp == 0
    return None


def _tilde_matches(ver: str, world: str) -> bool | None:
    parts = _version_parts(ver)
    if not parts:
        return None
    lower = ".".join(str(part) for part in parts)
    if len(parts) == 1:
        upper = str(parts[0] + 1)
    else:
        upper = f"{parts[0]}.{parts[1] + 1}"
    low = _cmp_op(">=", world, lower)
    high = _cmp_op("<", world, upper)
    if low is None or high is None:
        return None
    return low and high


def _wildcard_matches(token: str, world: str) -> bool | None:
    pieces = token.lower().replace("*", "x").split(".")
    world_parts = world.split(".")
    for index, piece in enumerate(pieces):
        if piece in {"x", "*"}:
            continue
        if not piece.isdigit():
            return None
        if index >= len(world_parts):
            if int(piece) != 0:
                return False
            continue
        if not world_parts[index].isdigit():
            return None
        if int(world_parts[index]) != int(piece):
            return False
    return True


def _fabric_token(token: str, world: str) -> bool | None:
    if token in {"*", "x", "X"}:
        return True
    if token.startswith("^"):
        return None
    if token.startswith("~"):
        return _tilde_matches(token[1:], world)
    for op in (">=", "<=", ">", "<", "="):
        if token.startswith(op):
            return _cmp_op(op, world, token[len(op) :])
    if re.search(r"[xX*]", token):
        return _wildcard_matches(token, world)
    if _version_parts(token):
        return _cmp_op("=", world, token)
    return None


def fabric_predicate_matches(spec: str, world: str) -> bool | None:
    """True/False if the predicate parses, None if it should not block."""

    text = (spec or "").strip()
    if not text or text == "*":
        return True
    intervals = re.split(r"\s*\|\|\s*", text)
    saw = False
    for interval in intervals:
        tokens = interval.split()
        if not tokens:
            return None
        matched = True
        for token in tokens:
            result = _fabric_token(token, world)
            if result is None:
                return None
            saw = True
            matched = matched and result
        if matched and saw:
            return True
    return False if saw else None


def _one_maven_range(match: re.Match[str], world: str) -> bool | None:
    if match.group("one"):
        if match.group("obrk") == "[" and match.group("ebrk") == "]":
            return _cmp_op("=", world, match.group("one"))
        return None
    lo = match.group("lo") or ""
    hi = match.group("hi") or ""
    if lo:
        cmp = _cmp_versions(world, lo)
        if cmp is None:
            return None
        if cmp < 0 or (cmp == 0 and match.group("lbrk") == "("):
            return False
    if hi:
        cmp = _cmp_versions(world, hi)
        if cmp is None:
            return None
        if cmp > 0 or (cmp == 0 and match.group("hbrk") == ")"):
            return False
    if not lo and not hi:
        return None
    return True


def maven_range_matches(spec: str, world: str) -> bool | None:
    """Maven versionRange. None means unparseable (do not block)."""

    text = (spec or "").strip()
    if not text:
        return None
    matches = list(_MAVEN_RANGE_RE.finditer(text))
    if not matches:
        if _version_parts(text):
            return _cmp_op("=", world, text)
        return None
    leftover = text
    ok = False
    for match in matches:
        result = _one_maven_range(match, world)
        if result is None:
            return None
        ok = ok or result
        leftover = leftover.replace(match.group(0), "", 1)
    if leftover.replace(",", "").strip():
        return None
    return ok


def requirement_status(hit: ModHit, world_version: str) -> str:
    """``missing``, ``unknown``, ``ok``, or ``incompatible``."""

    world = (world_version or "").strip()
    if not hit.specs or not world:
        return "missing"
    states: list[str] = []
    for spec in hit.specs:
        if not str(spec).strip():
            continue
        if hit.loader == "neoforge":
            result = maven_range_matches(spec, world)
        else:
            result = fabric_predicate_matches(spec, world)
        if result is None:
            states.append("unknown")
        elif result:
            states.append("ok")
        else:
            states.append("incompatible")
    if not states:
        return "missing"
    if "incompatible" in states:
        return "incompatible"
    if "unknown" in states:
        return "unknown"
    return "ok"


def scan_mods(folder: Path, minecraft_version: str) -> ScanResult:
    """Choose vanilla, fabric, or neoforge. Errors refuse the boot."""

    result = ScanResult(loader="vanilla")
    if not folder.is_dir():
        return result
    fabric_jars: list[str] = []
    neo_jars: list[str] = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") or name.lower().endswith(".partial"):
            continue
        if name.lower() == FINGERPRINT_NAME:
            continue
        if path.suffix.lower() != ".jar":
            continue
        if is_injected_filename(name):
            continue
        hit = classify_jar(path)
        if hit.mod_id.lower() in INJECTED_MOD_IDS:
            continue
        result.hits.append(hit)
        if hit.loader == "unknown":
            result.warnings.append(f"{name}: {hit.detail}")
            continue
        if hit.loader == "library":
            result.warnings.append(f"{name}: {hit.detail}")
            continue
        if hit.loader == "forge":
            result.errors.append(
                f"Refusing to start: {name} is a Forge mod (META-INF/mods.toml). "
                "This server runs vanilla, Fabric, or NeoForge, not Forge."
            )
            continue
        if hit.loader == "quilt":
            result.errors.append(
                f"Refusing to start: {name} is a Quilt mod (quilt.mod.json) without "
                "Fabric metadata. Quilt is not supported."
            )
            continue
        if hit.loader == "mixed":
            result.errors.append(
                f"Refusing to start: {name} contains both Fabric and NeoForge metadata."
            )
            continue
        if hit.loader == "fabric":
            fabric_jars.append(name)
        elif hit.loader == "neoforge":
            neo_jars.append(name)
        status = requirement_status(hit, minecraft_version)
        if status == "incompatible":
            mod = hit.mod_id or hit.display_name or "mod"
            shown = hit.minecraft_spec or ", ".join(hit.specs)
            result.errors.append(
                f"Refusing to start: {name} ({mod}) does not support Minecraft "
                f"{minecraft_version} (supported range: {shown})."
            )
        elif status == "unknown":
            result.warnings.append(
                f"{name}: could not parse Minecraft requirement "
                f"{hit.minecraft_spec!r}; not blocking"
            )
    if fabric_jars and neo_jars:
        result.errors.append(
            "Refusing to start: this world has both Fabric and NeoForge mods. "
            f"Fabric: {', '.join(fabric_jars)}. "
            f"NeoForge: {', '.join(neo_jars)}."
        )
    if result.errors:
        result.loader = ""
    elif fabric_jars:
        result.loader = "fabric"
    elif neo_jars:
        result.loader = "neoforge"
    else:
        result.loader = "vanilla"
    return result
