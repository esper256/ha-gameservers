# Necesse Dedicated Server

## Configure and start

1. On **Configuration**, set at least:
   - **World name** (default `World`)
   - **Server password** (recommended)
2. **Start** the app. First Steam download can take several minutes — watch **Logs**.
3. Forward **UDP 14159** on your router to this Home Assistant host (change the Network port in HA only if 14159 is already taken).
4. In Necesse, join your HA host IP on that port.

## OPEN WEB UI

With the app **started**, use **OPEN WEB UI** on the Info tab (optional: **Show in sidebar**).

That page shows server status, players, game version / updates, world save, backups, restore, and troubleshooting tools. It uses Home Assistant Ingress — no extra host port.

The Info **Ingress** chip that only explains ingress is not the UI.

## Settings that matter

| Setting | Notes |
| --- | --- |
| World name / password / slots / MOTD | What players join |
| Pause when empty | Pause simulation with nobody online |
| Java options | Default `-Xms512M -Xmx2G` |
| Steam branch | `public` (default) or `experimental` — clients must match |
| Update on start | SteamCMD before launch (recommended) |
| Daily Steam check hour | Default **5**; clear to use the interval instead |
| Update only when empty | Wait until Idle before restarting; after 24h apply anyway |
| Backup retention | `minimal` / `standard` / `extended` |
| HA notifications | Crash / update failure / version mismatch |
| Network → UDP port | Host port players use (default 14159) |

## Backups and restore

Use **OPEN WEB UI** → **World backups** to restore a listed backup, start **NEW WORLD**, or upload a save. Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected. Switch world name before restoring a backup from another world.

## Logs

- App **Logs** — supervisor, SteamCMD, and game output
- **OPEN WEB UI** → **Troubleshooting** — captures and status tools
