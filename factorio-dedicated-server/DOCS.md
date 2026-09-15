# Factorio Dedicated Server

This app downloads Wube’s **free Linux headless package** from factorio.com (not SteamCMD). No Factorio.com login is required to install. Players still need an owned Factorio client to join.

## Configure and start

1. On **Configuration**, set at least:
   - **Save name** (default `World` — no spaces)
   - **Game password** (recommended)
2. Leave **Server name** blank for a stable generated `HAOS Factorio ####`.
3. Leave **Space Age DLC** **off** unless every player owns Space Age (set this before first start).
4. Leave **Public server listing** off and Factorio.com username/token empty unless you want the public browser (see below).
5. **Start** the app. First package download can take several minutes — watch **Logs**.
6. Forward **UDP 34197** on your router to this Home Assistant host.
7. In Factorio → Multiplayer → Connect to address → your HA host IP (port 34197).

## OPEN WEB UI

With the app **started**, use **OPEN WEB UI** on the Info tab (optional: **Show in sidebar**).

That page shows server status, players, game version / updates, world save, backups, restore, and troubleshooting tools. It uses Home Assistant Ingress — no extra host port.

The Info **Ingress** chip that only explains ingress is not the UI.

## Space Age DLC

Default **off** = base-game mode (plain clients can join).

The headless download is always the Space Age–capable binary. This option only toggles the official DLC mods in `mod-list.json`. **On** = every player needs Space Age. Changing this after a world exists usually needs **OPEN WEB UI** → **NEW WORLD**.

## Public server listing

For LAN / direct IP (default): leave **Public server listing** off; leave Factorio.com username and token empty.

To appear in Factorio’s public browser: turn **Public server listing** on, then set Factorio.com **username** and **token** from [factorio.com/profile](https://www.factorio.com/profile) (Reveal token). This is matching-server auth only — not used to download the game. Keep the token secret.

## Settings that matter

| Setting | Notes |
| --- | --- |
| Save name / password / slots | What players join |
| Server name | Optional; blank → `HAOS Factorio ####` |
| LAN visibility | On by default |
| Public server listing | Needs Factorio.com username + token |
| Space Age DLC | Default off (base game) |
| Release channel | `stable` (default) or `experimental` — clients must match |
| Pause when empty | Pause with nobody online |
| Autosave interval | Minutes (default 10) |
| Update on start | Check/download package before launch (recommended) |
| Daily update check hour | Default **5** |
| Update only when empty | Wait for nobody online before restarting |
| Backup retention | `minimal` / `standard` / `extended` |
| HA notifications | Crash / update failure alerts |
| Network → UDP port | Host port players use (default 34197) |

## Backups and restore

Use **OPEN WEB UI** → **World backups** to restore a listed backup, start **NEW WORLD**, or upload a save. Restoring stops the server, makes a world backup, then restores onto the active world. Anyone online is disconnected. After **NEW WORLD**, the next start creates a fresh map.

## Logs

- App **Logs** — supervisor, download/install, and game output
- **OPEN WEB UI** → **Troubleshooting** — captures and status tools
