"""Locate the active world save and backup roots from the game plugin.

Happy path: the plugin declares ``world_save.paths`` templates (named_path).
Backups prefer that same active-world artifact and its ``kind`` (copy a
single-file save as-is; zip a folder save). Explicit ``backup_paths`` remain
the fallback when no named world exists yet, and for restoring legacy
``.tar.gz`` snapshots of those roots.

Games must declare paths — there is no cross-game path guessing.
"""

from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .disk import path_total_bytes

LOG = logging.getLogger("game_server.world_save")

_TEMPLATE_KEY_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Strategies the plugin may declare.
STRATEGY_NAMED_PATH = "named_path"
STRATEGY_BACKUP_SOURCES = "backup_sources"

# Result scopes returned to status/UI.
SCOPE_NAMED_PATH = "named_path"
SCOPE_BACKUP_SOURCES = "backup_sources"
SCOPE_MISSING = "missing"

# How the active world artifact is stored on disk (upload + by-kind backups).
KIND_FILE = "file"
KIND_DIRECTORY = "directory"
KIND_UNKNOWN = "unknown"

# Replaced world files are owner-writable. New parent directories are traversable
# by the owner. Matches a typical non-root game user rewriting its own save.
_WORLD_FILE_MODE = 0o644
_WORLD_DIR_MODE = 0o755
# Discard inflated bytes past this so a tiny gzip cannot expand without bound.
_GZIP_UNCOMPRESSED_CAP = 8 * 1024 * 1024 * 1024
_GZIP_READ_CHUNK = 1024 * 1024

_UPLOAD_ERROR_KEYS = ("empty", "oversize", "suffix", "magic", "content", "damaged")
_DEFAULT_UPLOAD_ERRORS = {
    "empty": "uploaded world file is missing or empty",
    "oversize": "uploaded world file is too large",
    "suffix": "this file type is not accepted for world upload",
    "magic": "this file is not an accepted world save",
    "content": "this file is not a valid world save",
    "damaged": "this world file looks incomplete or damaged",
}


@dataclass(frozen=True)
class WorldSaveSpec:
    """Plugin-owned description of how to find the active world save."""

    strategy: str = STRATEGY_NAMED_PATH
    # Path templates expanded with {data_dir}, {world_name}, and option keys.
    paths: list[str] = field(default_factory=list)
    world_name_option: str = "world_name"

    @classmethod
    def from_dict(cls, data: Any) -> "WorldSaveSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("world_save must be a mapping when provided")
        strategy = str(data.get("strategy") or STRATEGY_NAMED_PATH).strip().lower()
        if strategy == "heuristic":
            raise ValueError(
                "world_save.strategy 'heuristic' was removed; declare "
                "world_save.paths templates (named_path) instead"
            )
        if strategy not in {STRATEGY_NAMED_PATH, STRATEGY_BACKUP_SOURCES}:
            raise ValueError(
                f"Unsupported world_save.strategy {strategy!r}; "
                f"expected named_path or backup_sources"
            )
        if data.get("allow_heuristic_fallback"):
            raise ValueError(
                "world_save.allow_heuristic_fallback was removed; declare "
                "world_save.paths templates instead"
            )
        paths = [str(p) for p in (data.get("paths") or []) if str(p).strip()]
        return cls(
            strategy=strategy,
            paths=paths,
            world_name_option=str(
                data.get("world_name_option") or "world_name"
            ).strip()
            or "world_name",
        )


@dataclass(frozen=True)
class WorldUploadReject:
    """One plugin-supplied reason to refuse an upload before it is applied."""

    suffix: str = ""
    magic: bytes = b""
    error: str = ""


