"""Pre-launch helpers: write config files and prepare a missing world.

Game plugins declare these declaratively (no game names in this module):

- ``config_files`` — rewrite JSON/INI files from HA options before each start
- ``world_prepare`` — run a one-shot argv (same executable) when the active
  world artifact is missing (e.g. create-save before headless host)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import format_bool, format_option_value
from .privileges import make_preexec
from .world_save import locate_active_world

LOG = logging.getLogger("game_server.launch_prepare")

_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SUPPORTED_FORMATS = frozenset({"json", "ini", "mod_list"})
_SUPPORTED_WHEN = frozenset({"missing"})
_SUPPORTED_TYPES = frozenset({"str", "string", "int", "integer", "bool", "boolean", "float"})


@dataclass
class ModListEntry:
    """One mod toggle for ``format: mod_list`` config files."""

    name: str
    # Literal enable state when ``enabled_option`` is unset.
    enabled: bool | None = None
    # Option key whose truthiness sets enabled (e.g. space_age → DLC mods).
    enabled_option: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "ModListEntry":
        if not isinstance(data, dict):
            raise ValueError("mod_list mods entries must be mappings")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("mod_list mods entry requires name")
        option = data.get("enabled_option")
        option_key = str(option).strip() if option is not None and str(option).strip() else None
        if "enabled" in data and data.get("enabled") is not None:
            enabled = bool(data.get("enabled"))
        else:
            enabled = None
        if option_key is None and enabled is None:
            raise ValueError(
                f"mod_list mod {name!r} requires enabled or enabled_option"
            )
        return cls(name=name, enabled=enabled, enabled_option=option_key)


@dataclass
class ConfigFileSpec:
    """One config file to materialize before launching the game process."""

    path: str
    format: str = "json"
    # Always written (nested mappings OK for JSON; section→key for INI).
    fixed: dict[str, Any] = field(default_factory=dict)
    # option_key → dotted destination key (JSON path or ``section.key`` for INI).
    map: dict[str, str] = field(default_factory=dict)
    # Destination key → coercion type (int/bool/float/str).
    types: dict[str, str] = field(default_factory=dict)
    # Skip mapped options whose value is None/"".
    omit_empty: bool = True
    # For format=mod_list: declarative mod enable/disable rows.
    mods: list[ModListEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "ConfigFileSpec":
        if not isinstance(data, dict):
            raise ValueError("config_files entries must be mappings")
        path = str(data.get("path") or "").strip()
        if not path:
            raise ValueError("config_files entry requires path")
        fmt = str(data.get("format") or "json").strip().lower()
        if fmt not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported config_files format {fmt!r}; "
                "expected json, ini, or mod_list"
            )
        fixed = data.get("fixed") or {}
        if not isinstance(fixed, dict):
            raise ValueError("config_files.fixed must be a mapping")
        raw_map = data.get("map") or {}
        if not isinstance(raw_map, dict):
            raise ValueError("config_files.map must be a mapping")
        raw_types = data.get("types") or {}
        if not isinstance(raw_types, dict):
            raise ValueError("config_files.types must be a mapping")
        types: dict[str, str] = {}
        for key, value in raw_types.items():
            t = str(value or "").strip().lower()
            if t not in _SUPPORTED_TYPES:
                raise ValueError(
                    f"Unsupported config_files type {t!r} for {key!r}; "
                    f"expected str/int/bool/float"
                )
            types[str(key)] = t
        mods: list[ModListEntry] = []
        if fmt == "mod_list":
            raw_mods = data.get("mods") or []
            if not isinstance(raw_mods, (list, tuple)) or not raw_mods:
                raise ValueError("config_files format mod_list requires non-empty mods")
            mods = [ModListEntry.from_dict(item) for item in raw_mods]
        return cls(
            path=path,
            format=fmt,
            fixed=dict(fixed),
            map={str(k): str(v) for k, v in raw_map.items()},
            types=types,
            omit_empty=bool(data.get("omit_empty", True)),
            mods=mods,
        )


@dataclass
class WorldPrepareSpec:
    """One-shot command to create the active world when it is missing."""

    when: str = "missing"
    argv: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    @classmethod
    def from_dict(cls, data: Any) -> "WorldPrepareSpec | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("world_prepare must be a mapping when provided")
        when = str(data.get("when") or "missing").strip().lower()
        if when not in _SUPPORTED_WHEN:
            raise ValueError(
                f"Unsupported world_prepare.when {when!r}; expected missing"
            )
        argv = [str(x) for x in (data.get("argv") or [])]
        if not argv:
            raise ValueError("world_prepare.argv must be a non-empty list")
        timeout = int(data.get("timeout_seconds") or 300)
        if timeout < 5:
            timeout = 5
        return cls(when=when, argv=argv, timeout_seconds=timeout)


def launch_options(
    plugin: Any,
    game_options: Mapping[str, Any] | None,
    *,
    working_dir: str | Path | None = None,
    install_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Options dict used for template expansion (CLI + config files)."""

    options = dict(game_options or {})
    if "data_dir" not in options:
        options["data_dir"] = getattr(plugin, "data_dir", "/data/world")
    if "logs_dir" not in options:
        options["logs_dir"] = getattr(plugin, "logs_dir", "/data/logs")
    work = (
        working_dir
        or options.get("working_dir")
        or install_dir
        or getattr(plugin, "working_dir", "/data/game")
    )
    options.setdefault("working_dir", str(work))
    if install_dir is not None:
        options.setdefault("install_dir", str(install_dir))
    return options


