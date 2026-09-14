"""Discover named worlds and optional plugin-declared create fields.

Game plugins supply globs and labels. This module does not interpret
loader names or other game identity.
"""

from __future__ import annotations

import glob as globmod
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .world_save import KIND_FILE, expand_world_path_template, infer_world_kind

LOG = logging.getLogger("game_server.world_catalog")

_FIELD_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CREATE_FIELD_KINDS = frozenset({"text", "select"})
_NAME_FROM = frozenset({"stem", "directory", "strip_suffix"})


@dataclass(frozen=True)
class WorldCatalogSpec:
    globs: list[str] = field(default_factory=list)
    name_from: str = "stem"
    suffix: str = ""
    caption_file: str = ""
    caption_json_path: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "WorldCatalogSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("world_catalog must be a mapping when provided")
        globs = [str(p) for p in (data.get("globs") or []) if str(p).strip()]
        single = str(data.get("glob") or "").strip()
        if single:
            globs.append(single)
        name_from = str(data.get("name_from") or "stem").strip().lower()
        if name_from not in _NAME_FROM:
            raise ValueError(
                f"Unsupported world_catalog.name_from {name_from!r}; "
                "expected stem, directory, or strip_suffix"
            )
        suffix = str(data.get("suffix") or "").strip()
        if name_from == "strip_suffix" and not suffix:
            raise ValueError("world_catalog.strip_suffix requires suffix")
        return cls(
            globs=globs,
            name_from=name_from,
            suffix=suffix,
            caption_file=str(data.get("caption_file") or "").strip(),
            caption_json_path=str(data.get("caption_json_path") or "").strip(),
        )


@dataclass(frozen=True)
class WorldCreateOption:
    value: str
    label: str


@dataclass(frozen=True)
class WorldCreateField:
    id: str
    kind: str
    label: str
    default: str = ""
    options: list[WorldCreateOption] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "default": self.default,
        }
        if self.options:
            payload["options"] = [
                {"value": item.value, "label": item.label} for item in self.options
            ]
        return payload


@dataclass(frozen=True)
class WorldCreateSpec:
    fields: list[WorldCreateField] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "WorldCreateSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("world_create must be a mapping when provided")
        raw_fields = data.get("fields") or []
        if not isinstance(raw_fields, list):
            raise ValueError("world_create.fields must be a list")
        fields = [_parse_create_field(item) for item in raw_fields]
        return cls(fields=fields)

    def field_by_id(self) -> dict[str, WorldCreateField]:
        return {item.id: item for item in self.fields}

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.fields]


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    path: str
    caption: str = ""
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "active": self.active,
        }
        if self.caption:
            payload["caption"] = self.caption
        return payload


def validate_world_name(name: str) -> str:
    text = str(name or "").strip()
    if not _WORLD_NAME_RE.fullmatch(text):
        raise ValueError(
            "World name must be 1–64 letters, digits, `.`, `_`, or `-`"
        )
    return text


def validate_create_fields(
    spec: WorldCreateSpec | None, raw: Mapping[str, Any] | None
) -> dict[str, str]:
    """Return sanitized extra field values. Unknown ids are rejected."""

    values = {
        str(key): str(value).strip()
        for key, value in dict(raw or {}).items()
        if str(key).strip()
    }
    if spec is None or not spec.fields:
        if values:
            raise ValueError("This game does not accept extra world-create fields")
        return {}
    known = spec.field_by_id()
    extra = sorted(set(values) - set(known))
    if extra:
        raise ValueError(f"Unknown world-create fields: {', '.join(extra)}")
    out: dict[str, str] = {}
    for field in spec.fields:
        chosen = values.get(field.id, field.default)
        if field.kind == "select":
            allowed = {item.value for item in field.options}
            if chosen not in allowed:
                raise ValueError(f"Invalid value for {field.id}")
        out[field.id] = chosen
    return out


