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

Vanilla kicks a non-op player whose chat spam score reaches 200 (+20 per
message, -1 per tick). A message that would take the bot's score past 160 is
not sent: `{"ok": true, "dropped": true}`, and the bot logs it. That allows a
burst of 8 messages, then about one a second.

## POST /reset_episode  `{}`

Call after Python has rebuilt the arena and teleported the bot. Stops the
pathfinder, clears the spiral state and the `/mine` skip list, sets the spiral
origin to the current position, zeroes `blocks_walked`, waits 10 ticks for the
inventory to settle, and returns the same body as `/observe`.

## Error codes

`not_found`, `missing_tool`, `unreachable`, `dig_failed`,
`missing_ingredients`, `missing_crafting_table`, `craft_failed`, `timeout`,
`not_connected`.

---

# v2: natural terrain (SchemaVoyager v2)

Everything above stays valid: a request without the v2 options below behaves
exactly as in v1, and v1 clients ignore the added response fields. v2 adds
options and fields to `/mine`, `/craft` and `/explore`, and four new endpoints.
The Python client is `MineflayerBackend(natural=True)`; the module layout and
the Python side are in `docs/v2-interfaces.md`.

Ids are always raw Minecraft ids (`birch_log`, `deepslate_coal_ore`). The bot
knows nothing about groups (`#log`) or variant families: Python expands them
into lists of raw ids before calling, and normalizes what comes back. No bot
code names a wood species, a coordinate, a seed or a biome.

## Process (v2)

The bot is configured by the same variables as v1; the v2 scripts set them from
the instance settings: `MC_PORT=$SV2_MC_PORT`, `BOT_API_PORT=$SV2_BOT_PORT`,
`VIEWER_PORT=$SV2_VIEWER_PORT`, `BOT_USERNAME=VoyagerBot`. Instance `a`:
25566 / 3200 / 3207; instance `b`: 25567 / 3210 / 3217.

## Fields added to every action response

```jsonc
{
  // ...the v1 fields...
  "events": [                       // bot events during this call, oldest first (see /status)
    {"seq": 41, "t": 1760731234.512, "type": "damage", "phase": "travel", "amount": 2.0, "health": 18.0}
  ],
  "travel": {                       // getting into position, before the step's own dig / craft / smelt
    "distance": 23.4,               // horizontal blocks walked
    "dug": {"dirt": 3, "stone": 1}, // blocks the pathfinder broke on the way (raw ids)
    "placed": {"dirt": 1},          // blocks placed on the way (pillaring, bridging)
    "picked_up": {"dirt": 3, "cobblestone": 1}  // inventory gains before arrival
  },
  "inventory_before_step": {"oak_log": 2}       // snapshot on arrival, right before the step itself
}
```

`travel` is always present on `/mine`, `/explore`, `/craft` and `/smelt` (all
zero / empty when the bot didn't move). Everything in `travel` is tolerant: it
is never evidence for or against a rule. Every exact field of a step (`drops`,
`block_removed`, `inventory_delta`) is measured from `inventory_before_step`
and the step's own window, so travel can't pollute it.

Every event in an action response has `"phase": "travel"` (getting into
position) or `"phase": "step"` (from `inventory_before_step` to the end of the
call).

### Event types

| type | when | extra fields |
|---|---|---|
| `damage` | health went down | `amount`, `health` |
| `death` | the bot died | `position` |
| `respawn` | the bot respawned after a death | `position` |
| `target_despawned` | the step's target block changed before the dig, or a drop entity vanished before its stack could be read | `detail` |
| `interference` | during a dig's drop window, another block within 2 blocks of the target changed (leaf decay, falling gravel, flowing liquid) or a `falling_block` entity appeared there | `block`, `position` |
| `stray_pickup` | the bot collected an item entity that didn't come from this step (not its own drop, not its own crafting table or furnace) | `items` |
| `hazard` | the bot refused a target or a path because of lava or water | `kind` (`lava` or `water`), `position` |

Python decides what an event means for the check (`verifier.QUARANTINE_EVENTS`):
the bot only reports.

## POST /mine (v2 options)

```jsonc
{
  "block": "coal_ore",                              // v1: one raw id
  "blocks": ["coal_ore", "deepslate_coal_ore"],     // v2: any of these (wins over "block")
  "tool": "wooden_pickaxe",                         // or null
  "dig_through": true,                              // v2: path may dig and pillar; target may be buried
  "max_distance": 32,                               // v2: search radius, default 16
  "scaffold": ["dirt", "cobbled_deepslate"]         // v2: the only blocks pathing may place; absent = none
}
```

- Target choice: the nearest block whose id is in `blocks`, within
  `max_distance`, not on the episode's skip list and not next to lava or flowing
  water (those are skipped with a `hazard` event). Without `dig_through` the
  target must have an air neighbour (v1). With `dig_through` an exposed target is
  preferred when one is within `max_distance`; otherwise the nearest buried one.
- Pathing with `dig_through`: may dig, and may pillar (1x1 towers) or bridge
  with blocks listed in `scaffold` that the bot holds (none when `scaffold` is
  absent, so pathing never spends an item the plan needs). It never digs the block straight below the bot,
  never breaks a block next to lava or water, never walks into lava, avoids
  drops of more than 3 blocks, and won't dig under a gravity block (gravel, sand). Without
  `dig_through`, pathing is world-preserving as in v1.
- On arrival: wait until 5 ticks have passed since the last travel dig, then
  equip `tool` (or empty the hand) again, because the pathfinder switches to the
  best tool for each dig on the way. `tool_used` is what is held when the dig
  starts (null for an empty hand); Python fails a step whose `tool_used`
  differs from the requested `tool`. Then take `inventory_before_step`.
- The step's own window starts when the dig starts. `drops`: as in v1, but only
  item entities whose spawn position lies inside the target's own cell (its
  1x1x1 box plus 0.25 on each side), so drops of neighbouring digs, leaf decay
  or gravel can't be counted. `block_removed` needs the server's block update
  for the target cell; without one within 2 s, the step is `dig_failed`.
  `picked_up`: tolerant gains since arrival.
