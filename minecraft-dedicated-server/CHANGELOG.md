# Changelog

## 3.9.0

- Shared supervisor **3.9**: after a crash loop the supervisor stays up (`/healthz` stays healthy while lifecycle is `failed`) so Ingress and sidecars remain usable; start the game again from the UI or a restart request. Vendored `game_server/` sync.
- Copyparty runs in a SIGHUP-proof loop so a Minecraft crash cannot take down the jar inbox; delete the breaking mod from `/mods/` then restart.

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
