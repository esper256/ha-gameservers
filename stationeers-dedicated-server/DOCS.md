# Stationeers Dedicated Server

## Configure and start

1. On **Configuration**, set at least:
   - **Save name** (default `World` — no spaces)
   - **World / map** (default `Mars2`; used when the save does not exist yet)
   - **Server password** (recommended)
2. Leave **Server name** blank for a stable generated `HAOS Stationeers ####`.
3. Keep **List on server browser** off unless you want a public listing.
4. **Start** the app. First Steam download can take several minutes — watch **Logs**.
5. Forward **UDP 27016** (game) and **UDP 27015** (Steam query) on your router to this Home Assistant host.
6. In Stationeers, join via direct connect to your HA host IP on port 27016 (or the public list if you enabled listing).

## OPEN WEB UI

With the app **started**, use **OPEN WEB UI** on the Info tab (optional: **Show in sidebar**).

That page shows server status, players, game version / updates, world save, backups, restore, and troubleshooting tools. It uses Home Assistant Ingress — no extra host port.

The Info **Ingress** chip that only explains ingress is not the UI.

## Settings that matter

| Setting | Notes |
| --- | --- |
| Save name / world map / password / slots | What players join (world default `Mars2`) |
| Server name | Optional; blank → `HAOS Stationeers ####` |
| List on server browser | Public listing (default off) |
| Pause when empty | Pause with nobody online |
| Autosave / interval | World persistence (default every 300s) |
| Difficulty / start condition / location | Optional; only for **new** worlds |
| Steam branch | `public` (default) or `beta` — clients must match |
| Update on start | SteamCMD before launch (recommended) |
| Daily Steam check hour | Default **5** |
| Update only when empty | Wait for nobody online before restarting |
| Backup retention | `minimal` / `standard` / `extended` |
| HA notifications | Crash / update failure / version mismatch |
| Network → UDP ports | Host ports (defaults 27016 + 27015) |

Common **world / map** values for new saves: `Mars2` (default), `Lunar`, `Europa3`, `MimasHerschel`, `Vulcan`, `Venus`.

## Backups and restore

Use **OPEN WEB UI** → **World backups** to restore a listed backup, start **NEW WORLD**, or upload a save. Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected.

## Logs

- App **Logs** — supervisor, SteamCMD, and game output
- **OPEN WEB UI** → **Troubleshooting** — captures and status tools
