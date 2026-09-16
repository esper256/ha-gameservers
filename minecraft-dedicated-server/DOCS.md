# Minecraft Dedicated Server

Minecraft Java dedicated server. Worlds can be **NeoForge** or **Fabric**. Upload JARs on a LAN web page; the server restarts and AutoModpack updates Prism clients.

Accept the Minecraft EULA (the **EULA** option, default on). Mojang requires this to run a server.

## Configure and start

1. On **Configuration**, set **World name**, **Minecraft version** (pin, e.g. `1.21.1` or `1.21.11`), and **Upload page password**.
2. Leave **Update interval** at **0** so Minecraft itself stays pinned. Changing the version option later is an intentional upgrade (a new loader tree is installed beside the previous pin).
3. **Start**. First boot downloads Fabric and NeoForge for that pin (needs outbound HTTPS).
4. **Open Web UI** → **Worlds** → create extra worlds and pick the loader there (NeoForge default).
5. On **Network**, map the Minecraft Java port to whatever host port you want (default 25565). Map the upload port the same way (container 8765 → whatever host port you set; keep it on the LAN only, not the public internet).
6. On each player PC: Prism instance with the same Minecraft version, matching loader, and AutoModpack. Join once, trust the server fingerprint, let mods sync, relaunch.

## OPEN WEB UI

World switch/create, backups, restore, status. Home Assistant Ingress — no extra host port. The **Uploads** card shows how many files are in the drop folder and opens the mod-upload page on the host port from **Network**. Switching worlds restarts Minecraft only; the upload page stays up.

## Mod uploads

Open `http://<home-assistant-host>:<upload-host-port>/` (the host port on **Network** for container 8765; default 8765), sign in as **mods** with the upload page password. The page is a file drop, not a media site: the player, search, zip, and other Copyparty extras are turned off.

This site is the **upload** folder (`uploaded_mods/`), not the running server’s `mods/` snapshot. Drop a JAR to add or replace a mod (same mod id replaces the last build even if the filename is different). The jar must match this world’s **Minecraft version** (the Configuration pin) and **loader**. Do not upload NeoForge/Fabric installer jars; those are the world type, not a mod.

Minecraft stages a snapshot of the upload folder into `mods/` when trying a new pin or upload set. Delete a jar on the same page to take it off the next *attempt* (not AutoModpack / Fabric API). If nobody is connected, the game restarts after a short pause so several jars can land together. If anyone is playing, it waits until the last player leaves, then restarts. Relaunch Minecraft if AutoModpack asks. If a new pin or upload crashes before it is proven, the last proven snapshot starts again; the upload folder is left as your next experiment. Home Assistant restarts the add-on if even that snapshot will not start.

Do not upload AutoModpack or Fabric API — those are protected.

## Settings that matter

| Setting | Notes |
| --- | --- |
| World name | Default profile folder under `/data/worlds` |
| Minecraft version | Pin (do not chase latest) |
| Upload page password | Login for the mod-upload page (username `mods`) |
| EULA | Must stay true |
| Online mode | Recommended on (Microsoft accounts) |
| Java options | Heap; 4 GB host RAM is a practical floor |
| Network (Minecraft Java) | Host port you mapped for the game (add-on Network settings) |
| Network (upload page) | Container 8765, host port from Network, LAN only |

## Backups

**OPEN WEB UI** backs up the whole world **profile** (save + mods + config). Restore onto the matching world name.

## Logs

App **Logs** for supervisor, installer, and Minecraft output.
