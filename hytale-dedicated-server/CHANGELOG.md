# Changelog

## 3.12.0

- No player-facing changes for this game.

## 3.11.0

- No player-facing changes for this game.

## 3.10.1

- Default welcome message is generic.

## 3.10.0

- No player-facing changes for this game.

## 3.9.0

- A crashed server no longer looks healthy to Home Assistant.

## 3.8.0

- Switch and create worlds from OPEN WEB UI.

## 3.7.0

- Safer world restore. More reliable crash recovery while an update is waiting.

## 3.6.0

- Quieter Home Assistant logs.
- Sign-in card follows live Java login, not leftover files. It appears only for a device-verify URL and clears when login succeeds.

## 3.5.0

- Encrypted server login survives rebuilding the container.

## 3.4.0

- Listen on UDP **5520** again. Direct Connect must include `:5520`.
- Sign-in card only when Java says tokens are missing. After sign-in, persistence is Encrypted.
- Detect ready, version, and players from logs.
- Quieter Home Assistant logs.

## 3.3.1

- Listen on UDP **25565** (Hytale Direct Connect default if you omit the port). Forward **UDP 25565**. Reverted in 3.4.0.

## 3.3.0

- Stopping the app during first install no longer looks like a crash.
- If the app restarts during first sign-in, press **Start** again for a new device code. Uninstall is not required.

## 3.2.1

- Fix truncated sign-in codes in OPEN WEB UI.

## 3.2.0

- OPEN WEB UI warns if live status stops updating.
- Sign-in card: keep the full device-code URL; retry download sign-in when the official downloader times out.

## 3.1.2

- Keep the device code on the sign-in URL. The emailed Hytale login code is a different code.

## 3.1.1

- Store art: official H tile; wordmark no longer cropped.

## 3.1.0

- First Hytale dedicated-server app.
- Official Linux downloader and OPEN WEB UI sign-in (download, then in-game device login).
- UDP **5520**; Java 25; universe backups.
