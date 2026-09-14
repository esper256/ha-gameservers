# Stationeers Dedicated Server

Run a **[Stationeers](https://store.steampowered.com/app/544550/Stationeers/)** dedicated multiplayer server on Home Assistant OS (or Docker).  
SteamCMD keeps the build current, the world is backed up automatically, and **Open Web UI** (Ingress) is the day-to-day control surface.

![Stationeers Open Web UI](images/ingress-ui.png)

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- SteamCMD install and updates (`public` or `beta` branch)
- Player-aware update restarts (join/leave detection once patterns are promoted)
- Generational world backups, plus pre-update and pre-restore safety copies
- **Open Web UI**: status, players, game version / updates, world download, restore, upload, troubleshooting
- HA notifications for crash / update failure / version mismatch
- Optional public server browser listing
- Image based on Debian Trixie for Stationeers’ glibc requirement

**Architecture:** amd64 only (SteamCMD). Not offered on aarch64 HAOS.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Stationeers Dedicated Server**.
3. Open the app → **Documentation** tab for configuration, ports, and Open Web UI notes.
4. Set at least **Save name**, **World / map** (default `Mars2`), and a **Server password**, then **Start**.
5. Forward **UDP 27016** (game) and **UDP 27015** (Steam query) on your router to the Home Assistant host.
6. In Stationeers, join via direct connect to your HA host IP on port 27016.

With the app started, use **Open Web UI** on the Info tab (optional: **Show in sidebar**).

---

## Open Web UI

Ingress status page (no extra host port to publish):

- Server / players / game version / update
- World save download, backups, restore, and upload
- Collapsed **Troubleshooting** (log captures and log pattern prompt)

Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected. Switch world name before restoring a backup from another world.

---

## Docker / Portainer

```bash
docker compose -f stationeers-dedicated-server/docker-compose.yml up -d --build
```

Set `SERVER_PASSWORD` / `WORLD_NAME` / `WORLD_TYPE` in the compose environment. UDP **27016** + **27015** for players; status UI on **localhost:8099** only (no Ingress auth outside HA — do not expose 8099 publicly). Data: `./data` → `/data`.

---

## Data layout

```text
/data/game/          # Steam install (rocketstation_DedicatedServer.x86_64)
/data/world/         # SavePath (saves/<world_name>/, settings.xml)
/data/logs/          # reserved for file logs if you change -logFile
/data/backups/       # world backups
/data/supervisor/    # status.json, steam gate, log captures
```

---

## More

- In-app docs: [DOCS.md](DOCS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Shared supervisor / packaging another game: [game-server-base](../game-server-base/README.md)
- All games in this repo: [repository README](../README.md)
