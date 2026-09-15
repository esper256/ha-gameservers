# Changelog

## 3.8.0

- Shared supervisor **3.8**: Ingress world picker and create-world extra fields; in-process game restart API; optional live-backup stdin flush. World switch/create reserves the restart before changing the live world; `.`/`..` names are rejected; `/healthz` stays healthy during a planned game restart. Vendored `game_server/` sync.

## 3.7.0

- Shared supervisor **3.7**: folder restores extract into staging then swap (failed zips no longer wipe the live world); internal folder backups keep a nested sole directory; pre-update aborts when backup cannot snapshot existing world data; crash recovery still runs while an update is queued, unexpected exit 0 counts toward the restart budget, and `wait()` no longer busy-loops on a dead process. Vendored `game_server/` sync.


## 3.6.0

- Shared supervisor **3.6**: Ingress 5-second status poll no longer logs `Game version from logs` on throwaway rescans; `[game-log]` no longer replays stdout already shown as `[game]` during large boot floods. Vendored `game_server/` sync.

## 3.5.0

- Shared supervisor **3.5**: persist a Linux machine-id under `/data/supervisor` and copy it to `/etc/machine-id` when the overlay allows (no host bind, no `/sys`, no extra privileges; images do not ship a baked id). Vendored `game_server/` sync.

## 3.4.0

- Shared supervisor **3.4**: empty player / version-mismatch pattern notes log only when the live tailer starts (Ingress 5-second poll no longer repeats them). Vendored `game_server/` sync.

## 3.3.0

- Shared supervisor **3.3**: SIGTERM during first install exits cleanly (no false “install failed” crash); package install commands honor stop without waiting for the next log line; `/healthz` stays reachable for HA watchdog and Docker healthchecks. Vendored `game_server/` sync.

## 3.2.0

- Shared supervisor **3.2**: Ingress toast when live status refresh fails (app stopped or unresponsive). Vendored `game_server/` sync.

## 3.1.0

- Shared supervisor **3.1**: `package_install.kind: command` (plugin argv installers), Ingress operator-action card for device-code sign-in, `waiting` lifecycle while that file is present. Vendored `game_server/` sync.

## 3.0.0

- Version scheme: `{supervisor}.{minor}.{game patch}`. Shared supervisor is **3.0**; this app is **3.0.0**. Future supervisor features bump every game to `3.1.0`; a Necesse-only fix is `3.0.1`
- Ingress subtitle advertises supervisor **3.0** separately from the HA app version
- Sync shared supervisor: promote prompt lists other interesting log lines on configured patterns; shared dry-run guess regexes refreshed

## 2.1.50

- Sync shared supervisor: drop the JSON API expander; one **Log pattern prompt** (`/api/logs/prompt`) matching the debug textarea (file rescan included); tighter AI prompt for promoting regexes

## 2.1.49

- Sync shared supervisor: JSON API log-pattern links rescan the on-disk log and return example lines for not-yet-configured categories (works without Debug mode)

## 2.1.48

- Sync shared supervisor: backups named by world file; retention and pre-update keep-one grouped per world; restore of a different world's archive is refused until the active world matches

## 2.1.47

- Sync shared supervisor: CLI option rendering keeps digit strings `0`/`1` (do not treat them as booleans)

## 2.1.46

- Hero card messages match the server status: lowercase (“running”, “player last joined…”, “up to date”)
- Pattern watching: **configured** / **stale** / **not configured**; not-configured guesses collapse under **Not configured log patterns**
- Replace highlighted lines with a copy-to-clipboard AI prompt for promoting `games/game.yaml` regexes; drop the extra “view recent game output” button (HA Logs already covers that)

## 2.1.45

- Free disk uses normal ink when healthy; warning color only when low
- Collapse pattern hits, log capture tools, and JSON API under one **Troubleshooting** expander
- World backups copy: restoring stops the server, makes a world backup, then restores the selected backup

