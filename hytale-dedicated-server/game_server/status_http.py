"""Status + log-capture HTTP server for Ingress / browser use (no SSH needed)."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .backup import EMPTY_WORLD, backup_generation_key
from .disk import format_bytes
from .lifecycle import LIFECYCLE_HEALTHY
from .log_bridge import strip_ansi
from .version import app_version, supervisor_version

LOG = logging.getLogger("game_server.status_http")

# Home Assistant Ingress proxy source address (Supervisor).
INGRESS_PEER = "172.30.32.2"


def canonical_peer(host: str) -> str:
    """Strip IPv4-mapped IPv6 so Ingress/watchdog allowlists match."""

    raw = (host or "").strip()
    if raw.startswith("::ffff:"):
        raw = raw[7:]
    if raw in {"::1", "https://example.net/id/garnet"}:
        return "127.0.0.1"
    return raw


def peer_is_allowed(host: str) -> bool:
    """Whether this TCP peer may hit Ingress UI/API (not /healthz)."""

    # Outside HA (Portainer/Docker), allow all peers.
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return True
    return canonical_peer(host) == INGRESS_PEER

# Default Ingress palette; games override via ``ui_theme`` in games/game.yaml.
DEFAULT_UI_THEME: dict[str, str] = {
    "bg": "#1a1d24",
    "panel": "#242933",
    "ink": "#e8eaed",
    "muted": "#9aa3b2",
    "accent": "#7aa2f7",
    "good": "#9ece6a",
    "bad": "#f7768e",
    "glow": "#2a3344",
    "wash": "#12151a",
    "depth": "#1a1714",
}
UI_THEME_KEYS = tuple(DEFAULT_UI_THEME.keys())


def resolve_ui_theme(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Merge game ``ui_theme`` overrides onto the base default palette."""

    theme = dict(DEFAULT_UI_THEME)
    if not overrides:
        return theme
    for key in UI_THEME_KEYS:
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            theme[key] = value.strip()
    return theme

def healthz_ok(snapshot: dict[str, Any]) -> bool:
    """Whether /healthz should report ok for this status/health snapshot."""

    if "ok" in snapshot:
        return bool(snapshot["ok"])
    phase = str(snapshot.get("lifecycle") or "")
    if phase:
        return phase in LIFECYCLE_HEALTHY
    # Legacy fallback before lifecycle existed.
    return bool(snapshot.get("running")) or bool(snapshot.get("starting"))


def _html_escape(text: str) -> str:
    return html.escape(str(text), quote=True)


