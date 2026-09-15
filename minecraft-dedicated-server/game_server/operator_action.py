"""Optional Ingress 'operator must do something' card (device-code login, etc.).

Game-layer install/launch scripts may write ``operator_action.json`` under the
supervisor state dir while they block on a human (open a URL, enter a code).
This module only *reads* that file — no game names, OAuth clients, or URLs
are hardcoded here.

Delete the file when the action is finished so the card disappears.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LOG = logging.getLogger("game_server.operator_action")

OPERATOR_ACTION_FILENAME = "operator_action.json"
_STEP_STATES = frozenset({"pending", "active", "done"})
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def operator_action_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / OPERATOR_ACTION_FILENAME


def _safe_http_url(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""
    return text


def _safe_code(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or not _CODE_RE.fullmatch(text):
        return ""
    return text


def _safe_steps(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        state = str(item.get("state") or "pending").strip().lower()
        if state not in _STEP_STATES:
            state = "pending"
        steps.append({"label": label[:80], "state": state})
        if len(steps) >= 8:
            break
    return steps


def read_operator_action(state_dir: str | Path) -> dict[str, Any] | None:
    """Return a sanitized action dict, or None when absent/invalid."""

    path = operator_action_path(state_dir)
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        LOG.debug("Could not read operator action file", exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None

    url = _safe_http_url(payload.get("url"))
    code = _safe_code(payload.get("code"))
    title = str(payload.get("title") or "").strip()[:120]
    detail = str(payload.get("detail") or "").strip()[:400]
    steps = _safe_steps(payload.get("steps"))
    if not url and not code and not title and not detail:
        return None
    if not title:
        title = "Sign in required"
    return {
        "title": title,
        "detail": detail,
        "url": url,
        "code": code,
        "steps": steps,
    }
