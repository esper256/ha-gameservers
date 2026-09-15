# Changelog

## 3.11.0

- No player-facing changes for this game.

## 3.10.1

- Default world name is generic.

## 3.10.0

- No player-facing changes for this game.

## 3.9.0

- Updates and restarts can wait until nobody is playing. A crashed server no longer looks healthy to Home Assistant.

## 3.8.0

- Switch and create worlds from OPEN WEB UI.

## 3.7.0

- Safer world restore. More reliable crash recovery while an update is waiting.

## 3.6.0

- Quieter Home Assistant logs.

## 3.5.0

- A stable machine identity so logins can survive rebuilding the container.

## 3.4.0

- Quieter Home Assistant logs.

## 3.3.0

- Stopping the app during first install no longer looks like a crash.

## 3.2.0

- OPEN WEB UI warns if live status stops updating.

## 3.1.0

- Platform update. No player-facing changes for this game.

## 3.0.0

- Join is Direct Connect **and** Steam Game ID (LAN IP + remote Game ID by default). Port-forward UDP 7778 only for remote IP join. Password is IP-only.
- Optional **Admin Steam IDs** (SteamID64). Blank keeps first-joiner admin.

## 1.0.2

- More reliable ready / join / leave detection from logs.
- Ingress web UI fixes.

## 1.0.1

- Detect an outdated client from logs and offer an update.

## 1.0.0

- First Core Keeper dedicated-server app.
- Direct Connect on UDP 7778 (password) and Steam Game ID join. No public listing.
- Worlds are slots 0–29. Backups are per slot.