def _as_confirm_flag(value: Any) -> bool:
    """True for JSON true / common truthy confirm encodings."""

    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def _read_http_body(handler: BaseHTTPRequestHandler, *, max_bytes: int = 1_000_000) -> bytes:
    """Read a request body even when Ingress omits Content-Length (chunked)."""

    length_header = handler.headers.get("Content-Length")
    if length_header is not None and str(length_header).strip() != "":
        try:
            length = int(length_header)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        return handler.rfile.read(min(length, max_bytes))

    encoding = (handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" not in encoding:
        return b""

    chunks: list[bytes] = []
    total = 0
    while True:
        size_line = handler.rfile.readline()
        if not size_line:
            break
        size_token = size_line.strip().split(b";", 1)[0]
        try:
            size = int(size_token, 16)
        except ValueError:
            break
        if size == 0:
            # Consume optional trailers through the blank line.
            while True:
                trailer = handler.rfile.readline()
                if trailer in (b"", b"\r\n", b"\n"):
                    break
            break
        if total + size > max_bytes:
            raise ValueError("request body too large")
        chunk = handler.rfile.read(size)
        chunks.append(chunk)
        total += len(chunk)
        handler.rfile.read(2)  # trailing CRLF after each chunk
    return b"".join(chunks)


def _parse_json_object(raw: bytes) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON body") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <base href="{base_href}" />
  <title>{game} server status</title>
  <style>
    :root {{
      --bg: {theme_bg};
      --panel: {theme_panel};
      --ink: {theme_ink};
      --muted: {theme_muted};
      --accent: {theme_accent};
      --good: {theme_good};
      --bad: {theme_bad};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, {theme_glow} 0%, transparent 40%),
        linear-gradient(160deg, {theme_wash}, var(--bg) 55%, {theme_depth});
      color: var(--ink);
      min-height: 100vh;
      padding: 2rem;
    }}
    main {{ max-width: 920px; margin: 0 auto; }}
    h1 {{
      font-family: "IBM Plex Serif", Georgia, serif;
      font-weight: 600;
      font-size: clamp(1.8rem, 4vw, 2.6rem);
      margin: 0 0 0.35rem;
      letter-spacing: -0.02em;
    }}
    h2 {{ margin: 1.75rem 0 0.55rem; font-size: 1.15rem; }}
    .section-label {{
      margin: 0.85rem 0 0.4rem;
      font-size: 0.92rem;
      font-weight: 600;
      color: var(--ink);
    }}
    .sub {{ color: var(--muted); margin-bottom: 1.25rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 1rem;
    }}
    .grid-primary {{ margin-bottom: 0.85rem; }}
    .grid-secondary {{
      margin: 1rem 0 0.25rem;
      gap: 0.75rem;
    }}
    .grid-secondary .stat {{
      padding: 0.75rem 0.9rem;
    }}
    .grid-secondary .stat .value {{ font-size: 1.1rem; }}
    .stat {{
      background: color-mix(in srgb, var(--panel) 88%, black);
      border: 1px solid color-mix(in srgb, var(--muted) 25%, transparent);
      padding: 1rem 1.1rem;
    }}
    .stat .label {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 0.35rem; }}
    .stat .value {{ font-size: 1.35rem; font-weight: 600; }}
    .stat .hint {{ color: var(--muted); font-size: 0.78rem; margin-top: 0.35rem; }}
    .stat .hint:empty {{ display: none; margin: 0; }}
    .operator-action {{
      background: color-mix(in srgb, var(--accent) 14%, var(--panel));
      border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
      padding: 1rem 1.15rem;
      margin: 0 0 1.1rem;
    }}
    .operator-action.hidden {{ display: none; }}
    .operator-action .op-title {{
      font-weight: 600;
      font-size: 1.15rem;
      margin: 0 0 0.35rem;
    }}
    .operator-action .op-detail {{
      color: var(--muted);
      margin: 0 0 0.75rem;
      font-size: 0.92rem;
    }}
    .operator-action .op-detail:empty {{ display: none; }}
    .operator-action .op-code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 1.45rem;
      font-weight: 600;
      letter-spacing: 0.06em;
      margin: 0 0.35rem 0 0;
    }}
    .operator-action .op-code:empty,
    .operator-action .op-code-row.hidden {{ display: none; }}
    .operator-action .op-steps {{
      margin: 0.65rem 0 0;
      padding: 0;
      list-style: none;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .operator-action .op-step-active {{ color: var(--accent); font-weight: 600; }}
    .operator-action .op-step-done {{ color: var(--good); }}
    .live-toast {{
      position: sticky;
      top: 0;
      z-index: 30;
      margin: 0 0 1rem;
      padding: 0.85rem 1rem;
      border-radius: 8px;
      border: 1px solid var(--bad);
      background: color-mix(in srgb, var(--bad) 18%, var(--panel));
      color: var(--ink);
      font-weight: 600;
    }}
    .live-toast.hidden {{ display: none; }}
    .live-toast .live-toast-detail {{
      display: block;
      font-weight: 400;
      color: var(--muted);
      margin-top: 0.3rem;
      font-size: 0.9rem;
    }}
    .good {{ color: var(--good); }}
    .bad {{ color: var(--bad); }}
    .idle {{ color: var(--accent); }}
    .accent {{ color: var(--accent); }}
    .stat .btn-in-card {{
      display: block;
      width: 100%;
      margin-top: 0.55rem;
      padding: 0.35rem 0.55rem;
      font-size: 0.88rem;
      text-align: center;
    }}
    .stat .value.players-last-join {{
      font-size: 1.05rem;
      line-height: 1.25;
    }}
    pre,
    textarea.promote-prompt {{
      background: rgba(0,0,0,0.28);
      padding: 1rem;
      overflow: auto;
      font-size: 0.78rem;
      line-height: 1.4;
      border: 1px solid color-mix(in srgb, var(--muted) 20%, transparent);
      max-height: 320px;
    }}
    textarea.promote-prompt {{
      display: block;
      width: 100%;
      box-sizing: border-box;
      min-height: 12rem;
      margin-top: 0.45rem;
      color: var(--ink);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      resize: vertical;
    }}
    a {{ color: var(--accent); }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin: 0.5rem 0 0.75rem;
    }}
    .btn,
    .actions a, .actions button,
    .capture-row > a, .capture-row > button,
    label.file-btn {{
      display: inline-block;
      padding: 0.45rem 0.75rem;
      border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
      background: transparent;
      color: var(--accent);
      text-decoration: none;
      font: inherit;
      cursor: pointer;
    }}
    .btn:hover,
    .actions a:hover, .actions button:hover,
    .capture-row > a:hover, .capture-row > button:hover,
    label.file-btn:hover {{
      background: color-mix(in srgb, var(--accent) 12%, transparent);
    }}
    .btn:disabled,
    .actions a:disabled, .actions button:disabled,
    .capture-row > a:disabled, .capture-row > button:disabled {{
      opacity: 0.5;
      cursor: not-allowed;
    }}
    .btn-primary {{
      border-color: var(--accent);
      background: color-mix(in srgb, var(--accent) 82%, black);
      color: var(--ink);
      font-weight: 600;
    }}
    .btn-primary:hover {{
      background: color-mix(in srgb, var(--accent) 92%, black);
    }}
    .btn-caution {{
      border-color: color-mix(in srgb, var(--bad) 70%, var(--accent));
      color: color-mix(in srgb, var(--bad) 70%, var(--ink));
    }}
    .btn-caution:hover {{
      background: color-mix(in srgb, var(--bad) 14%, transparent);
    }}
    .btn-ghost {{
      border-color: color-mix(in srgb, var(--muted) 40%, transparent);
      color: var(--muted);
      padding: 0.3rem 0.55rem;
      font-size: 0.9rem;
    }}
    .btn-ghost:hover {{
      background: color-mix(in srgb, var(--muted) 12%, transparent);
      color: var(--ink);
    }}
    .restore-block {{
      margin: 0.35rem 0 1rem;
    }}
    .capture-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: center;
      margin: 0.35rem 0 0.5rem;
    }}
    .capture-row-stack {{
      flex-direction: column;
      align-items: stretch;
      max-width: 36rem;
    }}
    .capture-row-stack select,
    .capture-row-stack .file-picker {{
      width: 100%;
      min-width: 0;
    }}
    .file-picker {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.55rem;
      align-items: center;
    }}
    .capture-row > label:not(.file-btn),
    .capture-row > .row-label {{
      color: var(--muted);
      font-size: 0.9rem;
      min-width: 7.5rem;
    }}
    /* Hide native file control; label.file-btn is the visible picker. */
    .file-input {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      border: 0;
    }}
    .file-name {{
      color: var(--muted);
      font-size: 0.88rem;
      max-width: min(100%, 18rem);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .sub:empty {{ display: none; margin: 0; }}
    select {{
      background: rgba(0,0,0,0.28);
      color: var(--ink);
      border: 1px solid color-mix(in srgb, var(--muted) 35%, transparent);
      padding: 0.45rem 0.6rem;
      font: inherit;
      min-width: min(100%, 28rem);
    }}
    input[type=text] {{
      background: rgba(0,0,0,0.28);
      color: var(--ink);
      border: 1px solid color-mix(in srgb, var(--muted) 35%, transparent);
      padding: 0.45rem 0.6rem;
      font: inherit;
      min-width: min(100%, 16rem);
    }}
    details.trouble {{
      margin-top: 1.5rem;
      color: var(--ink);
      font-size: 1rem;
    }}
    details.trouble > summary {{
      cursor: pointer;
      color: var(--accent);
      font-size: 1.15rem;
      font-weight: 600;
      list-style: disclosure-closed;
    }}
    details.trouble[open] > summary {{
      margin-bottom: 0.6rem;
    }}
    /* Nested expanders share equal weight inside Troubleshooting. */
    details.trouble details.log-watch,
    details.trouble details.unused-patterns {{
      margin-top: 1rem;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    details.trouble details.log-watch > summary,
    details.trouble details.unused-patterns > summary {{
      cursor: pointer;
      color: var(--accent);
      font-size: inherit;
      font-weight: inherit;
    }}
    details.trouble details.log-watch[open] > summary,
    details.trouble details.unused-patterns[open] > summary {{
      margin-bottom: 0.45rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
      margin-top: 0.5rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.4rem 0.45rem;
      border-bottom: 1px solid color-mix(in srgb, var(--muted) 18%, transparent);
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .tag {{
      display: inline-block;
      padding: 0.1rem 0.35rem;
      border: 1px solid color-mix(in srgb, var(--muted) 35%, transparent);
      margin-right: 0.25rem;
      font-size: 0.72rem;
    }}
    .tag.active,
    .tag.configured {{ border-color: var(--good); color: var(--good); }}
    .tag.dry_run,
    .tag.unused,
    .tag.not-configured {{ border-color: var(--accent); color: var(--accent); }}
    .tag.stale {{ border-color: var(--bad); color: var(--bad); }}
    code {{ color: var(--ink); }}
    .hidden {{ display: none !important; }}
    .recent-matches {{
      font-size: 0.75rem;
      line-height: 1.35;
      max-height: 9.5rem;
      overflow: auto;
      max-width: 42rem;
    }}
    .recent-matches .match-line {{
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .recent-matches .match-line.active,
    .recent-matches .match-line.configured {{ color: var(--good); }}
    .recent-matches .match-line.dry_run,
    .recent-matches .match-line.unused,
    .recent-matches .match-line.not-configured {{ color: var(--accent); }}
    @media (max-width: 640px) {{
      body {{ padding: 1rem; }}
      .grid {{
        grid-template-columns: 1fr 1fr;
        gap: 0.65rem;
      }}
      .grid-secondary {{
        grid-template-columns: 1fr;
      }}
      select {{
        min-width: 0;
        width: 100%;
      }}
      .capture-row {{
        align-items: stretch;
      }}
      .capture-row > label:not(.file-btn),
      .capture-row > .row-label {{
        min-width: 0;
      }}
      .file-name {{
        max-width: 100%;
      }}
      table {{
        display: block;
        overflow-x: auto;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <div id="live-toast" class="live-toast hidden" role="status" aria-live="polite">
      This app looks stopped or unresponsive.
      <span class="live-toast-detail" id="live-toast-detail">Live status refresh failed. The cards on this page may be stale until you start the app again.</span>
    </div>
    <h1>{game}</h1>
    <p class="sub" id="subtitle">{subtitle}</p>
    <div class="operator-action {operator_action_class}" id="operator-action">
      <div class="op-title" id="op-title">{operator_action_title}</div>
      <p class="op-detail" id="op-detail">{operator_action_detail}</p>
      <div class="actions">
        <a class="btn btn-primary {operator_action_url_class}" id="op-url" href="{operator_action_url}" target="_blank" rel="noopener noreferrer">Open sign-in page</a>
      </div>
      <div class="capture-row {operator_action_code_class}" id="op-code-row">
        <span class="op-code" id="op-code">{operator_action_code}</span>
        <button type="button" class="btn" id="btn-copy-op-code" onclick="return copyOperatorCode(event)">Copy code</button>
      </div>
      <ol class="op-steps" id="op-steps">{operator_action_steps}</ol>
    </div>
    <div class="grid grid-primary" id="status-grid-primary">
      <div class="stat"><div class="label">Server</div><div class="value {running_class}" id="v-running">{running}</div></div>
      <div class="stat {players_card_class}" id="card-players">
        <div class="label" id="l-players">{players_label}</div>
        <div class="value {players_class}" id="v-players">{players}</div>
        <div class="hint" id="h-players">{players_hint}</div>
      </div>
      <div class="stat">
        <div class="label">Game version</div>
        <div class="value" id="v-game-version">{game_version}</div>
        <div class="hint" id="h-game-version-build">{game_version_build}</div>
        <div class="hint" id="h-game-version-installed">{game_version_installed}</div>
      </div>
      <div class="stat" id="card-update">
        <div class="label">Update</div>
        <div class="value" id="v-update">{update_pending}</div>
        <div class="hint" id="h-update">{update_check_hint}</div>
        <button type="button" class="btn btn-primary btn-in-card {update_btn_class}" id="btn-force-update" onclick="return forceUpdate(event)">
          Update now
        </button>
      </div>
    </div>

    <div class="grid grid-secondary" id="status-grid-secondary">
      <div class="stat">
        <div class="label">Uptime</div>
        <div class="value" id="v-uptime">{uptime}</div>
        <div class="hint" id="h-uptime">{uptime_hint}</div>
      </div>
      <div class="stat">
        <div class="label">Game server crashes</div>
        <div class="value" id="v-crashes">{crashes}</div>
        <div class="hint" id="h-crashes">{crashes_hint}</div>
      </div>
      <div class="stat">
        <div class="label">World save</div>
        <div class="value" id="v-world">{world_save}</div>
        <div class="hint" id="h-world">{world_save_hint}</div>
      </div>
      <div class="stat">
        <div class="label">Backups</div>
        <div class="value" id="v-backups">{backups}</div>
        <div class="hint" id="h-backups-oldest">{backups_oldest}</div>
        <div class="hint" id="h-backups-newest">{backups_newest}</div>
      </div>
      <div class="stat">
        <div class="label">Free disk</div>
        <div class="value {disk_class}" id="v-disk">{disk}</div>
        <div class="hint" id="h-disk">{disk_hint}</div>
      </div>
    </div>

    <h2>Worlds</h2>
    <p class="sub">
      Switching or creating a world stops the game server and starts it on the selected save. Anyone online is disconnected. The app itself stays running.
    </p>
    <div class="restore-block">
      <div class="section-label">Active world</div>
      <div class="capture-row capture-row-stack">
        <label class="hidden" for="world-select">World</label>
        <select id="world-select">{world_options}</select>
        <button type="button" class="btn btn-primary" id="btn-world-switch" onclick="return switchWorld(event)">
          Switch
        </button>
      </div>
    </div>
    <div class="restore-block">
      <div class="section-label">New world</div>
      <div class="capture-row capture-row-stack">
        <label class="hidden" for="world-create-name">Name</label>
        <input type="text" id="world-create-name" placeholder="World name" autocomplete="off" />
        <span id="world-create-fields">{world_create_fields}</span>
        <button type="button" class="btn btn-caution" id="btn-world-create" onclick="return createWorld(event)">
          Create
        </button>
      </div>
    </div>

    <h2>World backups</h2>
    <p class="sub">
      Restoring stops the server, makes a world backup, then restores onto the active world shown above. Anyone online is disconnected. Switch worlds above before restoring a backup from another world.
    </p>
    <div class="restore-block">
      <div class="section-label">Restore from backup</div>
      <div class="capture-row capture-row-stack">
        <label class="hidden" for="backup-select">Saved backup</label>
        <select id="backup-select">{backup_options}</select>
        <button type="button" class="btn btn-caution" id="btn-restore" onclick="return restoreBackup(event)">
          Restore
        </button>
      </div>
    </div>
    <div class="restore-block {world_upload_class}" id="world-upload-row">
      <div class="section-label">Or upload a save</div>
      <div class="capture-row capture-row-stack">
        <div class="file-picker">
          <input type="file" id="world-upload" class="file-input" accept="{world_upload_accept}"
                 onchange="onWorldUploadChosen()" />
          <label class="file-btn" for="world-upload">Choose file</label>
          <span class="file-name" id="world-upload-name">No file chosen</span>
        </div>
        <button type="button" class="btn btn-caution" id="btn-world-upload" onclick="return uploadWorld(event)">
          Restore from upload
        </button>
      </div>
      <p class="sub" id="world-upload-hint">{world_upload_hint}</p>
    </div>

    <details class="trouble" id="troubleshooting">
      <summary>Troubleshooting</summary>
      <details class="log-watch {log_watch_class}" id="log-watch"{log_watch_open}>
        <summary>Game server log watching pattern hits</summary>
        <p class="sub">
          <span class="tag configured">configured</span>
          <span class="tag not-configured">not configured</span>
          <span class="tag stale">stale</span> configured, but no hits this process.
        </p>
        <table>
          <thead>
            <tr><th>Mode</th><th>Category</th><th>Hits</th><th>Recent matches (newest first)</th></tr>
          </thead>
          <tbody id="pattern-rows">
            {pattern_rows}
          </tbody>
        </table>
        <details class="unused-patterns {unused_patterns_class}" id="unused-patterns">
          <summary>Not configured log patterns</summary>
          <table>
            <thead>
              <tr><th>Mode</th><th>Category</th><th>Hits</th><th>Recent matches (newest first)</th></tr>
            </thead>
            <tbody id="unused-pattern-rows">
              {unused_pattern_rows}
            </tbody>
          </table>
        </details>
        <h2>Promote patterns with AI</h2>
        <p class="sub">
          Copy this into an AI chat to propose <code>games/game.yaml</code> regexes and open a GitHub pull request.
          Same text as the Troubleshooting link: live hits plus a log-file rescan (startup lines the live tailer missed).
        </p>
        <textarea id="promote-prompt" class="promote-prompt" readonly rows="18">{promote_prompt}</textarea>
        <div class="actions">
          <button type="button" class="btn" id="btn-copy-prompt" onclick="return copyPromotePrompt(event)">Copy prompt</button>
        </div>
      </details>
      <p class="sub">Capture a snapshot of recent game logs when something looks wrong.</p>
      <div class="actions">
        <a class="btn" href="api/logs/capture" onclick="return postCapture(event)">Capture logs now</a>
      </div>
      <div class="capture-row">
        <label for="capture-select">Saved captures</label>
        <select id="capture-select">{capture_options}</select>
        <a class="btn" id="capture-download" href="#" onclick="return downloadCapture(event)">Download</a>
      </div>
      <p class="sub">
        <a href="api/logs/prompt">Log pattern prompt</a>
        — plain text for an AI to update <code>games/game.yaml</code>.
        Rescans the on-disk log (includes lines before the live tailer started).
        Debug mode only unhides the pattern table above.
      </p>
    </details>
  </main>
  <script>
    async function postCapture(ev) {{
      ev.preventDefault();
      const res = await fetch('api/logs/capture', {{ method: 'POST' }});
      const data = await res.json();
      if (data.download_path) {{
        window.location = data.download_path.replace(/^\\//, '');
      }} else {{
        alert(JSON.stringify(data, null, 2));
      }}
      return false;
    }}
    async function restoreBackup(ev) {{
      ev.preventDefault();
      const select = document.getElementById('backup-select');
      if (!select || !select.value) {{
        alert('No backup selected');
        return false;
      }}
      const name = select.value;
      const emptyWorld = name === '{empty_world_token}';
      const ok = window.confirm(
        emptyWorld
          ? (
            'Start a new empty world?\\n\\n' +
            'The server will stop and keep a safety copy of any existing world first. ' +
            'Anyone playing will be disconnected.'
          )
          : (
            'Restore this backup over the live world?\\n\\n' +
            name + '\\n\\n' +
            'The server will stop and keep a safety copy first. ' +
            'Anyone playing will be disconnected.'
          )
      );
      if (!ok) return false;
      const btn = document.getElementById('btn-restore');
      if (btn) btn.disabled = true;
      try {{
        // Query-string mirrors the JSON body: some Ingress paths drop/omit the
        // POST body (missing Content-Length), which used to look like a failed confirm.
        const qs = emptyWorld
          ? 'empty=1&confirm=1'
          : ('archive=' + encodeURIComponent(name) + '&confirm=1');
        const body = emptyWorld
          ? {{ empty: true, confirm: true }}
          : {{ archive: name, confirm: true }};
        const res = await fetch('api/backups/restore?' + qs, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(body),
        }});
        const data = await res.json();
        if (data.ok) {{
          softRefresh();
        }} else {{
          alert(data.error || (emptyWorld ? 'Could not schedule empty-world reset.' : 'Could not schedule restore.'));
        }}
      }} catch (e) {{
        alert(emptyWorld ? 'Could not schedule empty-world reset.' : 'Could not schedule restore.');
      }} finally {{
        if (btn) btn.disabled = false;
      }}
      return false;
    }}
    function onWorldUploadChosen() {{
      const input = document.getElementById('world-upload');
      const name = document.getElementById('world-upload-name');
      if (!name) return;
      if (input && input.files && input.files[0]) {{
        name.textContent = input.files[0].name;
      }} else {{
        name.textContent = 'No file chosen';
      }}
    }}
    async function uploadWorld(ev) {{
      ev.preventDefault();
      const input = document.getElementById('world-upload');
      if (!input || !input.files || !input.files[0]) {{
        alert('Choose a world save file to upload');
        return false;
      }}
      const file = input.files[0];
      const ok = window.confirm(
        'Restore the live world from this upload?\\n\\n' +
        file.name + '\\n\\n' +
        'The server will stop and keep a safety copy first. ' +
        'Anyone playing will be disconnected.'
      );
      if (!ok) return false;
      const btn = document.getElementById('btn-world-upload');
      if (btn) btn.disabled = true;
      try {{
        const res = await fetch(
          'api/world/upload?confirm=1&filename=' + encodeURIComponent(file.name),
          {{
            method: 'POST',
            headers: {{
              'Content-Type': file.type || 'application/octet-stream',
            }},
            body: file,
          }}
        );
        const data = await res.json();
        if (data.ok) {{
          softRefresh();
        }} else {{
          alert(data.error || 'Could not schedule world upload.');
        }}
      }} catch (e) {{
        alert('Could not schedule world upload.');
      }} finally {{
        if (btn) btn.disabled = false;
      }}
      return false;
    }}
    async function switchWorld(ev) {{
      ev.preventDefault();
      const sel = document.getElementById('world-select');
      const name = sel ? sel.value : '';
      if (!name) {{
        alert('Choose a world');
        return false;
      }}
      const ok = window.confirm(
        'Switch to world ' + name + '?\\n\\n' +
        'The game server will restart. Anyone playing will be disconnected.'
      );
      if (!ok) return false;
      const btn = document.getElementById('btn-world-switch');
      if (btn) btn.disabled = true;
      try {{
        const res = await fetch('api/worlds/switch', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ name: name }}),
        }});
        const data = await res.json();
        if (data.ok) {{
          softRefresh();
        }} else {{
          alert(data.error || 'Could not switch world.');
        }}
      }} catch (e) {{
        alert('Could not switch world.');
      }} finally {{
        if (btn) btn.disabled = false;
      }}
      return false;
    }}
    async function createWorld(ev) {{
      ev.preventDefault();
      const nameEl = document.getElementById('world-create-name');
      const name = nameEl ? nameEl.value.trim() : '';
      if (!name) {{
        alert('Enter a world name');
        return false;
      }}
      const fields = {{}};
      document.querySelectorAll('[data-world-create-field]').forEach((el) => {{
        const key = el.getAttribute('data-world-create-field');
        if (key) fields[{{key}}] = el.value;
      }});
      const ok = window.confirm(
        'Create world ' + name + '?\\n\\n' +
        'The game server will restart. Anyone playing will be disconnected.'
      );
      if (!ok) return false;
      const btn = document.getElementById('btn-world-create');
      if (btn) btn.disabled = true;
      try {{
        const res = await fetch('api/worlds/create', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ name: name, fields: fields }}),
        }});
        const data = await res.json();
        if (data.ok) {{
          softRefresh();
        }} else {{
          alert(data.error || 'Could not create world.');
        }}
      }} catch (e) {{
        alert('Could not create world.');
      }} finally {{
        if (btn) btn.disabled = false;
      }}
      return false;
    }}
    async function forceUpdate(ev) {{
      ev.preventDefault();
      const ok = window.confirm(
        'Update the game server now?\\n\\n' +
        'The server will stop, download the latest build, and restart. ' +
        'Anyone playing will be disconnected.'
      );
      if (!ok) return false;
      const btn = document.getElementById('btn-force-update');
      if (btn) btn.disabled = true;
      try {{
        const res = await fetch('api/update', {{ method: 'POST' }});
        const data = await res.json();
        if (data.ok) {{
          softRefresh();
        }} else {{
          alert(data.error || 'Could not schedule update.');
        }}
      }} catch (e) {{
        alert('Could not schedule update.');
      }} finally {{
        if (btn) btn.disabled = false;
      }}
      return false;
    }}
    async function copyPromotePrompt(ev) {{
      ev.preventDefault();
      const el = document.getElementById('promote-prompt');
      const btn = document.getElementById('btn-copy-prompt');
      const text = el ? el.value : '';
      if (!text) return false;
      try {{
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          await navigator.clipboard.writeText(text);
        }} else if (el) {{
          el.focus();
          el.select();
          document.execCommand('copy');
        }}
        if (btn) {{
          const prev = btn.textContent;
          btn.textContent = 'Copied';
          setTimeout(() => {{ btn.textContent = prev || 'Copy prompt'; }}, 1500);
        }}
      }} catch (e) {{
        if (el) {{
          el.focus();
          el.select();
        }}
        alert('Copy the prompt from the text box.');
      }}
      return false;
    }}
    function downloadCapture(ev) {{
      ev.preventDefault();
      const select = document.getElementById('capture-select');
      if (!select || !select.value) {{
        alert('No capture selected');
        return false;
      }}
      window.location = select.value;
      return false;
    }}
    function setText(id, value) {{
      const el = document.getElementById(id);
      if (el) el.textContent = value == null ? '' : String(value);
    }}
    function setHtml(id, value) {{
      const el = document.getElementById(id);
      if (el) el.innerHTML = value == null ? '' : String(value);
    }}
    async function copyOperatorCode(ev) {{
      ev.preventDefault();
      const code = (document.getElementById('op-code') || {{}}).textContent || '';
      const text = code.trim();
      if (!text) return false;
      try {{
        await navigator.clipboard.writeText(text);
      }} catch (e) {{
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        try {{ document.execCommand('copy'); }} catch (e2) {{}}
        document.body.removeChild(area);
      }}
      return false;
    }}
    function setLiveStatus(ok, detail) {{
      const toast = document.getElementById('live-toast');
      const detailEl = document.getElementById('live-toast-detail');
      if (!toast) return;
      toast.classList.toggle('hidden', !!ok);
      if (detailEl && detail) detailEl.textContent = detail;
    }}
    async function softRefresh() {{
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 8000);
      try {{
        const res = await fetch('api/ui', {{
          headers: {{ 'Accept': 'application/json' }},
          signal: ctl.signal,
        }});
        if (!res.ok) {{
          setLiveStatus(false, 'Live status refresh failed (HTTP ' + res.status + '). This app looks stopped or unresponsive.');
          return;
        }}
        const u = await res.json();
        setLiveStatus(true);
        setText('subtitle', u.subtitle);
        const opCard = document.getElementById('operator-action');
        if (opCard) {{
          opCard.classList.toggle('hidden', !!u.operator_action_hidden);
        }}
        setText('op-title', u.operator_action_title);
        setText('op-detail', u.operator_action_detail);
        setText('op-code', u.operator_action_code);
        const opUrl = document.getElementById('op-url');
        if (opUrl) {{
          opUrl.className = 'btn btn-primary ' + (u.operator_action_url_class || '');
          if (u.operator_action_url) opUrl.setAttribute('href', u.operator_action_url);
        }}
        const opCodeRow = document.getElementById('op-code-row');
        if (opCodeRow) {{
          opCodeRow.classList.toggle('hidden', !!u.operator_action_code_hidden);
        }}
        setHtml('op-steps', u.operator_action_steps);
        const running = document.getElementById('v-running');
        if (running) {{
          running.textContent = u.running;
          running.className = 'value ' + (u.running_class || '');
        }}
        setText('l-players', u.players_label);
        const playersVal = document.getElementById('v-players');
        if (playersVal) {{
          playersVal.textContent = u.players == null ? '' : String(u.players);
          playersVal.className = 'value ' + (u.players_class || '');
        }}
        setText('h-players', u.players_hint);
        const playersCard = document.getElementById('card-players');
        if (playersCard) {{
          playersCard.classList.toggle('hidden', !!u.players_card_hidden);
        }}
        setText('v-uptime', u.uptime);
        setText('h-uptime', u.uptime_hint);
        setText('v-crashes', u.crashes);
        setText('h-crashes', u.crashes_hint);
        setText('v-game-version', u.game_version);
        setText('h-game-version-build', u.game_version_build);
        setText('h-game-version-installed', u.game_version_installed);
        setText('v-update', u.update_pending);
        setText('h-update', u.update_check_hint);
        const updateBtn = document.getElementById('btn-force-update');
        if (updateBtn) {{
          updateBtn.classList.toggle('hidden', !!u.update_btn_hidden);
        }}
        setText('v-backups', u.backups);
        setText('h-backups-oldest', u.backups_oldest);
        setText('h-backups-newest', u.backups_newest);
        const logWatch = document.getElementById('log-watch');
        if (logWatch) {{
          logWatch.classList.toggle('hidden', !!u.log_watch_hidden);
        }}
        setText('v-world', u.world_save);
        setHtml('h-world', u.world_save_hint);
        setText('world-upload-hint', u.world_upload_hint);
        const uploadRow = document.getElementById('world-upload-row');
        if (uploadRow) {{
          uploadRow.classList.toggle('hidden', !!u.world_upload_hidden);
        }}
        const uploadInput = document.getElementById('world-upload');
        if (uploadInput && u.world_upload_accept) {{
          uploadInput.accept = u.world_upload_accept;
        }}
        const disk = document.getElementById('v-disk');
        if (disk) {{
          disk.textContent = u.disk;
          disk.className = 'value ' + (u.disk_class || '');
        }}
        setText('h-disk', u.disk_hint);
        setHtml('pattern-rows', u.pattern_rows);
        setHtml('unused-pattern-rows', u.unused_pattern_rows);
        const unusedBox = document.getElementById('unused-patterns');
        if (unusedBox) {{
          unusedBox.classList.toggle('hidden', !!u.unused_patterns_hidden);
        }}
        const prompt = document.getElementById('promote-prompt');
        if (prompt) prompt.value = u.promote_prompt || '';
        const sel = document.getElementById('capture-select');
        if (sel && u.capture_options) {{
          const prev = sel.value;
          sel.innerHTML = u.capture_options;
          if (prev) sel.value = prev;
        }}
        const bsel = document.getElementById('backup-select');
        if (bsel && u.backup_options) {{
          const prev = bsel.value;
          bsel.innerHTML = u.backup_options;
          if (prev) bsel.value = prev;
        }}
        const wsel = document.getElementById('world-select');
        if (wsel && u.world_options) {{
          const prev = wsel.value;
          wsel.innerHTML = u.world_options;
          if (prev) wsel.value = prev;
        }}
        const wfields = document.getElementById('world-create-fields');
        if (wfields && u.world_create_fields != null) {{
          wfields.innerHTML = u.world_create_fields;
        }}
      }} catch (e) {{
        setLiveStatus(false, 'Live status refresh failed. This app looks stopped or unresponsive; the cards on this page may be stale.');
      }} finally {{
        clearTimeout(timer);
      }}
    }}
    setInterval(softRefresh, 5000);
  </script>
