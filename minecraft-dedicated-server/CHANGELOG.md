# Changelog

## 3.16.1

- Each world stores its own Minecraft version, chosen on the New world form. The Configuration options for Minecraft version, NeoForge version, and Fabric loader version are gone. An add-on update does not change a world's version. A world with no stored version keeps the version it already runs (current install, then the last proven snapshot, then the old Configuration pin).
- The loader is detected from jars in that world's upload folder. An empty folder (ignoring AutoModpack, Fabric API, and the fingerprint file) runs vanilla. Fabric or NeoForge installs the newest loader for that Minecraft version and adds AutoModpack. Mixed loaders, Forge, Quilt-only, and a mod that declares another Minecraft range refuse to start, and the log names the jars. Replacing Fabric mods with NeoForge mods (or the other way) switches the loader on the next boot.
- The world card shows the pinned version and the detected loader (for example `1.21.1 · Vanilla`).
- Every world shares one AutoModpack certificate (`/data/automodpack-identity/`). The fingerprint file in each world shows the same value, and the private key is not in the upload folder. An existing world's certificate is kept when that shared pair is first created. A world restore cannot replace it; the next start points the world back at the shared pair.
- The mod-upload page refuses files that are not `.jar`s. A non-jar that still lands in the folder is moved out. `AUTOMODPACK-FINGERPRINT.txt` stays.
- The version list is the releases that have both a stable Fabric build and a stable NeoForge build (October 2026). Adding a newer release takes an add-on update.

## 3.16.0

- No player-facing changes for this game.

## 3.15.0

- Docker and compose honor `SERVER_MOTD`, `EULA`, and `ONLINE_MODE` as separate settings. A YAML indent had merged those three names into one, so custom values were ignored.
- The Uploads card counts mod files only. `AUTOMODPACK-FINGERPRINT.txt` stays on the upload page and is left out of that count.

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
