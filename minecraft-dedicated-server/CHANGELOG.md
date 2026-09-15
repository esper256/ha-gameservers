# Changelog

## 3.9.0

- Shared supervisor **3.9**: optional Copyparty file-drop (`copyparty.port` + `copyparty.root` on a live mods directory), optional `player_probe` / `restart_when_empty`, optional `hold_on_crash_loop` (Ingress stays up after a crash loop). `/healthz` is **not** healthy while lifecycle is `failed` — titles without Copyparty keep the HA watchdog. Vendored `game_server/` sync.
- Minecraft: Copyparty is the live `mods/` folder (unique port 8765). Kids drop/delete JARs there. After a crash loop the supervisor stays up so the drop page keeps working (no add-on `watchdog`). Game restart waits until the last player leaves (server-list ping), except world switch/create.

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
