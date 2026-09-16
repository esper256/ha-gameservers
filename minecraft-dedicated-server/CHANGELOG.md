# Changelog

## 3.14.9

- After the server has started once, the mod-upload page has a read-only `AUTOMODPACK-FINGERPRINT.txt`. Paste that value when the Minecraft client warns about mods. It is the public hash of the AutoModpack cert, not a password.

## 3.14.8

- The whitelist toggle is gone. There was no way to add players, and turning it off did not work (JSON `false` was ignored). The server now writes `white-list=false`. Online mode is the access check.
- JSON `false` and `0` in `/data/options.json` are kept for every Configuration key (online mode, EULA, backups, update interval, and the rest). A leftover process env value does not override a key that is already in that file.

## 3.14.7

- A user start or Configuration restart always tries the requested Minecraft version, loader pin, and uploaded mods. The last proven snapshot is used only when the supervisor restarts the game after a crash.

## 3.14.6

- Configuration can pin NeoForge (`latest`, `beta`, or an exact id such as `21.11.10-beta`) and the Fabric loader (`latest` or an exact id). Each pin keeps its own install tree so the last proven snapshot is not overwritten.

## 3.14.5

- A finished Copyparty upload now reaches publish (and the empty-server restart). The idle hook used to drop the file path Copyparty writes on stdin.

## 3.14.4

- The version on Configuration is only `/data/options.json`. Startup downloads that install if it is missing. Copyparty uploads that already match the current `mods/` hardlink snapshot launch as-is; a mismatch or a different install link makes a new untested snapshot. A crash restores the last proven snapshot (hardlinks + install link). That proven snapshot is the only extra mutable copy.

## 3.14.3

- The Minecraft version on Configuration is read from the live Home Assistant Supervisor API, not only `/data/options.json`. Logs now say which source supplied the pin (API, options file, env, or default).

## 3.14.2

- Changing Minecraft version on Configuration starts a new server+mods snapshot even after the first launch. If that try fails, the last proven snapshot still starts; a later start tries the new pin again.

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
