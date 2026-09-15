# Minecraft Dedicated Server

Run a **[Minecraft](https://www.minecraft.net/)** Java dedicated server on Home Assistant OS.  
Each world is a profile (save + mods + Fabric or NeoForge). Publish JARs on a LAN page; **AutoModpack** keeps Prism clients in sync.

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- Pinned Minecraft version (not auto-upgraded like Steam titles)
- NeoForge and Fabric **installs shared**, **mods per world**
- Ingress world picker (create a Fabric world next to a NeoForge world)
- Mod-upload page (no Home Assistant account)
- Replace-by-mod-id so `cool-creepers-final.jar` overwrites the last build
- AutoModpack server → client sync
- Generational world+mod backups

**Architecture:** amd64 only. Needs outbound HTTPS for the first loader download.

---

## Install in Home Assistant

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install **Minecraft Dedicated Server**.
3. Open **Documentation**. Accept EULA, set the upload page password, **Start**.
4. Forward the Minecraft Java port you mapped in the add-on Network settings. Keep the upload port on the LAN.
5. Create worlds in **Open Web UI**. Default loader is NeoForge.
6. Prism: one instance per loader. First join trusts AutoModpack, then relaunch.

---

## Publishing a mod

Build a normal Fabric or NeoForge JAR (must match the world). Open the upload page, sign in, drop the JAR onto the upload folder. Same mod id replaces the last build. The running server keeps a launch snapshot of `mods/` until it restarts (empty-server restart applies the new set). If the server is empty it restarts soon after; if anyone is playing it waits until they leave. Relaunch Minecraft if AutoModpack asks.

To remove a mod, delete the jar on that same page (leave AutoModpack).

---

## Docker

See `docker-compose.yml` in this folder. Prefer the Home Assistant app.
