# esper256's Home Assistant OS game servers

A small ecosystem of **dedicated game server** apps for Home Assistant OS, built on one shared supervisor (`game-server-base`).

> **AI experiment:** This repository is a deliberate experiment in AI-assisted coding. The add-ons work and are useful, but the project has been **100% written with AI**.

## Games

| Game | Guide |
| --- | --- |
| **[Necesse](https://necessegame.com/)** | [necesse-dedicated-server/GUIDE.md](necesse-dedicated-server/GUIDE.md) |
| **[Stationeers](https://store.steampowered.com/app/544550/Stationeers/)** | [stationeers-dedicated-server/GUIDE.md](stationeers-dedicated-server/GUIDE.md) |
| **[Factorio](https://factorio.com/)** | [factorio-dedicated-server/GUIDE.md](factorio-dedicated-server/GUIDE.md) |
| **[Core Keeper](https://store.steampowered.com/app/1621690/Core_Keeper/)** | [core-keeper-dedicated-server/GUIDE.md](core-keeper-dedicated-server/GUIDE.md) |
| **[Hytale](https://hytale.com/)** | [hytale-dedicated-server/GUIDE.md](hytale-dedicated-server/GUIDE.md) |

Want a game that is not listed? [Skip to the bottom](#using-agentic-coding-to-add-support-for-another-game) to learn how to add support for a new game.

Current titles are **amd64 only**.

## Quick start (Home Assistant)

1. **Settings → Apps → App store → ⋮ → Repositories** → add:

   ```text
   https://github.com/esper256/ha-gameservers
   ```

2. Install the game you want from that repository.
3. Open that game’s [guide](#games) for ports and first-run notes (and the in-app **Documentation** tab after install).
4. Configure → **Start** → port-forward → join from the game client.

## What each app provides

Each Home Assistant app gives you automatic updates, world backups, Home Assistant notifications, and an HTTP dashboard to monitor the game server and supervisor — open it with **Open Web UI** on the app Info tab once the app is running:

![Open Web UI example (Necesse)](necesse-dedicated-server/images/ingress-ui.png)

---

## Shared supervisor

`game-server-base` is not an installable app. It is the reusable engine every game folder vendors. The base supervisor handles SteamCMD, HTTP archive, or command-based package install and auto-update, process supervision, crash restarts, scheduled world save backups, and a log-pattern scraping toolkit to enable the supervisor to have some vision into the game server state.

Each game add-on is a thin layer on top (Steam/package identity, ports, world paths, log patterns, theme). Packaging another dedicated server is mostly: copy a game folder, fill in `games/game.yaml`, keep game identity out of the base package, sync, bump `{supervisor}.{minor}.{game patch}` (supervisor **3.6** → apps **3.6.0**).

→ [How to package a game](game-server-base/README.md)

## Docker without Home Assistant

Each game folder includes a `docker-compose.yml` that *may* run outside HAOS. That path is **not tested** as a first-class product: you would need to supply a Home Assistant–style options JSON (and related env) yourself, and there is **no separate documentation** for that setup. Prefer the Home Assistant apps above unless you are comfortable reverse-engineering from the compose file and `config.yaml` options.

## Repository layout

| Path | Role |
| --- | --- |
| `*-dedicated-server/GUIDE.md` | GitHub end-user guide (features, install, screenshot) |
| `*-dedicated-server/README.md` | Short HA Info / store blurb |
| `*-dedicated-server/DOCS.md` | HA Documentation tab (configure / ports / Open Web UI) |
| `game-server-base/` | Shared supervisor; sync into game apps with `sync-into-addons.sh` |
| `tests/` | Repo checks for HA app `config.yaml` rules |

---

## Using agentic coding to add support for another game

This repository is set up so a visitor can add another title with an AI coding agent (Cursor or similar) without first becoming a Home Assistant add-on expert.

1. Copy the prompt below.
2. Replace the placeholder in the first sentence with the official game name, a Steam store URL, dedicated-server docs, or all three.
3. Open this repository in the agent and paste it.

The prompt tells the agent to follow the [packaging guide](game-server-base/README.md), keep game-specific details out of the shared supervisor, and stop to confirm before large platform changes.

```text
Add a Home Assistant OS dedicated-server add-on for <<<PASTE: official game name, Steam store URL, and/or dedicated-server docs>>> to this repository.

You are a guest in a small, honed repo. Match the quality and shape of the existing game add-ons. Do not drive-by refactor, restyle, or “improve” unrelated code. Do not invent features the sibling games do not have unless this title cannot ship without them.

First step, before copying a folder or opening a PR: research whether this title is even a good candidate. This supervisor is a Linux/amd64 Home Assistant add-on around SteamCMD or a simple HTTP package, a long-running dedicated-server process, a documented join method (bindable port and/or relay id), and a persistable world path. If official docs show it cannot work in that shape without bending the shared supervisor around this one game (Windows-only server, must use host networking, no dedicated server, player-host only, etc.), stop and warn the human with the blockers. Do not invest in a PR that will never be acceptable.

If it is a fit, read game-server-base/README.md in full, then copy the closest sibling add-on folder (Necesse for SteamCMD + simple flags, Stationeers for Unity settings, Factorio for a non-Steam HTTP package, Core Keeper for Unity + Xvfb / join codes) to a new `*-dedicated-server/` directory. Follow that README’s docs split, store images, Ingress theme, compose/.dockerignore, plugin YAML, log-pattern, and version rules. Use the game’s official name as the store lists it (do not guess pluralization). Prefer official dedicated-server arguments and README over hosting-panel wikis.

Hard rule: game identity stays out of game-server-base. No game names, Steam app ids, ports, runtimes, join codes, admin files, or game option env keys in the shared package. `rg -i <gamename> game-server-base` must find nothing after you are done (a one-line sibling example in game-server-base/README.md is the only exception). Docker/compose env keys come from the plugin (`arg_map` / `settings_map` / templates / `env_options`), not a hardcoded allowlist in config.py. Unusual needs (Xvfb, steamclient.so, a second Steam app id for art vs SteamCMD, stable join codes, Admins.json, a launch wrapper) belong in the game layer: Dockerfile, games/game.yaml, launch_wrapper.sh, haos_defaults.py, config.yaml, translations, GUIDE/DOCS/README.

Change game-server-base only if it is strictly necessary and the change is generic: it must make the next unknown game easier, not encode this title’s nouns, ports, or file names. Prefer existing plugin fields (arg_map, argv_prefix, config_files, world_save named_path, env_options, player_tracking_mode) over new Python. If you need per-world backups, group by the generic world_save label (basename of the named path), not a game-specific slot type. If you believe the supervisor must change, stop and confirm with the human before a large platform edit. After a justified base change: ./game-server-base/sync-into-addons.sh, bump SUPERVISOR_VERSION and every game add-on to {major}.{minor}.0. A game-only add is a patch on this title only (3.0.0 on supervisor 3.0); do not bump siblings.

Do not ship host_network: true. Do not add UDP game ports to “make SteamCMD work” (compose Steam CM failures through nested Docker NAT are an environment issue). HA timeout must be ≤ 300. Copy run.sh’s SERVER_PORT export when HA publishes a container port the process must bind; the Network UI remaps the host port only. New games ship log_patterns: {} and put guesses in log_pattern_candidates; promote precise regexes only from a live boot using Ingress Debug mode or Troubleshooting → Log pattern prompt (/api/logs/prompt). Join and leave must share one identity namespace. ready is bind / accepting connections, not a later GameInfo or public-IP line. Do not activate player_count without a real headcount line.

Store art is official Steam/developer assets (client app id for Steam CDN, not the dedicated-server app id), never AI-generated, never leftover from the copied folder. Sample ui_theme from that same art; accent must differ from every sibling. Short HA README, spartan DOCS.md, GitHub GUIDE.md, translations, a root README games-table row, .dockerignore excluding data/. Do not commit worlds, Game IDs, join passwords, WAN IPs, or user Steam IDs.

Keep existing installs safe: live world paths and unlabeled backups must still restore. Add game-layer tests. After a working instance, contribute any snags and clarifications back to game-server-base/README.md. Open a focused PR for this game (plus only those confirmed generic supervisor edits).
```
