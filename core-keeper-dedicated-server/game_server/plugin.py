"""Game plugin definitions loaded from YAML/JSON files."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .copyparty import CopypartySpec
from .launch_prepare import ConfigFileSpec, WorldPrepareSpec
from .package_install import PackageInstallSpec
from .world_catalog import WorldCatalogSpec, WorldCreateSpec
from .world_save import WorldSaveSpec

_OPTION_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


# How player occupancy is interpreted for UI + update-when-empty.
# count — numeric / named join-leave (Necesse-style)
# presence — idle vs players-active only (Stationeers-style)
PLAYER_TRACKING_COUNT = "count"
PLAYER_TRACKING_PRESENCE = "presence"
PLAYER_TRACKING_MODES = frozenset({PLAYER_TRACKING_COUNT, PLAYER_TRACKING_PRESENCE})


@dataclass
class StatusProbeSpec:
    """Optional argv that prints a JSON object of live game status fields."""

    argv: list[str]
    interval_seconds: float = 10.0

    @classmethod
    def from_dict(cls, data: Any) -> StatusProbeSpec | None:
        if not data:
            return None
        if not isinstance(data, dict):
            raise ValueError("status_probe must be an object")
        argv = [str(x) for x in (data.get("argv") or []) if str(x).strip()]
        if not argv:
            raise ValueError("status_probe requires argv")
        interval = float(data.get("interval_seconds") or 10)
        if interval < 1:
            interval = 1.0
        return cls(argv=argv, interval_seconds=interval)


@dataclass
class LogPatterns:
    """Regex patterns used to interpret game server log output."""

    player_join: list[str] = field(default_factory=list)
    player_leave: list[str] = field(default_factory=list)
    version_mismatch: list[str] = field(default_factory=list)
    player_count: list[str] = field(default_factory=list)
    # Definitive "nobody online" lines (useful for presence-mode games).
    players_empty: list[str] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    # Human-readable game/server version (e.g. "1.3.1"), not Steam build ids.
    game_version: list[str] = field(default_factory=list)

    def compiled(self, key: str) -> list[re.Pattern[str]]:
        return [re.compile(p, re.IGNORECASE) for p in getattr(self, key)]


@dataclass
class GamePlugin:
    """Describes how to install and run one dedicated game server."""

    name: str
    working_dir: str
    executable: list[str]
    data_dir: str
    logs_dir: str
    install_marker: str
    # SteamCMD app id when using Steam installs. Optional for package_install games.
    steam_app_id: int | None = None
    backup_paths: list[str] = field(default_factory=list)
    steam_branch: str = "public"
    steam_login: str = "anonymous"
    steam_password: str = ""
    # Optional SteamCMD platform pin (e.g. "linux"). Empty = host-native depots.
    steam_platform: str = ""
    validate_on_update: bool = True
    # Optional non-Steam HTTP archive install (free headless tarballs, etc.).
    package_install: PackageInstallSpec | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Simple option_key → CLI flag pairs (``-flag value`` or ``-flag=value``).
    arg_map: dict[str, str] = field(default_factory=dict)
    # Ordered argv tokens after the executable. Supports ``{option_key}``
    # templates; empty renderings are omitted (handy for optional tokens).
    argv_prefix: list[str] = field(default_factory=list)
    # Optional ``-settings Key Value Key Value …`` style block (Unity / etc.).
    # When settings_flag is set, fixed_settings then settings_map are appended.
    settings_flag: str = ""
    # SettingName → literal/template value (always attempted).
    fixed_settings: dict[str, str] = field(default_factory=dict)
    # option_key → SettingName (skip when the option is empty).
    settings_map: dict[str, str] = field(default_factory=dict)
    # Extra UPPER_SNAKE env vars to accept as game options in Docker/compose
    # (e.g. JAVA_OPTS). Keys already declared via arg_map / settings_map /
    # ``{option}`` templates are picked up automatically — see docker_env_keys().
    env_options: list[str] = field(default_factory=list)
    bool_style: str = "true_false"  # or "one_zero"
    log_patterns: LogPatterns = field(default_factory=LogPatterns)
    # Optional extra dry-run candidates merged with generic defaults.
    log_pattern_candidates: dict[str, list[str]] = field(default_factory=dict)
    ready_timeout_seconds: int = 180
    java_opts_env: str = "JAVA_OPTS"
    stop_timeout_seconds: int = 60
    stop_stdin_commands: list[str] = field(default_factory=list)
    # Optional live-backup flush via game stdin (does not stop the process).
    pre_backup_stdin_commands: list[str] = field(default_factory=list)
    min_backup_bytes: int = 1024
    # Optional runtime packages hint for image authors (documentation only).
    runtime_notes: str = ""
    # How to find the active world artifact for status UI (not backup roots).
    world_save: WorldSaveSpec | None = None
    # Optional JSON/INI files rewritten from options before each launch.
    config_files: list[ConfigFileSpec] = field(default_factory=list)
    # Optional one-shot argv when the active world is missing (create-save, etc.).
    world_prepare: WorldPrepareSpec | None = None
    # Optional Ingress world list (globs). Games without this keep a single name.
    world_catalog: WorldCatalogSpec | None = None
    # Optional extra fields on Ingress "create world" (text/select; plugin labels).
    world_create: WorldCreateSpec | None = None
    # Optional Ingress status page CSS color overrides (see status_http.DEFAULT_UI_THEME).
    ui_theme: dict[str, str] = field(default_factory=dict)
    # count (default) or presence — see PLAYER_TRACKING_* constants.
    # Presence: join → occupied; matching leave may keep others; unknown leave → idle.
    player_tracking_mode: str = PLAYER_TRACKING_COUNT
    # Stay in the supervisor loop after the crash restart budget (Ingress and
    # Copyparty keep running). Default off so HA watchdog can recycle others.
    hold_on_crash_loop: bool = False
    # Delay a pending game restart until player count is 0 (or the process is
    # already down). World switch/create still restart immediately.
    restart_when_empty: bool = False
    copyparty: CopypartySpec | None = None
    status_probe: StatusProbeSpec | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GamePlugin":
        patterns = data.get("log_patterns") or {}
        raw_candidates = data.get("log_pattern_candidates") or {}
        candidates: dict[str, list[str]] = {}
        if isinstance(raw_candidates, dict):
            for key, values in raw_candidates.items():
                candidates[str(key)] = [str(v) for v in (values or [])]
        install_marker = data.get("install_marker")
        if not install_marker:
            raise ValueError("Game plugin requires install_marker")
        tracking_mode = str(
            data.get("player_tracking_mode") or PLAYER_TRACKING_COUNT
        ).strip().lower()
        if tracking_mode not in PLAYER_TRACKING_MODES:
            raise ValueError(
                f"Unsupported player_tracking_mode {tracking_mode!r}; "
                f"expected count or presence"
            )
        package_install = PackageInstallSpec.from_dict(data.get("package_install"))
        raw_app_id = data.get("steam_app_id")
        if package_install is None and raw_app_id is None:
            raise ValueError(
                "Game plugin requires steam_app_id (SteamCMD) or package_install"
            )
        steam_app_id = int(raw_app_id) if raw_app_id is not None else None
        return cls(
            name=data["name"],
            steam_app_id=steam_app_id,
            working_dir=data.get("working_dir", "/data/game"),
            executable=list(data["executable"]),
            data_dir=data.get("data_dir", "/data/world"),
            logs_dir=data.get("logs_dir", "/data/logs"),
            install_marker=str(install_marker),
            backup_paths=list(
                data.get("backup_paths") or [data.get("data_dir", "/data/world")]
            ),
            steam_branch=data.get("steam_branch", "public"),
            steam_login=data.get("steam_login", "anonymous"),
            steam_password=data.get("steam_password", ""),
            steam_platform=str(data.get("steam_platform") or "").strip().lower(),
            validate_on_update=bool(data.get("validate_on_update", True)),
            package_install=package_install,
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            arg_map={str(k): str(v) for k, v in (data.get("arg_map") or {}).items()},
            argv_prefix=[str(x) for x in (data.get("argv_prefix") or [])],
            settings_flag=str(data.get("settings_flag") or "").strip(),
            fixed_settings={
                str(k): str(v)
                for k, v in (data.get("fixed_settings") or {}).items()
            },
            settings_map={
                str(k): str(v) for k, v in (data.get("settings_map") or {}).items()
            },
            env_options=_coerce_env_options(data.get("env_options")),
            bool_style=data.get("bool_style", "true_false"),
            log_patterns=LogPatterns(
                player_join=list(patterns.get("player_join") or []),
                player_leave=list(patterns.get("player_leave") or []),
                version_mismatch=list(patterns.get("version_mismatch") or []),
                player_count=list(patterns.get("player_count") or []),
                players_empty=list(patterns.get("players_empty") or []),
                ready=list(patterns.get("ready") or []),
                game_version=list(patterns.get("game_version") or []),
            ),
            log_pattern_candidates=candidates,
            ready_timeout_seconds=int(data.get("ready_timeout_seconds", 180)),
            java_opts_env=data.get("java_opts_env", "JAVA_OPTS"),
            stop_timeout_seconds=int(data.get("stop_timeout_seconds", 60)),
            stop_stdin_commands=[
                str(x) for x in (data.get("stop_stdin_commands") or [])
            ],
            pre_backup_stdin_commands=[
                str(x) for x in (data.get("pre_backup_stdin_commands") or [])
            ],
            min_backup_bytes=int(data.get("min_backup_bytes", 1024)),
            runtime_notes=str(data.get("runtime_notes") or ""),
            world_save=WorldSaveSpec.from_dict(data.get("world_save")),
            config_files=_coerce_config_files(data.get("config_files")),
            world_prepare=WorldPrepareSpec.from_dict(data.get("world_prepare")),
            world_catalog=WorldCatalogSpec.from_dict(data.get("world_catalog")),
            world_create=WorldCreateSpec.from_dict(data.get("world_create")),
            ui_theme=_coerce_ui_theme(data.get("ui_theme")),
            player_tracking_mode=tracking_mode,
            hold_on_crash_loop=bool(data.get("hold_on_crash_loop", False)),
            restart_when_empty=bool(data.get("restart_when_empty", False)),
            copyparty=CopypartySpec.from_dict(data.get("copyparty")),
            status_probe=StatusProbeSpec.from_dict(data.get("status_probe")),
        )

    @property
    def uses_package_install(self) -> bool:
        return self.package_install is not None

    def apply_install_channel_options(self, game_options: Mapping[str, Any] | None) -> None:
        """Apply HA/Docker install-channel overrides onto this plugin in place.

        - ``steam_branch`` — SteamCMD ``-beta`` name (default remains game.yaml)
        - ``release_channel`` — substitutes ``{release_channel}`` in
          ``package_install.version_json_path`` / ``download_url`` (e.g. Factorio
          ``stable`` / ``experimental``)
        """

        options = dict(game_options or {})
        raw_branch = options.get("steam_branch")
        if raw_branch is not None and str(raw_branch).strip():
            branch = str(raw_branch).strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", branch):
                raise ValueError(
                    f"Invalid steam_branch {branch!r}; use letters, digits, "
                    "`.`, `_`, or `-`"
                )
            self.steam_branch = branch

        if self.package_install is None:
            return
        raw_channel = options.get("release_channel")
        channel = (
            str(raw_channel).strip()
            if raw_channel is not None and str(raw_channel).strip()
            else "stable"
        )
        if not re.fullmatch(r"[A-Za-z0-9._-]+", channel):
            raise ValueError(
                f"Invalid release_channel {channel!r}; use letters, digits, "
                "`.`, `_`, or `-`"
            )
        token = "{release_channel}"
        spec = self.package_install
        self.package_install = PackageInstallSpec(
            kind=spec.kind,
            version_url=spec.version_url,
            version_json_path=spec.version_json_path.replace(token, channel),
            download_url=spec.download_url.replace(token, channel),
            strip_components=spec.strip_components,
            version_filename=spec.version_filename,
            version_argv=[item.replace(token, channel) for item in spec.version_argv],
            install_argv=[item.replace(token, channel) for item in spec.install_argv],
        )

    def docker_env_keys(self) -> list[str]:
        """UPPER_SNAKE env var names this game accepts as Docker/compose options.

        Union of explicit ``env_options`` and option keys declared via
        ``arg_map``, ``settings_map``, ``argv_prefix`` / ``fixed_settings`` /
        ``world_save`` ``{placeholders}``. Used by ``load_config(game_env_keys=…)``
        so game-specific names stay out of the base supervisor allowlist.
        """

        keys: set[str] = set()
        for raw in self.env_options:
            name = str(raw or "").strip().upper()
            if name:
                keys.add(name)
        for option_key in self.arg_map:
            keys.add(str(option_key).strip().upper())
        for option_key in self.settings_map:
            keys.add(str(option_key).strip().upper())
        for token in self.argv_prefix:
            keys.update(_template_option_env_keys(token))
        for value in self.fixed_settings.values():
            keys.update(_template_option_env_keys(value))
        if self.world_save is not None:
            for path in self.world_save.paths:
                keys.update(_template_option_env_keys(path))
            world_opt = (self.world_save.world_name_option or "").strip()
            if world_opt:
                keys.add(world_opt.upper())
        for cfg in self.config_files:
            keys.update(_template_option_env_keys(cfg.path))
            keys.update(_collect_template_env_keys(cfg.fixed))
            for option_key in cfg.map:
                keys.add(str(option_key).strip().upper())
            for mod in getattr(cfg, "mods", None) or []:
                option = getattr(mod, "enabled_option", None)
                if option:
                    keys.add(str(option).strip().upper())
        if self.world_prepare is not None:
            for token in self.world_prepare.argv:
                keys.update(_template_option_env_keys(token))
        if self.copyparty is not None:
            keys.update(_template_option_env_keys(self.copyparty.root))
            if self.copyparty.password_option:
                keys.add(self.copyparty.password_option.upper())
        if self.status_probe is not None:
            for token in self.status_probe.argv:
                keys.update(_template_option_env_keys(token))
        # ProcessManager only injects java_opts when argv[0] is java.
        if self.executable and str(self.executable[0]).strip() == "java":
            java_env = (self.java_opts_env or "JAVA_OPTS").strip().upper()
            if java_env:
                keys.add(java_env)
        # Install channel / Steam beta branch (HA options or Docker env).
        keys.add("RELEASE_CHANNEL")
        keys.add("STEAM_BRANCH")
        return sorted(keys)


def _coerce_env_options(raw: Any) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("env_options must be a list of env var names")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip().upper()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _coerce_config_files(raw: Any) -> list[ConfigFileSpec]:
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("config_files must be a list")
    return [ConfigFileSpec.from_dict(item) for item in raw]


def _template_option_env_keys(text: str) -> set[str]:
    return {match.group(1).upper() for match in _OPTION_TEMPLATE_RE.finditer(str(text))}


def _collect_template_env_keys(node: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            keys.update(_collect_template_env_keys(value))
    elif isinstance(node, list):
        for value in node:
            keys.update(_collect_template_env_keys(value))
    elif isinstance(node, str):
        keys.update(_template_option_env_keys(node))
    return keys


def _coerce_ui_theme(raw: Any) -> dict[str, str]:
    """Keep only non-empty string color values from game.yaml ``ui_theme``."""

    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip().lower()
        if not name or not isinstance(value, str):
            continue
        color = value.strip()
        if color:
            out[name] = color
    return out


def _parse_plugin_text(text: str, suffix: str) -> dict[str, Any]:
    stripped = text.lstrip()
    if suffix == ".json" or stripped.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise RuntimeError("Plugin JSON root must be an object")
        return data

    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PyYAML is required for .yaml game plugins. Install python3-yaml."
        ) from exc

    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise RuntimeError("Plugin YAML root must be a mapping")
    return loaded


def load_plugin(path: str | Path) -> GamePlugin:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Game plugin not found: {path}")

    text = path.read_text(encoding="utf-8")
    data = _parse_plugin_text(text, path.suffix.lower())
    return GamePlugin.from_dict(data)


def resolve_plugin_path(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("GAME_PLUGIN")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path("/opt/games/game.yaml"),
            Path("/opt/games/game.json"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No game plugin found. Set GAME_PLUGIN to a plugin YAML/JSON path "
        "(for example /opt/games/game.yaml)."
    )