## 2.1.44

- Backup card counts every restorable archive (scheduled, pre-update, pre-restore) — same set as the restore dropdown minus NEW WORLD
- Update card: when not up to date, show “Update available” and an in-card **Update now** button (replaces the “Checked … ago” hint)
- Join/leave games: Players card shows “Player last joined … ago” (green when likely occupied, amber when idle); exact count games keep a numeric count with “No count yet” while waiting

## 2.1.43

- Ingress UI polish for release users: primary status cards, update banner only when pending, button hierarchy, shorter backup copy, stacked restore/upload, collapsed troubleshooting logs, mobile layout, Steam vs package update wording

## 2.1.39

- Sync shared supervisor: broader dry-run log candidates; keep dry-run pattern rows visible alongside active ones

## 2.1.38

- Sync shared supervisor: package install clean-replace (no stale merge on HTTP archive updates)

## 2.1.37

- **Steam branch** option: Public (default) or Experimental
- Sync shared supervisor: install-channel overrides, keep game stdin open, generic `mod_list` config_files format

## 2.1.36

- Sync shared supervisor: HTTP `package_install` path for non-Steam games; recent game output is game process only (Necesse still uses SteamCMD)

## 2.1.35

- Sync shared supervisor: generic `config_files` + `world_prepare` launch helpers; signal-first stop when no stdin quit commands (Necesse still uses stdin save/exit)

## 2.1.34

- Player tracking uses **presence** (Idle / Players Active): join → occupied; unknown leave identity → idle so a mismatched disconnect cannot stick updates forever
- Pending Steam updates still wait for Idle, but never longer than **24 hours** (`update_empty_max_wait_hours`)
- Live log monitor searches `data_dir` (e.g. `/data/world/latest-server-log.txt`) when `/data/logs` is empty; follows without cross-source double-fires; drains/reopens across truncate/rotate so version-mismatch catching keeps working

## 2.1.33

- Sync shared supervisor: last 5 regex matches per pattern; optional presence player tracking (Necesse stays on count mode)

## 2.1.32

- Sync shared supervisor: remove cross-game world-save path heuristic (Necesse already declares `world_save.paths`)

## 2.1.31

- Sync shared supervisor: Docker/compose game option env keys come from each plugin (`docker_env_keys`), not a hardcoded base allowlist

## 2.1.30

- Sync shared supervisor: generic `argv_prefix` / `settings_map` CLI building for non-Java Steam servers (no Necesse behavior change)

## 2.1.29

- Documentation cleanup: concise get-started guides (repo README, Necesse README/DOCS, packaging guide)
- Clarify Configuration help text for by-kind world backups

## 2.1.28

- Backups follow the active world **kind** from `world_save` (same as upload restore):
  - Single-file saves (Necesse `.zip`): copied as-is — no wrapping/recompressing into another archive
  - Folder saves: zipped with the same layout as world download/upload
- Legacy `*.tar.gz` datadir snapshots remain restorable
- `backup_paths` stays as the fallback when no named world exists yet

## 2.1.27

- Ingress: **Restore from upload** — upload a world save; apply mode comes from the active world kind (single-file save vs folder save), not from guessing the upload alone
  - File worlds (e.g. a game that stores the world as one `.zip`): replace that file as-is
  - Folder worlds: require a `.zip` and extract into the save directory
  - Upload controls styled like other action buttons and placed with backup restore
- Generic `world_save` locator exposes `kind` / upload hints for any game plugin
- Remove the Ingress update “waits until nobody is online” note (and related copy) entirely
- After confirming NEW WORLD / restore / upload, skip the second OK-only “scheduled” alert; errors still alert

## 2.1.26

- Fix NEW WORLD / restore from Ingress: when the POST body is dropped (missing Content-Length), accept `confirm` / `empty` / `archive` from the query string so the second “confirmation required” alert no longer blocks the action
- Server hero shows **stopping for restore** / **restoring world** while a restore runs (and the same idea for updates)

