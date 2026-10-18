# SchemaVoyager bot server

One Mineflayer bot on the arena server, driven over HTTP by the Python agent.
The API is specified in `contracts/bot_api.md`.

## Start

```bash
cd mc/bot
node server.js            # or: npm start
```

The Minecraft server (`docker compose up -d` in `mc/`) must be running. The bot
reconnects 3 s after a kick or disconnect; actions answer
`{"ok": false, "error": "not_connected"}` meanwhile.

| Env var        | Default     | Notes                          |
| -------------- | ----------- | ------------------------------ |
| `MC_HOST`      | `127.0.0.1` |                                |
| `MC_PORT`      | `25565`     |                                |
| `MC_VERSION`   | `1.21.1`    |                                |
| `BOT_USERNAME` | `SchemaBot` | offline mode, any name works   |
| `BOT_API_PORT` | `3000`      |                                |
| `VIEWER_PORT`  | `3007`      | prismarine-viewer; `0` disables |

Each request is logged to stdout as one line with its body, status, duration
and response.

## Layout

- `server.js`: bot lifecycle (connect, reconnect, viewer, walked distance),
  HTTP routes, the one-action-at-a-time lock, `/observe`, `/reset_episode`, `/chat`.
- `lib/mine.js`, `lib/craft.js`, `lib/explore.js`: the three world actions.
- `lib/smelt.js`, `lib/probe.js`, `lib/targets.js` (target search and
  `/look`): the v2 endpoints. `/status` is in `server.js`.
- `lib/world.js`: shared helpers (inventory counts and settle, block scan,
  pathing with a timeout, drop watching, pickup).
- v2 plumbing: `lib/events.js` (event ring buffer and listeners),
  `lib/travel.js` (travel accounting, digging movements per request),
  `lib/movements.js` (`DiggingMovements`, `GoalBesideBlock`),
  `lib/grid.js` (grid filling and mixed-variant planning),
  `lib/station.js` (crafting table and furnace placement),
  `lib/blockchange.js` (server-confirmed block changes),
  `lib/geometry.js` (directions, sectors, cell tests).

## Behaviour beyond the contract text

- Error bodies may carry a `detail` string. A busy server answers 409
  `{"ok": false, "error": "busy"}`; invalid bodies get 400 `bad_request`.
  An action still running after 120 s is interrupted and answers `timeout`.
- `/mine` reads `block_removed` after the 20-tick drop window, because
  Mineflayer marks the block as air locally before the server confirms.
- `/craft` asks the server for a full inventory resync after crafting, then
  waits for the 5-tick settle.

## v2 notes

- A request without v2 options takes the v1 path: world-preserving
  `Movements`, the 16-block exposed-block search, the 1.5-block drop window.
  `dig_through` requests get their own `DiggingMovements` object for the call
  and the v1 one is put back afterwards.
- Digging is made safe by overriding pathfinder hooks, not by checking
  afterwards: `safeToBreak` refuses any block with lava or water on a side,
  `getMoveDown` drops every move that breaks the block under the bot,
  `exclusionAreasBreak` protects the step's own target, `maxDropDown` is 3,
  `gravityBlocks` includes gravel, and `scafoldingBlocks` is the request's
  `scaffold` list (empty = no pillaring or bridging). `GoalBesideBlock` stands
  beside a target, never on it.
- Every action response carries `events` (this call's slice of the 200-event
  ring buffer, each with a `phase`). A death during an action ends it with
  `quarantined`. `/status` reads the same buffer and takes no action lock.
- `/smelt` has its own action timeout: 30 s + 12 s per item.

## Tests

```bash
node --test mc/bot/test/unit/    # from the repo root; pure helpers, no ports, no server
```

`test/unit/index.js` exists because this Node resolves a directory argument as
a module; it requires the `*.test.js` files next to it.

## Live tests

Each starts its own server (`SpikeBot` on 3100, `T1Bot` on 3101, `FixBot` on
3102, viewer off) and uses RCON through `docker exec schema-voyager-mc rcon-cli`.
Run them only while no episode is running.

```bash
node mc/bot/test/r2_spike.js              # gate G1: R2 loot table drops; leaves R2 enabled
node mc/bot/test/scripted_t1.js --runs 3  # gate G2: logs -> planks -> stick -> table -> wooden pickaxe
node mc/bot/test/layout8_fixes.js         # refused table cells, the /mine skip list, the chat spam guard
```

`test/v2_iron.js` is the v2 live test. It starts nothing: it drives the bot that
already runs at `SV2_BOT_PORT` and uses RCON at `SV2_RCON_PORT` with
`RCON_PASSWORD` (both loaded from `$SV2_HOME/.env` when not set), and refuses
the demo's ports. It needs no docker.

```bash
node mc/bot/test/v2_iron.js   # logs -> wooden pickaxe -> stone pickaxe (the gate) -> iron pickaxe
```