def list_catalog_worlds(
    spec: WorldCatalogSpec | None,
    *,
    data_dir: str,
    options: Mapping[str, Any],
    active_name: str,
) -> list[CatalogEntry]:
    found: dict[str, CatalogEntry] = {}
    if spec is not None:
        for template in spec.globs:
            expanded = expand_world_path_template(
                template,
                data_dir=data_dir,
                world_name=active_name,
                options=options,
            )
            if not expanded:
                continue
            try:
                if any(ch in expanded for ch in "*?[]"):
                    matches = [Path(item) for item in sorted(globmod.glob(expanded))]
                else:
                    path = Path(expanded)
                    matches = [path] if path.exists() else []
            except OSError:
                LOG.warning("World catalog glob failed for %s", expanded)
                matches = []
            for match in matches:
                if match.name.startswith("."):
                    continue
                name = _entry_name(spec, match)
                if not name:
                    continue
                caption = _read_caption(spec, match)
                found[name] = CatalogEntry(
                    name=name,
                    path=str(match),
                    caption=caption,
                    active=name == active_name,
                )
    if active_name and active_name not in found:
        found[active_name] = CatalogEntry(
            name=active_name, path="", caption="", active=True
        )
    entries = list(found.values())
    entries.sort(key=lambda item: (not item.active, item.name.lower()))
    return entries


def write_world_create_payload(
    *,
    expected_path: str | None,
    fields: Mapping[str, str],
) -> Path | None:
    """Write opaque world_create.json next to/inside the new world artifact."""

    if not fields or not expected_path:
        return None
    target = Path(expected_path)
    kind = infer_world_kind(target)
    if kind == KIND_FILE:
        directory = target.parent
    else:
        directory = target
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "world_create.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(fields), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def expected_world_path(
    paths: list[str],
    *,
    data_dir: str,
    world_name: str,
    options: Mapping[str, Any],
) -> str | None:
    for template in paths:
        expanded = expand_world_path_template(
            template,
            data_dir=data_dir,
            world_name=world_name,
            options=options,
        )
        if expanded:
            return expanded
    return None


def _parse_create_field(data: Any) -> WorldCreateField:
    if not isinstance(data, dict):
        raise ValueError("world_create.fields entries must be mappings")
    field_id = str(data.get("id") or "").strip()
    if not _FIELD_ID_RE.fullmatch(field_id):
        raise ValueError(
            "world_create field id must be letters, digits, or `_` "
            f"(got {field_id!r})"
        )
    kind = str(data.get("kind") or "text").strip().lower()
    if kind not in _CREATE_FIELD_KINDS:
        raise ValueError(
            f"Unsupported world_create field kind {kind!r}; expected text or select"
        )
    label = str(data.get("label") or field_id).strip()
    default = str(data.get("default") or "").strip()
    options: list[WorldCreateOption] = []
    raw_options = data.get("options") or []
    if kind == "select":
        if not isinstance(raw_options, list) or not raw_options:
            raise ValueError(f"world_create field {field_id} select requires options")
        for item in raw_options:
            if isinstance(item, dict):
                value = str(item.get("value") or "").strip()
                opt_label = str(item.get("label") or value).strip()
            else:
                value = str(item).strip()
                opt_label = value
            if not value:
                raise ValueError(f"world_create field {field_id} has an empty option")
            options.append(WorldCreateOption(value=value, label=opt_label or value))
        if default and default not in {item.value for item in options}:
            raise ValueError(
                f"world_create field {field_id} default is not in options"
            )
        if not default:
            default = options[0].value
    return WorldCreateField(
        id=field_id, kind=kind, label=label, default=default, options=options
    )


def _entry_name(spec: WorldCatalogSpec, path: Path) -> str:
    if spec.name_from == "directory":
        return path.name
    if spec.name_from == "strip_suffix":
        name = path.name
        suffix = spec.suffix
        if suffix and name.endswith(suffix):
            return name[: -len(suffix)]
        return path.stem
    if path.is_dir():
        return path.name
    return path.stem


def _read_caption(spec: WorldCatalogSpec, match: Path) -> str:
    if not spec.caption_file:
        return ""
    if match.is_dir():
        sidecar = match / spec.caption_file
    else:
        sidecar = match.parent / spec.caption_file
    if not sidecar.is_file():
        return ""
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    key = spec.caption_json_path or "caption"
    value = raw.get(key)
    return str(value).strip() if value is not None else ""