## 2.1.25

- Keep bridge networking (no `host_network`): Necesse always listens on UDP **14159** inside the app
- Network tab sets the port players use on the Home Assistant machine; leave it at 14159 unless that port is already taken
- Drop the host-network experiment that would have exposed app ports on the LAN

## 2.1.24

- Clarify Network UDP port help text (players’ join port on the HA machine; prefer leaving at 14159)

## 2.1.23

- **Update only when empty** copy clarifies it needs working player join/leave detection
- Remove HA `backup_interval_minutes` and `server_port` options (parallel/confusing knobs)
  - Scheduled backups run daily; history is controlled only by **Backup retention**
  - Network UDP port is what players use; Home Assistant forwards it into the app
- **Backup before update** keeps only the newest pre-update archive (no growing trail)
- Backup families under one retention profile: rolling scheduled archives, one pre-update snapshot, and pre-restore safety copies kept for **1 / 7 / 30 days** (`minimal` / `standard` / `extended`)

## 2.1.22

- Version bump so Home Assistant refreshes the Configuration schema (2.1.21 follow-ups were invisible to already-installed apps)
- **Debug mode** toggle moved next to update settings; when off, Number of players hero stays hidden unless an *active* `player_count` log pattern exists (join/leave still used for empty-server updates)

## 2.1.21

- Activate Necesse `version_mismatch` log pattern for `Client "…" had wrong version (…)` — probes Steam for a newer build first; only then schedules stop/apply (bypass quiet hours, wait for empty, orderly `save`/`exit` with 240s budget, SteamCMD)
- Ingress theme colors come from per-game `ui_theme` in `games/game.yaml` (Necesse keeps green/amber)
- World restore UI: style restore like other action buttons; drop separate empty-world button; add **NEW WORLD** at the bottom of the backup selector
- Free disk hero no longer shows the “Min 512 MiB” hint (threshold still enforced for backups/updates)
- Ingress hero order: Uptime → Crashes … World save → Backups → Free disk; world save name links to download (file as-is, folder as stdlib zip)
- HA option **Debug mode** (default off): when off, hide Ingress log-watch pattern tooling; also hide Number of players until an active `player_count` pattern exists
- Log-watch table: one row per category, “Recent possible matches” (up to 3 lines per regex), Pattern column removed
- Base dry-run candidate regexes made cross-game (dropped Necesse-specific ready/player_count phrasing)

## 2.1.20

- Fix Ingress status page crash on GET `/` (`KeyError: '"archive"'`) caused by unescaped JSON examples in the HTML `str.format` template; add full-page render + placeholder lint tests so this class of bug fails CI

## 2.1.19

- Orderly stop: on HA/Docker SIGTERM, ask Necesse to `save`/`exit` and wait for a voluntary quit before SIGTERM/SIGKILL; use most of the 300s add-on stop grace for the game (240s) so supervisor cleanup can still finish; exit 0 on intentional stop; abort in-flight SteamCMD when stop is requested

## 2.1.18

- Ingress: **Start new empty world** — same confirm → stop → optional pre-restore safety copy → clear world files → restart flow as restore, but with no archive (game creates a fresh world)
- Save-game safety: refuse to wipe/replace live world data without a successful safety backup on disk; safety copies skip the "tiny world" skip; retention is the only archive deletion path (removed keep-5 pre-restore prune); restore API requires `confirm:true`

## 2.1.17

- Baseline cleanup: explicit `lifecycle` for status/healthz (crash-loop no longer looks healthy forever); `/healthz` uses a cheap snapshot; status reads no longer mutate `local_build_id`; shared privilege helpers; `outside_rotation` safety backups; `html.escape`; dead install-age formatter removed; UI tests assert goals rather than exact copy

## 2.1.16

