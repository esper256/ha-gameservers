# Changelog

## 3.9.0

- Shared supervisor **3.9**: after a crash loop the supervisor stays up (`/healthz` stays healthy while lifecycle is `failed`) so Ingress remains usable; start the game again from the UI or a restart request. Vendored `game_server/` sync.

## 3.8.0

- Shared supervisor **3.8**: Ingress world picker and create-world extra fields; in-process game restart API; optional live-backup stdin flush. World switch/create reserves the restart before changing the live world; `.`/`..` names are rejected; `/healthz` stays healthy during a planned game restart. Vendored `game_server/` sync.

## 3.7.0

- Shared supervisor **3.7**: folder restores extract into staging then swap (failed zips no longer wipe the live world); internal folder backups keep a nested sole directory; pre-update aborts when backup cannot snapshot existing world data; crash recovery still runs while an update is queued, unexpected exit 0 counts toward the restart budget, and `wait()` no longer busy-loops on a dead process. Vendored `game_server/` sync.


## 3.6.0

- Shared supervisor **3.6**: Ingress 5-second status poll no longer logs `Game version from logs` on throwaway rescans; `[game-log]` no longer replays stdout already shown as `[game]` during large boot floods. Vendored `game_server/` sync.
- Drive the Ingress sign-in card from live Java auth lines, not `auth.enc`. Boot's `No server tokens configured` is ignored (it always prints before Encrypted restore). Show the card only for a device-verify URL. Clear it on `Authentication successful` / `Session restored`. Do not treat `Loaded encrypted credentials` or `https://sessions.hytale.com` as sign-in state.

## 3.5.0

- Shared supervisor **3.5**: persist a Linux machine-id under `/data/supervisor` and copy it to `/etc/machine-id` when the overlay allows (no host bind, no `/sys` DMI, no extra privileges; images do not ship a baked id). Vendored `game_server/` sync.
- Hytale Encrypted `auth.enc` uses that machine-id so server login survives container recreate (the "Failed to get hardware UUID" warning).

## 3.4.0

- Shared supervisor **3.4**: empty player / version-mismatch pattern notes log only when the live tailer starts, so the Ingress 5-second status poll no longer repeats them. Vendored `game_server/` sync.
- Do not show the activate-server card or inject `/auth login device` unless Java says tokens are missing. After a successful Java sign-in, send `/auth persistence Encrypted`.
- Listen on official UDP **5520** again (revert 3.3.1's 25565 bind). Direct Connect must include `:5520`.
- Active log patterns from a live 0.6.1 boot: ready (ServerManager Listening), game version, in-world join/leave.

## 3.3.1

- Listen on UDP **25565**, the Hytale client Direct Connect default when you omit the port (the join box hint is `:25565`). The dedicated-server binary still defaults to 5520 if you omit `--bind`; we pass `--bind 0.0.0.0:25565`. After updating, forward **UDP 25565** and join with the host IP alone (or `:25565`). A previous 5520 forward will not match.

## 3.3.0

- Shared supervisor **3.3**: a Home Assistant stop (SIGTERM) during first install exits cleanly instead of crashing with `package install failed`; install commands notice stop without waiting for the next log line; `/healthz` stays reachable for watchdog and Docker healthchecks when Ingress peer checks apply. Vendored `game_server/` sync.
- If the app restarts during the first Hytale sign-in, press **Start** again — a new device code is issued. Uninstall is not required.

## 3.2.1

- Strip ANSI color resets from Java `/auth` device-code lines so `user_code` is not `KuFxp9fw` plus ESC[m

## 3.2.0

- Shared supervisor **3.2**: Ingress toast when live status refresh fails (app stopped or unresponsive)
- Keep the downloader's complete `?user_code=` sign-in URL (the fallback URL without the code no longer overwrites it)
- Sign-in card: device code vs emailed login code; finish Hytale login first, then open the link to reach Authorize a device; 10-minute downloader wait
- Retry download sign-in with a fresh device code when the official downloader hits `error obtaining token: context deadline exceeded` (keeps waiting until sign-in succeeds or you stop the app)

## 3.1.2

- Keep the downloader's complete `?user_code=` sign-in URL (the fallback URL without the code no longer overwrites it)
- Sign-in card copy: the code on the card is the device login; an emailed Hytale login code is a different code

## 3.1.1

- Store art: square tile is the official Hytale H; Info header wordmark is letterboxed so letters are not cropped

## 3.1.0

- First Hytale dedicated-server app on supervisor **3.1**
- Official Linux downloader (`package_install.kind: command`) plus Ingress sign-in card for the two device-code logins (download, then `/auth login device`)
- UDP **5520** QUIC; Java 25; universe folder backups; empty active log patterns until a live boot
