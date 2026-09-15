# Minecraft Dedicated Server

Family Minecraft Java server. Worlds can be **NeoForge** or **Fabric**. Kids upload JARs on a LAN web page; the server restarts and AutoModpack updates Prism clients.

Accept the Minecraft EULA (the **EULA** option, default on). Mojang requires this to run a server.

## Configure and start

1. On **Configuration**, set **World name**, **Minecraft version** (pin, e.g. `1.21.1`), and **Publisher password**.
2. Leave **Update interval** at **0** so Minecraft itself stays pinned. Changing the version option later is an intentional upgrade.
3. **Start**. First boot downloads Fabric and NeoForge for that pin (needs outbound HTTPS).
4. **Open Web UI** → **Worlds** → create extra worlds and pick the loader there (NeoForge default).
5. Forward **TCP 25565** for play. Forward **TCP 8765** only on the LAN for uploads (do not put it on the public internet).
6. On each kid PC: Prism instance with the same Minecraft version, matching loader, and AutoModpack. Join once, trust the server fingerprint, let mods sync, relaunch.

## OPEN WEB UI

World switch/create, backups, restore, status. Home Assistant Ingress — no extra host port. Switching worlds restarts Minecraft only; the upload page stays up.

## Kid uploads

Open `http://<home-assistant-host>:8765/`, sign in with the publisher password. The page is a file drop, not a media site: the player, search, zip, and other Copyparty extras are turned off.

The home page is an **inbox**. Drop a JAR to add or replace a mod (same mod id replaces the last build). After Copyparty finishes the upload handshake (~2s idle), the file is copied into `mods/` and removed from this folder — that does not mean the mod is gone. The jar must match this world’s **Minecraft version** and **loader** (1.21.1 NeoForge here, not 1.21.11). Do not upload NeoForge/Fabric installer jars; those are the world type, not a mod.

**Installed mods** (`/mods/`) is the live `mods/` folder for the current world. Delete a jar there to take it off the server (not AutoModpack / Fabric API). The game restarts; relaunch Minecraft if AutoModpack asks. If a bad jar already crashed the game, delete it here. The upload page stays up even when Minecraft will not start (Home Assistant must not stop the app).

Do not upload AutoModpack or Fabric API — those are protected.

## Settings that matter

| Setting | Notes |
| --- | --- |
| World name | Default profile folder under `/data/worlds` |
| Minecraft version | Pin (do not chase latest) |
| Publisher password | Copyparty login |
| EULA | Must stay true |
| Online mode / whitelist | Recommended on |
| Java options | Heap; 4 GB host RAM is a practical floor |
| Network TCP 25565 | Players |
| Network TCP 8765 | Upload page |

## Backups

**OPEN WEB UI** backs up the whole world **profile** (save + mods + config). Restore onto the matching world name.

## Logs

App **Logs** for supervisor, installer, and Minecraft output.
