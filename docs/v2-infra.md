# v2 infrastructure (M0)

Vanilla 1.21.1 server and the v1 bot per instance, each in a detached `screen`
session. All commands run from a checkout; set `SV2_HOME` to the checkout that
holds `server-*/`, `run/`, `logs/` and `mc/bot/node_modules` (default: the
script's own repo root). Settings are read from the command line first, then
from `$SV2_HOME/.env` (only the `SV2_*` keys, `RCON_PASSWORD`, `JAVA_BIN`).

**Seed: `SV2_SEED=1001`** (add it to `.env`; the scripts also accept it on the command line).

## Commands

```
SV2_SEED=1001 scripts/v2-up.sh [--no-bot]   # jar, server, gamerules, bot, world border
scripts/v2-down.sh                          # graceful stop, bot, screens, pid files
scripts/world-snapshot.sh <name>            # server-<inst>/snapshots/<name>
scripts/world-restore.sh <name>             # replaces the world, restarts the server (~10 s)
```

Instance b: prefix every command with
`SV2_INSTANCE=b SV2_MC_PORT=25567 SV2_RCON_PORT=25577 SV2_BOT_PORT=3210 SV2_VIEWER_PORT=3217`.

Layout: screens `sv2-<inst>-mc` and `sv2-<inst>-bot`; pids `run/{mc,bot}-<inst>.pid`;
logs `logs/{mc,bot}-<inst>.log`; world `server-<inst>/world-<seed>` (the level
name follows the seed, so each scouted seed gets a fresh folder); jar
`server-cache/server-1.21.1.jar` (downloaded once from Mojang's manifest, sha1 checked).

`v2-up.sh` rewrites `server.properties` (port, RCON, offline, peaceful, spawn
protection 0, survival, seed, view 8, simulation 6), applies the gamerules over
RCON (keepInventory, no daylight or weather cycle, `spawnRadius 0`, no
advancement announcements, day, clear), and, if the world has no border yet,
centres a 512-wide border on the bot's join position (the world spawn, thanks to
`spawnRadius 0`). Without `SV2_SEED` it keeps the level named in the existing
properties file; `world-restore.sh` relies on that.

Safety: refuses demo ports (25565, 25575, 3000, 3007); stops when a port is held
by anything but this instance's pid file; only touches `sv2-<inst>-*` screens
and pids from `run/`.

## Scouting report

`scripts/v2-scout.js` joins as VoyagerBot (stop the instance's bot first, run
`v2-up.sh --no-bot`) and prints logs, exposed stone and exposed coal within 40
blocks. Run it a few seconds after `v2-up.sh` (the first try right after a
server start once failed on a protocol parse error). Per seed: ~18 s.

Candidates 1001, 2002, 3003, 4004, 5005, 6006, 7007, 8008. Many spawned in
ocean or on mountain tops; 1001 fit everything.

Seed 1001: spawn (0, 79, 0), snowy taiga, spruce.
- Logs: 563 spruce logs within 40; nearest (3, 79, -3), 4 blocks.
- Exposed stone: 39; nearest (9, 74, 7), 12 blocks.
- Exposed coal: 22 within 40, two near the surface: (4, 73, 19), 20 blocks,
  and (-21, 69, 31), 39 blocks. The rest is in caves around y 61, e.g. (7, 61, 1).
- Also: copper ore at (12, 71, 2), snow on the ground, iron ore nearby.

Instance a was left running on 1001 with snapshot `fresh` (taken right after
generation, gamerules and border, before the bot acted).

## Timings

`v2-up.sh` on an existing world about 20 s (fresh generation about the same);
`world-restore.sh fresh` 9 to 10 s; snapshot under 1 s (APFS clone).

## Gotchas

- macOS has no `setsid`; screen keeps the processes alive past the tool call.
  The recorded pid is the java or node process itself (`exec` after writing `$$`).
- The `rcon` Python client needs `.venv/bin/python`; system python lacks it.
- The scripts are bash; zsh can't `source` `v2-env.sh`.
- The v1 bot reconnects every 3 s, so it survives `world-restore.sh`; `/health`
  shows `connected: false` for a few seconds.
- Plain `pgrep node server.js` also matches the live demo's bot; use the pid files.
