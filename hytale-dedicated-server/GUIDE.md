# Hytale Dedicated Server

Run a **[Hytale](https://hytale.com/)** dedicated multiplayer server on Home Assistant OS (or Docker).  
The app uses Hypixel’s **official Linux downloader** (OAuth device-code, not SteamCMD), backs up the universe folder, and **Open Web UI** (Ingress) is the day-to-day control surface.

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- Official dedicated server install (Java 25). You must own Hytale; hosting does **not** consume your client play session (one license may authenticate many servers).
- First-run **Open Web UI** sign-in cards: download login, then server login (two different Hytale OAuth clients; they cannot be merged). Later starts reuse saved tokens unless the app was off for ~30 days.
- Release or pre-release channel (clients must match exactly)
- Generational universe backups, plus pre-update and pre-restore safety copies
- **Open Web UI**: status, sign-in when needed, world download, restore, upload, troubleshooting
- HA notifications for crash / update failure

**Architecture:** amd64 only. Not offered on aarch64 HAOS. The official downloader is Linux amd64.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Hytale Dedicated Server**.
3. Open the app → **Documentation** tab for configuration, ports, and Open Web UI notes.
4. Set at least **World name** and a **Game password**, then **Start**.
5. Open **Open Web UI**, or stay on the **Logs** tab. Complete both sign-ins (new browser tab). Logs print `Sign-in from HA Logs` plus the URL and device code. Hytale emails a login code first; after you are signed in, open the URL again to reach **Authorize a device**. Paste the device code only on that page. The downloader waits 10 minutes. If the app restarts during that wait, press **Start** again (new code; uninstall is not required).
6. Forward **UDP 5520** on your router to the Home Assistant host (QUIC; do not forward TCP only). That is the official dedicated-server port.
7. In Hytale → Multiplayer → Direct Connect → your HA host IP **and `:5520`**. Omitting the port uses the client hint `:25565` and will not connect.

With the app started, use **Open Web UI** on the Info tab (optional: **Show in sidebar**).

---

## Open Web UI

Ingress status page (no extra host port to publish):

- Server / players / game version / update
- Sign-in card while the downloader or `/auth login device` is waiting (open the URL in a **new tab**; sign in first, then click again for Authorize a device; do not paste the card code into an email-login box)
- Toast on the status page if live refresh fails (app stopped or unresponsive)
- Universe download, backups, restore, and upload
- Collapsed **Troubleshooting** (log captures and log pattern prompt)

Restoring stops the server, makes a world backup, then restores onto the active universe. Anyone online is disconnected.

---

## Docker / Portainer

```bash
docker compose -f hytale-dedicated-server/docker-compose.yml up -d --build
```

Set `SERVER_PASSWORD` / `WORLD_NAME` in the compose environment. UDP **5520** for players (official dedicated-server port; Direct Connect needs `:5520`); status UI on **localhost:8099** only (no Ingress auth outside HA — do not expose 8099 publicly). Data: `./data` → `/data`.

---

## Data layout

```text
/data/game/          # Downloader payload (Assets.zip, Server/HytaleServer.jar)
/data/world/         # cwd: config.json, universe/, auth.enc, mods/
/data/supervisor/    # status.json, downloader credentials, machine-id, operator_action.json
/data/logs/          # reserved
/data/backups/       # universe backups
```

Do not commit worlds, Game IDs, join passwords, WAN IPs, or account tokens.

---

## More

- In-app docs: [DOCS.md](DOCS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Shared supervisor / packaging another game: [game-server-base](../game-server-base/README.md)
- All games in this repo: [repository README](../README.md)
- Official server manual: [Hytale Server Manual](https://support.hytale.com/hc/en-us/articles/45326769420827-Hytale-Server-Manual)
