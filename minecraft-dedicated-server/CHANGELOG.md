# Changelog

## 3.8.3

- Slim Copyparty: hide the top menu (media player, search, unpost), REPL, control panel, sidebar tree, zip/thumbnails/transcode. Login and `/mods` breadcrumbs stay.

## 3.8.2

- Copyparty landing banner explains the inbox; `/mods` lists the active world’s jars for delete (AutoModpack protected, then a game restart).

## 3.8.1

- Enable Copyparty `e2dsa` so the post-upload `xau` hook actually runs (without it, JAR drops never reach `publish_mod.py`).

## 3.8.0

- First release: pinned Minecraft Java dedicated server with per-world Fabric/NeoForge profiles, Copyparty JAR publishing, AutoModpack, and shared supervisor 3.8 (game restart, Ingress world picker, live backup flush).
- NeoForge profiles link `run.sh` / JVM arg files for ServerStarterJar; Fabric launch uses the helper `--results-file` `SERVER=` jar. Copyparty `xau` post-upload hook under volume `flags:`.
