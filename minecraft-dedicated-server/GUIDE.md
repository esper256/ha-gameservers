# Minecraft Dedicated Server

Run a **[Minecraft](https://www.minecraft.net/)** Java dedicated server on Home Assistant OS.  
Each world is a profile (save + mods + Fabric or NeoForge). Kids publish JARs on a LAN page; **AutoModpack** keeps Prism clients in sync.

> Looking at this from inside Home Assistant? Use the app’s **Documentation** tab (`DOCS.md`) for configure/start. This page is the GitHub guide.

---

## What you get

- Pinned Minecraft version (not auto-upgraded like Steam titles)
- NeoForge and Fabric **installs shared**, **mods per world**
- Ingress world picker (create a Fabric world next to a NeoForge world)
- Copyparty upload page for kid JARs (no Home Assistant account)
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
3. Open **Documentation**. Accept EULA, set the publisher password, **Start**.
4. Forward **TCP 25565**. Keep **TCP 8765** on the LAN.
5. Create worlds in **Open Web UI**. Default loader is NeoForge.
6. Prism: one instance per loader. First join trusts AutoModpack, then relaunch.

---

## Publishing a mod

Build a normal Fabric or NeoForge JAR (must match the world). Open the upload page, sign in, drop the JAR onto the live mods folder. Same mod id replaces the last build. If anyone is playing, the server waits until they leave, then restarts. Relaunch Minecraft if AutoModpack asks.

To remove a mod, delete the jar on that same page (leave AutoModpack).

---

## Docker

See `docker-compose.yml` in this folder. Prefer the Home Assistant app.