def render_template(text: str, options: Mapping[str, Any], bool_style: str) -> str:
    """Expand ``{option_key}`` templates; empty option → empty string."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in options:
            return ""
        value = options[key]
        if value is None or value == "":
            return ""
        return format_option_value(value, bool_style)

    if "{" not in text:
        return text
    return _TEMPLATE_RE.sub(repl, text)


def _coerce_value(value: object, type_name: str, bool_style: str) -> Any:
    t = (type_name or "str").lower()
    if t in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        return bool(value)
    if t in {"int", "integer"}:
        if isinstance(value, bool):
            return int(value)
        return int(str(value).strip())
    if t == "float":
        return float(str(value).strip())
    # str / string — still expand bools consistently when callers ask for str
    if isinstance(value, bool):
        return format_bool(value, bool_style)
    return str(value)


def _set_dotted(root: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        raise ValueError("empty config_files map destination")
    cur: dict[str, Any] = root
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _render_tree(node: Any, options: Mapping[str, Any], bool_style: str) -> Any:
    if isinstance(node, dict):
        return {
            str(k): _render_tree(v, options, bool_style) for k, v in node.items()
        }
    if isinstance(node, list):
        return [_render_tree(v, options, bool_style) for v in node]
    if isinstance(node, str):
        return render_template(node, options, bool_style)
    return node


def build_config_payload(
    spec: ConfigFileSpec,
    options: Mapping[str, Any],
    bool_style: str,
) -> dict[str, Any]:
    """Merge fixed + mapped options into a nested dict (JSON-shaped)."""

    payload = _render_tree(spec.fixed, options, bool_style)
    if not isinstance(payload, dict):
        raise ValueError("config_files.fixed must render to a mapping")
    for option_key, dest in spec.map.items():
        if option_key not in options:
            continue
        value = options[option_key]
        if spec.omit_empty and (value is None or value == ""):
            continue
        type_name = spec.types.get(dest) or spec.types.get(option_key) or "str"
        coerced = _coerce_value(value, type_name, bool_style)
        if isinstance(coerced, str):
            coerced = render_template(coerced, options, bool_style)
        _set_dotted(payload, dest, coerced)
    return payload


def _option_truthy(options: Mapping[str, Any], key: str) -> bool:
    if key not in options:
        return False
    value = options[key]
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(value)


def build_mod_list_payload(
    spec: ConfigFileSpec,
    options: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Factorio-style ``{"mods":[{"name","enabled"},…]}``, merging others."""

    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    if isinstance(existing, Mapping):
        for item in existing.get("mods") or []:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in by_name:
                continue
            by_name[name] = {
                "name": name,
                "enabled": bool(item.get("enabled", True)),
            }
            order.append(name)

    managed: list[str] = []
    for entry in spec.mods:
        if entry.enabled_option:
            enabled = _option_truthy(options, entry.enabled_option)
        else:
            enabled = bool(entry.enabled)
        by_name[entry.name] = {"name": entry.name, "enabled": enabled}
        managed.append(entry.name)
        if entry.name not in order:
            order.append(entry.name)

    # Managed mods first (plugin order), then any preserved extras.
    final_order = list(managed)
    for name in order:
        if name not in final_order:
            final_order.append(name)
    return {"mods": [by_name[name] for name in final_order if name in by_name]}


