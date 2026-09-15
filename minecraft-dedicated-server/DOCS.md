# Minecraft Dedicated Server

Family Minecraft Java server. Worlds can be **NeoForge** or **Fabric**. Kids upload JARs on a LAN web page; the server restarts and AutoModpack updates Prism clients.

Accept the Minecraft EULA (the **EULA** option, default on). Mojang requires this to run a server.

## Configure and start

1. On **Configuration**, set **World name**, **Minecraft version** (pin, e.g. `1.21.1`), and **Publisher password**.
2. Leave **Update interval** at **0** so Minecraft itself stays pinned. Changing the version option later is an intentional upgrade.
3. **Start**. First boot downloads Fabric and NeoForge for that pin (needs outbound HTTPS).
4. **Open Web UI** → **Worlds** → create extra worlds and pick the loader there (NeoForge default).
5. On **Network**, map the Minecraft Java port to whatever host port you want (default 25565). Keep the upload port (8765) on the LAN only (do not put it on the public internet).
6. On each kid PC: Prism instance with the same Minecraft version, matching loader, and AutoModpack. Join once, trust the server fingerprint, let mods sync, relaunch.

## OPEN WEB UI

World switch/create, backups, restore, status. Home Assistant Ingress — no extra host port. The **Uploads** card shows how many files are in the drop folder and opens the Copyparty page on the mapped LAN port. Switching worlds restarts Minecraft only; the upload page stays up.

## Kid uploads

Open `http://<home-assistant-host>:8765/`, sign in with the publisher password. The page is a file drop, not a media site: the player, search, zip, and other Copyparty extras are turned off.

This site is the **upload** folder (`uploaded_mods/`), not the running server’s `mods/` snapshot. Drop a JAR to add or replace a mod (same mod id replaces the last build even if the filename is different). The jar must match this world’s **Minecraft version** and **loader** (1.21.1 NeoForge here, not 1.21.11). Do not upload NeoForge/Fabric installer jars; those are the world type, not a mod.

Minecraft keeps the last launch snapshot until it restarts (empty-server restart still applies the new set). Delete a jar on the same page to take it off next boot (not AutoModpack / Fabric API). If nobody is connected, the game restarts after a short pause so several jars can land together. If anyone is playing, it waits until the last player leaves, then restarts. Relaunch Minecraft if AutoModpack asks. If a bad jar already crashed the game, delete it here. The upload page stays up even when Minecraft will not start (this add-on has no Home Assistant watchdog).

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
| Network (Minecraft Java) | Host port you mapped for the game (add-on Network settings) |
| Network (upload page) | Default 8765, LAN only |

## Backups

**OPEN WEB UI** backs up the whole world **profile** (save + mods + config). Restore onto the matching world name.

## Logs

App **Logs** for supervisor, installer, and Minecraft output.
