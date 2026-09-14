# Core Keeper Dedicated Server

Run a **[Core Keeper](https://store.steampowered.com/app/1621690/Core_Keeper/)** dedicated multiplayer server on Home Assistant OS (or Docker).  
SteamCMD keeps the build current, the world is backed up automatically, and **Open Web UI** (Ingress) is the day-to-day control surface.

![Core Keeper Open Web UI](images/ingress-ui.png)

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- SteamCMD install and updates (`public` or `beta` branch)
- **Direct Connect and Game ID together** (UDP **7778**): not XOR. LAN friends Join Game Via IP; Steam friends paste the Game ID (Valve relay, no port-forward). Cross-play (Epic / Xbox / GOG) uses IP. There is no public server-browser listing
- Player-aware update restarts (join/leave detection once patterns are promoted)
- Several world **slots** on one install (`0.world.gzip` …); Open Web UI follows the active slot; backups keep a separate history per slot
- Generational world backups, plus pre-update and pre-restore safety copies
- **Open Web UI**: status, players, game version / updates, world download, restore, upload, troubleshooting
- HA notifications for crash / update failure / version mismatch
- Image includes Xvfb — Pugstorm’s dedicated server needs a virtual display (world gen uses the GPU; `-nographics` is not supported)

**Architecture:** amd64 only (SteamCMD). Not offered on aarch64 HAOS.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Core Keeper Dedicated Server**.
3. Open the app → **Documentation** tab for configuration, ports, and Open Web UI notes.
4. Set at least **World name**. Leave **Game ID**, **Join password**, and **Admin Steam IDs** blank unless you already have values to pin. Leave the default **World slot** `0` unless you already keep several caverns. Nothing is listed on a public browser.
5. **Start**. First Steam download is ~650 MB.
6. Copy the **Game ID** and **Join password** from **Logs**. Do not post them in a public Discord/forum if you want the cavern private.
7. In Core Keeper → **Multiplayer**:
   - **Join Game** with the Game ID (Steam Datagram Relay — no router forward)
   - **Join Game Via IP** with the Home Assistant host LAN IP, port **7778**, and the join password
8. Forward **UDP 7778** on your router to the Home Assistant host only if someone will Join Via IP from outside the LAN (change the Network port in HA only if 7778 is already taken). Game ID joiners do not need that forward.

With the app started, use **Open Web UI** on the Info tab (optional: **Show in sidebar**).

---

## Open Web UI

Ingress status page (no extra host port to publish):

- Server / players / game version / update
- World save download, backups, restore, and upload (the **active** slot)
- Collapsed **Troubleshooting** (log captures and log pattern prompt)

Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected. Switch **World slot** before restoring a backup from another slot.

---

## Joining (Direct Connect **and** Game ID)

Official `ARGUMENTS.txt`: passing `-port` **adds** Direct Connect (the server accepts IP joins and cross-play). Omitting `-port` makes it Steam Datagram Relay only, with no IP join. Game ID (`-gameid`) is independent. This app always passes `-port`, so **both methods are on at once**. Mixed play is the point: household on the LAN via IP, remote Steam friends via Game ID.

| How they join | Who | Port-forward UDP 7778? | Password? |
| --- | --- | --- | --- |
| **Join Game** (Game ID) | Steam | No — Valve relay | No (the Game ID is the secret) |
| **Join Game Via IP** on the LAN | Any platform | No | Yes |
| **Join Game Via IP** from the internet | Any platform (cross-play: Epic / Xbox / GOG) | Yes | Yes |

Direct Connect is the lower-lag path (no Valve relay). Game ID join hides the host IP and needs no router change. Cross-play IP join uses the password; the Game ID remains the secret for relay join.

Unity’s `GameInfo.txt` / `Started session with info:` line often shows the router WAN address after `failed get internal IP`. That is Steam discovering a public IP, not the address LAN clients should type. On the LAN, **Join Game Via IP** still uses the Home Assistant host’s LAN IP and port **7778**. Ingress **Server: running** means the Unity process is up; the game port is not open until Logs show `Listening on ip:`. A first Direct Connect attempt during ECS conversion can be connection refused; a retry after that line works. Stale SteamNet sockets from a previous process can log `InvalidState` / `Misc_Timeout` / `SteamNet Bug` on the next boot — that is leftover UDP cleanup, not a bind failure.

The server is still **invite-only** in the same sense as the other games here: password-protected Direct Connect, Game ID for relay, **no public server-browser listing**. Direct Connect does share the host IP with players you give the address to.

Leave **Game ID** and **Join password** blank for stable per-install values (generated once from a salt on `/data/supervisor`, reused on restart). Game ID is also recovered from `GameID.txt` / `ServerConfig.json` if that salt is missing. Pin values only if you already have codes you want to keep. An invalid pin is ignored rather than passed through (Pugstorm would otherwise mint a **new random** ID or password and friends would bounce).

Wiping the whole add-on data disk is a new install and gets a new Game ID and join password. Restoring a Home Assistant backup of this app restores the salt (and the world), so the codes stay put.

Steam Datagram Relay sends traffic through Valve’s relay. That avoids port-forwarding and keeps the host IP off the wire, and it can add latency. There is no SDR quality slider on the dedicated server. This app does not omit `-port` (that would drop IP join and cross-play).

---

## Who is admin (the server is not a player)

The dedicated server process is not a character. On a **new** world, the first player who joins becomes a full admin (privilege 2 in `Admins.json` under `-datapath` `/data/world`). From in-game, that person opens ESC → player list → star to grant others. Admins can kick/ban and reset the Game ID.

That first-join race is easy to lose to a guest. **Admin Steam IDs** (comma-separated SteamID64 — the 17-digit number in a Steam profile URL) merges those accounts into `Admins.json` as privilege-2 admins **before** Unity starts, and does not wipe anyone starred in-game. Leave it blank to keep first-joiner behavior. Steam only — Epic / Xbox / GOG clients have no SteamID in this file.

Find a SteamID64 from the Steam profile URL or a lookup site; it is not the friend-code shown in Core Keeper.

---

## World slots

The dedicated server keeps many caverns in one datapath: `worlds/0.world.gzip`, `worlds/1.world.gzip`, … (official index **0–29**). **World slot** (`-world`) selects which file this process hosts. Switching slot does **not** delete the previous file; it just hosts a different one. Seed and world mode apply only when that slot’s file does not exist yet.

Open Web UI follows the **active** slot:

| Action | What happens when you bounce slots |
| --- | --- |
| World card / download / upload | Only the active file (`0.world.gzip`, …). Other slots stay on disk under `/data/world/worlds/` |
| **NEW WORLD** | Clears the **active** slot only |
| Scheduled / manual backups | Named with that file (`…-0.world.gzip`). Retention (daily → weekly → monthly) is **per slot**, so slot 3’s history is not thinned when you are hosting slot 0 |
| Pre-update snapshot | Newest **per slot** is kept |
| Restore dropdown | Lists every archive, grouped by world file; the active slot is first. Restoring a backup from another slot is **refused** until you switch **World slot** to match |

Bouncing back and forth is safe: each slot is a separate file, and backup retention no longer shares one global pool that could prune the cavern you are not hosting.

---

## Docker / Portainer

```bash
docker compose -f core-keeper-dedicated-server/docker-compose.yml up -d --build
```

Set `WORLD_NAME` in the compose environment. Leave `GAME_ID` / `SERVER_PASSWORD` unset for stable generated values. Optional `ADMIN_STEAM_IDS` pins full admins. UDP **7778** for Direct Connect (forward only for remote IP join); Game ID join needs no forward. Status UI on **localhost:8099** only (no Ingress auth outside HA — do not expose 8099 publicly). Data: `./data` → `/data`.

First SteamCMD download is ~650 MB. If Logs show `Connecting anonymously to Steam Public... Retrying... FAILED (No Connection)` while HTTPS to Steam’s CDN works, the Docker **bridge** cannot reach Steam connection managers. That is an environment NAT issue, not this app. See [Local Compose](../game-server-base/README.md#local-compose). Do **not** turn on HA `host_network`.

---

## Data layout

```text
/data/game/          # Steam install (CoreKeeperServer, GameInfo.txt, GameID.txt)
/data/world/         # -datapath (ServerConfig.json, Admins.json, worlds/<n>.world.gzip)
/data/logs/          # Unity -logfile (server.log)
/data/backups/       # world backups (names include the slot file)
/data/supervisor/    # status.json, steam gate, log captures, instance salt
```

The `.world.gzip` for slot 0 is often missing until Unity has created the cavern — that can be the first player join, or a graceful stop after the server has started a new world.

---

## More

- In-app docs: [DOCS.md](DOCS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Shared supervisor / packaging another game: [game-server-base](../game-server-base/README.md)
- All games in this repo: [repository README](../README.md)