</body>
</html>
"""



class StatusServer:
    def __init__(
        self,
        host: str,
        port: int,
        status_provider: Callable[[], dict[str, Any]],
        *,
        health_provider: Callable[[], dict[str, Any]] | None = None,
        game_name: str = "Game",
        ui_theme: dict[str, str] | None = None,
        log_toolbox=None,
        capture_callback: Callable[[str], dict[str, Any]] | None = None,
        update_callback: Callable[[], dict[str, Any]] | None = None,
        restore_callback: Callable[[str], dict[str, Any]] | None = None,
        upload_callback: Callable[[Path], dict[str, Any]] | None = None,
        upload_staging_dir: str | Path | None = None,
        world_download_callback: Callable[[], dict[str, Any] | None] | None = None,
        restart_callback: Callable[..., dict[str, Any]] | None = None,
        world_switch_callback: Callable[[str], dict[str, Any]] | None = None,
        world_create_callback: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.status_provider = status_provider
        self.health_provider = health_provider
        self.game_name = game_name
        self.ui_theme = resolve_ui_theme(ui_theme)
        self.log_toolbox = log_toolbox
        self.capture_callback = capture_callback
        self.update_callback = update_callback
        self.restore_callback = restore_callback
        self.upload_callback = upload_callback
        self.upload_staging_dir = Path(upload_staging_dir) if upload_staging_dir else None
        self.world_download_callback = world_download_callback
        self.restart_callback = restart_callback
        self.world_switch_callback = world_switch_callback
        self.world_create_callback = world_create_callback
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        provider = self.status_provider
        health = self.health_provider
        game_name = self.game_name
        ui_theme = self.ui_theme
        toolbox = self.log_toolbox
        capture_cb = self.capture_callback
        update_cb = self.update_callback
        restore_cb = self.restore_callback
        upload_cb = self.upload_callback
        upload_dir = self.upload_staging_dir
        world_dl_cb = self.world_download_callback
        restart_cb = self.restart_callback
        world_switch_cb = self.world_switch_callback
        world_create_cb = self.world_create_callback

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                LOG.debug("%s - %s", self.address_string(), fmt % args)

            def _peer_allowed(self) -> bool:
                # Ingress UI/API: Supervisor proxy only. /healthz is exempt so
                # HA watchdog and in-container Docker healthchecks (127.0.0.1)
                # are not 403'd when SUPERVISOR_TOKEN is set.
                return peer_is_allowed(self.client_address[0])

            def _ingress_base(self) -> str:
                raw = (self.headers.get("X-Ingress-Path") or "").strip()
                if not raw:
                    return "/"
                if not raw.startswith("/"):
                    raw = "/" + raw
                return raw.rstrip("/") + "/"

            def _send(
                self,
                code: int,
                body: bytes,
                content_type: str,
                headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if headers:
                    for key, value in headers.items():
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, payload: Any) -> None:
                body = json.dumps(payload, indent=2, default=str).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")

            def _send_file(
                self,
                path: Path,
                *,
                filename: str,
                content_type: str,
            ) -> None:
                data_path = Path(path)
                size = data_path.stat().st_size
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "download"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{safe_name}"',
                )
                self.end_headers()
                with data_path.open("rb") as fh:
                    shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                peer = canonical_peer(self.client_address[0])
                if not self._peer_allowed():
                    if not (
                        path == "/api/restart"
                        and peer == "127.0.0.1"
                        and restart_cb is not None
                    ):
                        self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
                        return
                if path == "/api/logs/capture":
                    if capture_cb is None:
                        self._json(501, {"error": "log capture unavailable"})
                        return
                    self._json(200, capture_cb("manual"))
                    return
                if path == "/api/update":
                    if update_cb is None:
                        self._json(501, {"ok": False, "error": "manual update unavailable"})
                        return
                    result = update_cb()
                    self._json(200 if result.get("ok") else 409, result)
                    return
                if path == "/api/restart":
                    if restart_cb is None:
                        self._json(501, {"ok": False, "error": "restart unavailable"})
                        return
                    try:
                        payload = _parse_json_object(_read_http_body(self))
                    except ValueError:
                        payload = {}
                    debounce = payload.get("debounce_seconds") or 0
                    reason = str(payload.get("reason") or "manual")
                    try:
                        debounce_f = float(debounce)
                    except (TypeError, ValueError):
                        debounce_f = 0.0
                    result = restart_cb(reason, debounce_seconds=debounce_f)
                    self._json(200 if result.get("ok") else 409, result)
                    return
                if path == "/api/worlds/switch":
                    if world_switch_cb is None:
                        self._json(501, {"ok": False, "error": "world switch unavailable"})
                        return
                    try:
                        payload = _parse_json_object(_read_http_body(self))
                    except ValueError as exc:
                        self._json(400, {"ok": False, "error": str(exc)})
                        return
                    name = str(payload.get("name") or payload.get("world") or "").strip()
                    result = world_switch_cb(name)
                    self._json(200 if result.get("ok") else 409, result)
                    return
                if path == "/api/worlds/create":
                    if world_create_cb is None:
                        self._json(501, {"ok": False, "error": "world create unavailable"})
                        return
                    try:
                        payload = _parse_json_object(_read_http_body(self))
                    except ValueError as exc:
                        self._json(400, {"ok": False, "error": str(exc)})
                        return
                    name = str(payload.get("name") or payload.get("world") or "").strip()
                    fields = payload.get("fields")
                    extra = fields if isinstance(fields, dict) else {}
                    result = world_create_cb(name, extra)
                    self._json(200 if result.get("ok") else 409, result)
                    return
                if path == "/api/world/upload":
                    if upload_cb is None or upload_dir is None:
                        self._json(
                            501, {"ok": False, "error": "world upload unavailable"}
                        )
                        return
                    parsed = urlparse(self.path)
                    query = parse_qs(parsed.query)
                    if not _as_confirm_flag((query.get("confirm") or [""])[0]):
                        self._json(
                            400,
                            {
                                "ok": False,
                                "error": (
                                    "Could not confirm the upload request. "
                                    "Try again from OPEN WEB UI."
                                ),
                            },
                        )
                        return
                    length_header = self.headers.get("Content-Length")
                    if length_header is None or str(length_header).strip() == "":
                        self._json(
                            411,
                            {
                                "ok": False,
                                "error": (
                                    "Content-Length required for world upload "
                                    "(Ingress must forward the file body)"
                                ),
                            },
                        )
                        return
                    try:
                        length = int(length_header)
                    except (TypeError, ValueError):
                        length = -1
                    if length <= 0:
                        self._json(
                            400, {"ok": False, "error": "empty world upload"}
                        )
                        return
                    # Hard cap: 8 GiB. Disk free is still checked by backup manager.
                    max_upload = 8 * 1024 * 1024 * 1024
                    if length > max_upload:
                        self._json(
                            413,
                            {
                                "ok": False,
                                "error": f"upload too large (max {max_upload} bytes)",
                            },
                        )
                        return
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    filename = (query.get("filename") or ["world-upload.bin"])[0]
                    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
                    safe = safe.strip("._") or "world-upload.bin"
                    staged = upload_dir / f"upload-pending-{stamp}-{safe}"
                    tmp = staged.with_suffix(staged.suffix + ".partial")
                    remaining = length
                    try:
                        with tmp.open("wb") as out:
                            while remaining > 0:
                                chunk = self.rfile.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                out.write(chunk)
                                remaining -= len(chunk)
                        if remaining != 0:
                            tmp.unlink(missing_ok=True)
                            self._json(
                                400,
                                {
                                    "ok": False,
                                    "error": "upload ended before Content-Length bytes",
                                },
                            )
                            return
                        tmp.replace(staged)
                    except OSError as exc:
                        tmp.unlink(missing_ok=True)
                        staged.unlink(missing_ok=True)
                        self._json(
                            500, {"ok": False, "error": f"failed to stage upload: {exc}"}
                        )
                        return
                    try:
                        result = upload_cb(staged)
                    except Exception as exc:  # noqa: BLE001
                        staged.unlink(missing_ok=True)
                        self._json(
                            500, {"ok": False, "error": f"upload schedule failed: {exc}"}
                        )
                        return
                    if not result.get("ok"):
                        staged.unlink(missing_ok=True)
                    self._json(200 if result.get("ok") else 409, result)
                    return
                if path == "/api/backups/restore":
                    if restore_cb is None:
                        self._json(
                            501, {"ok": False, "error": "restore unavailable"}
                        )
                        return
                    parsed = urlparse(self.path)
                    query = parse_qs(parsed.query)
                    try:
                        payload = _parse_json_object(_read_http_body(self))
                    except ValueError as exc:
                        self._json(400, {"ok": False, "error": str(exc)})
                        return
                    # Prefer JSON body; fall back to query string when Ingress
                    # delivers an empty body (common when Content-Length is lost).
                    if "archive" not in payload and "name" not in payload:
                        archive_q = (query.get("archive") or [""])[0]
                        if archive_q:
                            payload["archive"] = archive_q
                    if "empty" not in payload and (query.get("empty") or [""])[0]:
                        payload["empty"] = (query.get("empty") or [""])[0]
                    if "confirm" not in payload and (query.get("confirm") or [""])[0]:
                        payload["confirm"] = (query.get("confirm") or [""])[0]

                    archive = str(
                        payload.get("archive") or payload.get("name") or ""
                    ).strip()
                    empty_raw = payload.get("empty")
                    empty = bool(empty_raw) and str(empty_raw).strip().lower() not in {
                        "0",
                        "false",
                        "no",
                        "off",
                        "",
                    }
                    if not _as_confirm_flag(payload.get("confirm")):
                        self._json(
                            400,
                            {
                                "ok": False,
                                "error": (
                                    "Could not confirm the restore request. "
                                    "Try again from OPEN WEB UI."
                                ),
                            },
                        )
                        return
                    if empty and archive:
                        self._json(
                            400,
                            {
                                "ok": False,
                                "error": "pass either empty:true or an archive name, not both",
                            },
                        )
                        return
                    if empty:
                        result = restore_cb(EMPTY_WORLD)
                        self._json(200 if result.get("ok") else 409, result)
                        return
                    if not archive:
                        self._json(
                            400,
                            {
                                "ok": False,
                                "error": "missing archive name (or set empty:true)",
                            },
                        )
                        return
                    result = restore_cb(archive)
                    self._json(200 if result.get("ok") else 409, result)
                    return
                self._json(404, {"error": "not found"})

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                # Probe paths must stay reachable from localhost and Supervisor.
                if path not in ("/healthz", "/health") and not self._peer_allowed():
                    self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
                    return

                if path in ("/healthz", "/health"):
                    # Cheap path: avoid full status() disk/manifest scans.
                    snapshot = health() if health is not None else provider()
                    ok = healthz_ok(snapshot)
                    payload = b"ok\n" if ok else b"degraded\n"
                    self._send(200 if ok else 503, payload, "text/plain; charset=utf-8")
                    return

                status = provider()

                if path in ("/api/status", "/status.json"):
                    self._json(200, status)
                    return

                if path == "/api/ui":
                    self._json(
                        200,
                        _ui_view(
                            status,
                            game_name,
                            ui_theme=ui_theme,
                            toolbox=toolbox,
                        ),
                    )
                    return

                if path == "/api/world/download":
                    if world_dl_cb is None:
                        self._json(
                            501, {"error": "world save download unavailable"}
                        )
                        return
                    info = world_dl_cb()
                    if not info:
                        self._json(
                            404, {"error": "world save not available for download"}
                        )
                        return
                    file_path = Path(str(info["path"]))
                    cleanup = info.get("cleanup_path")
                    try:
                        self._send_file(
                            file_path,
                            filename=str(info.get("filename") or file_path.name),
                            content_type=str(
                                info.get("content_type") or "application/octet-stream"
                            ),
                        )
                    finally:
                        if cleanup:
                            Path(str(cleanup)).unlink(missing_ok=True)
                    return

                if path == "/api/logs/prompt":
                    text = _log_pattern_prompt(status, toolbox, game_name)
                    self._send(
                        200,
                        text.encode("utf-8"),
                        "text/plain; charset=utf-8",
                    )
                    return

                if path == "/api/logs/capture":
                    # GET must not mutate state (CSRF / link-prefetch hazard).
                    self._json(
                        405,
                        {
                            "error": "Use POST /api/logs/capture to create a capture",
                        },
                    )
                    return

                if path.startswith("/api/logs/captures/") and path.endswith("/download"):
                    if toolbox is None:
                        self._json(501, {"error": "log toolbox unavailable"})
                        return
                    capture_id = path[len("/api/logs/captures/") : -len("/download")]
                    archive = toolbox.capture_archive_path(capture_id)
                    if archive is None:
                        self._json(404, {"error": "capture not found"})
                        return
                    data = archive.read_bytes()
                    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", capture_id) or "capture"
                    self._send(
                        200,
                        data,
                        "application/gzip",
                        headers={
                            "Content-Disposition": (
                                f'attachment; filename="{safe_name}.tar.gz"'
                            )
                        },
                    )
                    return

                if path in ("/", "/index.html", "/ingress"):
                    view = _ui_view(
                        status,
                        game_name,
                        ui_theme=ui_theme,
                        toolbox=toolbox,
                    )
                    html = render_status_html(
                        view, base_href=self._ingress_base()
                    ).encode("utf-8")
                    self._send(200, html, "text/html; charset=utf-8")
                    return

                self._send(404, b"not found\n", "text/plain; charset=utf-8")

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="status-http", daemon=True
        )
        self._thread.start()
        LOG.info("Status HTTP listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None


def _fmt_seconds(value: Any) -> str:
    try:
        total = int(value)
    except (TypeError, ValueError):
        return "0s"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _fmt_ago(timestamp: Any, *, now: float | None = None) -> str:
    """Human relative time like '12m ago' / '3d ago' from a unix timestamp."""

    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return "unknown"
    if ts <= 0:
        return "unknown"
    age = max(0, int((now if now is not None else time.time()) - ts))
    if age < 45:
        return "just now"
    if age < 3600:
        minutes = max(1, age // 60)
        return f"{minutes}m ago"
    if age < 86400:
        hours = max(1, age // 3600)
        return f"{hours}h ago"
    if age < 86400 * 14:
        days = max(1, age // 86400)
        return f"{days}d ago"
    if age < 86400 * 60:
        weeks = max(1, age // (86400 * 7))
        return f"{weeks}w ago"
    months = max(1, age // (86400 * 30))
    return f"{months}mo ago"


def _format_subtitle(status: dict[str, Any]) -> str:
    supervisor = str(
        status.get("supervisor_version") or supervisor_version()
    ).strip()
    app = str(status.get("app_version") or app_version()).strip()
    subtitle = f"Dedicated server supervisor {supervisor}"
    if app and app not in {"unknown", "", supervisor}:
        subtitle += f" · app {app}"
    method = str(status.get("install_method") or "steamcmd").strip().lower()
    if method == "package":
        channel = str(status.get("release_channel") or "").strip()
        if channel:
            subtitle += f" · {channel} channel"
    else:
        steamcmd_ver = str(status.get("steamcmd_version") or "").strip()
        if steamcmd_ver:
            subtitle += f" · SteamCMD {steamcmd_ver}"
    return subtitle


def _format_game_version(status: dict[str, Any]) -> tuple[str, str, str]:
    """Return (human version, build hint, installed-ago hint)."""

    monitor = status.get("monitor") or {}
    version = str(
        status.get("game_version") or monitor.get("game_version") or ""
    ).strip()
    if not version:
        version = "unknown"
    build = str(status.get("local_build_id") or "").strip()
    method = str(status.get("install_method") or "steamcmd").strip().lower()
    if build:
        if method == "package":
            # Package games often use the version string as the build id.
            build_hint = "" if build == version else f"Package {build}"
        else:
            build_hint = f"Steam build {build}"
    else:
        build_hint = ""
    install_ts = status.get("install_last_updated_at")
    applied_ts = status.get("last_update_applied_at")
    if install_ts:
        installed_hint = f"Installed {_fmt_ago(install_ts)}"
    elif applied_ts:
        installed_hint = f"Installed {_fmt_ago(applied_ts)}"
    else:
        installed_hint = ""
    return version, build_hint, installed_hint


def _format_uptime(status: dict[str, Any]) -> tuple[str, str]:
    if status.get("running"):
        value = _fmt_seconds(status.get("game_uptime_seconds", 0))
    else:
        value = "—"
    reason = str(status.get("last_start_reason") or "boot")
    if reason == "crash":
        hint = "Since crash restart"
    elif reason in ("update", "update_failed"):
        hint = "Since server update"
    elif reason in ("restore", "restore_failed"):
        hint = "Since world restore"
    else:
        hint = "Since first start"
    return value, hint


def _format_crashes_hint(status: dict[str, Any]) -> str:
    supervisor = _fmt_seconds(status.get("supervisor_uptime_seconds", 0))
    return f"Supervisor uptime: {supervisor}"


def _format_backups(status: dict[str, Any]) -> tuple[str, str, str]:
    """Return (count, oldest hint, newest hint).

    Count matches restore-dropdown options excluding NEW WORLD (scheduled,
    pre-update, and pre-restore archives).
    """

    info = status.get("backups") or {}
    restorable = info.get("restorable") or []
    named: list[dict[str, Any]] = []
    if isinstance(restorable, list):
        for item in restorable:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                named.append(item)
    count = len(named)
    if count <= 0:
        return "0", "No backups yet", ""
    mtimes: list[float] = []
    for item in named:
        raw = item.get("mtime")
        try:
            if raw is not None:
                mtimes.append(float(raw))
        except (TypeError, ValueError):
            continue
    # Fall back to summary timestamps when restorable entries lack mtime.
    oldest_at = min(mtimes) if mtimes else info.get("oldest_backup_at")
    newest_at = max(mtimes) if mtimes else info.get("newest_backup_at")
    oldest = (
        f"Oldest: {_fmt_ago(oldest_at)}"
        if oldest_at
        else "Oldest: unknown"
    )
    newest = (
        f"Newest: {_fmt_ago(newest_at)}"
        if newest_at
        else "Newest: unknown"
    )
    return str(count), oldest, newest


def _format_update_check_hint(status: dict[str, Any]) -> str:
    checked_at = status.get("last_update_check_at")
    interval = int(status.get("auto_update_interval_minutes") or 0)
    check_hour = status.get("auto_update_check_hour")
    error = status.get("last_update_error")
    if checked_at:
        hint = f"Checked {_fmt_ago(checked_at)}"
        if status.get("update_pending") and status.get("update_reason"):
            hint += f" · {status.get('update_reason')}"
        elif error and not status.get("update_pending"):
            # Keep this short; full error remains in status JSON.
            hint += " · last check had an error"
        return hint
    if interval <= 0:
        return "Update checks disabled"
    if check_hour is not None:
        try:
            hour = max(0, min(23, int(check_hour)))
        except (TypeError, ValueError):
            hour = 5
        return f"Next update check around {hour:02d}:00 local"
    return "Not checked yet"


def _format_disk(status: dict[str, Any]) -> tuple[str, str, str]:
    """Return (value, css class, hint) for free disk under the backup volume."""

    info = status.get("disk") or {}
    free = info.get("free_mb")
    ok = bool(info.get("ok"))
    try:
        free_mb = float(free) if free is not None else None
    except (TypeError, ValueError):
        free_mb = None
    if free_mb is None:
        return "Unknown", "", ""
    if free_mb >= 1024:
        value = f"{free_mb / 1024:.1f} GiB"
    else:
        value = f"{free_mb:.0f} MiB"
    # Only color the value when low — green on a healthy free-disk card
    # overstates how important that secondary metric is.
    return value, ("" if ok else "bad"), ""


def _format_backup_options(status: dict[str, Any]) -> str:
    info = status.get("backups") or {}
    archives = info.get("restorable") or []
    world_info = status.get("world_save") or {}
    active_label = str(world_info.get("label") or "").strip()
    grouped: dict[str, list[str]] = {}
    group_order: list[str] = []
    if isinstance(archives, list):
        for item in archives:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            kind = str(item.get("kind") or "backup")
            mtime = item.get("mtime")
            age = _fmt_ago(mtime) if mtime else "unknown age"
            generation = str(
                item.get("generation") or backup_generation_key(name) or ""
            ).strip()
            option = (
                f'<option value="{_html_escape(name)}">'
                f"{_html_escape(f'{name} ({kind}, {age})')}</option>"
            )
            if generation not in grouped:
                grouped[generation] = []
                group_order.append(generation)
            grouped[generation].append(option)
    options: list[str] = []
    if not grouped:
        options.append('<option value="">No backups yet</option>')
    elif any(grouped):
        if active_label and active_label in grouped:
            group_order = [active_label] + [
                key for key in group_order if key != active_label
            ]
        for generation in group_order:
            heading = generation or "Other worlds"
            if generation and generation == active_label:
                heading = f"{generation} (active)"
            options.append(f'<optgroup label="{_html_escape(heading)}">')
            options.extend(grouped[generation])
            options.append("</optgroup>")
    else:
        for generation in group_order:
            options.extend(grouped[generation])
    options.append(
        f'<option value="{_html_escape(EMPTY_WORLD)}">NEW WORLD</option>'
    )
    return "\n".join(options)


def _format_world_options(status: dict[str, Any]) -> str:
    worlds = status.get("worlds") or {}
    catalog = worlds.get("catalog") or []
    active = str(worlds.get("active") or "").strip()
    options: list[str] = []
    seen: set[str] = set()
    if isinstance(catalog, list):
        for item in catalog:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            caption = str(item.get("caption") or "").strip()
            label = f"{name} ({caption})" if caption else name
            if name == active:
                label = f"{label} (active)"
            selected = " selected" if name == active else ""
            options.append(
                f'<option value="{_html_escape(name)}"{selected}>'
                f"{_html_escape(label)}</option>"
            )
    if active and active not in seen:
        options.insert(
            0,
            f'<option value="{_html_escape(active)}" selected>'
            f"{_html_escape(active)} (active)</option>",
        )
    if not options:
        options.append('<option value="">No worlds yet</option>')
    return "\n".join(options)


def _format_world_create_fields(status: dict[str, Any]) -> str:
    worlds = status.get("worlds") or {}
    fields = worlds.get("create_fields") or []
    chunks: list[str] = []
    if not isinstance(fields, list):
        return ""
    for item in fields:
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("id") or "").strip()
        if not field_id:
            continue
        kind = str(item.get("kind") or "text").strip().lower()
        label = str(item.get("label") or field_id)
        default = str(item.get("default") or "")
        attr = f'data-world-create-field="{_html_escape(field_id)}"'
        if kind == "select":
            opts = []
            raw_opts = item.get("options") or []
            if isinstance(raw_opts, list):
                for opt in raw_opts:
                    if isinstance(opt, dict):
                        value = str(opt.get("value") or "").strip()
                        opt_label = str(opt.get("label") or value)
                    else:
                        value = str(opt).strip()
                        opt_label = value
                    if not value:
                        continue
                    selected = " selected" if value == default else ""
                    opts.append(
                        f'<option value="{_html_escape(value)}"{selected}>'
                        f"{_html_escape(opt_label)}</option>"
                    )
            chunks.append(
                f'<label class="world-create-field">{_html_escape(label)} '
                f"<select {attr}>{''.join(opts)}</select></label>"
            )
        else:
            chunks.append(
                f'<label class="world-create-field">{_html_escape(label)} '
                f'<input type="text" {attr} value="{_html_escape(default)}" />'
                f"</label>"
            )
    return "\n".join(chunks)


def _format_world_save(status: dict[str, Any]) -> tuple[str, str]:
    """Return (size value, hint HTML). Hint may be a download link."""

    info = status.get("world_save") or {}
    raw_bytes = info.get("bytes")
    try:
        size = int(raw_bytes or 0)
    except (TypeError, ValueError):
        size = 0
    label = str(info.get("label") or "").strip()
    scope = str(info.get("scope") or "")
    if size <= 0 and scope == "missing":
        if label:
            return "—", _html_escape(f"Waiting for {label}")
        return "—", "No world save found yet"
    value = format_bytes(size)
    if scope == "named_path" and label:
        hint_text = label
    elif scope == "backup_sources":
        hint_text = label or "World data directory"
    elif label:
        hint_text = label
    else:
        hint_text = "World data"
    if bool(info.get("downloadable")) and size > 0:
        return (
            value,
            f'<a href="api/world/download">{_html_escape(hint_text)}</a>',
        )
    return value, _html_escape(hint_text)


def _format_world_upload(status: dict[str, Any]) -> tuple[str, str, str]:
    """Return (hint, accept attribute, row css class) for upload restore UI."""

    info = status.get("world_save") or {}
    uploadable = bool(info.get("uploadable"))
    hint = str(info.get("hint") or "").strip()
    accept = str(info.get("accept") or "").strip()
    if not uploadable:
        return (
            hint
            or "World upload is unavailable until the game plugin declares a named save path.",
            "",
            "hidden",
        )
    return hint, accept or ".zip,application/zip", ""


def _format_operator_action(
    status: dict[str, Any],
) -> tuple[str, str, str, str, str, bool]:
    """Return title, detail, url, code, steps HTML, hidden."""

    action = status.get("operator_action")
    if not isinstance(action, dict) or not action:
        return "", "", "", "", "", True
    title = str(action.get("title") or "Sign in required").strip()
    detail = str(action.get("detail") or "").strip()
    url = str(action.get("url") or "").strip()
    code = str(action.get("code") or "").strip()
    steps_html_parts: list[str] = []
    for index, item in enumerate(action.get("steps") or [], start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        state = str(item.get("state") or "pending").strip().lower()
        if state not in {"pending", "active", "done"}:
            state = "pending"
        steps_html_parts.append(
            f'<li class="op-step op-step-{_html_escape(state)}">'
            f"{index}. {_html_escape(label)}</li>"
        )
    return title, detail, url, code, "".join(steps_html_parts), False


def _format_running(status: dict[str, Any]) -> tuple[str, str]:
    """Hero server label from lifecycle (falls back to running bool)."""

    action = status.get("operator_action")
    if isinstance(action, dict) and action:
        return "waiting for sign-in", "accent"
    phase = str(status.get("lifecycle") or "").strip()
    # Restore/update stop the game first; surface that while it is still up.
    if phase == "restoring":
        if status.get("running"):
            return "stopping for restore", "accent"
        return "restoring world", "accent"
    if phase == "updating":
        if status.get("running"):
            return "stopping for update", "accent"
        return "updating", "accent"
    labels = {
        "running": ("running", "good"),
        "installing": ("installing", "accent"),
        "waiting": ("waiting", "accent"),
        "starting": ("starting", "accent"),
        "stopping": ("stopping", "accent"),
        "failed": ("failed", "bad"),
        "stopped": ("stopped", "bad"),
    }
    if phase in labels:
        return labels[phase]
    if status.get("running"):
        return "running", "good"
    return "stopped", "bad"


def _ui_view(
    status: dict[str, Any],
    game_name: str,
    *,
    ui_theme: dict[str, str] | None = None,
    toolbox: Any = None,
    extra_examples: dict[str, list[str]] | None = None,
    alternate_examples: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Formatted strings for the status page and soft-refresh JSON."""

    monitor = status.get("monitor") or {}
    log_patterns = status.get("log_patterns") or {}
    patterns = log_patterns.get("patterns") or []
    has_active = any((item.get("mode") or "") == "active" for item in patterns)
    active_categories = _active_pattern_categories(patterns)
    pattern_rows, unused_pattern_rows, unused_patterns_hidden = _format_pattern_tables(
        patterns
    )
    tracking_mode = str(status.get("player_tracking_mode") or "count").strip().lower()
    promote_prompt = _log_pattern_prompt(
        status,
        toolbox,
        game_name,
        extra_examples=extra_examples,
        alternate_examples=alternate_examples,
    )
    players_known = bool(monitor.get("players_known"))
    presence_mode = tracking_mode == "presence"
    debug_mode = bool(status.get("debug_mode"))
    # Without debug mode, hide the players card until an active pattern can
    # populate it — otherwise the card is a permanent empty state that looks broken.
    # Count mode needs player_count; join/leave games use the last-joined card.
    has_active_player_count = "player_count" in active_categories
    has_active_presence = bool(
        active_categories
        & {"player_join", "player_leave", "players_empty", "player_count"}
    )
    use_last_join_card = (not has_active_player_count) and (
        "player_join" in active_categories
    )
    players_class = ""
    if use_last_join_card:
        players_label = "Players"
        present = monitor.get("players_present")
        if present is None:
            count = monitor.get("player_count")
            present = None if count is None else int(count) > 0
        last_join = monitor.get("last_player_join_at")
        if last_join:
            players = f"player last joined {_fmt_ago(last_join)}"
            players_class = "good players-last-join" if present else "idle players-last-join"
            players_hint = ""
        else:
            players = "no joins yet"
            players_class = "idle"
            players_hint = ""
    elif presence_mode:
        # Presence without a join pattern (e.g. empty-only) — keep idle/active.
        players_label = "Players"
        if players_known:
            present = monitor.get("players_present")
            if present is None:
                count = monitor.get("player_count")
                present = None if count is None else int(count) > 0
            players = "players active" if present else "idle"
            players_class = "good" if present else "idle"
            players_hint = ""
        else:
            players = "no joins yet"
            players_class = "idle"
            players_hint = ""
    else:
        players_label = "Number of players"
        players = str(monitor.get("player_count")) if players_known else "—"
        players_hint = "Detected from game log" if players_known else "No count yet"
    if presence_mode or use_last_join_card:
        players_card_hidden = (not debug_mode) and (not has_active_presence)
    else:
        players_card_hidden = (not debug_mode) and (not has_active_player_count)
    log_watch_hidden = not debug_mode
    uptime, uptime_hint = _format_uptime(status)
    game_version, game_version_build, game_version_installed = _format_game_version(
        status
    )
    backups, backups_oldest, backups_newest = _format_backups(status)
    world_save, world_save_hint = _format_world_save(status)
    world_upload_hint, world_upload_accept, world_upload_class = _format_world_upload(
        status
    )
    disk, disk_class, disk_hint = _format_disk(status)
    running_label, running_class = _format_running(status)
    op_title, op_detail, op_url, op_code, op_steps, op_hidden = _format_operator_action(
        status
    )
    update_pending = bool(status.get("update_pending"))
    update_check_hint = _format_update_check_hint(status)
    theme = resolve_ui_theme(ui_theme)
    view: dict[str, Any] = {
        "game": game_name,
        "subtitle": _format_subtitle(status),
        "running": running_label,
        "running_class": running_class,
        "players_label": players_label,
        "players": players,
        "players_hint": players_hint,
        "players_class": players_class,
        "players_card_class": "hidden" if players_card_hidden else "",
        "players_card_hidden": players_card_hidden,
        "uptime": uptime,
        "uptime_hint": uptime_hint,
        "game_version": game_version,
        "game_version_build": game_version_build,
        "game_version_installed": game_version_installed,
        "update_pending": "update available" if update_pending else "up to date",
        # When an update is waiting, the in-card button replaces the check hint.
        "update_check_hint": "" if update_pending else update_check_hint,
        "update_btn_class": "" if update_pending else "hidden",
        "update_btn_hidden": not update_pending,
        "backups": backups,
        "backups_oldest": backups_oldest,
        "backups_newest": backups_newest,
        "crashes": int(status.get("crash_count") or 0),
        "crashes_hint": _format_crashes_hint(status),
        "world_save": world_save,
        "world_save_hint": world_save_hint,
        "world_options": _format_world_options(status),
        "world_create_fields": _format_world_create_fields(status),
        "world_upload_hint": world_upload_hint,
        "world_upload_accept": world_upload_accept,
        "world_upload_class": world_upload_class,
        "world_upload_hidden": world_upload_class == "hidden",
        "disk": disk,
        "disk_class": disk_class,
        "disk_hint": disk_hint,
        # Collapse once any active pattern exists (setup complete enough).
        "log_watch_open": "" if has_active else " open",
        "log_watch_class": "hidden" if log_watch_hidden else "",
        "log_watch_hidden": log_watch_hidden,
        "pattern_rows": pattern_rows,
        "unused_pattern_rows": unused_pattern_rows,
        "unused_patterns_class": "hidden" if unused_patterns_hidden else "",
        "unused_patterns_hidden": unused_patterns_hidden,
        "promote_prompt": promote_prompt,
        "capture_options": _format_capture_options(status.get("log_captures") or []),
        "backup_options": _format_backup_options(status),
        "empty_world_token": EMPTY_WORLD,
        "operator_action_title": op_title,
        "operator_action_detail": op_detail,
        "operator_action_url": op_url,
        "operator_action_code": op_code,
        "operator_action_steps": op_steps,
        "operator_action_class": "hidden" if op_hidden else "",
        "operator_action_hidden": op_hidden,
        "operator_action_url_class": "hidden" if not op_url else "",
        "operator_action_code_class": "hidden" if not op_code else "",
        "operator_action_code_hidden": not op_code,
    }
    for key in UI_THEME_KEYS:
        view[f"theme_{key}"] = theme[key]
    return view


