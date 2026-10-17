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
- `lib/world.js`: shared helpers (inventory counts and settle, block scan,
  pathing with a timeout, drop watching, pickup).

## Behaviour beyond the contract text

- Error bodies may carry a `detail` string. A busy server answers 409
  `{"ok": false, "error": "busy"}`; invalid bodies get 400 `bad_request`.
  An action still running after 120 s is interrupted and answers `timeout`.
- `/mine` reads `block_removed` after the 20-tick drop window, because
  Mineflayer marks the block as air locally before the server confirms.
- `/craft` asks the server for a full inventory resync after crafting, then
  waits for the 5-tick settle.

## Live tests

Both start their own server (`SpikeBot` on 3100, `T1Bot` on 3101, viewer off)
and use RCON through `docker exec schema-voyager-mc rcon-cli`.

```bash
node mc/bot/test/r2_spike.js              # gate G1: R2 loot table drops; leaves R2 enabled
node mc/bot/test/scripted_t1.js --runs 3  # gate G2: logs -> planks -> stick -> table -> wooden pickaxe
```
