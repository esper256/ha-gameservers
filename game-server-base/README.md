# Build a game add-on on `game-server-base`

**Not an installable Home Assistant app** — no `config.yaml` on purpose. Only thin game folders (like Necesse) appear in the App store.

Most visitors want a **specific game**: start at the [repository README](../README.md) (e.g. [Necesse guide](../necesse-dedicated-server/GUIDE.md)).

This guide is for packaging **another dedicated server** on the same supervisor (auto-update, by-kind world backups, crash restart, Ingress status, Steam rate gate).

> **AI experiment:** like the rest of this repo, `game-server-base` is a deliberate AI-coding experiment. It works and is useful, and it has been **100% written with AI**. See the [repository README](../README.md).

---

## Docs split (HA vs GitHub)

Home Assistant shows two markdown files from each game folder ([presentation docs](https://developers.home-assistant.io/docs/add-ons/presentation/)):

| File | Where it appears | Keep it |
| --- | --- | --- |
| `README.md` | Info / store panel under the HA chrome | Two short paragraphs: what it is (game name linked), then Documentation vs **Open Web UI**. No H1, no images, no amd64 / GitHub / Docker. |
| `DOCS.md` | Documentation tab after install | Spartan: configure, ports, **Open Web UI**, essential settings. |
| `GUIDE.md` | GitHub only (HA ignores it) | End-user landing: features, screenshot, install-from-repo, Docker. |

The **repo root** [README.md](../README.md) is the ecosystem springboard (shared supervisor story + links into each `GUIDE.md`). One Ingress screenshot there is enough — per-game shots live in each guide.

---

## What players see (Ingress)

Each game add-on vendors this supervisor and exposes **OPEN WEB UI** through Home Assistant Ingress. Example from Necesse:

![Example Ingress status UI (Necesse)](../necesse-dedicated-server/images/ingress-ui.png)

Primary cards answer “is it up / can people play / do I need to act?” World backups and a collapsed **Troubleshooting** section sit below. Your game plugin supplies theme colors, world-save paths, and log patterns; the layout is shared.

---

## What you are building

`game-server-base` is a **game-agnostic** supervisor (SteamCMD, HTTP archive, or plugin command install). It does not know Necesse — or any other title.

A game add-on is a thin layer:

1. Vendored copy of `game_server/` (keep in sync with the script below)
2. Plugin: `games/game.yaml` (Steam app id, launch command, args, `world_save`, log patterns)
3. Runtime packages in **that** add-on’s Dockerfile (Java, etc.)
4. Home Assistant metadata: `config.yaml`, translations, ports, short `README.md`, spartan `DOCS.md`

**Hard rule:** do not put game names, Steam app ids, ports, runtimes, or game option env keys into `game-server-base/`. If `rg -i yourgamename game-server-base` finds anything, it belongs in the game layer. Docker/compose game options are contributed by each plugin via `docker_env_keys()` (derived from `arg_map` / `settings_map` / templates, plus optional `env_options`).

---

## Fast path (copy an existing game folder)

Copy the closest sibling (`necesse-dedicated-server/` for SteamCMD + simple flags, `stationeers-dedicated-server/` for Unity, `factorio-dedicated-server/` for a non-Steam HTTP package) → e.g. `mygame-dedicated-server/`. Then:

1. Edit `games/game.yaml` for your dedicated server (app id, executable, args, data/log dirs, `world_save`).
2. Adjust the Dockerfile runtime for your binary (Java, extra `.so`s, **Xvfb**, …). Game-only packages stay in that Dockerfile — not in `game-server-base`.
3. Update HA `config.yaml`: name, slug, ports, options/schema. **Replace `icon.png` / `logo.png` immediately** (a folder copy keeps the previous game’s store art). See [App store images](#app-store-images). Add a `.dockerignore` that excludes `data/` before the first `docker compose build` (see [Local Compose](#local-compose)).
4. Rewrite that add-on’s **short** `README.md` (HA Info), **spartan** `DOCS.md` (Documentation tab), and **`GUIDE.md`** (GitHub end-user guide). Add a row to the repo root README games table.
5. After any supervisor change, from the repo root:

   ```bash
   ./game-server-base/sync-into-addons.sh
   ```

   Copies **only** `game_server/` into each sibling add-on that has `config.yaml` + `games/` (bytecode `__pycache__` is skipped). Never overwrites `games/*.yaml`. Not part of the Docker build. CI runs `./game-server-base/check-addon-sync.sh` so forgotten syncs fail the build. Running the unit tests does not dirty this check.

6. Bump versions, then install via the HA repo or Docker compose modeled on an existing game.

   **Version scheme:** `{supervisor_major}.{supervisor_minor}.{game_patch}`.
   The shared supervisor advertises major.minor in `game_server/version.py`
   (`SUPERVISOR_VERSION`, currently `3.11`). Each game `config.yaml` is that
   plus a patch (`3.11.0` for the first release on supervisor 3.11).

   - Supervisor change → bump `SUPERVISOR_VERSION` (e.g. `3.10` → `3.11`) **and**
     set **every** game add-on to `{new}.0` (`3.11.0`) so users see them all
     update together.
   - Game-only fix (no supervisor change) → bump that game’s patch only
     (`3.11.0` → `3.11.1`). Leave other games and `SUPERVISOR_VERSION` alone.

**Copy `run.sh`’s `export SERVER_PORT=…` when HA publishes a container port the game must bind** (Necesse, Factorio, Stationeers, Core Keeper Direct Connect). The Network UI remaps the *host* port; the process still has to listen on the container port in `config.yaml`. Do **not** set `host_network: true`. Titles that join only through a relay with no listen port can omit it — Core Keeper is not that case: Direct Connect (`-port`) is the default, and Steam Game ID join still works alongside it.

The supervisor writes a stable Linux machine-id to `/data/supervisor/machine-id` (the add-on volume) and copies it to `/etc/machine-id` when that overlay path is writable. Images delete any baked `/etc/machine-id`. Supervisor does **not** bind-mount the HAOS host id into add-ons (only core / some plugins get that RO mount), and we do not write `/sys` DMI or set `privileged` / `host_dbus` / `host_network`. A writable image id is overwritten so each install is unique. If `/etc/machine-id` is a read-only bind, first boot adopts it into `/data`.

If the title needs something the supervisor does not have (virtual display, extra Steamworks redistributable, join codes, a second Steam app id), solve it in the **game layer** first (`Dockerfile`, `launch_wrapper.sh`, `haos_defaults.py`, docs). If you believe `game-server-base` itself must change, stop and confirm — this repo is deliberately small.

---

## App store images

Home Assistant’s App store / Info UI uses two files in the **same folder as `config.yaml`** ([presentation docs](https://developers.home-assistant.io/docs/apps/presentation/)):

| File | Where it shows | Rules |
| --- | --- | --- |
| `icon.png` | Store tile, app list, small chrome | PNG, **128×128** square (HA recommends 1×1; this repo CI-enforces 128×128) |
| `logo.png` | App Info header bar | PNG, **250×100** wide rectangle (HA’s usual Info chrome size) |

Use the **game’s official store / Steam art**. Do not generate a new illustration, and do not leave the previous game’s PNGs in place after a folder copy — CI fails if two apps share the same `icon.png` bytes.

Steam (app id of the **client**, not the dedicated server) publishes the two shapes HA wants. Example: Core Keeper’s store page is app **1621690**; the dedicated server is **1963720**. Fetch art with **1621690**.

| Steam asset | Typical URL | HA use |
| --- | --- | --- |
| Library capsule | `https://cdn.cloudflare.steamstatic.com/steam/apps/<id>/library_600x900_2x.jpg` (600×900) | Square **icon**: crop a 1×1 from the **top** (title + key art stay in frame), scale to **128×128** PNG |
| Store header | `https://cdn.cloudflare.steamstatic.com/steam/apps/<id>/header_2x.jpg` (920×430) | Wide **logo**: scale-to-cover **250×100**, crop from the **top** so the wordmark remains, PNG |

Prefer the `_2x` objects when they exist. Convert JPEG → PNG; keep the original palette. A center crop of a tall capsule often chops the title off; a top crop usually matches how the store presents the game.

```bash
# icon.png — top-square of the library capsule
curl -fsSL -o /tmp/capsule.jpg \
  "https://cdn.cloudflare.steamstatic.com/steam/apps/<CLIENT_APPID>/library_600x900_2x.jpg"
magick /tmp/capsule.jpg -gravity North -crop 600x600+0+0 +repage -resize 128x128 icon.png

# logo.png — cover-fit the store header into the HA Info bar
curl -fsSL -o /tmp/header.jpg \
  "https://cdn.cloudflare.steamstatic.com/steam/apps/<CLIENT_APPID>/header_2x.jpg"
magick /tmp/header.jpg -resize 250x100^ -gravity North -extent 250x100 logo.png
```

(`convert` works in place of `magick` on older ImageMagick.) This repo’s CI requires those exact pixel sizes so the App store tile and Info header match the other games.

Non-Steam titles: the same idea from the developer’s store page (square box art / vertical capsule → icon; horizontal header / capsule → logo).

`README.md` for HA Info must **not** embed these images (HA already shows `icon.png` / `logo.png` in the chrome). GitHub `GUIDE.md` may show an Ingress screenshot under `images/ingress-ui.png` once you have a real capture — do not leave a broken image link.

---

## Ingress theme colors

The status UI reads `ui_theme` from `games/game.yaml` (keys merged onto `status_http.DEFAULT_UI_THEME`). This is **not** generated art — it is a 10-color CSS palette so Open Web UI matches the title without looking like a clone of Necesse.

| Key | Where it shows | Pick |
| --- | --- | --- |
| `accent` | Buttons, links, focus, the distinctive brand stripe | The one color people would name from the store page (Necesse gold, Factorio factory orange, Stationeers ice blue, Core Keeper cavern teal) |
| `bg` / `wash` / `panel` / `glow` | Page, header wash, cards | Shadows from the same store header / capsule — dark, not pure black unless the marketing is |
| `ink` / `muted` | Body text / secondary | High contrast on `panel`; muted can be a greyed accent |
| `good` / `bad` | Running / error | Stay readable green / red; a slight tint toward the brand is fine |
| `depth` | Recessed wells | Darker than `panel` |

**How to choose (same assets as the store images):** eyedropper the Steam header / library capsule (or the developer’s site). Do not invent a complementary scheme in a palette generator. Then compare `accent` against the other `*-dedicated-server/games/game.yaml` files — CI fails if two games share the same accent hex.

A usable check: open two Ingress tabs (or the screenshots in each `GUIDE.md`) and confirm you can tell the games apart at a glance from the header stripe and background, not only the title string.

---

## Local Compose

Plain Docker / Portainer uses each game folder’s `docker-compose.yml` (`build.context: .`, volume `./data:/data`).

**`.dockerignore` must list `data/`.** Compose `build.context: .` tars this folder and sends it to the Docker daemon *before* the Dockerfile runs. The Steam/game install lives on the runtime volume `./data` and is never `COPY`’d into the image — but without an ignore file that whole tree still rides along as build context (Core Keeper’s dedicated server is ~650 MB). That is slow and a footgun if a `COPY` is ever too broad. Sibling add-ons ship this file; CI checks that it exists and excludes `data/`.

**SteamCMD `FAILED (No Connection)` on the default bridge** while `curl https://steamcdn-a.akamaihd.net` works is almost always Steam **connection managers** (not the CDN) failing through nested Docker NAT. It is not an add-on bug and it is **not** a reason to set `host_network: true` in `config.yaml` (that drops the Home Assistant security rating). HAOS has its own network stack; real installs typically reach Steam CMs on the default bridge.

Diagnostic only:

```bash
docker run --rm --network host --entrypoint /opt/steamcmd/steamcmd.sh IMAGE \
  +login anonymous +quit
```

`Connecting anonymously to Steam Public...OK` here, and retries / `FAILED (No Connection)` in compose, means this environment’s Docker NAT is the problem. For a one-off local boot you may run that same image with `--network host` (and no `ports:` mapping). Do not ship `host_network: true`. Do not add UDP game ports to “make Steam work.”

---

## Plugin YAML (game identity)

Point the container at your plugin with `GAME_PLUGIN` (Necesse’s `run.sh` does this).

| Field | Purpose |
| --- | --- |
| `name` | Display / notification label |
| `steam_app_id` | SteamCMD app id (required unless `package_install` is set) |
| `steam_branch` | Default SteamCMD branch (overridable by HA option `steam_branch`) |
| `package_install` | Optional non-Steam install. `kind: http_archive` needs `version_url`, `version_json_path`, `download_url`, `strip_components`. `kind: command` needs `version_argv` / `install_argv` (plugin-supplied commands; stdout is logged). `{release_channel}` is substituted from HA option `release_channel` (default `stable`) in JSON paths, download URLs, and argv tokens. Command installers may write `operator_action.json` under the state dir while they wait on a human (device-code login): Ingress shows the URL + code. |
| `executable` | argv to launch the server (may be a game-layer wrapper script) |
| `install_marker` | File **or directory** under the install dir that means “install looks present” (`.exists()`, so a Unity `*_Data/` folder is fine when the binary name varies) |
| `arg_map` | HA/options keys → simple CLI flags (`-flag value`) |
| `argv_prefix` | Ordered tokens after the executable; `{option}` templates; empty → omitted |
| `settings_flag` / `settings_map` / `fixed_settings` | Optional `-settings Key Value …` style block (Unity servers, etc.) |
| `config_files` | Optional JSON/INI/`mod_list` files rewritten from options before each launch (`path`, `format`, `fixed`, `map`, `types`, or `mods` for mod_list) |
| `world_prepare` | Optional one-shot argv (same executable) when the active world is missing — e.g. create-save before host |
| `env_options` | Extra UPPER_SNAKE Docker/compose env vars (optional). Keys from `arg_map` / `settings_map` / `{option}` templates are accepted automatically |
| `data_dir` / `logs_dir` / `working_dir` | Usually under `/data/...` |
| `stop_stdin_commands` | Optional graceful stop |
| `pre_backup_stdin_commands` | Optional stdin lines sent before a scheduled live backup (save flush). Does not stop the process. |
| `world_save` | Active world artifact: `strategy: named_path` + `paths` templates. Drives status UI, upload restore, and **by-kind backups** (file = copy as-is; folder = zip). Backup archives are named with that file/folder, retention is grouped per name, and restore refuses a snapshot from a different world until the active name/slot matches. |
| `world_catalog` | Optional Ingress world list (`glob` / `globs`, `name_from`, optional `caption_file` + `caption_json_path`). |
| `world_create` | Optional extra Ingress create-world fields (`text` or `select`). Labels and values come from the plugin YAML. |
| `backup_paths` | Fallback roots when no named world exists yet; also used to restore legacy `*.tar.gz` snapshots |
| `log_patterns` | Active regexes (ready, players, version, `players_empty`, …). Prefer empty until proven. |
| `log_pattern_candidates` | Extra dry-run regexes for Ingress highlighting |
| `ui_theme` | Ingress CSS colors (`accent`, `bg`, `panel`, …). Sample from official store art; keep `accent` unique vs sibling games. See [Ingress theme colors](#ingress-theme-colors) |
| `player_tracking_mode` | `count` (default, numeric/named) or `presence` (Idle vs occupied; unknown leave → idle) |
| `status_probe` | Optional argv that prints one JSON object of live status fields (`player_count`, `ready`, `game_version`, `players`). Peer to `log_patterns`: include only keys you can assert this tick; omit a key (or print `{}`) to yield nothing for that field. `null` is omit, not 0. Invalid JSON / non-zero exit applies nothing. Do not invent defaults. |
| `restart_when_empty` | Delay a pending game restart until occupancy is a **known** 0 (from logs and/or `status_probe`) or the process is down. Unknown occupancy waits. World switch/create still restart immediately. Do not set this unless something can honestly report empty. |
| `copyparty` | Optional file-drop: unique `port` and `root` template aimed at a live mods (or similar) directory. The supervisor writes Copyparty config and keeps the process up across game crashes and world switches. Game-layer hook argv (`before_upload`, `after_idle_upload`, `before_delete`, `after_delete`) validate and rename files. Not a generic sidecar runner. Ingress shows an **Uploads** hero card (file count) that opens `http://<home-assistant-host>:<port>/`. |
| `hold_on_crash_loop` | Stay in the supervisor loop after the crash budget so Ingress (and Copyparty) keep running. `/healthz` is still **not** healthy while lifecycle is `failed`. Omit the HA add-on `watchdog` on titles that use this; other games keep the watchdog. |

Shape reference: `game-server-base/tests/fixtures/example.game.yaml`

### Operator action card (optional)

A game-layer install or launch script may write `/data/supervisor/operator_action.json` while it blocks on a human (device-code login, “open this URL and enter this code”). Ingress shows a first-class card: open the `url` in a **new browser tab** (HA Ingress is already an iframe — do not embed third-party login pages), copy the `code`, optional `steps`. Delete the file when the action is finished so the card disappears. The supervisor only *reads* this file; OAuth clients and token storage stay in the game layer. URLs must be `http` or `https`.

```json
{
  "title": "Sign in required",
  "detail": "Open the link in a new tab, enter the code, then return here.",
  "url": "https://example.invalid/device?user_code=ABCD-1234",
  "code": "ABCD-1234",
  "steps": [
    {"label": "Download files", "state": "active"},
    {"label": "Authenticate server", "state": "pending"}
  ]
}
```

`steps[].state` is `pending`, `active`, or `done`. Command `package_install` argv processes inherit `INSTALL_DIR` and `STATE_DIR`.

### Log patterns (dry-run first)

- **New games must ship `log_patterns: {}`** (empty active). Put guesses in `log_pattern_candidates` plus the shared generics in `patterns.py`.
- Generic + game candidates only **highlight** in Ingress Debug mode; they do **not** gate updates or player presence.
- Prefer many broad, case-insensitive guesses (over-match) over one clever regex. The debug table shows **one row per category** (Mode / Hits / recent matches); regex text stays out of the UI.
- Turn on **Debug mode**, start the server, watch which categories light up (green = active, orange = dry-run), then promote a precise regex into `log_patterns` for that category.
- Copy the debug textarea (or **Troubleshooting → Log pattern prompt**, `/api/logs/prompt`) into an AI chat. Both are the same text: live hits plus a log-file rescan (the live tailer starts at EOF, so startup lines are only in the rescan).
- Write a new precise regex from the sample lines. Do not copy guess regexes — a guess can hit the right line with the wrong pattern. Join and leave must capture the **same** identity token (Steam id, internal userid, and display name are different namespaces). `ready` is port bind / accepting connections, not a later GameInfo or public-IP line. Configured categories still list **other interesting lines** guesses found, so a working regex can still be replaced later.
- Without Debug mode, Ingress hides the HTML table. The **Log pattern prompt** link still works.
- Without active join/leave patterns, “update only when empty” cannot wait for players to leave.

---

## What the supervisor already provides

- HA `/data/options.json` (+ env overrides)
- SteamCMD install/update with a rate gate (serialize, spacing, backoff)
- Non-Steam `package_install` (`http_archive` or plugin `command` argv)
- Optional Ingress operator-action card (`operator_action.json`: open URL, copy code)
- Process supervision, crash restarts, in-process game restart (`POST /api/restart` or `restart.request`; `/healthz` stays healthy while `restarting`), optional Copyparty on a plugin-declared port+root, optional `status_probe` JSON peer to log patterns, privilege drop to `gameserver`
- By-kind world backups + retention profiles (per world name/slot); Ingress restore / NEW WORLD / upload; optional live save-flush via `pre_backup_stdin_commands`
- Ingress world picker (catalog glob + create-world extra fields from the plugin)
- HA Core notifications + `/data/supervisor/status.json`
- Ingress status HTTP and log capture toolkit
- Mirrored streams on the HA Logs tab (`[game]`, `[game-log]`, `[steamcmd]`)

---

## Tests

```bash
PYTHONPATH=game-server-base python3 -m unittest discover -s game-server-base/tests -q
python3 -m unittest discover -s tests -q
```

CI runs both suites (plus `check-addon-sync.sh`). After supervisor changes: sync → bump `SUPERVISOR_VERSION` if needed → set **each** game add-on to `{major}.{minor}.0` → rebuild/reinstall.

---

## Layout

| Path | Role |
| --- | --- |
| `game-server-base/game_server/` | Shared Python package |
| `game-server-base/Dockerfile` | Generic SteamCMD + Python image |
| `game-server-base/tests/` | Unit tests + synthetic plugin |
| `game-server-base/games/` | Empty on purpose — plugins live in each add-on