def write_config_file(
    spec: ConfigFileSpec,
    options: Mapping[str, Any],
    bool_style: str,
) -> Path:
    """Write one config file; return the absolute path written."""

    rendered_path = render_template(spec.path, options, bool_style)
    if not rendered_path:
        raise ValueError(f"config_files path rendered empty: {spec.path!r}")
    path = Path(rendered_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if spec.format == "mod_list":
        existing: dict[str, Any] | None = None
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                LOG.warning("Ignoring unreadable existing mod list at %s", path)
        payload = build_mod_list_payload(spec, options, existing)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        enabled = [
            str(m.get("name"))
            for m in payload.get("mods") or []
            if isinstance(m, dict) and m.get("enabled")
        ]
        disabled = [
            str(m.get("name"))
            for m in payload.get("mods") or []
            if isinstance(m, dict) and not m.get("enabled")
        ]
        LOG.info(
            "Wrote mod_list config %s (enabled=%s disabled=%s)",
            path,
            ",".join(enabled) or "-",
            ",".join(disabled) or "-",
        )
        return path

    payload = build_config_payload(spec, options, bool_style)

    if spec.format == "json":
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    else:
        # Write compact ``key=value`` INI (no spaces around ``=``). Some game
        # engines reject ConfigParser's default ``key = value`` spacing.
        lines: list[str] = []
        for section, values in payload.items():
            if not isinstance(values, dict):
                raise ValueError(
                    f"INI config_files section {section!r} must be a mapping"
                )
            lines.append(f"[{section}]")
            for key, value in values.items():
                lines.append(f"{key}={value}")
            lines.append("")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.replace(path)

    LOG.info("Wrote %s config %s", spec.format, path)
    return path


def write_config_files(
    plugin: Any,
    options: Mapping[str, Any],
) -> list[Path]:
    """Write every ``plugin.config_files`` entry; return paths written."""

    specs = list(getattr(plugin, "config_files", None) or [])
    if not specs:
        return []
    bool_style = str(getattr(plugin, "bool_style", "true_false") or "true_false")
    written: list[Path] = []
    for spec in specs:
        written.append(write_config_file(spec, options, bool_style))
    return written


def world_needs_prepare(plugin: Any, options: Mapping[str, Any]) -> bool:
    """True when world_prepare.when=missing and the active world is absent."""

    spec = getattr(plugin, "world_prepare", None)
    if spec is None:
        return False
    if spec.when != "missing":
        return False
    active = locate_active_world(
        plugin,
        options,
        data_dir=str(options.get("data_dir") or getattr(plugin, "data_dir", "")),
    )
    if not active.path:
        return True
    path = Path(active.path)
    try:
        return not path.exists()
    except OSError:
        return True


def build_world_prepare_command(
    plugin: Any,
    options: Mapping[str, Any],
) -> list[str]:
    """Executable + rendered world_prepare.argv."""

    spec = getattr(plugin, "world_prepare", None)
    if spec is None:
        raise ValueError("world_prepare is not configured")
    bool_style = str(getattr(plugin, "bool_style", "true_false") or "true_false")
    cmd = list(getattr(plugin, "executable", None) or [])
    if not cmd:
        raise ValueError("plugin executable is required for world_prepare")
    for token in spec.argv:
        rendered = render_template(str(token), options, bool_style)
        if rendered == "":
            continue
        cmd.append(rendered)
    return cmd


def run_world_prepare(
    plugin: Any,
    options: Mapping[str, Any],
    *,
    working_dir: Path,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> None:
    """Run the one-shot world prepare command when the world is missing."""

    spec = getattr(plugin, "world_prepare", None)
    if spec is None:
        return
    if not world_needs_prepare(plugin, options):
        LOG.info("Active world already present; skipping world_prepare")
        return

    cmd = build_world_prepare_command(plugin, options)
    env = os.environ.copy()
    env.update(getattr(plugin, "env", None) or {})
    timeout = float(spec.timeout_seconds)
    LOG.info(
        "Preparing missing world (timeout=%.0fs): %s (cwd=%s)",
        timeout,
        " ".join(cmd),
        working_dir,
    )
    started = time.time()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(working_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            preexec_fn=make_preexec(run_uid, run_gid),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"world_prepare timed out after {timeout:.0f}s: {' '.join(cmd)}"
        ) from exc

    output = (completed.stdout or "").strip()
    if output:
        for line in output.splitlines()[-40:]:
            LOG.info("[world-prepare] %s", line)
    if completed.returncode != 0:
        raise RuntimeError(
            f"world_prepare exited {completed.returncode} after "
            f"{time.time() - started:.1f}s: {' '.join(cmd)}"
        )
    if world_needs_prepare(plugin, options):
        raise RuntimeError(
            "world_prepare finished but the active world artifact is still missing"
        )
    LOG.info("World prepare complete in %.1fs", time.time() - started)


def prepare_launch(
    plugin: Any,
    game_options: Mapping[str, Any] | None,
    *,
    working_dir: Path,
    install_dir: str | Path | None = None,
    run_uid: int | None = None,
    run_gid: int | None = None,
) -> dict[str, Any]:
    """Write config files and prepare a missing world. Returns expanded options."""

    options = launch_options(
        plugin,
        game_options,
        working_dir=working_dir,
        install_dir=install_dir,
    )
    write_config_files(plugin, options)
    run_world_prepare(
        plugin,
        options,
        working_dir=working_dir,
        run_uid=run_uid,
        run_gid=run_gid,
    )
    return options
