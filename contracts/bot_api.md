# Bot HTTP API (contract)

The Node bot server (`mc/bot/server.js`) owns one Mineflayer bot and exposes it
over HTTP so the Python agent can drive it. The Python adapter
(`src/schema_voyager/mineflayer_backend.py`) is the only client.

Arena building, inventory resets, teleports and datapack switches happen over
RCON from Python, not through this API.

## Process

- Start: `node mc/bot/server.js`
- Env (defaults): `MC_HOST=127.0.0.1`, `MC_PORT=25565`, `MC_VERSION=1.21.1`,
  `BOT_USERNAME=SchemaBot`, `BOT_API_PORT=3000`, `VIEWER_PORT=3007`
  (`VIEWER_PORT=0` disables the viewer).
- prismarine-viewer runs third person on `VIEWER_PORT`.
- One action at a time. A request while another action runs gets HTTP 409.
- All bodies are JSON. Item and block names never carry the `minecraft:` prefix.
- Every action response is HTTP 200 with `ok: false` and an `error` code on
  in-game failure. HTTP 4xx/5xx are reserved for bad requests and crashes.

## Shared shapes

```jsonc
// Inventory: item name -> total count across all slots (zero counts omitted)
{"oak_log": 3, "wooden_pickaxe": 1}

// Position
{"x": 1.5, "y": -60, "z": -3.5}
```

## GET /health

```json
{"ok": true, "connected": true, "username": "SchemaBot", "version": "1.21.1"}
```

## POST /observe  `{}`

```jsonc
{
  "ok": true,
  "position": {"x": 0.5, "y": -60, "z": 0.5},
  "inventory": {"oak_log": 2},
  // Block types within 16 blocks, excluding air, cave_air, grass_block, dirt,
  // bedrock, barrier, short_grass, tall_grass. `nearest` is the closest one
  // that has at least one air neighbour (diggable from outside); null if none.
  "nearby_blocks": {
    "oak_log": {"count": 8, "nearest": {"x": 6, "y": -60, "z": 2}, "distance": 6.1}
  },
  "blocks_walked": 12.4   // horizontal distance walked since last /reset_episode
}
```

## POST /mine  `{"block": "coal_ore", "tool": "wooden_pickaxe" | null}`

1. Find the nearest `block` within 16 blocks that has an air neighbour,
   skipping positions that already failed this episode (below).
   None: `ok: false, error: "not_found"`.
2. If `tool` is set and not in the inventory: `ok: false, error: "missing_tool"`.
   Equip `tool`, or empty the hand if `tool` is null.
3. Path to within reach (30 s timeout): `error: "unreachable"` on failure.
4. Dig. Start watching for item entities **before** the dig starts.
5. `block_removed`: the block at the target position is no longer `block`,
   read after the dig resolves.
6. `drops`: item entities (not experience orbs) that spawned within 1.5 blocks
   of the block centre between dig start and 20 ticks after the dig resolves,
   counted from the entity's item metadata. This is what the server dropped,
   independent of whether the bot picks it up.
7. Pickup (tolerant): walk to each dropped entity that still exists, wait up to
   1 s for collection, retry twice. `picked_up` is the inventory gain over
   the whole call.

A target that fails with `unreachable` (step 3) or `dig_failed` (step 4, or
the block still there after the dig) is added to the episode's skip list, so
the next `/mine` picks the next nearest block instead of the same one.
`/reset_episode` clears the list.

```jsonc
{
  "ok": true,
  "error": null,
  "block": "coal_ore",
  "target": {"x": 9, "y": -60, "z": 4},
  "tool": "wooden_pickaxe",
  "block_removed": true,
  "drops": {},                 // exact, server-decided
  "picked_up": {},             // tolerant
  "inventory": {"wooden_pickaxe": 1, "stick": 2},
  "blocks_walked": 31.0
}
```

## POST /craft  `{"item": "stick", "times": 1}`

`times` is how many times the recipe runs (Mineflayer `bot.craft` count).

1. Pick a recipe with `bot.recipesFor(item, null, 1, tableOrNull)` that the
   current inventory can afford for `times` runs. None:
   `ok: false, error: "missing_ingredients"`, plus `"missing"` listing what the
   cheapest recipe from `bot.recipesAll` lacks.
2. If the recipe needs a crafting table: use a placed `crafting_table` within
   4 blocks, else place one from the inventory next to the bot, else
   `ok: false, error: "missing_crafting_table"`. Placement tries the free
   cells at the bot's feet level, closest first; when the server refuses one,
   it waits 5 ticks and tries the next, up to 6 cells, then answers
   `craft_failed` with a `detail`. A table the bot placed is dug back up and
   picked up after crafting (`table_retrieved`).
3. Craft, close the window, then wait until the player inventory has had no
   slot updates for 5 ticks (cap 40 ticks). Mineflayer writes its predicted
   result into the output slot before the server confirms, so the inventory is
   read only after this settle.
4. `inventory_delta` = inventory after settle minus inventory before the call.
   A placed-and-retrieved table nets to zero.

```jsonc
{
  "ok": true,
  "error": null,
  "item": "stick",
  "times": 1,
  "used_table": false,
  "table_retrieved": null,     // true/false when the bot placed a table
  "inventory_delta": {"oak_planks": -2, "stick": 4},
  "inventory": {"oak_planks": 2, "stick": 4},
  "blocks_walked": 31.0
}
```

## POST /explore  `{}`

Walk one leg (10 blocks) of an outward square spiral around the episode's
start position. Stops early at barriers or after 30 s.

```json
{"ok": true, "error": null, "position": {"x": 10.5, "y": -60, "z": 0.5}, "blocks_walked": 41.0}
```

## POST /chat  `{"message": "..."}`

`bot.chat(message)`, truncated to 250 characters. Used to narrate the plan and
mismatches in game. `{"ok": true}`.

## POST /reset_episode  `{}`

Call after Python has rebuilt the arena and teleported the bot. Stops the
pathfinder, clears the spiral state and the `/mine` skip list, sets the spiral
origin to the current position, zeroes `blocks_walked`, waits 10 ticks for the
inventory to settle, and returns the same body as `/observe`.

## Error codes

`not_found`, `missing_tool`, `unreachable`, `dig_failed`,
`missing_ingredients`, `missing_crafting_table`, `craft_failed`, `timeout`,
`not_connected`.