@dataclass(frozen=True)
class WorldUploadSpec:
    """Declarative checks for an HTTP world upload. Game nouns stay in the plugin.

    ``content: gzip`` checks the gzip header and inflates the member in chunks
    (output discarded). ``reject`` entries supply the message for a suffix or
    a magic prefix. Nothing here names a game.
    """

    allowed_suffixes: tuple[str, ...] = ()
    reject: tuple[WorldUploadReject, ...] = ()
    magic: bytes = b""
    content: str = ""
    max_bytes: int = 0
    hint: str = ""
    accept: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    def message(self, key: str) -> str:
        text = (self.errors.get(key) or "").strip()
        if text:
            return text
        return _DEFAULT_UPLOAD_ERRORS[key]

    @classmethod
    def from_dict(cls, data: Any) -> "WorldUploadSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("world_upload must be a mapping when provided")
        content = str(data.get("content") or "").strip().lower()
        if content not in {"", "gzip"}:
            raise ValueError(
                f"Unsupported world_upload.content {content!r}; expected gzip or omit"
            )
        try:
            max_bytes = int(data.get("max_bytes") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("world_upload.max_bytes must be an integer") from exc
        if max_bytes < 0:
            raise ValueError("world_upload.max_bytes must be >= 0")
        suffixes = tuple(
            _normalize_upload_suffix(item)
            for item in (data.get("allowed_suffixes") or [])
        )
        rules: list[WorldUploadReject] = []
        for item in data.get("reject") or []:
            if not isinstance(item, dict):
                raise ValueError("world_upload.reject entries must be mappings")
            error = str(item.get("error") or "").strip()
            if not error:
                raise ValueError("world_upload.reject entries need an error message")
            suffix = str(item.get("suffix") or "").strip()
            magic_raw = item.get("magic")
            has_magic = magic_raw is not None and str(magic_raw).strip() != ""
            if bool(suffix) == bool(has_magic):
                raise ValueError(
                    "world_upload.reject entry needs exactly one of suffix or magic"
                )
            rules.append(
                WorldUploadReject(
                    suffix=_normalize_upload_suffix(suffix) if suffix else "",
                    magic=_parse_hex_magic(magic_raw) if has_magic else b"",
                    error=error,
                )
            )
        raw_errors = data.get("errors") or {}
        if not isinstance(raw_errors, dict):
            raise ValueError("world_upload.errors must be a mapping")
        errors: dict[str, str] = {}
        for key, value in raw_errors.items():
            name = str(key).strip()
            if name not in _UPLOAD_ERROR_KEYS:
                raise ValueError(
                    f"Unsupported world_upload.errors key {name!r}; "
                    f"expected one of {', '.join(_UPLOAD_ERROR_KEYS)}"
                )
            text = str(value or "").strip()
            if text:
                errors[name] = text
        magic_raw = data.get("magic")
        magic = (
            _parse_hex_magic(magic_raw)
            if magic_raw is not None and str(magic_raw).strip() != ""
            else b""
        )
        return cls(
            allowed_suffixes=suffixes,
            reject=tuple(rules),
            magic=magic,
            content=content,
            max_bytes=max_bytes,
            hint=str(data.get("hint") or "").strip(),
            accept=str(data.get("accept") or "").strip(),
            errors=errors,
        )


def _normalize_upload_suffix(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith(".") or text in {".", ".."} or any(
        ch in text for ch in "/\\ \t"
    ):
        raise ValueError(
            f"world_upload suffix {value!r} must be a file extension starting with '.'"
        )
    return text


def _parse_hex_magic(value: Any) -> bytes:
    raw = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    if len(raw) < 2 or len(raw) % 2 != 0 or len(raw) > 64:
        raise ValueError(
            f"world_upload magic {value!r} must be an even number of hex digits"
        )
    return bytes.fromhex(raw)


@dataclass(frozen=True)
class ActiveWorld:
    """Resolved active world artifact (for size UI) — not the backup root list."""

    bytes: int
    path: str | None
    label: str | None
    scope: str
    sources: list[str] = field(default_factory=list)
    expected_paths: list[str] = field(default_factory=list)
    # file | directory | unknown — from live path, else from path naming
    # (suffix ⇒ file, bare name ⇒ directory). Drives upload restore and
    # by-kind backups (copy file as-is vs zip folder).
    kind: str = KIND_UNKNOWN

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "bytes": self.bytes,
            "path": self.path,
            "label": self.label,
            "scope": self.scope,
            "sources": list(self.sources),
            "expected_paths": list(self.expected_paths),
            "kind": self.kind,
        }
        return payload


def effective_world_kind(active: ActiveWorld) -> str:
    """Resolve file/directory kind from ActiveWorld, falling back to path naming."""

    if active.kind in {KIND_FILE, KIND_DIRECTORY}:
        return active.kind
    return infer_world_kind(active.path)


def infer_world_kind(path: str | Path | None) -> str:
    """Infer file vs directory from an existing path or a configured path name.

    Games declare templates like ``{name}.zip`` (single-file save) or ``{name}``
    (folder save). Upload restore and by-kind backups use this — not the
    archive's extension alone.
    """

    if path is None or str(path).strip() == "":
        return KIND_UNKNOWN
    p = Path(path)
    try:
        if p.is_file():
            return KIND_FILE
        if p.is_dir():
            return KIND_DIRECTORY
    except OSError:
        pass
    # Missing / not created yet: basename with a suffix ⇒ file artifact.
    name = p.name
    if not name or name in {".", ".."}:
        return KIND_UNKNOWN
    if p.suffix:
        return KIND_FILE
    return KIND_DIRECTORY


def world_upload_accepts(
    active: ActiveWorld,
    upload_spec: WorldUploadSpec | None = None,
) -> dict[str, object]:
    """UI hints for whether / how an HTTP world upload can be applied.

    ``upload_spec.hint`` / ``accept`` override the generic file-kind text when
    the plugin declares them. The override is display-only; checks live in
    ``validate_world_upload``.
    """

    kind = active.kind if active.kind in {KIND_FILE, KIND_DIRECTORY} else infer_world_kind(
        active.path
    )
    uploadable = (
        kind in {KIND_FILE, KIND_DIRECTORY}
        and active.scope in {SCOPE_NAMED_PATH, SCOPE_MISSING}
        and bool(active.path)
    )
    if kind == KIND_DIRECTORY:
        accept = ".zip,application/zip"
        mode = "extract_zip_into_directory"
        hint = "Upload a .zip of the world folder contents (extracted into the save directory)."
    elif kind == KIND_FILE:
        suffix = Path(active.path or "").suffix.lower()
        if suffix == ".zip":
            accept = ".zip,application/zip"
            mode = "replace_file"
            hint = "Upload the world save file (this game stores the world as a single .zip)."
        else:
            accept = f"{suffix},application/octet-stream" if suffix else "*/*"
            mode = "replace_file"
            hint = "Upload the world save file (replaces the configured save file as-is)."
    else:
        accept = ""
        mode = "unavailable"
        hint = "World upload needs a named file or folder save path from the game plugin."
        uploadable = False
    if uploadable and upload_spec is not None:
        if upload_spec.hint:
            hint = upload_spec.hint
        if upload_spec.accept:
            accept = upload_spec.accept
    return {
        "uploadable": uploadable,
        "kind": kind,
        "mode": mode,
        "accept": accept,
        "hint": hint,
    }


def validate_world_upload(
    spec: WorldUploadSpec,
    upload_path: str | Path,
    *,
    filename: str | None = None,
) -> str | None:
    """Return a plugin error message, or None when the upload may be applied.

    Reads the file in small chunks. Does not modify ``upload_path`` or any
    live world, and does not stop a game process. Call this before either.
    """

    path = Path(upload_path)
    name = Path(filename).name if filename else path.name
    lower_name = name.lower()
    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        size = 0
    if size < 1:
        return spec.message("empty")
    if spec.max_bytes and size > spec.max_bytes:
        return spec.message("oversize")
    for rule in spec.reject:
        if rule.suffix and lower_name.endswith(rule.suffix):
            return rule.error
    if spec.allowed_suffixes and not any(
        lower_name.endswith(suffix) for suffix in spec.allowed_suffixes
    ):
        return spec.message("suffix")

    need = 16
    if spec.magic:
        need = max(need, len(spec.magic))
    for rule in spec.reject:
        if rule.magic:
            need = max(need, len(rule.magic))
    try:
        with path.open("rb") as fh:
            prefix = fh.read(need)
    except OSError:
        return spec.message("empty")
    for rule in spec.reject:
        if rule.magic and prefix.startswith(rule.magic):
            return rule.error
    if spec.magic and not prefix.startswith(spec.magic):
        return spec.message("magic")
    if spec.content == "gzip":
        verdict = _gzip_member_verdict(path)
        if verdict == "ok":
            return None
        if verdict == "stream":
            return spec.message("damaged")
        return spec.message("content")
    return None


def _gzip_member_verdict(path: Path) -> str:
    """Return ``ok``, ``header``, or ``stream`` without holding the inflate.

    ``header`` means the gzip header itself is incomplete or illegal.
    ``stream`` means the header parsed but the deflate member did not
    (truncated, bad CRC, or inflated past the cap).
    """

    if not _gzip_header_valid(path):
        return "header"
    total = 0
    try:
        with gzip.open(path, "rb") as fh:
            while True:
                chunk = fh.read(_GZIP_READ_CHUNK)
                if not chunk:
                    return "ok"
                total += len(chunk)
                if total > _GZIP_UNCOMPRESSED_CAP:
                    return "stream"
    except (EOFError, zlib.error, OSError):
        return "stream"


def _gzip_header_valid(path: Path) -> bool:
    """True when the first member has a legal gzip header (not the whole file)."""

    try:
        with path.open("rb") as fh:
            hdr = fh.read(10)
            if len(hdr) < 10 or hdr[0:2] != b"\x1f\x8b" or hdr[2] != 8:
                return False
            flags = hdr[3]
            if flags & 0xE0:
                return False
            if flags & 0x04:
                raw_len = fh.read(2)
                if len(raw_len) < 2:
                    return False
                extra_len = int.from_bytes(raw_len, "little")
                if len(fh.read(extra_len)) != extra_len:
                    return False
            if flags & 0x08 and not _skip_gzip_cstr(fh):
                return False
            if flags & 0x10 and not _skip_gzip_cstr(fh):
                return False
            if flags & 0x02 and len(fh.read(2)) != 2:
                return False
            return True
    except OSError:
        return False


def _skip_gzip_cstr(fh: Any, limit: int = 4096) -> bool:
    seen = 0
    while seen < limit:
        chunk = fh.read(1)
        if not chunk:
            return False
        seen += 1
        if chunk == b"\0":
            return True
    return False


def backup_sources_for(plugin: Any, data_dir: str | None = None) -> list[str]:
    """Explicit backup roots from the plugin (no guessing)."""

    root = str(data_dir or getattr(plugin, "data_dir", "") or "/data/world")
    configured = list(getattr(plugin, "backup_paths", None) or [])
    if configured:
        return [str(p) for p in configured]
    return [root]


def locate_active_world(
    plugin: Any,
    game_options: Mapping[str, Any] | None = None,
    *,
    data_dir: str | None = None,
) -> ActiveWorld:
    """Resolve the active world save using the plugin strategy.

    Order of preference inside this function:
    1. ``named_path`` templates from the plugin
    2. honest ``backup_sources`` sum (not presented as a named world file)
    """

    options = dict(game_options or {})
    root = str(data_dir or options.get("data_dir") or plugin.data_dir)
    sources = backup_sources_for(plugin, root)
    spec = getattr(plugin, "world_save", None)
    if spec is None:
        # No plugin declaration: do not guess paths. Sum backup roots only.
        return _from_backup_sources(sources)

    strategy = spec.strategy
    if strategy == STRATEGY_NAMED_PATH:
        found = _locate_named_path(spec, root, options)
        if found is not None:
            return found
        return _missing_named(spec, root, options, sources)

    # strategy == backup_sources (or unknown handled at parse time)
    return _from_backup_sources(sources)


def expand_world_path_template(
    template: str,
    *,
    data_dir: str,
    world_name: str,
    options: Mapping[str, Any],
) -> str | None:
    """Expand ``{data_dir}`` / ``{world_name}`` / option keys. None if a key is empty."""

    values: dict[str, str] = {
        str(k): _stringify_option(v) for k, v in options.items()
    }
    values["data_dir"] = data_dir
    values["world_name"] = world_name

    keys = _TEMPLATE_KEY_RE.findall(template)
    for key in keys:
        value = values.get(key)
        if value is None or value == "":
            return None

    def repl(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _TEMPLATE_KEY_RE.sub(repl, template)


def _stringify_option(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _world_name(spec: WorldSaveSpec, options: Mapping[str, Any]) -> str:
    return _stringify_option(options.get(spec.world_name_option))


def _locate_named_path(
    spec: WorldSaveSpec,
    data_dir: str,
    options: Mapping[str, Any],
) -> ActiveWorld | None:
    world_name = _world_name(spec, options)
    expected: list[str] = []
    for template in spec.paths:
        expanded = expand_world_path_template(
            template,
            data_dir=data_dir,
            world_name=world_name,
            options=options,
        )
        if not expanded:
            continue
        expected.append(expanded)
        path = Path(expanded)
        if path.is_file() or path.is_dir():
            return ActiveWorld(
                bytes=path_total_bytes(path),
                path=str(path),
                label=path.name,
                scope=SCOPE_NAMED_PATH,
                sources=[str(path)],
                expected_paths=expected,
                kind=infer_world_kind(path),
            )
    return None


def _missing_named(
    spec: WorldSaveSpec,
    data_dir: str,
    options: Mapping[str, Any],
    backup_sources: list[str],
) -> ActiveWorld:
    world_name = _world_name(spec, options)
    expected: list[str] = []
    for template in spec.paths:
        expanded = expand_world_path_template(
            template,
            data_dir=data_dir,
            world_name=world_name,
            options=options,
        )
        if expanded:
            expected.append(expanded)
    label = Path(expected[0]).name if expected else (world_name or None)
    # Prefer reporting the intended artifact as missing over silently summing
    # the whole data dir (that would hide a misconfigured template).
    if expected:
        return ActiveWorld(
            bytes=0,
            path=expected[0],
            label=label,
            scope=SCOPE_MISSING,
            sources=[],
            expected_paths=expected,
            kind=infer_world_kind(expected[0]),
        )
    if _any_exists(backup_sources):
        LOG.debug(
            "world_save named_path templates unresolved; using backup_sources for size"
        )
        return _from_backup_sources(backup_sources)
    return ActiveWorld(
        bytes=0,
        path=None,
        label=None,
        scope=SCOPE_MISSING,
        sources=[],
        expected_paths=[],
        kind=KIND_UNKNOWN,
    )


def _from_backup_sources(sources: list[str]) -> ActiveWorld:
    total = 0
    existing: list[str] = []
    for raw in sources:
        path = Path(raw)
        if path.exists():
            total += path_total_bytes(path)
            existing.append(str(path))
    if not existing:
        return ActiveWorld(
            bytes=0,
            path=None,
            label=None,
            scope=SCOPE_MISSING,
            sources=[],
            expected_paths=[],
            kind=KIND_UNKNOWN,
        )
    single = existing[0] if len(existing) == 1 else None
    return ActiveWorld(
        bytes=total,
        path=single,
        label="world data",
        scope=SCOPE_BACKUP_SOURCES,
        sources=existing,
        expected_paths=[],
        # Whole backup roots are not a named save artifact — refuse upload apply.
        kind=KIND_UNKNOWN,
    )


def _any_exists(sources: list[str]) -> bool:
    return any(Path(p).exists() for p in sources)


@dataclass(frozen=True)
class WorldDownload:
    """On-disk artifact ready to stream as an Ingress attachment."""

    path: Path
    filename: str
    content_type: str
    # Temporary zip (directory worlds); unlink after the response is sent.
    cleanup_path: Path | None = None


def confined_world_path(
    raw_path: str | None,
    *,
    data_dir: str | Path,
) -> Path | None:
    """Return ``raw_path`` only when it resolves under ``data_dir``."""

    if not raw_path:
        return None
    try:
        root = Path(data_dir).resolve()
        path = Path(raw_path).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path


def world_save_is_downloadable(
    active: ActiveWorld,
    *,
    data_dir: str | Path,
) -> bool:
    """True when the active world is a real file/dir under the data volume."""

    if active.scope == SCOPE_MISSING or int(active.bytes or 0) <= 0:
        return False
    path = confined_world_path(active.path, data_dir=data_dir)
    if path is None:
        return False
    return path.is_file() or path.is_dir()


def prepare_world_download(
    active: ActiveWorld,
    *,
    data_dir: str | Path,
) -> WorldDownload | None:
    """Prepare a file (as-is) or directory (stdlib zip) for download."""

    path = confined_world_path(active.path, data_dir=data_dir)
    if path is None or not path.exists():
        return None
    if path.is_file():
        name = path.name or "world-save"
        content_type = (
            "application/zip"
            if name.lower().endswith(".zip")
            else "application/octet-stream"
        )
        return WorldDownload(path=path, filename=name, content_type=content_type)
    if path.is_dir():
        label = (active.label or path.name or "world").strip() or "world"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._") or "world"
        if not safe.lower().endswith(".zip"):
            safe = f"{safe}.zip"
        tmp = tempfile.NamedTemporaryFile(
            prefix="world-download-",
            suffix=".zip",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            with tmp, zipfile.ZipFile(
                tmp, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as zf:
                for child in sorted(path.rglob("*")):
                    if not child.is_file():
                        continue
                    arcname = child.relative_to(path).as_posix()
                    zf.write(child, arcname)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        return WorldDownload(
            path=tmp_path,
            filename=safe,
            content_type="application/zip",
            cleanup_path=tmp_path,
        )
    return None


def resolve_upload_target(
    active: ActiveWorld,
    *,
    data_dir: str | Path,
) -> tuple[Path, str]:
    """Return ``(target_path, kind)`` for an HTTP world upload, or raise."""

    meta = world_upload_accepts(active)
    if not meta["uploadable"]:
        raise RuntimeError(str(meta["hint"]))
    kind = str(meta["kind"])
    target = confined_world_path(active.path, data_dir=data_dir)
    if target is None:
        raise RuntimeError("world upload target is outside the data directory")
    return target, kind


def backup_name_suffix(path: str | Path, kind: str) -> str:
    """File extension for a by-kind backup of ``path`` (includes the dot)."""

    if kind == KIND_DIRECTORY:
        return ".zip"
    suffix = Path(path).suffix
    return suffix if suffix else ".bin"


def write_world_backup(
    path: str | Path,
    kind: str,
    dest: str | Path,
) -> dict[str, Any]:
    """Write a by-kind backup of a world artifact to ``dest``.

    - ``file`` — byte-for-byte copy (no recompress; a game ``.zip`` save stays
      a single ``.zip``)
    - ``directory`` — zip of folder contents (same layout as download/upload)
    """

    source = Path(path)
    out = Path(dest)
    if kind == KIND_FILE:
        if not source.is_file() or source.stat().st_size < 1:
            raise RuntimeError(f"world save file missing or empty: {source}")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.partial")
        try:
            shutil.copyfile(source, tmp)
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        return {
            "ok": True,
            "mode": "copy_file",
            "kind": kind,
            "source": str(source),
            "archive": str(out),
            "bytes": out.stat().st_size,
        }
    if kind == KIND_DIRECTORY:
        if not source.is_dir():
            raise RuntimeError(f"world save directory missing: {source}")
        if path_total_bytes(source) < 1:
            raise RuntimeError(f"world save directory is empty: {source}")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.partial")
        try:
            with zipfile.ZipFile(
                tmp, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as zf:
                for child in sorted(source.rglob("*")):
                    if not child.is_file() or child.is_symlink():
                        continue
                    zf.write(child, child.relative_to(source).as_posix())
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        return {
            "ok": True,
            "mode": "zip_directory",
            "kind": kind,
            "source": str(source),
            "archive": str(out),
            "bytes": out.stat().st_size,
        }
    raise RuntimeError(f"unsupported world kind for backup: {kind}")


def clear_world_artifact(
    active: ActiveWorld,
    *,
    data_dir: str | Path,
) -> dict[str, Any]:
    """Remove the active world artifact (+ sibling expected paths)."""

    kind = effective_world_kind(active)
    target = confined_world_path(active.path, data_dir=data_dir)
    cleared: list[str] = []
    if target is not None and target.exists():
        if target.is_file() or target.is_symlink():
            LOG.info("Removing world save file: %s", target)
            target.unlink()
            cleared.append(str(target))
        elif target.is_dir():
            LOG.info("Clearing world save directory in place: %s", target)
            for child in list(target.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            cleared.append(str(target))
    if target is not None:
        _remove_sibling_expected_paths(active, keep=target, data_dir=data_dir)
    return {
        "ok": True,
        "empty": True,
        "cleared": cleared,
        "kind": kind,
        "path": str(target) if target is not None else active.path,
    }


def apply_world_upload(
    active: ActiveWorld,
    upload_path: str | Path,
    *,
    data_dir: str | Path,
    strip_wrapper: bool = True,
) -> dict[str, Any]:
    """Replace the active world artifact from an uploaded file or backup.

    Mode is chosen from the **configured/live world kind**, not from guessing
    the upload alone:

    - ``file`` — write upload bytes to the target path (game's single-file save,
      which may itself be a ``.zip``). The new file keeps the previous owner's
      uid/gid (or the parent directory's, if the file is new) and mode 0644.
    - ``directory`` — extract a zip into a staging folder, then swap into the
      live directory (preserves that directory's inode / ownership)

    ``strip_wrapper`` (default True) unwraps a sole top-level folder, which is
    what user uploads of ``WorldFolder.zip`` look like. Internal by-kind
    backups already store paths relative to the live folder; pass False so a
    real child directory such as ``worlds/save.dat`` round-trips.

    Caller owns process stop/start and the pre-restore safety backup. This only
    mutates the active world artifact (+ sibling expected paths that would
    confuse the locator).
    """

    upload = Path(upload_path)
    if not upload.is_file() or upload.stat().st_size < 1:
        raise RuntimeError("uploaded world file is missing or empty")

    target, kind = resolve_upload_target(active, data_dir=data_dir)
    LOG.info(
        "Applying world upload %s → %s (kind=%s, mode=%s)",
        upload.name,
        target,
        kind,
        world_upload_accepts(active)["mode"],
    )

    if kind == KIND_FILE:
        _remove_sibling_expected_paths(active, keep=target, data_dir=data_dir)
        replace_world_file(upload, target)
        return {
            "ok": True,
            "mode": "replace_file",
            "path": str(target),
            "bytes": target.stat().st_size,
            "kind": kind,
        }

    if kind == KIND_DIRECTORY:
        if not zipfile.is_zipfile(upload):
            raise RuntimeError(
                "this game uses a folder world save; upload a .zip of that folder"
            )
        _remove_sibling_expected_paths(active, keep=target, data_dir=data_dir)
        install_zip_into_directory(upload, target, strip_wrapper=strip_wrapper)
        return {
            "ok": True,
            "mode": "extract_zip_into_directory",
            "path": str(target),
            "bytes": path_total_bytes(target),
            "kind": kind,
        }

    raise RuntimeError(f"unsupported world kind for upload: {kind}")


def _remove_sibling_expected_paths(
    active: ActiveWorld,
    *,
    keep: Path,
    data_dir: str | Path,
) -> None:
    """Drop alternate expected artifacts (e.g. zip ↔ folder) so locate stays clean."""

    keep_resolved = keep.resolve()
    for raw in active.expected_paths:
        other = confined_world_path(raw, data_dir=data_dir)
        if other is None:
            continue
        try:
            if other.resolve() == keep_resolved:
                continue
        except OSError:
            continue
        if not other.exists():
            continue
        LOG.info("Removing alternate world path before upload apply: %s", other)
        if other.is_dir() and not other.is_symlink():
            shutil.rmtree(other)
        else:
            other.unlink()


def install_zip_into_directory(
    archive: Path,
    target: Path,
    *,
    strip_wrapper: bool = True,
) -> None:
    """Extract ``archive`` into a staging dir, then swap into ``target``.

    ``target``'s directory inode (owner/mode) is kept. Live contents are not
    removed until staging extract succeeds. A failed extract leaves the live
    world untouched; a failed swap restores the aside copy.
    """

    with zipfile.ZipFile(archive, "r") as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        if not names:
            raise RuntimeError("uploaded zip has no files")

    target.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.extract-staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        _extract_zip_into_directory(
            archive,
            staging,
            strip_wrapper=strip_wrapper,
            wrapper_name=target.name,
        )
        _chown_tree_like(staging, target)
        _swap_directory_contents(target, staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def replace_world_file(upload: Path, target: Path) -> None:
    """Copy ``upload`` onto ``target`` and give it the game user's ownership.

    The new inode takes the uid/gid of the existing file, or of the nearest
    existing parent when the file is new (the supervisor is often root; the
    game process is not). Parent directories created along the way get that
    same owner. Mode is ``0o644`` so the owner can rewrite the save. chown
    and chmod are best-effort, same as a directory extract.
    """

    upload = Path(upload)
    target = Path(target)
    missing_dirs: list[Path] = []
    cursor = target.parent
    while not cursor.exists():
        missing_dirs.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    ancestor = cursor if cursor.exists() else None
    ancestor_owner = _owner_ids(ancestor) if ancestor is not None else None
    if target.is_dir() and not target.is_symlink():
        file_owner = _owner_ids(target) or ancestor_owner
        shutil.rmtree(target)
    elif target.exists():
        file_owner = _owner_ids(target) or ancestor_owner
    else:
        file_owner = ancestor_owner

    target.parent.mkdir(parents=True, exist_ok=True)
    if ancestor_owner is not None:
        for directory in reversed(missing_dirs):
            if directory.is_dir():
                _chown_one(directory, ancestor_owner[0], ancestor_owner[1])
                _chmod_one(directory, _WORLD_DIR_MODE)

    tmp = target.with_name(f".{target.name}.upload-tmp")
    try:
        shutil.copyfile(upload, tmp)
        if file_owner is not None:
            _chown_one(tmp, file_owner[0], file_owner[1])
        _chmod_one(tmp, _WORLD_FILE_MODE)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def _owner_ids(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_uid, st.st_gid


def _chown_one(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError:
        return


def _chmod_one(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        return


def _chown_tree_like(path: Path, owner_src: Path) -> None:
    """Best-effort: copied files take the live world's uid/gid (root→gameserver)."""

    owner = _owner_ids(owner_src)
    if owner is None:
        return
    uid, gid = owner
    for current in [path, *path.rglob("*")]:
        _chown_one(current, uid, gid)


def _swap_directory_contents(target: Path, staging: Path) -> None:
    """Replace ``target`` children with ``staging`` children; roll back on error."""

    aside = target.with_name(f".{target.name}.replace-old")
    if aside.exists():
        shutil.rmtree(aside)
    aside.mkdir()
    moved_old: list[str] = []
    moved_new: list[str] = []
    try:
        for child in list(target.iterdir()):
            child.rename(aside / child.name)
            moved_old.append(child.name)
        for child in list(staging.iterdir()):
            child.rename(target / child.name)
            moved_new.append(child.name)
    except Exception:
        for name in moved_new:
            new_path = target / name
            if new_path.exists():
                dest = staging / name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                new_path.rename(dest)
        for name in moved_old:
            old_path = aside / name
            if old_path.exists():
                old_path.rename(target / name)
        raise
    finally:
        if aside.exists():
            shutil.rmtree(aside, ignore_errors=True)


def _extract_zip_into_directory(
    archive: Path,
    target: Path,
    *,
    strip_wrapper: bool = True,
    wrapper_name: str | None = None,
) -> None:
    """Extract zip members under ``target``, rejecting path traversal.

    When ``strip_wrapper`` is True, a sole top-level directory is removed only
    if it matches ``wrapper_name`` (the live world folder). That unwraps a user
    zip of the folder itself without flattening a real child such as ``worlds/``.
    """

    root = target.resolve()
    with zipfile.ZipFile(archive, "r") as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        if not names:
            raise RuntimeError("uploaded zip has no files")
        strip_prefix = None
        if strip_wrapper:
            strip_prefix = _single_top_level_dir_prefix(zf.namelist())
            if (
                strip_prefix
                and wrapper_name
                and strip_prefix.rstrip("/") != wrapper_name
            ):
                strip_prefix = None
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                continue
            member = name
            if strip_prefix and member.startswith(strip_prefix):
                member = member[len(strip_prefix) :]
            if not member or member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"refusing unsafe zip member: {name}")
            out = (target / member).resolve()
            try:
                out.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"refusing zip member outside target: {name}") from exc
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _single_top_level_dir_prefix(names: list[str]) -> str | None:
    """If every entry lives under one top-level folder, return that prefix."""

    tops: set[str] = set()
    for name in names:
        cleaned = name.lstrip("/").replace("\\", "/")
        if not cleaned or cleaned in {".", ".."}:
            continue
        first = cleaned.split("/", 1)[0]
        if first in {".", ".."}:
            return None
        tops.add(first)
        if len(tops) > 1:
            return None
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    # Only strip when it is a directory prefix (not a single file at root).
    has_nested = any(
        n.replace("\\", "/").lstrip("/").startswith(top + "/") for n in names
    )
    if not has_nested:
        return None
    return top + "/"