- Response adds `"block": "<raw id actually mined>"`, `tool_used`, `travel`,
  `events`, `inventory_before_step`.
- Errors: v1 codes, plus `hazard` (every candidate or path is next to lava or water)
  and `quarantined` (the bot died, or took damage and stopped).

## POST /craft (v2 fields)

Request unchanged. `item` is always a raw id (Python resolves `#planks` to a
species first). Response adds `travel` (normally empty; set if the bot moved to
place a table), `events` (including `stray_pickup`), and
`inventory_before_step`. `inventory_delta` is measured from
`inventory_before_step`. When no single-variant recipe is affordable but the
inventory can pay a tag ingredient by mixing variants (2 oak + 1 birch planks
for a pickaxe, 2 cobblestone + 1 cobbled_deepslate for a stone pickaxe), the bot
fills the grid itself (the `/probe` code path), takes the output and reports
the real delta. Only if that fails: `missing_ingredients` with
`"detail": "mixed_species"`.

## POST /explore (v2 options)

```jsonc
{"direction": "NE", "distance": 32, "dig_through": true, "scaffold": ["dirt"]}
```

Without `direction`, it's the v1 spiral. With it, the bot walks up to
`distance` blocks (default 32) along one of `N` (-z), `NE`, `E` (+x), `SE`,
`S` (+z), `SW`, `W` (-x), `NW`, stopping at the world border, at
barriers, before lava, or after 30 s. Response: v1 fields plus `travel`,
`events`. `unreachable` when it moved less than 1 block.

## POST /look  `{"blocks": ["coal_ore", "deepslate_coal_ore"], "radius": 64}`

For each of 8 directions, the nearest block of each requested id within
`radius` (default 64, max 96) of the bot, by 45-degree sector around the
bot's position (`N` is centred on -z). It doesn't move the bot.

```jsonc
{
  "ok": true,
  "position": {"x": 10.5, "y": 71, "z": -3.5},
  "radius": 64,
  "directions": {
    "N":  {"coal_ore": {"x": 12, "y": 64, "z": -40, "distance": 37.6, "exposed": true}},
    "NE": {},
    "E":  {"deepslate_coal_ore": {"x": 50, "y": -3, "z": -1, "distance": 85.1, "exposed": false}},
    "SE": {}, "S": {}, "SW": {}, "W": {}, "NW": {}
  }
}
```

## POST /smelt  `{"ingredient": "raw_iron", "item": "iron_ingot", "times": 3, "fuel": "coal"}`

1. Inventory needs `times` of `ingredient`, enough `fuel` (coal and charcoal
   smelt 8 items each; anything else is counted as 1 item per fuel item), and a
   placed furnace within 4 blocks or a `furnace` in the inventory. Else
   `missing_ingredients` (with `missing`) or `missing_furnace`.
2. Place the furnace like the crafting table (same cell retries), load the
   input and fuel, wait for the output (about 10 s per item), take it, take back
   any unburnt fuel and unsmelted input, then dig the furnace with the best
   pickaxe held and pick it up (`furnace_retrieved`).
3. Resync and settle as for `/craft`. `inventory_delta` is measured from
   `inventory_before_step`. `fuel_used` reports the fuel removed (tolerant).
   The action timeout for `/smelt` is `30 s + 12 s × times`.

```jsonc
{
  "ok": true, "error": null, "ingredient": "raw_iron", "item": "iron_ingot", "times": 3,
  "fuel": "coal", "fuel_used": 1, "used_furnace": true, "furnace_retrieved": true,
  "inventory_delta": {"raw_iron": -3, "iron_ingot": 3, "coal": -1},
  "inventory": {"iron_ingot": 3, "furnace": 1},
  "travel": {"distance": 0, "dug": {}, "placed": {}, "picked_up": {}},
  "events": [], "inventory_before_step": {"raw_iron": 3, "coal": 1, "furnace": 1}
}
```

## POST /probe  `{"grid": [["birch_planks", null], ["birch_planks", null]]}`

A 2x2 grid (player inventory) or 3x3 grid (needs a crafting table, placed and
retrieved as for `/craft`). Puts the ingredients in the grid, reads the
server's output slot after a resync, takes everything back, and checks the
inventory is unchanged.

```jsonc
{"ok": true, "output": {"item": "stick", "count": 4}, "inventory_delta": {}, "events": []}
// output is null when the grid makes nothing
```

Errors: `missing_ingredients`, `missing_crafting_table`, `craft_failed`.

## POST /status  `{"since": 40}`

Doesn't need the action lock (it answers while an action runs).

```jsonc
{
  "ok": true, "connected": true, "alive": true,
  "health": 18.0, "food": 20,
  "position": {"x": 10.5, "y": 71, "z": -3.5},
  "events": [{"seq": 41, "t": 1760731234.512, "type": "damage", "amount": 2.0, "health": 18.0}],
  "last_seq": 41
}
```

`events` are those with `seq > since` (the last 200 are kept). `seq` increases
for the bot process's lifetime.

## Error codes (v2)

v1 codes, plus `hazard`, `quarantined` and `missing_furnace`.