_STATUS_HTML_KEYS = (
    "game",
    "subtitle",
    "running",
    "running_class",
    "players_label",
    "players",
    "players_hint",
    "players_class",
    "players_card_class",
    "uptime",
    "uptime_hint",
    "game_version",
    "game_version_build",
    "game_version_installed",
    "update_pending",
    "update_check_hint",
    "update_btn_class",
    "backups",
    "backups_oldest",
    "backups_newest",
    "crashes",
    "crashes_hint",
    "world_save",
    "world_save_hint",
    "world_options",
    "world_create_fields",
    "world_upload_hint",
    "world_upload_accept",
    "world_upload_class",
    "disk",
    "disk_class",
    "disk_hint",
    "log_watch_open",
    "log_watch_class",
    "pattern_rows",
    "unused_pattern_rows",
    "unused_patterns_class",
    "promote_prompt",
    "capture_options",
    "backup_options",
    "empty_world_token",
    "operator_action_class",
    "operator_action_title",
    "operator_action_detail",
    "operator_action_url",
    "operator_action_url_class",
    "operator_action_code",
    "operator_action_code_class",
    "operator_action_steps",
) + tuple(f"theme_{key}" for key in UI_THEME_KEYS)


def render_status_html(view: dict[str, Any], *, base_href: str = "/") -> str:
    """Render the Ingress status page from a ``_ui_view`` dict.

    Kept as a pure function so unit tests exercise the same ``str.format``
    path the HTTP handler uses (catches unescaped ``{...}`` in the template).
    """

    theme = resolve_ui_theme(
        {key: str(view.get(f"theme_{key}") or "") for key in UI_THEME_KEYS}
    )
    return HTML_PAGE.format(
        base_href=_html_escape(base_href),
        game=_html_escape(view["game"]),
        subtitle=_html_escape(view["subtitle"]),
        running=_html_escape(view["running"]),
        running_class=_html_escape(view["running_class"]),
        players_label=_html_escape(view.get("players_label") or "Number of players"),
        players=_html_escape(view["players"]),
        players_hint=_html_escape(view["players_hint"]),
        players_class=_html_escape(view.get("players_class") or ""),
        players_card_class=_html_escape(view.get("players_card_class") or ""),
        uptime=_html_escape(view["uptime"]),
        uptime_hint=_html_escape(view["uptime_hint"]),
        game_version=_html_escape(view["game_version"]),
        game_version_build=_html_escape(view["game_version_build"]),
        game_version_installed=_html_escape(view["game_version_installed"]),
        update_pending=_html_escape(view["update_pending"]),
        update_check_hint=_html_escape(view["update_check_hint"]),
        update_btn_class=_html_escape(view.get("update_btn_class") or ""),
        backups=_html_escape(str(view["backups"])),
        backups_oldest=_html_escape(view["backups_oldest"]),
        backups_newest=_html_escape(view["backups_newest"]),
        crashes=_html_escape(str(view["crashes"])),
        crashes_hint=_html_escape(view["crashes_hint"]),
        world_save=_html_escape(view["world_save"]),
        # Hint may include a download <a>; _format_world_save already escapes text.
        world_save_hint=view["world_save_hint"],
        world_options=view.get("world_options") or "",
        world_create_fields=view.get("world_create_fields") or "",
        world_upload_hint=_html_escape(view.get("world_upload_hint") or ""),
        world_upload_accept=_html_escape(view.get("world_upload_accept") or ""),
        world_upload_class=_html_escape(view.get("world_upload_class") or ""),
        disk=_html_escape(view["disk"]),
        disk_class=_html_escape(view["disk_class"]),
        disk_hint=_html_escape(view["disk_hint"]),
        log_watch_open=view["log_watch_open"],
        log_watch_class=_html_escape(view.get("log_watch_class") or ""),
        pattern_rows=view["pattern_rows"],
        unused_pattern_rows=view.get("unused_pattern_rows") or "",
        unused_patterns_class=_html_escape(view.get("unused_patterns_class") or ""),
        promote_prompt=_html_escape(view.get("promote_prompt") or ""),
        capture_options=view["capture_options"],
        backup_options=view["backup_options"],
        empty_world_token=_html_escape(view.get("empty_world_token") or EMPTY_WORLD),
        operator_action_class=_html_escape(view.get("operator_action_class") or ""),
        operator_action_title=_html_escape(view.get("operator_action_title") or ""),
        operator_action_detail=_html_escape(view.get("operator_action_detail") or ""),
        operator_action_url=_html_escape(view.get("operator_action_url") or "#"),
        operator_action_url_class=_html_escape(
            view.get("operator_action_url_class") or ""
        ),
        operator_action_code=_html_escape(view.get("operator_action_code") or ""),
        operator_action_code_class=_html_escape(
            view.get("operator_action_code_class") or ""
        ),
        operator_action_steps=view.get("operator_action_steps") or "",
        theme_bg=_html_escape(theme["bg"]),
        theme_panel=_html_escape(theme["panel"]),
        theme_ink=_html_escape(theme["ink"]),
        theme_muted=_html_escape(theme["muted"]),
        theme_accent=_html_escape(theme["accent"]),
        theme_good=_html_escape(theme["good"]),
        theme_bad=_html_escape(theme["bad"]),
        theme_glow=_html_escape(theme["glow"]),
        theme_wash=_html_escape(theme["wash"]),
        theme_depth=_html_escape(theme["depth"]),
    )


