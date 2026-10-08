# Minecraft Dedicated Server

Run a **[Minecraft](https://www.minecraft.net/)** Java dedicated server on Home Assistant OS.  
Each world pins its own Minecraft version. Drop mods on a LAN page and the loader is chosen for you: Fabric, NeoForge, or vanilla when the folder is empty. **AutoModpack** keeps Prism clients in sync when a loader is in use.

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- A Minecraft version stored on each world (an add-on update does not change it)
- Vanilla, Fabric, and NeoForge, detected from the jars in that world's upload folder
- Newest Fabric Loader or newest NeoForge build for the world's Minecraft version
- Ingress world picker: create a world and pick the version there
- Mod-upload page (no Home Assistant account)
- Replace-by-mod-id so `cool-creepers-final.jar` overwrites the last build
- AutoModpack server → client sync for Fabric and NeoForge worlds
- Generational world+mod backups

**Architecture:** amd64 only. Needs outbound HTTPS the first time a world's loader (or the vanilla server jar) is downloaded.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Minecraft Dedicated Server**.
3. Open **Documentation**. Accept EULA, set the upload page password, **Start**.
4. Forward the Minecraft Java port you mapped in the add-on Network settings. Keep the upload port on the LAN.
5. Create worlds in **Open Web UI** and pick a Minecraft version. The first automatic world is 1.21.1. To change a world's version later, edit `profile.json` `minecraft_version` in that world's folder.
6. Drop mods in the upload page. An empty folder is vanilla. All-Fabric or all-NeoForge installs that loader and AutoModpack. Mixed jars refuse to start.
7. Prism: one instance with the same Minecraft version and the loader the world detected. Copy `AUTOMODPACK-FINGERPRINT.txt` from the mod-upload page (after the first modded start) and paste it when the client warns about mods, then relaunch. That fingerprint is the same on every world. The upload page accepts `.jar` files only.

The version list on New world is the releases that have both a stable Fabric build and a stable NeoForge build. Adding a newer release takes an add-on update.

---

## Publishing a mod

Build a normal Fabric or NeoForge JAR that supports the world's Minecraft version. Open the upload page, sign in, drop the JAR onto the upload folder. Same mod id replaces the last build. The running server keeps a launch snapshot of `mods/` until it restarts (empty-server restart applies the new set). If the server is empty it restarts soon after; if anyone is playing it waits until they leave. Relaunch Minecraft if AutoModpack asks.

To remove a mod, delete the jar on that same page (leave AutoModpack). Clearing every player mod switches the world to vanilla on the next boot.

---

## Docker

See `docker-compose.yml` in this folder. Prefer the Home Assistant app. `MINECRAFT_VERSION` applies only when a world has no stored version yet.
