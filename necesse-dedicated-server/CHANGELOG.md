# Changelog

## 3.14.0

- No player-facing changes for this game.

## 3.13.0

- No player-facing changes for this game.

## 3.12.0

- No player-facing changes for this game.

## 3.11.0

- No player-facing changes for this game.

## 3.10.1

- Default world name and welcome message are generic.

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

- Version numbers follow the shared platform. OPEN WEB UI shows that version.

## 2.1.50

- Ingress web UI fixes.

## 2.1.49

- Ingress web UI fixes.

## 2.1.48

- Backups are per world. Restore refuses a backup from a different world.

## 2.1.47

- Fix options that use 0 or 1.

## 2.1.46

- Ingress web UI fixes.

## 2.1.45

- Ingress web UI fixes.

## 2.1.44

- Ingress web UI: backup count, **Update now**, last-joined players.

## 2.1.43

- Ingress web UI polish.

## 2.1.39

- Ingress web UI: log-pattern watching.

## 2.1.38

- Cleaner game updates (no leftover files from the old version).

## 2.1.37

- **Steam branch** option: Public (default) or Experimental.

## 2.1.36

- Ingress web UI: recent output is the game process only.

## 2.1.35

- More reliable server stop.

## 2.1.34

- Updates wait until the server looks idle (at most 24 hours). More reliable player detection from logs.

## 2.1.33

- Ingress web UI: log-pattern matches.

## 2.1.32

- No player-facing changes.

## 2.1.31

- No player-facing changes.

## 2.1.30

- No player-facing changes.

## 2.1.29

- Documentation updates.

## 2.1.28

- World backups match how the game stores saves (file vs folder).

## 2.1.27

- Restore a world by uploading a save from OPEN WEB UI.

## 2.1.26

- Fix restore and NEW WORLD from OPEN WEB UI.

## 2.1.25

- Players use the mapped UDP port on the Home Assistant machine (default 14159).

## 2.1.24

- Clearer Network port help.

## 2.1.23

- Simpler backup and port settings.

## 2.1.22

- **Debug mode** option. OPEN WEB UI hides extra tools unless it is on.

## 2.1.21

- Detect when a client has the wrong game version and update from Steam.
- Ingress web UI improvements.

## 2.1.20

- Fix OPEN WEB UI crash.

## 2.1.19

- Cleaner shutdown: save the world before the app stops.

## 2.1.18

- Start a new empty world from OPEN WEB UI. Safer restores.

## 2.1.17

- Crash loops no longer look healthy. Other reliability fixes.

## 2.1.16

- Restore a world backup from OPEN WEB UI. Backup-failure notifications.

## 2.1.15

- Daily Steam update check. **Update now** in OPEN WEB UI.

## 2.1.14

- Fix fresh Steam installs.

## 2.1.13

- Detect players and server-ready from logs. Backups card in OPEN WEB UI.

## 2.1.12

- Ingress web UI layout.

## 2.1.11

- Ingress web UI: live refresh, game version, clearer uptime.

## 2.1.10

- Ingress web UI cleanup.

## 2.1.9

- Fix fresh Steam installs.

## 2.1.8

- OPEN WEB UI uses Home Assistant Ingress (no extra 8080 port).

## 2.1.7

- App shows up in the Home Assistant store.

## 2.1.6

- Remove leftover upgrade paths from older installs.

## 2.1.5

- Steam updates back off after failures instead of retrying in a tight loop.

## 2.1.4

- Steam and game logs show in Home Assistant Logs.

## 2.1.3

- Simpler backup retention (minimal / standard / extended).

## 2.1.2

- Safer log watching (no false player detection).

## 2.1.1

- Internal packaging cleanup.

## 2.1.0

- Steam install persists. World backups, crash restart, OPEN WEB UI.

## 2.0.0

- First-party Steam server (replaces the old third-party wrapper). Auto-update, backups, crash restart, OPEN WEB UI. UDP 14159.

## 1.7.0

- Previous generation (third-party SteamCMD image).