- Ingress: restore a world backup with confirm → stop server → save current world as a pre-restore safety copy (outside normal rotation) → replace world → restart
- Status hero again shows **World save** and **Free disk** cards, grouped at the end
- Home Assistant notification when backups fail (interval unchanged)

## 2.1.15

- Ask Steam for game updates once daily at local 05:00 by default (was every 30 minutes)
- Plain-language status copy: schedule restarts for when nobody is online — drop “gating” jargon
- Ingress: **Update game server now** button (confirms first; stops the server, updates from Steam, restarts — disconnects anyone playing)
- Confine capture downloads; require POST to create captures; bind compose status port to localhost

## 2.1.14

- Fix fresh SteamCMD installs failing with `Missing file permissions` by running SteamCMD as the `gameserver` user (same owner as `/data/game` / `/data/steam-home`) instead of root
- Pin the Linux Steam platform on app-info readiness probes (same pin as `app_update`)
- On post-readiness `Missing configuration`, keep the normal retry/backoff budget instead of hard-aborting after the first failure (no appcache wipe — that only forces extra anonymous Steam traffic)
- Do not treat empty `steamapps/` scaffolding as an existing install; ensure `HOME`/`STEAM_HOME` point at `/data/steam-home` from `run.sh`

## 2.1.13

- Promote proven Necesse log patterns from Ingress dry-run highlights: ready (`Started server using port`), player join/leave (SteamID64), and an earlier game-version line (`Loading dedicated server on version`)
- Empty-server update gating now uses join/leave tracking (not “saved players” / garbage-collection lines)
- Status hero: Backups card with archive count plus oldest and newest backup ages

## 2.1.12

- Reorganize status hero: Server, Number of players, Uptime, Game version, Update pending, Game server crashes
- Uptime hint shows since first start / crash restart / server update; supervisor uptime moves under crashes
- Game version card merges human version + Steam build + installed age (removes separate Game files / Restarts cards)
- Collapse “Game server log watching pattern hits” (table + highlighted lines) once any active pattern exists; hide dry-run rows/tags for categories that already have an active pattern
- Remove Recent output section (use the Home Assistant app Logs tab instead)

## 2.1.11

- Status UI polish: dual uptime (game process + supervisor), clearer restart wording (“First start” / “Last restart: game crash” / “Last restart: server update”), “Game server crashes” label, and “Game files installed / XYZ ago”
- Show SteamCMD client version next to the supervisor version line
- Soft-refresh the Ingress status page via `api/ui` (removes full-page meta refresh flicker)
- Shared world-save locator: plugin `world_save.paths` for the active artifact (UI size); `backup_paths` for archives; path-guessing quarantined behind explicit heuristic opt-in
- Capture human-readable game version from logs (e.g. `1.3.1`) and show it on the status page; Necesse activates the proven “game version …” startup pattern

## 2.1.10

- Status UI cleanup: “Dedicated server supervisor v…”, drop redundant App version / Steam build / Player gating squares
- Restarts no longer count the first boot; show last start reason (boot / crash / update)
- Update pending shows last Steam check age; Game files shows when the install was last updated; World save shows human-readable save size
- Strip ANSI color codes from game logs; broaden dry-run ready/empty-server highlights
- Log tools: human actions first, captures in a dropdown + download, raw tail falls back to recent output and can render as text
- Keep SteamCMD Linux-only (remove Windows depot fallback)

## 2.1.9

- Fix fresh-install SteamCMD failure `Failed to install app '1169370' (Missing configuration)` by **waiting for Steam app info readiness** (`app_info_print` buildid) before `app_update`, instead of retrying installs as "transient"
- Use CLI `+force_install_dir`/`+app_update`, persist Steam home under `/data/steam-home`, and pin Necesse to Linux Steam depots (no Windows fallback)
- Generate `en_US.UTF-8` in the image to silence SteamCMD locale warnings
- Tighten error handling: handle game crashes and Steam/SteamCMD failures explicitly; do not treat failed Steam checks as “up to date” or failed updates as success; stop broad `except Exception` swallows that hid supervisor bugs

