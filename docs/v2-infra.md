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

`SV2_DIFFICULTY` (`peaceful` default, `easy`, `normal`, `hard`) and `SV2_DAYLIGHT`
(`off` default, `on`) set the server difficulty and the day/night cycle. Both
can also come from `.env`, any other value stops `v2-env.sh` when it is sourced,
and both are applied on every up and restore through `sv2_apply_gamerules`
(`difficulty <value>` over RCON, plus `doDaylightCycle`; `off` also sets day).
S07's survival checks use `SV2_DIFFICULTY=easy SV2_DAYLIGHT=on`.

Safety: refuses demo ports (25565, 25575, 3000, 3007); stops when a port is held
by anything but this instance's pid file; only touches `sv2-<inst>-*` screens
and pids from `run/`.

## The instance lock

One live owner per instance: `run/lock-<inst>` holds `{"pid", "session", "purpose", "started"}`.
`v2-up.sh`, `v2-new-world.sh`, `world-restore.sh`, `v2-change.sh install|enable|disable`,
`v2-seeds.sh`, `v2-demo.sh` and `life --backend mc` take it (`v2-down.sh` and
`v2-change.sh status` don't; `life --backend sim` doesn't). A second taker dies naming
the holder. A lock whose pid is dead is stale: it is taken over with a warning. The lock is
released on normal exit, on errors and on SIGTERM/SIGINT.

Hand-over: a script exports `SV2_LOCK_SESSION`; child scripts with the same session pass
through, and a life with the same session takes the lock over (its pid and purpose `life`)
and hands it back on exit if the previous holder is still alive, else removes it.
Shell: `sv2_lock_acquire <purpose>` / `sv2_lock_release` in `v2-env.sh`; Python:
`schema_voyager.instance_lock.hold(instance, purpose)`. A left-over lock of a live pid:
stop that process, or `scripts/v2-demo.sh stop`.
`screen -S sv2-<inst>-life -X quit` is not a stop: the Python life outlives the screen and keeps the lock
(seen in S12's `runs/v3-m5-live*`). Stop a life with SIGINT to the pid in `run/lock-<inst>` (it saves and
releases the lock), or `scripts/v2-demo.sh stop`, then quit the screen.
The node bot is the same: quitting `sv2-<inst>-bot` leaves it running. Stop it with SIGTERM to the pid in
`run/bot-<inst>.pid`, or with `scripts/v2-down.sh` (bot, then server; it does not touch the life).
`scripts/v2-demo.sh stop` stops the life (SIGINT to its pid file, then SIGTERM, then kill); `stop --all` also
runs `v2-down.sh`. Never kill by name or pattern.

## The demo in one command

```
scripts/v2-demo.sh [start] [--run <name>]   # fresh world, packs, life in screen sv2-<inst>-life, panel
scripts/v2-demo.sh stop [--all] [--panel]   # SIGINT the life (it saves), lock released; --all: v2-down.sh
scripts/v2-demo.sh --rehearse N             # N fresh lives in the foreground, then change_report each
```

`start` refuses the live demo's ports, requires this instance's ports to be free or its own,
takes the lock (purpose `demo`), runs `world-restore.sh fresh`, `v2-up.sh` and
`v2-change.sh install`, then starts the life (`--llm live --change ${SV2_DEMO_CHANGE:-scramble}
--keep-going --minutes ${SV2_DEMO_MINUTES:-30}`, run `demo-<yyyymmdd-hhmm>`, log
`logs/life-<run>.log`, pid `run/life-<inst>.pid`) with the same `SV2_LOCK_SESSION`. It waits up
to 20 s until the lock shows the life's pid, starts the panel in screen `sv2-panel` on 8766
(reused if it already answers), prints the panel and viewer URLs, the log and the stop command,
and the seconds elapsed (target under 60 from a stopped instance). `SV2_SEED` defaults to 1001.
`SV2_DEMO_CHANGE` is a comma list of rule changes (`stick2`, `planks2`, `r2`) or `scramble`
(= `stick2,planks2,r2`): the life switches them on one at a time, mid-life, each only after the
previous one has been revised or 120 s have passed. `v2-change.sh install` copies every
`mc/datapacks/v2_*` pack (including `v2_planks2`), and `v2-change.sh enable|disable
r2|stick2|planks2` switches one by hand. `change_report` prints one block per change fired.
Instance b: set the four port variables and `SV2_INSTANCE=b` on the command line.

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
