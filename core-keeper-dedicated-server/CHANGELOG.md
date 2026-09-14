# Changelog

## 3.8.0

- Shared supervisor **3.8**: Ingress world picker and create-world extra fields; in-process game restart API; optional live-backup stdin flush. Vendored `game_server/` sync.

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

- Version scheme: `{supervisor}.{minor}.{game patch}`. Shared supervisor is **3.0**; this app is **3.0.0**. Future supervisor features bump every game to `3.1.0`; a Core Keeper-only fix is `3.0.1`
- Ingress subtitle advertises supervisor **3.0** separately from the HA app version
- Promote prompt lists other interesting log lines on configured patterns (guess hits the current regex did not capture)
- Shared dry-run guess regexes refreshed from the four live dedicated servers
- Join is **both** Direct Connect (`-port`) and Game ID (Steam Datagram Relay), not XOR. Mixed LAN IP + remote Game ID is the default; port-forward UDP 7778 only for remote IP join; password is IP-only
- Optional **Admin Steam IDs** merges SteamID64 values into `Admins.json` as privilege-2 admins. Blank keeps first-joiner admin (the dedicated server is not a player)

## 1.0.2

- Ready pattern is `Listening on ip:` (UDP port bound). `Started session with info:` is GameInfo / public-IP print and can happen after a client already connected
- Join/leave promoted from a live session: in-world `[userid:…] player Name connected` and `Disconnected from userid:` (same internal userid). Steam id on auth does not match leave. No player_count (the game does not log a headcount)
- `App_Min` / `AppException_Max` / `Misc_Timeout` are disconnect reasons, not version mismatch — `App_Min` also appears on a normal leave
- JSON API expander removed. Troubleshooting has one **Log pattern prompt** link (`/api/logs/prompt`) and the debug textarea share the same plain-text block, including a log-file rescan. Unused list/tail/suggest/patterns JSON endpoints deleted.
- Debug promote prompt: join/leave identity must match; write regexes from sample lines not guess patterns; ready is port bind not a later GameInfo line; disconnect reasons are not version_mismatch; omit player_count without a headcount; edit game.yaml plus tests

## 1.0.1

- Promote Unity NetCode `RpcSystem received bad protocol version` as the active version-mismatch pattern (from a real client-too-old session). Dump headers that repeat that phrase, and disconnect reasons like `App_Min`, are not mismatch signals
- JSON API log-pattern links rescan the on-disk log with the same matchers as Ingress and return example lines for not-yet-configured categories (works without Debug mode)

## 1.0.0

- First Core Keeper dedicated-server app on the shared supervisor
- SteamCMD app `1963720` (anonymous), Unity binary under Xvfb (official Pugstorm requirement)
- Default join path is **Direct Connect** on UDP 7778 (`-port` + password); Steam Game ID join still works; no public server listing
- Stable per-install Game ID and join password when those options are left blank; invalid pins are ignored; Game ID recovered from GameID.txt / ServerConfig.json if needed
- World save is `worlds/<slot>.world.gzip` under `-datapath` `/data/world` (slots 0–29)
- Backups named with the slot file; retention and pre-update keep-one are per slot; restore of another slot’s archive is refused until World slot matches
- Launch wrapper prints official `GameInfo.txt` (and legacy `GameID.txt`) to HA Logs
- Ready / game-version log patterns promoted from a live dedicated-server boot
- Official Steam library capsule + store header used for HA `icon.png` / `logo.png`
