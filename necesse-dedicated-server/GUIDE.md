# Necesse Dedicated Server

Run a **[Necesse](https://necessegame.com/)** dedicated multiplayer server on Home Assistant OS (or Docker).  
SteamCMD keeps the build current, the world is backed up automatically, and **Open Web UI** (Ingress) is the day-to-day control surface.

![Necesse Open Web UI](images/ingress-ui.png)

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- SteamCMD install and updates (`public` or `experimental` branch)
- Player-aware update restarts (wait until Idle; apply after 24h if still busy)
- Generational world backups, plus pre-update and pre-restore safety copies
- **Open Web UI**: status, players, game version / updates, world download, restore, upload, troubleshooting
- HA notifications for crash / update failure / version mismatch
- Optional quiet hours for when a pending update may restart

**Architecture:** amd64 only (SteamCMD). Not offered on aarch64 HAOS.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Necesse Dedicated Server**.
3. Open the app → **Documentation** tab for configuration, ports, and Open Web UI notes.
4. Set at least **World name** and a **Server password**, then **Start**.
5. Forward **UDP 14159** on your router to the Home Assistant host.
6. In Necesse, join your HA host IP on that port.

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
docker compose -f necesse-dedicated-server/docker-compose.yml up -d --build
```

Set `SERVER_PASSWORD` / `WORLD_NAME` in the compose environment. UDP **14159** for players; status UI on **localhost:8099** only (no Ingress auth outside HA — do not expose 8099 publicly). Data: `./data` → `/data`.

---

## Data layout

```text
/data/game/          # Steam install
/data/world/         # Necesse data (saves under saves/worlds/)
/data/logs/          # configured -logs path (often empty; Necesse may log under world/)
/data/backups/       # world backups
/data/supervisor/    # status.json, steam gate, log captures
```

---

## More

- In-app docs: [DOCS.md](DOCS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Shared supervisor / packaging another game: [game-server-base](../game-server-base/README.md)
- All games in this repo: [repository README](../README.md)