## 2.1.8

- Align status UI with Home Assistant Ingress defaults (port **8099**, Supervisor-only peers, `X-Ingress-Path` base href)
- Remove host `8080` mapping entirely — use **OPEN WEB UI** after start (not the Ingress info chip)
- Print a clear `Home Assistant app version: …` banner at the earliest startup point (shell + Python logs)

## 2.1.7

- Fix HAOS App store discovery: Supervisor rejects `timeout` above 300, which silently hid this app from the repository
- Cap start timeout at 300, set `io.hass.type=app`, drop unused `share` map
- Document amd64-only availability and Apps (formerly Add-ons) UI paths

## 2.1.6

- Drop v1 upgrade path: remove `/home/necesse` and `/opt/game` migrations, and the generic `path_migrations` machinery that existed only for that
- Remove transitional backup `keep_*` option shims; `backup_retention` is the only retention setting

## 2.1.5

- Add a process-wide Steam gate: serialize SteamCMD, enforce spacing, exponential retry backoff, and long cooldowns on failure/rate-limit signals
- Stop the failed-update 30s tight loop that could hammer Steam; pause applies and keep the current build running
- Floor auto-update polling at 15 minutes; hard-cap SteamCMD retries at 3
- Rewrite docs around user journeys: repo landing → game install guide; separate guide for building on `game-server-base`

## 2.1.4

- Stream SteamCMD install/update output into the Home Assistant Logs tab (`[steamcmd]`)
- Mirror file-only game log lines to Logs (`[game-log]`) with short-window dedupe against `[game]` stdout
- Flush supervisor logging so HA Logs stay near-realtime

## 2.1.3

- Simplify backup UX to one `backup_retention` profile (`minimal` / `standard` / `extended`)
- Standard = 7 daily → 4 weekly → 12 monthly cascade

## 2.1.2

- Alpha-safe log watching: no active Necesse regexes by default
- Generic candidate regexes run dry-run only and highlight hits in Ingress
- Pattern hit table + stale detection via `/api/logs/patterns`
- When player patterns are inactive, Steam updates still run; mismatch/player gating stays off

## 2.1.1

- Enforce clean separation: generic supervisor has no game identity; Necesse is plugin YAML + Java + HA metadata only
- Move legacy path moves into `games/game.yaml` `path_migrations` (handled by generic code)
- Base image no longer bundles OpenJDK, game ports, or a default plugin

## 2.1.0

- Persist Steam game install under `/data/game` (survives container recreate)
- Run game process as `gameserver` after fixing volume ownership
- Graceful stop via stdin `save`/`exit` before SIGTERM, backup, and update
- Generational backup retention (recent/daily/weekly/monthly/yearly)
- Skip empty/tiny world backups with exponential backoff on failures
- Disk free-space guard for backups/updates
- HA persistent notifications via Core API (no MQTT)
- Continuously write `/data/supervisor/status.json`
- Ingress log toolkit: capture, suggest patterns, raw tail, downloadable archives
- Auto log capture on version mismatch and crash

## 2.0.0

- Replace the thin wrapper around `andreasgl4ser/necesse-server` with a first-party SteamCMD supervisor shared as `game-server-base`
- Read Home Assistant `/data/options.json` with Python (no `jq`, no remapping `/data` over `/home/necesse`)
- Auto-update from Steam build IDs on a timer, preferring empty-server restarts
- Detect version-mismatch style log lines and force an update cycle (bypasses quiet hours)
- Crash restart loop with per-hour rate limit
- Optional Ingress status page (`/`, `/api/status`, `/healthz`)
- Periodic + pre-update world backups under `/data/backups`
- Expose UDP 14159 by default again
- Migrate legacy saves from `/home/necesse/.config/Necesse` when present

## 1.7.0

- Previous generation based on Andreas Glaser's SteamCMD image and a bash HA entrypoint
