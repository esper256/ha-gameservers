# Minecraft Dedicated Server

Minecraft Java dedicated server. Each world keeps its own Minecraft version. The loader is chosen from the mods uploaded to that world: Fabric, NeoForge, or vanilla when the upload folder is empty. Upload JARs on a LAN web page; when a loader is in use the server restarts and AutoModpack updates Prism clients.

Accept the Minecraft EULA (the **EULA** option, default on). Mojang requires this to run a server.

## Configure and start

1. On **Configuration**, set **World name** and **Upload page password**. Leave **Update interval** at **0**. An add-on update does not change a world's Minecraft version.
2. **Start**. The first world is created as Minecraft **1.21.1** (there is no New world form for that automatic first profile). Worlds that already exist, and have no version stored yet, keep the version they run today (the install they are linked to, then the last proven snapshot, then the old Configuration pin).
3. **Open Web UI** → **Worlds** → **New world**, and pick a Minecraft version. That choice is stored on the world. It stays until someone edits that world's `profile.json` key `minecraft_version` on purpose. The version list is the releases that have both a stable Fabric build and a stable NeoForge build.
4. On **Network**, map the Minecraft Java port to whatever host port you want (default 25565). Map the upload port the same way (container 8765 → whatever host port you set; keep it on the LAN only, not the public internet).
5. Drop mods on the upload page for that world:
   - Empty folder (AutoModpack, Fabric API, and the fingerprint file do not count): plain vanilla, no loader.
   - Every detected mod is Fabric: the newest Fabric Loader for this world's Minecraft version, plus AutoModpack.
   - Every detected mod is NeoForge: the newest NeoForge build for this world's Minecraft version, plus AutoModpack.
   - Fabric and NeoForge jars together, a Forge jar, a Quilt-only jar, or a mod whose metadata says it does not support this world's version: the server refuses to start. The log names the jars.
   - Replacing Fabric mods with NeoForge mods (or the other way) switches the loader on the next boot. The Minecraft version does not change.
6. On each player PC: a Prism instance with the **same Minecraft version** as the world, the matching loader (or vanilla), and AutoModpack when the folder is not empty. Open the mod-upload page and copy `AUTOMODPACK-FINGERPRINT.txt` (it appears after the first successful modded start). Join once, paste that fingerprint when the client warns about mods, let mods sync, relaunch. Same value for every player.

## OPEN WEB UI

World switch/create, backups, restore, status. Home Assistant Ingress — no extra host port. Each world card shows the pinned version and the detected loader (for example `1.21.1 · Vanilla` or `1.21.1 · Fabric`). The **Uploads** card shows how many mod files are in the drop folder and opens the mod-upload page on the host port from **Network**. Switching worlds restarts Minecraft only; the upload page stays up.

## Mod uploads

Open `http://<home-assistant-host>:<upload-host-port>/` (the host port on **Network** for container 8765; default 8765), sign in as **mods** with the upload page password. The page is a file drop, not a media site: the player, search, zip, and other Copyparty extras are turned off.

This site is the **upload** folder (`uploaded_mods/`), not the running server’s `mods/` snapshot. Drop a JAR to add or replace a mod (same mod id replaces the last build even if the filename is different). The jar must support this world’s Minecraft version. You do not pick a loader; the jars decide it. Do not upload NeoForge/Fabric installer jars, Forge mods, or Quilt-only mods.

Minecraft stages a snapshot of the upload folder into `mods/` when the loader or the upload set changes. Delete a jar on the same page to take it off the next *attempt* (not AutoModpack / Fabric API). If nobody is connected, the game restarts after a short pause so several jars can land together. If anyone is playing, it waits until the last player leaves, then restarts. Relaunch Minecraft if AutoModpack asks. If a valid launch crashes before it is proven, the last proven snapshot starts again, and only when that snapshot is the same Minecraft version as the world. A snapshot from a different version is never used. The upload folder is left as your next experiment. Home Assistant restarts the add-on if even that snapshot will not start.

Do not upload AutoModpack or Fabric API — those are protected.

## Settings that matter

| Setting | Notes |
| --- | --- |
| World name | Default profile folder under `/data/worlds`. The first world is Minecraft 1.21.1 |
| Upload page password | Login for the mod-upload page (username `mods`) |
| EULA | Must stay true |
| Online mode | Recommended on (Microsoft accounts) |
| Java options | Heap; 4 GB host RAM is a practical floor |
| Network (Minecraft Java) | Host port you mapped for the game (add-on Network settings) |
| Network (upload page) | Container 8765, host port from Network, LAN only |

## Backups

**OPEN WEB UI** backs up the whole world **profile** (save + mods + config). Restore onto the matching world name.

## Logs

App **Logs** for supervisor, installer, and Minecraft output. A refused start (mixed loaders, or a mod built for another Minecraft version) is printed there the same way as any other boot failure.
