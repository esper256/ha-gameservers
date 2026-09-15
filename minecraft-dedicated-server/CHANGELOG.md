# Changelog

## 3.10.0

- Shared supervisor **3.10**: Ingress **Uploads** hero card when a game opts into Copyparty (`copyparty.port` + `copyparty.root`): file count in the drop directory, link to `http://<home-assistant-host>:<port>/`. Hidden for titles without Copyparty. Vendored `game_server/` sync.

## 3.9.0

- Shared supervisor **3.9**: optional Copyparty file-drop (`copyparty.port` + `copyparty.root`), optional `status_probe` JSON (peer to log regexes; omitted keys do not overwrite), optional `restart_when_empty` (wait unless occupancy is a known 0), optional `hold_on_crash_loop`. `/healthz` is **not** healthy while lifecycle is `failed`. Vendored `game_server/` sync.
- Minecraft: Copyparty roots at `uploaded_mods/` (sealed jars via new inode + replace, never in-place write). Before each JVM start, `mods/` is rebuilt as a reflink (CoW clone when the filesystem supports it), else a hardlink, else a copy; `mods.prev/` is kept for a later last-known-good. Empty-server JAR drops restart after debounce; occupied servers wait for the last player to leave (`status_probe` via RCON `list`, then status ping on the process bind ports). Crash-loop hold, no add-on watchdog.

## 3.8.5

- Quarantine JARs whose Minecraft version range does not include the world’s pin (the 1.21.11 Jade crash). Loader installer trees stay off Copyparty.

## 3.8.4

- Copyparty publishes on idle `xiu` (not `xau`) so deleting the inbox JAR cannot desync up2k and make the browser re-upload in a loop.

## 3.8.3

- Slim Copyparty: hide the top menu (media player, search, unpost), REPL, control panel, sidebar tree, zip/thumbnails/transcode. Login and `/mods` breadcrumbs stay.

## 3.8.2

- Copyparty landing banner explains the inbox; `/mods` lists the active world’s jars for delete (AutoModpack protected, then a game restart).

## 3.8.1

- Enable Copyparty `e2dsa` so the post-upload `xau` hook actually runs (without it, JAR drops never reach `publish_mod.py`).

## 3.8.0

- First release: pinned Minecraft Java dedicated server with per-world Fabric/NeoForge profiles, Copyparty JAR publishing, AutoModpack, and shared supervisor 3.8 (game restart, Ingress world picker, live backup flush).
- NeoForge profiles link `run.sh` / JVM arg files for ServerStarterJar; Fabric launch uses the helper `--results-file` `SERVER=` jar. Copyparty `xau` post-upload hook under volume `flags:`.
