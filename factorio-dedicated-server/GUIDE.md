# Factorio Dedicated Server

Run a **[Factorio](https://factorio.com/)** dedicated multiplayer server on Home Assistant OS (or Docker).  
The app downloads Wube’s **free Linux headless package** (not SteamCMD), backs up the world, and **Open Web UI** (Ingress) is the day-to-day control surface.

![Factorio Open Web UI](images/ingress-ui.png)

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- Free headless install from factorio.com — no Steam ownership or Factorio.com login to install
- Stable or experimental release channel (clients must match)
- Optional Space Age DLC mods toggle (default off = base-game mode)
- Optional public server listing (Factorio.com username + token; LAN/direct IP needs neither)
- Player-aware update restarts (`[JOIN]` / `[LEAVE]`)
- Generational world backups, plus pre-update and pre-restore safety copies
- **Open Web UI**: status, players, game version / updates, world download, restore, upload, troubleshooting
- HA notifications for crash / update failure

**Architecture:** amd64 only. Not offered on aarch64 HAOS.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Factorio Dedicated Server**.
3. Open the app → **Documentation** tab for configuration (including Space Age and public listing), ports, and Open Web UI notes.
4. Set at least **Save name** and a **Game password**, then **Start**.
5. Forward **UDP 34197** on your router to the Home Assistant host.
6. In Factorio → Multiplayer → Connect to address → your HA host IP (port 34197).

With the app started, use **Open Web UI** on the Info tab (optional: **Show in sidebar**).

---

## Open Web UI

Ingress status page (no extra host port to publish):

- Server / players / game version / update
- World save download, backups, restore, and upload
- Collapsed **Troubleshooting** (log captures and log pattern prompt)

Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected. After **NEW WORLD**, the next start creates a fresh map.

---

## Docker / Portainer

```bash
docker compose -f factorio-dedicated-server/docker-compose.yml up -d --build
```

Set `SERVER_PASSWORD` / `WORLD_NAME` in the compose environment. UDP **34197** for players; status UI on **localhost:8099** only (no Ingress auth outside HA — do not expose 8099 publicly). Data: `./data` → `/data`.

---

## Data layout

```text
/data/game/          # Headless install (bin/x64/factorio)
/data/world/         # write-data (saves/, server-settings.json, config.ini, mods/)
/data/logs/          # reserved
/data/backups/       # world backups
/data/supervisor/    # status.json, log captures
```

---

## More

- In-app docs: [DOCS.md](DOCS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Shared supervisor / packaging another game: [game-server-base](../game-server-base/README.md)
- All games in this repo: [repository README](../README.md)
