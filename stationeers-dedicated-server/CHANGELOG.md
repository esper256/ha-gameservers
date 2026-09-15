# Changelog

## 3.9.0

- Shared supervisor **3.9**: optional Copyparty file-drop (`copyparty.port` + `copyparty.root` on a live mods directory), optional `player_probe` / `restart_when_empty`, optional `hold_on_crash_loop`. `/healthz` is **not** healthy while lifecycle is `failed` (HA watchdog still recycles titles that do not opt in). Vendored `game_server/` sync.

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

- Version scheme: `{supervisor}.{minor}.{game patch}`. Shared supervisor is **3.0**; this app is **3.0.0**. Future supervisor features bump every game to `3.1.0`; a Stationeers-only fix is `3.0.1`
- Ingress subtitle advertises supervisor **3.0** separately from the HA app version
- Sync shared supervisor: promote prompt lists other interesting log lines on configured patterns; shared dry-run guess regexes refreshed

## 1.0.19

- Sync shared supervisor: drop the JSON API expander; one **Log pattern prompt** (`/api/logs/prompt`) matching the debug textarea (file rescan included); tighter AI prompt for promoting regexes

## 1.0.18

- Sync shared supervisor: JSON API log-pattern links rescan the on-disk log and return example lines for not-yet-configured categories (works without Debug mode)

## 1.0.17

- Sync shared supervisor: backups named by world file; retention and pre-update keep-one grouped per world; restore of a different world's archive is refused until the active world matches

## 1.0.16

- Sync shared supervisor: CLI option rendering keeps digit strings `0`/`1` (do not treat them as booleans)

## 1.0.15

- Hero card messages match the server status: lowercase (“running”, “player last joined…”, “up to date”)
- Pattern watching: **configured** / **stale** / **not configured**; not-configured guesses collapse under **Not configured log patterns**
- Replace highlighted lines with a copy-to-clipboard AI prompt for promoting `games/game.yaml` regexes; drop the extra “view recent game output” button (HA Logs already covers that)

## 1.0.14

- Free disk uses normal ink when healthy; warning color only when low
- Collapse pattern hits, log capture tools, and JSON API under one **Troubleshooting** expander
- World backups copy: restoring stops the server, makes a world backup, then restores the selected backup

## 1.0.13

- Backup card counts every restorable archive (scheduled, pre-update, pre-restore) — same set as the restore dropdown minus NEW WORLD
- Update card: when not up to date, show “Update available” and an in-card **Update now** button (replaces the “Checked … ago” hint)
- Join/leave games: Players card shows “Player last joined … ago” (green when likely occupied, amber when idle); exact count games keep a numeric count with “No count yet” while waiting

## 1.0.12

- Ingress UI polish for release users: primary status cards, update banner only when pending, button hierarchy, shorter backup copy, stacked restore/upload, collapsed troubleshooting logs, mobile layout, Steam vs package update wording

## 1.0.8

- Sync shared supervisor: broader dry-run log candidates; keep dry-run pattern rows visible alongside active ones

## 1.0.7

- Sync shared supervisor: package install clean-replace (no stale merge on HTTP archive updates)

## 1.0.6

- **Steam branch** option: Public (default) or Beta
- Sync shared supervisor: install-channel overrides, keep game stdin open, generic `mod_list` config_files format

## 1.0.5

- Sync shared supervisor: HTTP `package_install` path for non-Steam games; recent game output is game process only (Stationeers still uses SteamCMD)

## 1.0.4

- Sync shared supervisor: generic `config_files` + `world_prepare` launch helpers; signal-first stop when no stdin quit commands (Stationeers still uses stdin save/quit)

## 1.0.3

- Sync shared supervisor: presence unknown-leave clears to Idle; 24h max wait for empty before applying updates; log monitor follows `data_dir` logs without cross-source double-fires; drains/reopens across truncate/rotate

## 1.0.2

- Default world / map is **Mars2**; public server-browser listing defaults to **off**
- Server name is optional; blank → stable `HAOS Stationeers ####` per install
- Start-condition help text lists common kit ids (DefaultStart / Brutal / …)

## 1.0.1

- Promote proven log patterns: game version, client connect/disconnect, “No clients connected”
- `player_tracking_mode: presence` — Ingress shows Idle / Players Active (no numeric count)
- Shared supervisor: last 5 regex matches per pattern in the log-watch table

## 1.0.0

- Initial Stationeers dedicated-server add-on (Steam app 600760, Unity Linux binary)
- Docker/compose env options contributed by this plugin (`arg_map` / `settings_map` / templates → `docker_env_keys`), not the base supervisor
- Shared supervisor has no cross-game world-path heuristic; this plugin declares `world_save.paths`
- Thin game layer over shared `game-server-base` supervisor (no Java)
- Debian Trixie image for Stationeers’ glibc requirement
- Ports: UDP 27016 (game) + UDP 27015 (Steam query); Ingress status UI
- High-tech light-blue / dark-blue Ingress theme
- World saves as folders under `/data/world/saves/<name>/` with by-kind zip backups
- Log join/leave/ready patterns ship as dry-run candidates until proven