def _active_pattern_categories(patterns: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("category") or "")
        for item in patterns
        if (item.get("mode") or "") == "active" and item.get("category")
    }


_RECENT_MATCH_LIMIT = 5
_CATEGORY_RECENT_MATCH_LIMIT = 10


def _recent_matches_for_pattern(item: dict[str, Any]) -> list[str]:
    """Up to 5 newest matching lines for one regex (newest first)."""

    recent = item.get("recent_lines")
    lines: list[str]
    if isinstance(recent, list) and recent:
        lines = [str(x) for x in recent if str(x).strip()]
    elif item.get("last_line"):
        lines = [str(item.get("last_line"))]
    else:
        lines = []
    # Deque stores oldest→newest; present newest first.
    return list(reversed(lines[-_RECENT_MATCH_LIMIT:]))


def _item_is_stale(item: dict[str, Any]) -> bool:
    """Configured regex used to match a previous process, 0 hits this process."""

    if "stale" in item:
        return bool(item.get("stale"))
    if (item.get("mode") or "") != "active":
        return False
    if "session_hits" not in item:
        return False
    session_hits = int(item.get("session_hits") or 0)
    hits = int(item.get("hits") or 0)
    return session_hits == 0 and hits > 0


def _ui_pattern_mode(mode: str) -> str:
    """Map monitor mode (active/dry_run) to Ingress labels."""

    if mode in ("active", "configured"):
        return "configured"
    if mode == "stale":
        return "stale"
    return "not configured"


