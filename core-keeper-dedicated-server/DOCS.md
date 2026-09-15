# Core Keeper Dedicated Server

## Configure and start

1. On **Configuration**, set at least:
   - **World name** (default `World`)
   - Leave **Game ID** and **Join password** blank unless you want to pin specific codes
   - Optional **Admin Steam IDs** (SteamID64 list) if you do not want first-join to decide who is admin
   - Leave **World slot** at `0` unless you already keep several caverns
2. Do **not** look for a “list on server browser” toggle — this app has none. Direct Connect (IP:port + password) **and** Steam Game ID join are both on. Mixed LAN IP + remote Game ID is supported. Nothing is listed publicly.
3. **Start** the app. First Steam download can take several minutes (~650 MB) — watch **Logs**.
4. Copy the **Game ID** and **Join password** from **Logs** (lines `Game ID: …` and `Join password: …`, or the `----- GameInfo.txt -----` block after Unity boots). Treat them like passwords.
5. In Core Keeper → **Multiplayer** → **Join Game** with the Game ID (Steam; no router forward), and/or **Join Game Via IP** (HA host LAN IP, port **7778**, join password). First IP join can fail with connection refused until Logs show `Listening on ip:` (Unity is still converting ECS). GameInfo may print your WAN address (`failed get internal IP`); LAN Direct Connect still uses the Home Assistant host’s LAN IP, not that WAN line.
6. Forward **UDP 7778** on your router to this Home Assistant host only if someone will Join Via IP from outside the LAN (change the Network port in HA only if 7778 is already taken). Game ID joiners need no forward. Cross-play (Epic / Xbox / GOG) uses IP, not Game ID.

## OPEN WEB UI

With the app **started**, use **OPEN WEB UI** on the Info tab (optional: **Show in sidebar**).

That page shows server status, players, game version / updates, world save, backups, restore, and troubleshooting tools. It uses Home Assistant Ingress — no extra host port.

The Info **Ingress** chip that only explains ingress is not the UI.

## Settings that matter

| Setting | Notes |
| --- | --- |
| World name / slots | Name connecting clients see; max players (default 8) |
| World slot | Which save to host (`0` → `0.world.gzip`, … `29`). Switching slot does not delete the other files |
| World mode / seed | Only for a **new** slot (`0` Normal, `1` Hard, `2` Creative, `4` Casual) |
| Game ID | Steam Join Game code; works **at the same time** as IP join. Blank → stable generated ID for this install. Invalid values are ignored |
| Join password | Direct Connect (IP join) password only; Game ID join does not use this. Blank → stable generated password |
| Admin Steam IDs | Optional comma-separated SteamID64 list, merged into `Admins.json` as full admins. Blank → first player to join a new world becomes admin (in-game ESC star). Does not remove in-game admins |
| Steam branch | `public` (default) or `beta` — clients must match |
| Update on start | SteamCMD before launch (recommended) |
| Daily Steam check hour | Default **5** |
| Update only when empty | Wait for nobody online before restarting |
| Backup retention | `minimal` / `standard` / `extended` — applied **per world slot** |
| HA notifications | Crash / update failure / version mismatch |
| Network → UDP port | Host port for Direct Connect (default 7778). Forward only for remote IP join; Game ID join needs no forward |

The world file is often missing until Unity creates the cavern (first player join, or a graceful stop after a new world has started). Backups skip an empty slot until then. Open Web UI backs up the **active** slot only; other slots keep their own backup history.

## Backups and restore

Use **OPEN WEB UI** → **World backups** to restore a listed backup, start **NEW WORLD**, or upload a save. Restoring stops the server, makes a world backup, then restores onto the **active** slot. Anyone online is disconnected. A backup named for another slot is refused until you switch **World slot** to match. **NEW WORLD** clears the active slot only. Upload a `.world.gzip` for this game (single-file save).

## Logs

- App **Logs** — supervisor, SteamCMD, and game output (includes **Game ID:** and **Join password:**)
- **OPEN WEB UI** → **Troubleshooting** — captures and **Log pattern prompt** (`/api/logs/prompt`, works without Debug mode)
