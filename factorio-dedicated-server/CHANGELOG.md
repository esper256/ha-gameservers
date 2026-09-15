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

- Supervisor **3.1** vendored sync (app **3.1.0**). Factorio still uses `package_install.kind: http_archive` (factorio.com headless tarball); install, version-check, and update behavior is unchanged. The new command-install path and Ingress operator-action card are unused by this app.

## 3.0.0

- Version scheme: `{supervisor}.{minor}.{game patch}`. Shared supervisor is **3.0**; this app is **3.0.0**. Future supervisor features bump every game to `3.1.0`; a Factorio-only fix is `3.0.1`
- Ingress subtitle advertises supervisor **3.0** separately from the HA app version
- Sync shared supervisor: promote prompt lists other interesting log lines on configured patterns; shared dry-run guess regexes refreshed

## 1.0.17

- Sync shared supervisor: drop the JSON API expander; one **Log pattern prompt** (`/api/logs/prompt`) matching the debug textarea (file rescan included); tighter AI prompt for promoting regexes

## 1.0.16

- Sync shared supervisor: JSON API log-pattern links rescan the on-disk log and return example lines for not-yet-configured categories (works without Debug mode)

## 1.0.15

- Sync shared supervisor: backups named by world file; retention and pre-update keep-one grouped per world; restore of a different world's archive is refused until the active world matches

## 1.0.14

- Sync shared supervisor: CLI option rendering keeps digit strings `0`/`1` (do not treat them as booleans)

## 1.0.13

- Hero card messages match the server status: lowercase (“running”, “player last joined…”, “up to date”)
- Pattern watching: **configured** / **stale** / **not configured**; not-configured guesses collapse under **Not configured log patterns**
- Replace highlighted lines with a copy-to-clipboard AI prompt for promoting `games/game.yaml` regexes; drop the extra “view recent game output” button (HA Logs already covers that)

## 1.0.12

- Free disk uses normal ink when healthy; warning color only when low
- Collapse pattern hits, log capture tools, and JSON API under one **Troubleshooting** expander
- World backups copy: restoring stops the server, makes a world backup, then restores the selected backup

## 1.0.11

- Backup card counts every restorable archive (scheduled, pre-update, pre-restore) — same set as the restore dropdown minus NEW WORLD
- Update card: when not up to date, show “Update available” and an in-card **Update now** button (replaces the “Checked … ago” hint)
- Join/leave games: Players card shows “Player last joined … ago” (green when likely occupied, amber when idle); exact count games keep a numeric count with “No count yet” while waiting

## 1.0.10

- Ingress UI polish for release users: primary status cards, update banner only when pending, button hierarchy, shorter backup copy, stacked restore/upload, collapsed troubleshooting logs, mobile layout, Steam vs package update wording

## 1.0.6

- Promote proven Factorio log patterns to **active**: ready (`Hosting game at` / CreatingGame→InGame), `Factorio X.Y.Z` version, `[JOIN]` / `[LEAVE]` presence
- No version-mismatch pattern — Factorio did not log a usable line when an experimental client hit a stable server; option defaults off

## 1.0.5

- Log patterns: ship **dry-run only** again (empty active `log_patterns`); Factorio-specific regexes moved to `log_pattern_candidates` until Ingress Debug mode proves hits
- Shared supervisor: broader generic dry-run guesses; UI keeps dry-run rows visible even when an active pattern exists

## 1.0.4

- Fix package updates: **clean-replace** the headless install tree instead of merging (stale `quality/.../recycling.lua` from 2.0 left after a 2.1 overlay caused crash loops)

## 1.0.3

- **Space Age DLC** option (default **off**): writes `mods/mod-list.json` so base-game servers do not load quality / elevated-rails / space-age / recycler
- Saves created with Space Age on cannot simply turn it off — use NEW WORLD after disabling

## 1.0.2

- **Release channel** option: Stable (default) or Experimental headless builds from factorio.com
- Keep game stdin open so Factorio no longer logs a scary “Got EOF on stdin” Error after a successful host
- Package update-check log lines no longer say “Steam”

## 1.0.1

- Install from Wube’s **free headless package** (factorio.com), not SteamCMD — anonymous Steam returns “No subscription” for app 427520
- Shared supervisor: generic `package_install` HTTP archive path + clearer “View recent game output” link (game process only; install/update stays in the HA Logs tab)
- Docs / HA option text: install needs no login; Factorio.com username + token are only for public listing

## 1.0.0

- Initial Factorio dedicated-server add-on
- Thin game layer over shared `game-server-base` supervisor
- Generic supervisor additions: `config_files` (server-settings.json + config.ini) and `world_prepare` (`--create` when the save is missing)
- Port UDP **34197**; Ingress status UI with black / Factorio-orange theme
- World saves as single `.zip` under `/data/world/saves/<name>.zip` with by-kind file-copy backups
- Player tracking via Factorio `[JOIN]` / `[LEAVE]` console lines (presence mode)
- Blank server name → stable `HAOS Factorio ####` per install