def _mode_css_class(mode: str) -> str:
    """CSS class for a display mode that may contain spaces."""

    if mode in ("unused", "not configured", "not_configured", "not-configured"):
        return "not-configured"
    return mode


def _category_display_mode(items: list[dict[str, Any]]) -> str:
    """Single Mode tag for a category: stale > configured > not configured."""

    if any(_item_is_stale(item) for item in items):
        return "stale"
    if any((item.get("mode") or "") == "active" for item in items):
        return "configured"
    return "not configured"


def _category_recent_matches(
    items: list[dict[str, Any]],
    *,
    include_unused: bool,
) -> list[tuple[str, str]]:
    """Merged recent lines for a category: (ui-mode, text), newest-ish first.

    Configured matches win on duplicate text so the UI can color them green.
    Not-configured guess lines are included when the configured regex is stale
    or the category has no plugin pattern — not when configured patterns are healthy.
    """

    ordered = sorted(
        items,
        key=lambda item: (0 if (item.get("mode") or "") == "active" else 1),
    )
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in ordered:
        raw_mode = item.get("mode") or "dry_run"
        if raw_mode not in ("active", "dry_run"):
            raw_mode = "dry_run"
        if raw_mode == "dry_run" and not include_unused:
            continue
        ui_mode = _ui_pattern_mode(raw_mode)
        for line in _recent_matches_for_pattern(item):
            text = strip_ansi(line).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            if len(text) > 160:
                text = text[:160] + "…"
            out.append((ui_mode, text))
            if len(out) >= _CATEGORY_RECENT_MATCH_LIMIT:
                return out
    return out


