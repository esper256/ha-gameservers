# Changelog

## 3.14.1

- Minecraft version on Configuration is used even if the add-on was not fully restarted. A missing install for that version is downloaded before boot.
- Player count is read from the Java status ping so the log is not filled with RCON connect/disconnect lines.

## 3.14.0

- OPEN WEB UI shows how many people are playing. Restarts wait until that live count is 0.

## 3.13.0

- A proven boot keeps the snapshot the server actually ran. A bad upload or pin that crashes before that boots the last proven snapshot without changing the upload folder.
- Home Assistant can restart the add-on if even that snapshot will not start.

## 3.12.2

- A proven boot (stock ready, or extra mods after a player joins) is kept as a fallback. A bad upload or pin that crashes before that boots the fallback without changing the upload folder.
- Home Assistant can restart the add-on if even the fallback will not start.

## 3.12.1

- Minecraft version on Configuration applies after the first start. Older loader installs stay on disk.
- If Minecraft crash-loops, the add-on and upload page stay up.

## 3.12.0

- Minecraft version on Configuration applies after the first start. Older loader installs stay on disk.
- A proven boot (stock ready, or extra mods after a player joins) is kept as a fallback. A bad upload or pin that crashes before that boots the fallback without changing the upload folder.
- Home Assistant can restart the add-on if even the fallback will not start.

## 3.11.0

- OPEN WEB UI: Uploads card opens the drop page on the host port from the add-on Network settings.
- Fix a crash while applying uploaded mods.

## 3.10.1

- Clearer names and defaults: mod-upload page (login user `mods`), default world `World`.

## 3.10.0

- OPEN WEB UI: Uploads card shows how many files are in the drop folder and opens the mod-upload page.

## 3.9.0

- Mod JAR uploads. The server waits until the last player leaves before restarting to apply them.
- Dropped mods are not used by a running game until that restart.
- If Minecraft crash-loops, the add-on and upload page stay up.

## 3.8.5

- Reject JARs built for a different Minecraft version than this world.

## 3.8.4

- Fix the upload page retrying the same JAR in a loop.

## 3.8.3

- Simpler upload page (media extras hidden).

## 3.8.2

- Upload page lists jars you can delete. AutoModpack stays protected.

## 3.8.1

- Fix JARs not applying after upload.

## 3.8.0

- First release: Fabric or NeoForge worlds, mod JAR uploads, AutoModpack, and world backups.
