"""Core Keeper-only helpers for Home Assistant / Docker defaults.

Kept outside the shared ``game_server`` package so title-specific naming
never leaks into ``game-server-base``.

Join is **both** at once, not XOR. Official ``ARGUMENTS.txt``: ``-port``
makes the server accept Direct Connect (IP); omit ``-port`` and it is Steam
Datagram Relay only. ``-gameid`` is independent. This app always passes
``-port``, so LAN IP, forwarded WAN IP, and Game ID work together.

An empty or invalid ``-gameid`` makes the dedicated server mint a new ID on
start, which would change the join code and strand players. An omitted
``-password`` under Direct Connect would mint a new IP-join password. This
helper pins stable per-install values so friends can keep using the same codes.

The dedicated server is not a player. Pugstorm makes the first character who
joins a new world a full admin (``Admins.json``, privilege 2). Optional
``admin_steam_ids`` merges SteamID64 values into that file so a guest cannot
win first-join admin. Blank leaves first-joiner / in-game star behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

INSTANCE_SALT_NAME = "instance_salt"
# Official ARGUMENTS.txt: 15–28 alphanumeric characters; may not include
# Y, y, x, 0, or O. Generated IDs also skip lookalikes I/l/o so the code
# stays readable; user-pinned IDs only have to satisfy the official rules.
_OFFICIAL_FORBIDDEN = frozenset("Yyx0O")
_GAME_ID_ALPHABET = "abcdefghijkmnpqrstuvwzABCDEFGHJKLMNPQRSTUVWXZ123456789"
_GAME_ID_LENGTH = 20
_GAME_ID_MIN = 15
_GAME_ID_MAX = 28
_PASSWORD_LENGTH = 16
_PASSWORD_MAX = 28
_ADMINS_FILENAME = "Admins.json"
_PRIVILEGE_FULL_ADMIN = 2
# Public-universe individual SteamID64 (what Core Keeper Admins.json stores):
# universe=1, type=Individual, instance=1. SteamID64 = base + 32-bit account id.
# Every value in this range is 17 decimal digits. They happen to start with
# 7656119 until account id exceeds ~2.04e9; after that they run 7656120… up
# to 7656120…. The "starts with 7656119" check is a current-era heuristic,
# not the format — we validate the range instead.
_STEAM64_INDIVIDUAL_BASE = 76561197960265728
_STEAM64_INDIVIDUAL_MAX = _STEAM64_INDIVIDUAL_BASE + 0xFFFFFFFF
_STEAM_ID_SPLIT = re.compile(r"[\s,;]+")


def is_valid_game_id(value: object) -> bool:
    """True when ``value`` is a Game ID the dedicated server will accept."""

    text = str(value or "").strip()
    if not (_GAME_ID_MIN <= len(text) <= _GAME_ID_MAX):
        return False
    if not text.isalnum():
        return False
    return not (set(text) & _OFFICIAL_FORBIDDEN)


def ensure_instance_salt(path: Path) -> str:
    """Return a stable per-install salt; create one if missing.

    Stored on the persistent data volume so the same add-on instance keeps the
    same value across restarts, without hashing host hardware identifiers.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    salt = secrets.token_hex(16)
    path.write_text(salt + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return salt


def default_game_id(
    *,
    state_dir: str | Path | None = None,
) -> str:
    """Build a stable 20-character Game ID from the persisted install salt."""

    root = Path(state_dir or os.environ.get("STATE_DIR") or "/data/supervisor")
    salt = ensure_instance_salt(root / INSTANCE_SALT_NAME)
    digest = hashlib.sha256(f"core-keeper-game-id|{salt}".encode("utf-8")).digest()
    alphabet = _GAME_ID_ALPHABET
    chars: list[str] = []
    # Consume digest bytes, then a counter stream, until we have 20 symbols.
    material = digest
    n = 0
    while len(chars) < _GAME_ID_LENGTH:
        if n >= len(material):
            material += hashlib.sha256(digest + n.to_bytes(4, "big")).digest()
        chars.append(alphabet[material[n] % len(alphabet)])
        n += 1
    return "".join(chars)


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _read_gameid_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("gameid:"):
            return stripped.split(":", 1)[1].strip()
    return text.splitlines()[0].strip()


def _read_server_config_game_id(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("gameId") or "").strip()


def resolve_game_id(
    *,
    options_file: str | Path | None = None,
    state_dir: str | Path | None = None,
    install_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Prefer an explicit Game ID; otherwise reuse a persisted one; else generate.

    Order: valid env ``GAME_ID``, valid HA ``game_id``, ``GameID.txt`` /
    ``GameInfo.txt`` next to the binary, ``ServerConfig.json`` ``gameId``, then
    the salt-derived default. Invalid values are skipped so the dedicated
    server never sees a code it would replace with a random one.
    """

    env = environ if environ is not None else os.environ
    candidates: list[tuple[str, str]] = []

    from_env = str(env.get("GAME_ID") or "").strip()
    if from_env:
        candidates.append(("GAME_ID", from_env))

    path = Path(
        options_file
        or env.get("OPTIONS_FILE")
        or env.get("HASSIO_OPTIONS_FILE")
        or "/data/options.json"
    )
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            from_options = str(data.get("game_id") or "").strip()
            if from_options:
                candidates.append(("options.game_id", from_options))

    game_root = Path(
        install_dir or env.get("INSTALL_DIR") or "/data/game"
    )
    candidates.append(("GameID.txt", _read_gameid_file(game_root / "GameID.txt")))
    candidates.append(("GameInfo.txt", _read_gameid_file(game_root / "GameInfo.txt")))

    world_root = Path(data_dir or env.get("DATA_DIR") or "/data/world")
    candidates.append(
        (
            "ServerConfig.json",
            _read_server_config_game_id(world_root / "ServerConfig.json"),
        )
    )

    for source, value in candidates:
        if not value:
            continue
        if is_valid_game_id(value):
            return value
        _warn(
            f"Ignoring invalid Core Keeper Game ID from {source} "
            f"({len(value)} chars); the dedicated server would mint a new "
            "random code if this were passed through."
        )

    return default_game_id(state_dir=state_dir)


def is_valid_server_password(value: object) -> bool:
    """True when Direct Connect ``-password`` is within Pugstorm's 1–28 limit."""

    text = str(value or "").strip()
    return 1 <= len(text) <= _PASSWORD_MAX


def default_server_password(
    *,
    state_dir: str | Path | None = None,
) -> str:
    """Stable per-install Direct Connect password from the same install salt."""

    root = Path(state_dir or os.environ.get("STATE_DIR") or "/data/supervisor")
    salt = ensure_instance_salt(root / INSTANCE_SALT_NAME)
    digest = hashlib.sha256(f"core-keeper-password|{salt}".encode("utf-8")).digest()
    alphabet = _GAME_ID_ALPHABET
    chars: list[str] = []
    material = digest
    n = 0
    while len(chars) < _PASSWORD_LENGTH:
        if n >= len(material):
            material += hashlib.sha256(digest + n.to_bytes(4, "big")).digest()
        chars.append(alphabet[material[n] % len(alphabet)])
        n += 1
    return "".join(chars)


def resolve_server_password(
    *,
    options_file: str | Path | None = None,
    state_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Prefer an explicit Direct Connect password; otherwise generate a stable one."""

    env = environ if environ is not None else os.environ
    from_env = str(env.get("SERVER_PASSWORD") or "").strip()
    if from_env:
        if is_valid_server_password(from_env):
            return from_env
        _warn(
            "Ignoring invalid Core Keeper join password from SERVER_PASSWORD "
            f"({len(from_env)} chars; max {_PASSWORD_MAX})."
        )

    path = Path(
        options_file
        or env.get("OPTIONS_FILE")
        or env.get("HASSIO_OPTIONS_FILE")
        or "/data/options.json"
    )
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            from_options = str(data.get("server_password") or "").strip()
            if from_options:
                if is_valid_server_password(from_options):
                    return from_options
                _warn(
                    "Ignoring invalid Core Keeper join password from "
                    f"options.server_password ({len(from_options)} chars; "
                    f"max {_PASSWORD_MAX})."
                )

    return default_server_password(state_dir=state_dir)


def is_valid_steam64(value: object) -> bool:
    """True when ``value`` is a public individual SteamID64."""

    text = str(value or "").strip()
    if not text.isdigit() or text.startswith("0"):
        return False
    steam_id = int(text)
    return _STEAM64_INDIVIDUAL_BASE <= steam_id <= _STEAM64_INDIVIDUAL_MAX


def parse_admin_steam_ids(value: object) -> list[int]:
    """Split a comma/space/semicolon list of SteamID64 values; skip invalids."""

    text = str(value or "").strip()
    if not text:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for part in _STEAM_ID_SPLIT.split(text):
        token = part.strip()
        if not token:
            continue
        if not is_valid_steam64(token):
            _warn(
                "Ignoring invalid Core Keeper admin Steam ID "
                f"({token!r}); expected a public individual SteamID64 "
                "(17-digit number from a Steam profile URL)."
            )
            continue
        steam_id = int(token)
        if steam_id in seen:
            continue
        seen.add(steam_id)
        out.append(steam_id)
    return out


def resolve_admin_steam_ids(
    *,
    options_file: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[int]:
    """Prefer env ``ADMIN_STEAM_IDS``, then HA ``admin_steam_ids``."""

    env = environ if environ is not None else os.environ
    from_env = str(env.get("ADMIN_STEAM_IDS") or "").strip()
    if from_env:
        return parse_admin_steam_ids(from_env)

    path = Path(
        options_file
        or env.get("OPTIONS_FILE")
        or env.get("HASSIO_OPTIONS_FILE")
        or "/data/options.json"
    )
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            from_options = str(data.get("admin_steam_ids") or "").strip()
            if from_options:
                return parse_admin_steam_ids(from_options)
    return []


def _entry_steam_id(entry: object) -> int | None:
    if not isinstance(entry, dict):
        return None
    raw = entry.get("steamId", entry.get("steam_id"))
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _entry_index(entry: object) -> int:
    if not isinstance(entry, dict):
        return 0
    try:
        return int(entry.get("index") or 0)
    except (TypeError, ValueError):
        return 0


def ensure_admins(
    *,
    options_file: str | Path | None = None,
    data_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Merge pinned SteamID64 admins into ``Admins.json`` under ``-datapath``.

    Blank option: do not create or rewrite the file (first player to join a
    new world becomes privilege-2 admin; in-game ESC star still works).
    Non-empty: add missing Steam IDs at privilege 2; never delete in-game
    admins; bump privilege to 2 when a pinned ID is already listed lower.
    """

    env = environ if environ is not None else os.environ
    steam_ids = resolve_admin_steam_ids(options_file=options_file, environ=env)
    world_root = Path(data_dir or env.get("DATA_DIR") or "/data/world")
    path = world_root / _ADMINS_FILENAME
    if not steam_ids:
        print(
            "Admins: none pinned; first player to join a new world becomes "
            "admin (in-game ESC player-list star). Set admin_steam_ids to "
            "pin SteamID64 values."
        )
        return None

    world_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"adminList": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            _warn(f"Ignoring unreadable {_ADMINS_FILENAME} ({exc}); rewriting.")
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded

    raw_list = payload.get("adminList")
    admin_list: list[object]
    if isinstance(raw_list, list):
        admin_list = list(raw_list)
    else:
        admin_list = []

    by_id: dict[int, dict] = {}
    max_index = 0
    for entry in admin_list:
        steam_id = _entry_steam_id(entry)
        if steam_id is not None and isinstance(entry, dict):
            by_id[steam_id] = entry
        max_index = max(max_index, _entry_index(entry))

    added = 0
    promoted = 0
    for steam_id in steam_ids:
        existing = by_id.get(steam_id)
        if existing is not None:
            try:
                current = int(existing.get("privileges") or 0)
            except (TypeError, ValueError):
                current = 0
            if current < _PRIVILEGE_FULL_ADMIN:
                existing["privileges"] = _PRIVILEGE_FULL_ADMIN
                existing["steamId"] = steam_id
                promoted += 1
            continue
        max_index += 1
        entry = {
            "index": max_index,
            "privileges": _PRIVILEGE_FULL_ADMIN,
            "name": "",
            "steamId": steam_id,
        }
        admin_list.append(entry)
        by_id[steam_id] = entry
        added += 1

    payload["adminList"] = admin_list
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(
        f"Admins: merged {len(steam_ids)} SteamID64 "
        f"(added {added}, privilege-bumped {promoted}) into {path} "
        f"(privilege {_PRIVILEGE_FULL_ADMIN}). In-game admins were kept."
    )
    return path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    what = (args[0] if args else "game-id").strip().lower().replace("_", "-")
    if what in {"password", "server-password"}:
        print(resolve_server_password())
    elif what in {"ensure-admins", "admins"}:
        ensure_admins()
    else:
        print(resolve_game_id())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