_MODE_SORT_RANK = {"stale": 0, "configured": 1, "not configured": 2}
_NOT_CONFIGURED = "not configured"


def _pattern_category_summaries(
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate pattern hit report into one summary dict per category."""

    if not patterns:
        return []

    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in patterns:
        category = str(item.get("category") or "")
        by_category.setdefault(category, []).append(item)

    def _category_sort_key(category: str) -> tuple[Any, ...]:
        items = by_category[category]
        display_mode = _category_display_mode(items)
        active_hits = sum(
            int(item.get("hits") or 0)
            for item in items
            if (item.get("mode") or "") == "active"
        )
        return (
            _MODE_SORT_RANK.get(display_mode, 9),
            category,
            -active_hits,
        )

    summaries: list[dict[str, Any]] = []
    for category in sorted(by_category, key=_category_sort_key)[:120]:
        items = by_category[category]
        display_mode = _category_display_mode(items)
        include_unused = display_mode in ("stale", _NOT_CONFIGURED)
        unused_hits = sum(
            int(item.get("hits") or 0)
            for item in items
            if (item.get("mode") or "") != "active"
        )
        active_hits = sum(
            int(item.get("hits") or 0)
            for item in items
            if (item.get("mode") or "") == "active"
        )
        summaries.append(
            {
                "category": category,
                "display_mode": display_mode,
                "active_hits": active_hits,
                "hits": unused_hits if display_mode == _NOT_CONFIGURED else active_hits,
                "recent_matches": _category_recent_matches(
                    items, include_unused=include_unused
                ),
            }
        )
    return summaries


def _format_category_recent_matches_html(
    recent: list[tuple[str, str]],
) -> str:
    if not recent:
        return ""
    parts = [
        f"<div class='match-line {_mode_css_class(mode)}'>{_html_escape(text)}</div>"
        for mode, text in recent
    ]
    return f"<div class='recent-matches'>{''.join(parts)}</div>"


def _pattern_row_html(item: dict[str, Any]) -> str:
    mode = str(item["display_mode"])
    css = _mode_css_class(mode)
    recent = _format_category_recent_matches_html(
        list(item.get("recent_matches") or [])
    )
    return (
        "<tr>"
        f"<td><span class='tag {css}'>{_html_escape(mode)}</span></td>"
        f"<td>{_html_escape(str(item.get('category') or ''))}</td>"
        f"<td>{int(item.get('hits') or 0)}</td>"
        f"<td>{recent}</td>"
        "</tr>"
    )


def _format_pattern_tables(
    patterns: list[dict[str, Any]],
) -> tuple[str, str, bool]:
    """Configured/stale rows, not-configured rows, and whether to hide the expander."""

    summaries = _pattern_category_summaries(patterns)
    configured = [item for item in summaries if item["display_mode"] != _NOT_CONFIGURED]
    unused = [item for item in summaries if item["display_mode"] == _NOT_CONFIGURED]
    if configured:
        pattern_rows = "\n".join(_pattern_row_html(item) for item in configured)
    else:
        pattern_rows = "<tr><td colspan='4'>(no configured patterns)</td></tr>"
    if unused:
        unused_rows = "\n".join(_pattern_row_html(item) for item in unused)
    else:
        unused_rows = "<tr><td colspan='4'>(no not configured log patterns)</td></tr>"
    return pattern_rows, unused_rows, not unused


def _format_pattern_rows(patterns: list[dict[str, Any]]) -> str:
    """Configured + stale table body (unused guesses live in the expander)."""

    rows, _unused, _hidden = _format_pattern_tables(patterns)
    return rows


def _addon_game_yaml_relpath(game_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (game_name or "game").strip().lower()).strip("-")
    return f"{slug}-dedicated-server/games/game.yaml"


def _yaml_regex(pattern: str) -> str:
    """Single-quoted YAML so backslashes survive."""

    return "'" + str(pattern).replace("'", "''") + "'"


def _prompt_sample_line(item: dict[str, Any]) -> str:
    lines = _recent_matches_for_pattern(item)
    if lines:
        return lines[0]
    last = strip_ansi(str(item.get("last_line") or "")).strip()
    return last


def _configured_pattern_matches_line(
    configured_items: list[dict[str, Any]], line: str
) -> bool:
    text = str(line).strip()
    if not text:
        return False
    for item in configured_items:
        pat = str(item.get("pattern") or "")
        if not pat:
            continue
        try:
            if re.search(pat, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


_PROMOTE_CATEGORY_ORDER = (
    "ready",
    "game_version",
    "player_join",
    "player_leave",
    "player_count",
    "players_empty",
    "version_mismatch",
)
_PROMOTE_CATEGORY_HELP = {
    "ready": (
        "Port bound / accepting connections, not a later GameInfo or public-IP line. "
        "No capture groups required."
    ),
    "game_version": (
        "Human game version like 1.3.1, not a Steam build id. "
        "Named group: version"
    ),
    "player_join": (
        "In-world join (not handshake or character-select). "
        "Named group: player. Optional: steam_id. "
        "The captured identity must also appear on leave."
    ),
    "player_leave": (
        "Someone left. Capture the same identity token as join "
        "(Steam id, internal userid, and display name are different namespaces)."
    ),
    "player_count": (
        "Exact headcount. Named group: count (integer). "
        "Only if the game logs a real count."
    ),
    "players_empty": "Nobody is online. No capture groups required.",
    "version_mismatch": (
        "A client was rejected for protocol/version — not a normal disconnect "
        "reason and not a crash dump that repeats the phrase. "
        "Optional named groups: steam_id, client_version"
    ),
}


def _examples_from_toolbox(
    toolbox: Any,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (configured/not-configured samples, alternate guess lines)."""

    extra: dict[str, list[str]] = {}
    alts: dict[str, list[str]] = {}
    try:
        if hasattr(toolbox, "example_groups_by_category"):
            groups = toolbox.example_groups_by_category() or {}
            for category, data in groups.items():
                if isinstance(data, dict) and not isinstance(data, list):
                    matches = [
                        str(line).strip()
                        for line in list(data.get("matches") or [])
                        if str(line).strip()
                    ]
                    other = [
                        str(line).strip()
                        for line in list(data.get("alternates") or [])
                        if str(line).strip()
                    ]
                    if matches:
                        extra[str(category)] = matches
                    if other:
                        alts[str(category)] = other
                elif isinstance(data, list):
                    lines = [
                        str(line).strip() for line in data if str(line).strip()
                    ]
                    if lines:
                        extra[str(category)] = lines
            return extra, alts
        extra = toolbox.example_lines_by_category() or {}
        return extra, {}
    except Exception:
        LOG.exception("log-file rescan for pattern prompt failed")
        return {}, {}


def _log_pattern_prompt(
    status: dict[str, Any],
    toolbox: Any,
    game_name: str,
    *,
    extra_examples: dict[str, list[str]] | None = None,
    alternate_examples: dict[str, list[str]] | None = None,
) -> str:
    """Same text as the debug textarea, with log-file rescan examples when possible."""

    log_patterns = status.get("log_patterns") or {}
    patterns = list(log_patterns.get("patterns") or [])
    tracking = str(status.get("player_tracking_mode") or "count")
    extra = extra_examples
    alts = alternate_examples
    if extra is None and toolbox is not None:
        extra, scanned_alts = _examples_from_toolbox(toolbox)
        if alts is None:
            alts = scanned_alts
    return _format_promote_prompt(
        game_name,
        patterns,
        player_tracking_mode=tracking,
        extra_examples=extra,
        alternate_examples=alts,
    )


def _format_promote_prompt(
    game_name: str,
    patterns: list[dict[str, Any]],
    *,
    player_tracking_mode: str = "count",
    extra_examples: dict[str, list[str]] | None = None,
    alternate_examples: dict[str, list[str]] | None = None,
) -> str:
    """Preamble + live regexes and sample lines so an AI can wire game.yaml."""

    yaml_path = _addon_game_yaml_relpath(game_name)
    tracking = (player_tracking_mode or "count").strip().lower()
    if tracking == "presence":
        tracking_note = (
            "presence — idle vs occupied from join/leave/empty. "
            "Do not add player_count unless the game logs a trustworthy numeric count."
        )
    else:
        tracking_note = (
            "count — use player_count when the game logs an exact headcount."
        )

    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in patterns:
        by_category.setdefault(str(item.get("category") or ""), []).append(item)
    extra = extra_examples or {}
    alts = alternate_examples or {}
    for category, lines in extra.items():
        if category and lines and category not in by_category:
            by_category[category] = []
    for category, lines in alts.items():
        if category and lines and category not in by_category:
            by_category[category] = []
    categories = [name for name in _PROMOTE_CATEGORY_ORDER if name in by_category]
    categories.extend(sorted(name for name in by_category if name not in set(categories)))

    work_blocks: list[str] = []
    example_blocks: list[str] = []
    for category in categories:
        items = by_category[category]
        display = _category_display_mode(items)
        help_text = _PROMOTE_CATEGORY_HELP.get(category, "")
        configured_items = [
            item for item in items if (item.get("mode") or "") == "active"
        ]
        guess_hits = [
            item
            for item in items
            if (item.get("mode") or "") != "active" and int(item.get("hits") or 0) > 0
        ]
        scan_lines = [
            str(line).strip()
            for line in list(extra.get(category) or [])
            if str(line).strip()
        ]
        lines_out: list[str] = [f"### {category}  [{display}]"]
        if help_text:
            lines_out.append(help_text)
        if configured_items:
            lines_out.append("Configured regexes (re.IGNORECASE, re.search):")
            for item in configured_items:
                hits = int(item.get("hits") or 0)
                if "session_hits" in item:
                    session_hits = int(item.get("session_hits") or 0)
                    hit_note = (
                        f"hits this process={session_hits}, lifetime hits={hits}"
                    )
                else:
                    hit_note = f"hits={hits}"
                sample = _prompt_sample_line(item)
                lines_out.append(f"  - {_yaml_regex(str(item.get('pattern') or ''))}")
                lines_out.append(f"    {hit_note}")
                if sample:
                    lines_out.append(f"    last match: {sample}")
        else:
            lines_out.append("No plugin regex in log_patterns.")
        if guess_hits and display in ("stale", _NOT_CONFIGURED):
            lines_out.append(
                "Not-configured guesses that hit (too broad to copy as-is):"
            )
            for item in guess_hits[:8]:
                sample = _prompt_sample_line(item)
                lines_out.append(
                    f"  - {_yaml_regex(str(item.get('pattern') or ''))}  "
                    f"hits={int(item.get('hits') or 0)}"
                )
                if sample:
                    lines_out.append(f"    sample: {sample}")
        alt_lines: list[str] = []
        seen_alts: set[str] = set()

        def _add_alt(text: str) -> None:
            line = str(text).strip()
            if not line or line in seen_alts:
                return
            if configured_items and _configured_pattern_matches_line(
                configured_items, line
            ):
                return
            seen_alts.add(line)
            alt_lines.append(line)

        for line in alts.get(category) or []:
            _add_alt(line)
        if configured_items:
            for item in guess_hits:
                _add_alt(_prompt_sample_line(item))
            kept_scan: list[str] = []
            for line in scan_lines:
                if _configured_pattern_matches_line(configured_items, line):
                    kept_scan.append(line)
                else:
                    _add_alt(line)
            scan_lines = kept_scan
        if scan_lines:
            header = (
                "Example log lines matching the configured regex (file rescan):"
                if configured_items
                else "Example log lines (file rescan):"
            )
            lines_out.append(header)
            for line in scan_lines[:25]:
                lines_out.append(f"  {line}")
        elif display == _NOT_CONFIGURED and not guess_hits and not alt_lines:
            lines_out.append("No sample log lines yet for this category.")
        if configured_items and alt_lines:
            lines_out.append(
                "Other interesting lines (not captured by the configured regex — "
                "possible better sources):"
            )
            for line in alt_lines[:25]:
                lines_out.append(f"  {line}")
        block = "\n".join(lines_out)
        if display in ("stale", _NOT_CONFIGURED):
            work_blocks.append(block)
        elif configured_items:
            example_blocks.append(block)

    if work_blocks:
        work_section = "\n\n".join(work_blocks)
    else:
        work_section = (
            "Nothing looks stale or not configured from pattern hits. "
            "If a category is wrong, paste recent server log lines here."
        )
    example_section = "\n\n".join(example_blocks) if example_blocks else "(none)"

    return "\n".join(
        [
            f"Write Python regexes for the {game_name} dedicated-server add-on.",
            "",
            "Repo: https://github.com/esper256/ha-gameservers",
            f"Edit: {yaml_path}  (log_patterns section)",
            "Open a pull request against that repo.",
            "Edit log_patterns in that game.yaml (candidates optional).",
            "Update tests if the add-on already has pattern tests.",
            "Do not change other games or the shared supervisor unless this prompt text is wrong.",
            "",
            "How matching works:",
            "- Each log_patterns regex is compiled with re.IGNORECASE and used as re.search on every log line (ANSI already stripped).",
            "- Only log_patterns change supervisor state. log_pattern_candidates and generic guesses never trigger actions.",
            "- Quote YAML strings with single quotes so backslashes survive.",
            "- Write a new precise regex from the sample log lines. Do not copy guess regexes — a guess can hit the right line with the wrong pattern.",
            "- The sample next to a guess is more useful than the guess regex itself.",
            "- One precise pattern per log shape. Prefer the named groups listed per category. Do not match on timestamps alone.",
            "- Join and leave must capture the same identity token. Steam ids, internal userids, and display names are different namespaces; pick the identifier that appears on both the in-world join line and the leave line.",
            "- Network connect, character select, and in-world spawn are different events. Promote the in-world line (or the pair that matches leave), not the handshake.",
            "- ready means the process is accepting connections (port bind / listening). Do not use a later GameInfo / public-IP / started-session line that can fire after a client already connected.",
            "- Hits do not prove a configured pattern is the right event. Keep it only if it still matches the category meaning.",
            "- Configured categories also list other interesting lines guesses found. Those can be a better source than the current regex; replace it when a sample is a clearer event.",
            "- version_mismatch is a client rejected for protocol/version. Disconnect reasons (App_Min, AppException_Max, Misc_Timeout, and similar) and crash-dump headers that repeat a version phrase are not mismatch.",
            "- Zero session hits on version_mismatch usually means it did not happen this boot, not that the regex is wrong.",
            "- Omit player_count unless the game logs an integer headcount. Omit any category with no trustworthy line.",
            "- Example log lines (file rescan) include startup history the live tailer missed (the tailer starts at EOF). Prefer those samples over last-match from this process alone.",
            "",
            "Categories the supervisor reads:",
            "  ready             port bound / accepting connections. not a later GameInfo or public-IP line. no capture groups",
            "  game_version      named group version (human version like 1.3.1, not a Steam build id)",
            "  player_join       in-world join (not handshake / character-select). named group player. optional steam_id",
            "  player_leave      same identity token as join (Steam id, userid, and name are different namespaces)",
            "  player_count      named group count (integer). only if the game logs a real headcount",
            "  players_empty     nobody online. no capture groups",
            "  version_mismatch  client rejected for protocol/version. not a disconnect reason or crash dump. optional steam_id, client_version",
            "",
            f"Player tracking mode: {tracking_note}",
            "",
            "Expected YAML shape:",
            "log_patterns:",
            "  <category>:",
            "    - '<python regex>'",
            "",
            "Needs work (stale or not configured):",
            work_section,
            "",
            "Working configured patterns (style examples — keep only if they still match the category meaning; hits are not proof):",
            example_section,
            "",
        ]
    )


def _format_capture_options(captures: list[dict[str, Any]]) -> str:
    if not captures:
        return '<option value="">No captures yet</option>'
    options = []
    for item in captures[:40]:
        href = str(item.get("download_path") or "").lstrip("/")
        label = f"{item.get('id')} · {item.get('reason')}"
        options.append(
            f'<option value="{_html_escape(href)}">{_html_escape(label)}</option>'
        )
    return "\n".join(options)
