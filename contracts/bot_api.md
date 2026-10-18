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
- One action at a time. A request while another action runs gets HTTP 409 `{"ok": false, "error": "busy"}`
  (v3.2: also while the guard fights, `"detail": "guard"`, or an aborted handler is still running, `"detail": "orphan"`).
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

**v2.3, no items left behind.** After every craft, mixed-variant fill and
probe, whether it worked or not, the bot moves whatever is left in the crafting
grid (window slots 1-4 of the inventory, or 1-9 of a table) and on the cursor
back into the inventory, resyncs, and settles. Only then is `inventory_delta`
measured. A craft answers `ok: true` only when, for the `k ≥ 1` runs the bot
actually performed, the ingredients lost are exactly `k ×` one recipe's
ingredients (tag variants allowed), the crafted item gained a positive multiple
of `k`, and nothing else changed; otherwise it answers `craft_failed` with
`"detail": "partial_delta"` and the real delta. The bot must **not** judge the
output count against its own recipe data: a datapack may change it (stick2
makes 2 sticks per run), and that is exactly what the exact check in Python
must see as a mismatch. Nothing lost in a grid can turn into a false
contradiction. `/craft` and `/probe` responses add `held_outside_inventory`
(what is still in window slots 0-4 or on the cursor; normally `{}`), for tests.

## POST /explore (v2 options)

```jsonc
{"direction": "NE", "distance": 32, "dig_through": true, "scaffold": ["dirt"]}
```

Without `direction`, it's the v1 spiral. With it, the bot walks up to
`distance` blocks (default 32) along one of `N` (-z), `NE`, `E` (+x), `SE`,
`S` (+z), `SW`, `W` (-x), `NW`, stopping at the world border, at
barriers, before lava, or after 30 s. Response: v1 fields plus `travel`,
`events`. `unreachable` when it moved less than 1 block.

`{"direction": "up", "dig_through": true, "scaffold": [...]}` (v2.1) returns
the bot to open sky: it climbs, digs upward and pillars (with `scaffold`
blocks only) until it stands where nothing solid is above it, within 100 s.
Already under open sky: `ok: true`, nothing moves. It never digs straight
down, and stops before water or lava overhead. Response as above plus
`"sky": true|false` (whether it reached open sky); `unreachable` when it
gained less than 1 block of height and isn't under open sky.

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

1. Inventory needs `times` of `ingredient`, enough `fuel`, and a placed
   furnace within 4 blocks or a `furnace` in the inventory. Else
   `missing_ingredients` (with `missing`) or `missing_furnace`.
   **v2.3 fuel values** (items smelted per fuel item, vanilla burn time / 200
   ticks): coal and charcoal 8; any `*_log`, `*_wood`, `*_stem`, `*_planks` 1.5;
   stick 0.5. Fuel needed is `ceil(times / value)`. Any other fuel is refused
   with `missing_ingredients` and `"detail": "not_fuel"`.
2. Place the furnace like the crafting table (same cell retries), load the
   input and fuel, wait for the output (about 10 s per item), take it, take back
   any unburnt fuel and unsmelted input, then dig the furnace with the best
   pickaxe held and pick it up (`furnace_retrieved`).
3. Resync and settle as for `/craft`. `inventory_delta` is measured from
   `inventory_before_step`. `fuel_used` reports the fuel removed (tolerant).
   The action timeout for `/smelt` is `30 s + 12 s × times`.
4. **v2.3:** the response adds `smelted` (inputs the furnace consumed, i.e.
   `times` minus the unsmelted input taken back) and `output_taken` (outputs
   taken out). `ok: true` only when `smelted == times` and `output_taken > 0`;
   any shortfall (fuel ran out, timeout, furnace closed) answers `craft_failed`
   with `"detail": "smelted <n> of <times>"` and the real delta, so the check is
   `failed` (tolerant), never a mismatch. The bot doesn't compare
   `output_taken` with `times`: a datapack may change a smelt's yield, and the
   exact check in Python must see that.

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

`/observe` (v2.1) also reports `"sky": true|false`: whether nothing solid is
above the bot.

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

## v2.5: positions and the viewer (lane `view`)

- **`position` on every action response** (`/mine`, `/craft`, `/smelt`,
  `/explore`, `/look`, `/probe`): `{"x", "y", "z"}`, the bot's position when
  the response is built. Python passes it into the step's `detail.position`.
  Tolerant: never part of a check.
- **CORS** on `GET`/`POST /status` and `POST /viewer`: answer
  `Access-Control-Allow-Origin` for any `http://localhost:<port>` or
  `http://127.0.0.1:<port>` origin, and `OPTIONS` preflights, so the trace
  panel (port 8766) can read the bot's live position and switch the camera.
  Nothing else gets CORS.
- **`POST /viewer {"mode": "third" | "first"}`**: restarts prismarine-viewer on
  the same `VIEWER_PORT` with that camera (`third` = today's, following the bot
  from behind and above; `first` = the bot's eyes), answers
  `{"ok": true, "mode": "..."}`. Doesn't take the action lock. `VIEWER_MODE`
  (env, default `third`) sets the mode at start.

## v3.0: `/explore` reports the attempt that moved (lane `explore-took`)

- A directional `/explore` (`{"direction": "NE", ...}`) answers `took: {"direction", "distance"} | null`: the
  attempt that moved the bot 1 block or more, out of the request, the 10-block hop the same way and the two
  neighbouring directions (v3 explorefast), with the distance that attempt asked for. `null` when no attempt moved.
  Blind (spiral) and `up` explores don't send it. Tolerant: never part of a check.

## v3.0: `/toss` and `free_slots` (session S04)

### `free_slots` on `/observe`

`/observe` (and `/reset_episode`, which answers with `/observe`) also reports
`"free_slots": 12`: the number of empty slots among the 27 main and 9 hotbar
slots (window slots 9-44), from 0 to 36. Armor, the offhand, the crafting grid
and the cursor don't count. A slot holding anything, even a stack that isn't
full, isn't free.

### POST /toss  `{"items": {"dirt": 40, "granite": 64}}`

Drops items on the ground to make room. Takes the action lock like any action.

1. **Toss.** Level the pitch first (keep the yaw), so the stacks fly forward
   instead of landing at the bot's feet. For each named item, toss up to that
   count from the main and hotbar slots, smallest stacks first (so the most
   slots come free). Never toss the held item if it is a tool (it has
   durability), and never an item the request doesn't name. A name the bot
   doesn't hold is skipped, not an error.
2. **Walk away**, straight after the last toss. If anything was tossed, walk
   to a spot at least 4.5 blocks (horizontal) from where the bot stood when
   it tossed, and not in front of it: the offset from the toss point has a
   dot product of 0 or less with the yaw's facing vector. One pathfinder goal,
   the world-preserving movements every other walk uses (no digging, no
   placing), 10 s at most.
   Why: a tossed stack can be picked up again after 2 s (40 ticks), and a
   pickup inside a later craft's or smelt's step window is a `stray_pickup`,
   which quarantines that step.
3. **Travel phase only.** `/toss` never opens a step window, so it never
   reports `stray_pickup`. `travel` is recorded from after the toss (once the
   inventory has settled) to the end of the walk, so anything picked up again
   shows in `travel.picked_up`, and isn't counted in `tossed`.

```jsonc
{
  "ok": true,
  // What left the inventory: before minus after, for the named items only,
  // measured after the walk. Zero counts omitted.
  "tossed": {"dirt": 40},
  "inventory": {"dirt": 24, "oak_log": 3},
  "free_slots": 14,
  // Ended 4 or more blocks (horizontal) from the toss point. false when
  // nothing was tossed, or when the walk didn't get that far.
  "walked_away": true,
  "travel": {"distance": 6.0, "dug": {}, "placed": {}, "picked_up": {}},
  "events": [],                     // all "phase": "travel"
  "position": {"x": 4.5, "y": 70, "z": -2.5}
}
```

Nothing to toss (no named item held, or only the held tool): `ok: true`,
`tossed: {}`, `walked_away: false`, no walk.

Errors: `bad_request` (HTTP 400) unless `items` is a non-empty object of item
name to positive integer; `busy` (HTTP 409) and `not_connected` as for any
action. A walk that falls short is not an error: `ok: true`,
`walked_away: false`.

## v3.0: `tools_broken` on `/craft`, `/smelt` and `/probe` (lane `wear-bot`)

- Digging the placed crafting table or furnace back up uses the cheapest tool that harvests it and that the dig
  won't break, or the bare hand when the hand harvests it. For a furnace, when no pickaxe has 2 durability left, the
  best pickaxe is used anyway.
- `tools_broken: [item, ...]`: tools whose count dropped across that dig, because they wore out. `[]` when none.
  Python adds them back to the step's delta (`docs/v3-interfaces.md` v3.0e).

## v3.1: chat commands (S06, lane `chat-bot`)

- **`BOT_OWNERS`** (env): comma-separated Minecraft usernames, trimmed and compared case-insensitively. Unset or
  empty means nobody, and no command is recorded. `scripts/v2-up.sh` passes `BOT_OWNERS='${BOT_OWNERS:-Otopo}'` on
  the bot's command line, and live tests add their test player (`BOT_OWNERS=Otopo,Tester`). `lib/chat_commands.js`
  reads it, not the `config` block. The servers run in offline mode, so a username isn't authenticated: the list
  only keeps out other players on a server that strangers can't reach.
- **The listener** is `attachChatCommands(bot, eventLog)` from `lib/chat_commands.js`, called once per `connect()`.
  On `bot.on('chat', (username, message))` it records a message when all of these hold:
  - it comes from an owner who isn't the bot itself;
  - it starts with `!`, and its first word matches `^![A-Za-z]+$`.
- **The event** is `chat_command {"player", "command", "args"}`, recorded through `eventLog.record`. `command` is the
  first word without `!`, lowercased. `args` is the rest of the message, trimmed and cut to 200 characters (`""` if
  there is none). Any `!word` is recorded, and Python answers unknown ones with the list of commands.

  ```jsonc
  {"seq": 57, "t": 1760731301.204, "type": "chat_command", "phase": "step",
   "player": "Otopo", "command": "goal", "args": "10 torches"}
  ```

- The life knows `goal`, `cancel`, `stop` and `status` (`docs/v3-interfaces.md`, v3.1g); M4 adds `yes` and `no`. It
  reads these events from `/status?since=` only, never from an action response's `events`.
- The bot never answers in chat by itself. Python answers through `POST /chat`, which keeps its anti-spam limit.

## v3.2: survival (S07)

### `nearby_entities` in `/observe` and `/status` (lane `entities-bot`)

```jsonc
"nearby_entities": {
  "zombie": {"count": 2, "nearest": 6.4, "kind": "hostile"},
  "cow":    {"count": 3, "nearest": 11.0, "kind": "animal"},
  "player": {"count": 1, "nearest": 4.2, "kind": "player"}
}
```

- Entity type → `count`, `nearest` and `kind`, over `bot.entities` within 16 blocks of the bot, not counting the bot.
  - `nearest` is the distance in blocks to the closest one, rounded to 0.1.
  - `kind` is minecraft-data's entity `type`: `hostile`, `mob` (slime, magma cube, phantom, golems, ...), `animal`,
    `passive` (villagers), `water_creature`, `ambient` or `player`.
- The kinds `other` (items, experience orbs, boats, falling blocks), `projectile` and `living` (armor stands) are
  left out. Players are all counted under `player`.
- The result is `{}` when nothing is near or the bot hasn't spawned.
- Tolerant: never part of a check.

### The guard (lanes `guard-core`, `guard-wire`)

- **Modes**: `off` (does nothing, v2 behavior), `passive` (runs from threats) and `defend` (fights back; the
  default). `BOT_GUARD` (env, read by `lib/guard.js`) sets the mode at start.
- **`POST /guard {"mode": "off" | "passive" | "defend"}`** answers `{"ok": true, "mode": "defend"}`, or 400 for any
  other mode. It doesn't take the action lock, so it works mid-fight. Switching to `off` ends a fight at the next
  guard tick.
- **`/status` gains `guard`**: `{"mode": "defend", "fighting": "zombie" | null}`.
- **The tick** runs every 4 game ticks. A threat is either of these:
  - a hostile entity the bot can see, within 5 blocks, or within 8 blocks and closing in (its distance fell by 1
    block or more over the last second). Hostile here means kind `hostile`, plus `slime`, `magma_cube` and
    `phantom`, but not `enderman`, `zombified_piglin` or `piglin`, which count only once they hit the bot. (S07: a
    plain 8-block radius would cut into most steps at night, when some hostile is in view nearly all the time);
  - any entity that damaged the bot in the last 3 s (mineflayer's `entityHurt(entity, source)`, where `source` is
    the cause: the skeleton, not its arrow). A hit from a player named in `BOT_OWNERS` never counts: the guard
    never fights an owner (and M3's `guard me` never targets the guarded owner).
  - A mob that died (`entityDead`) is never a threat while its body is still in `bot.entities`.
  - Several at once: a creeper first (at any distance), then the most recent attacker, then the nearest.
- **What the guard does** (`decide(mode, threat, health)` → `fight | flee | back_off | ignore`):
  - `off`: always `ignore`;
  - a creeper is never fought: any creeper threat is `back_off`, to 8 blocks or more, in `passive` and `defend`
    (an explosion at point blank can take about 20 health on easy);
  - health below 6: `flee`, and eat if it holds food and its food is below 20 (with `bot.consume()`: auto-eat's
    `eat()` does nothing while the guard holds the plugins), in `passive` and `defend`;
  - otherwise `passive` flees and `defend` fights.
- **A fight**: equip the best sword (else axe) the bot holds and a shield in the off-hand if it has one, then
  `bot.pvp.attack(target)`. It ends in one of these outcomes:
  - `killed`: the target died;
  - `lost`: the target is gone or more than 16 blocks away;
  - `timeout`: 30 s passed;
  - `fled`: health fell below 6 and the guard switched to `flee`;
  - `died`: the bot died.
- **Cutting into an action.** The guard cuts only into `/mine`, `/craft`, `/smelt` and `/explore` (later `/attack`,
  `/place`, `/clear`). Any other endpoint that holds the lock (`/observe`, `/chat`, `/look`, `/probe`,
  `/reset_episode`, `/toss`) runs to its end, and the guard acts after it. When the guard decides anything but
  `ignore` during one of those four, it rejects `rejectOnThreat()` (`lib/events.js`, next to `rejectOnDeath()`),
  which `action()` races like the death promise.
  - The wrapper aborts `session.signal`, an `AbortSignal` every handler gets, and stops the bot's pathing and
    digging. It does **not** close an open window on a guard abort: mineflayer sends a click on a closed window to
    the player inventory's slot of the same number (a furnace's output slot 2 is a crafting-grid slot). The
    handler that owns the window sees the abort at its next check, takes its items back out, closes the window and
    throws. A timeout or a death calls `interruptBot()` as in v2 (which also closes the window) and aborts the
    signal too.
  - Every handler that walks or waits checks the signal at its choke points and throws once it is aborted (S07
    lane `handler-abort`). The pathing helper throws at entry and on abort, so an aborted walk never counts as
    `unreachable`, never marks a target unreachable, and an orphaned handler can't replace the fight's path goal.
  - It then waits for the handler to settle, at most 10 s. If it doesn't settle, the wrapper records
    `orphaned_handler {"path"}` and goes on.
  - Then it awaits the guard's response: the fight or flight, at most 30 s.
  - It answers `{"ok": false, "error": "interrupted", "detail": {"threat": "zombie", "outcome": "killed"},
    "blocks_walked": ..., "position": ..., "events": [...]}`.
  - It releases `busy` only once the handler has settled, however long that takes. Until then every request gets
    `409 {"ok": false, "error": "busy", "detail": "orphan"}`, even after the response was sent. A timeout and a
    death release `busy` the same way.
  - A death at any point in this answers `quarantined`, as in v2.
  - `/smelt` checks the signal in its furnace wait loop and before it retrieves the furnace. On an abort it takes
    back what is in the furnace, closes the window and leaves the furnace standing. `/craft` checks it between
    runs.
  - **Stations left standing.** A crafting table or furnace that a handler placed and didn't dig back up because of
    an abort is remembered in `session.episode` (name and position). The next `/craft` or `/smelt` that needs that
    station uses it when it is within 24 blocks and still there, walking back to it, before it places a new one. A
    fight chase easily takes the bot past the 4 blocks `findPlacedStation` searches.
  - Backstop: a handler still unsettled 45 s after the abort makes the bot reconnect (`bot.quit()`, then the
    existing reconnect). The wrapper records `orphaned_handler {"path", "reconnected": true}` and releases `busy`.
- **Open windows.** While a container window is open (a craft at a table, a smelt), `fight` waits for the window to
  close, at most 10 s. `back_off` and `flee` don't wait.
- **Furnace leftovers.** Before its shortfall check, `/smelt` drains a placed furnace it is about to use (anything
  in it goes back into the inventory) and records `interference {"block": "furnace", "position"}`. Python then
  quarantines that step, so an aborted smelt's leftovers are never counted, and the retry gets its ingredients
  back.
- **While idle** (no action holds the lock), the guard fights directly and holds `busy` while it does. A request
  then gets `409 {"ok": false, "error": "busy", "detail": "guard"}`. The guard holds it at most 30 s at a time, and
  releases it for at least 1 s between fights so that a waiting request gets in. An idle fight also holds the
  survival plugins (`hold()`, below), so auto-eat and armor-manager click nothing mid-fight.

### Armor and food (lane `survival-plugins`)

- `attachSurvival(bot, eventLog)` loads `mineflayer-armor-manager` and `mineflayer-auto-eat`; the versions are in
  `docs/v3-interfaces.md`, v3.2g. It returns `{hold, onIdleTick}` (S07 changed this from `{onActionStart,
  onActionEnd}`):
  - `await hold()` returns a `release()` function. Holds are counted: the plugins click nothing while any hold is
    open. `action()` holds them for every action, before the handler starts, and the guard's idle fight holds them
    too, so an orphaned handler that settles mid-fight can't switch eating back on.
  - `hold()` disables auto-eat, then waits for an eat in progress (`bot.autoEat.isEating`, at most the plugin's
    3 s `eatingTimeout`) and an `equipAll()` in progress to end.
  - The last `release()` enables auto-eat, runs `equipAll()`, then calls `bot.autoEat.eat()`: the plugin only
    starts an eat on a `health` or `playerCollect` event, and one that came while a hold was open is dropped.
  - `onIdleTick()` (called by the guard tick while nothing holds the lock) runs `equipAll()` and `eat()`, at most
    once every 2 s.
- **auto-eat** options: `startAt: 14`, raised to 17 while health is below 20 (health only regenerates at food 18 or
  more, so at 14 the bot would sit at food 15-17 and never heal), the plugin's default ban list,
  `equipOldItem: false`, and `offhand: false` (the default `true` would push the shield out of the off-hand).
  It records `ate {"item", "food"}` when an eat finishes, and logs `autoeat_error` without throwing.
- **Armor**: armor-manager's own pickup listener is removed (it is registered when mineflayer injects the plugin,
  which can be after `loadPlugin` returns). `equipAll()` runs on the last `release()` and on idle ticks, so armor
  the bot picked up or crafted goes on once the lock is free. The guard holds a shield in the off-hand. Armor and
  off-hand slots are counted in every `inventory`, so equipping changes no count.

### Event types (v3.2)

| type | when | extra fields |
|---|---|---|
| `threat` | the guard decided to act on an entity | `mob`, `id`, `distance`, `reason` (`near` or `attacked`), `action` (the running path, or `null` when idle) |
| `fight_start` | a fight began | `target`, `id`, `distance`, `weapon` (`null` for a bare hand) |
| `fight_end` | a fight ended | `target`, `outcome` (`killed`, `lost`, `timeout`, `fled`, `died`), `damage_taken`, `duration_s` |
| `fled` | the guard ran or backed off | `from`, `reason` (`creeper`, `low_health` or `passive`), `distance`, `health` |
| `ate` | the bot ate | `item`, `food` (the food level after) |
| `orphaned_handler` | an aborted handler didn't settle within 10 s (the guard fights anyway; `busy` stays held until it settles), or within 45 s (the bot reconnects) | `path`, `reconnected` (`true` for the 45 s backstop) |

`interference` also covers furnace leftovers (`block: "furnace"`). As in v2, Python decides what an event means
for a check.

### Error codes (v3.2)

v2 codes, plus `interrupted` (an action response, `detail: {"threat", "outcome"}`) and `busy` (HTTP 409, now also
while the guard fights idle, with `detail: "guard"`, and while an aborted handler hasn't settled, with
`detail: "orphan"`).

## v3.4: building actions (S09, lane `build-bot`)

Three endpoints for building: place one block, clear one cell, and read the block names in a box. Coordinates are
integer block coordinates. The Python side and the blueprint axes are in `docs/v3-interfaces.md` v3.4a.

- `/place` and `/clear` take the action lock, and the guard cuts into them as it does into `/mine` (v3.2,
  `cutIn: true`). Both honour `session.signal`. `travel` is always present.
- `/scan` takes the lock like `/look`, and the guard doesn't cut into it. It never walks.
- **A free cell**, for `/place`: `air`, `cave_air`, `void_air`, or a plant the server replaces: `short_grass`,
  `tall_grass`, `fern`, `large_fern`, `dead_bush`, `snow` (the layer; `blueprint.REPLACEABLE` in Python). For
  `/clear`, only the three airs are free: grass in a cell that must be air gets dug.
- **The walk** goes within reach (4.5 blocks from the eyes, with a line of sight to the face it will use). It never
  ends with the bot inside the target cell (for a door, inside either cell), and for `/clear` never standing on it.
  It never digs, and never opens doors (pathfinder's `canOpenDoors` stays off), so a placed door is a wall. It may
  pillar and bridge with the request's `scaffold` blocks, which Python sets to `SCAFFOLD_BLOCKS`. For `/place`, the
  requested `block` is never used as scaffold. Pathfinder's `GoalPlaceBlock` fits `/place`, and `GoalLookAtBlock`
  fits `/clear`. 20 s at most.
- **`keep_out`**, optional on both: `{"origin": [x, y, z], "size": [w, h, d]}`, the build's box. The walk never
  places scaffold in it (pathfinder's `exclusionAreasPlace`, with a cost that rules the move out), and for a door the
  walk ends with the bot outside it. The bot may still stand and walk inside the box. Without `keep_out`, scaffold
  stays wherever the pathfinder put it, and shows in `travel.placed`.

### POST /place  `{"block": "oak_planks", "x": 10, "y": 64, "z": -3, "scaffold": ["dirt", "cobbled_deepslate"]}`

`scaffold` and `keep_out` are optional: by default the walk places nothing.

1. **Checks**, in this order. They run before the walk when the cell's chunk is loaded, and again on arrival.
   - The cell already holds `block` (or a `wall_torch` for a torch): `ok: true`, `placed: false`, no walk,
     nothing spent. A retry after an interruption ends here.
   - The bot holds no `block`: `missing_item`.
   - The cell isn't free: `occupied`, with `found`. A door also needs the cell above free.
   - Nothing to place against: `no_support`. A support is a neighbour with a full collision box
     (`boundingBox === 'block'`) that does nothing on use: never a door, trapdoor or fence gate, nor a block that opens
     a window (`chest`, `barrel`, `crafting_table`, `furnace` and the like). The faces are tried in this order: the
     block below (its top face), then north, south, east and west, then the block above. A torch takes only the one
     below or a side one, and a door only the one below.
2. **Walk** within reach (travel phase). Can't get there: `unreachable`.
3. **Place** (step phase, `inventory_before_step` on arrival). Equip `block`, place it against the first support,
   and read the cell back once the server confirms it (a block update, or a few ticks). If the cell is still free,
   the server refused: wait 5 ticks and try the next support face, 3 tries in all. Then: `place_failed`, with
   `found`. (A mob standing in the cell is the usual reason.) Reuse `lib/station.js`'s face and overlap logic; the
   read-back accepts `wall_torch` for a torch.
4. **Response:**

```jsonc
{
  "ok": true,
  "placed": true,                   // this call put the block there
  "block": "oak_planks",            // as requested
  "found": "oak_planks",            // the cell, read back after the step ("wall_torch" for a torch on a side)
  "inventory": {"oak_planks": 40},
  "inventory_before_step": {"oak_planks": 41},
  "travel": {"distance": 3.2, "dug": {}, "placed": {}, "picked_up": {}},
  "events": [],
  "position": {"x": 8.5, "y": 64, "z": -3.5}
}
```

Errors (HTTP 200, `ok: false`): `missing_item`; `occupied` (`found`: the block in the cell, or above it for a door);
`no_support`; `unreachable`; `place_failed` (`found`: the cell after the tries); and, as for any action,
`interrupted`, `quarantined` (the bot died), `timeout` and `not_connected`. `busy` is HTTP 409. `bad_request` (HTTP
400) unless `block` is a non-empty string, `x`, `y` and `z` are integers, `scaffold`, when given, is a list of
strings, and `keep_out`, when given, has an `origin` of three integers and a `size` of three positive integers. The
route's limit is 60 s.

### POST /clear  `{"x": 10, "y": 64, "z": -3, "scaffold": ["dirt"], "keep_out": {"origin": [8, 64, -5], "size": [7, 4, 5]}}`

Digs one cell, for site prep and for undoing a wrong block.

1. **Checks**, before the walk when the chunk is loaded, and again on arrival:
   - the cell is air: `ok: true`, `cleared: false`, `found: "air"`, no walk;
   - the block can't be broken (no hardness, such as `bedrock` or `barrier`) or is a liquid: `unbreakable`, with
     `found`;
   - lava next to it: `hazard`, as for `/mine`.
2. **Walk** within reach (travel phase): `unreachable` if it can't.
3. **Dig** (step phase). Equip the fastest tool the bot holds for this block, else the bare hand. If the dig would
   take more than 15 s with it, answer `missing_tool` without digging. Dig, and wait for the server to confirm. A
   door goes as a whole. Drops are not collected on purpose: whatever the bot picks up shows in `inventory`.
4. **Response:** `{"ok": true, "cleared": true, "removed": "dirt", "found": "air", "inventory", "inventory_before_step",
   "travel", "events", "position"}`. `found` is the cell read back after the dig. Sand that fell in, or water that
   flowed in, shows there, and Python checks it.

Errors: `unbreakable` (`found`), `hazard`, `unreachable`, `missing_tool`, `dig_failed` (the dig didn't confirm), and
the usual `interrupted`, `quarantined`, `timeout`, `not_connected`, `busy` (409) and `bad_request` (400, unless `x`,
`y` and `z` are integers, and `scaffold` and `keep_out` are as for `/place` when given). 60 s.

### POST /scan  `{"origin": [8, 64, -5], "size": [3, 2, 2]}`

The block names in a box, with the blueprint's axes: `layers[y][z][x]` is the block at
`(origin.x + x, origin.y + y, origin.z + z)`. Layers go bottom-up, rows north to south, names west to east.

```jsonc
{
  "ok": true,
  "origin": [8, 64, -5],
  "size": [3, 2, 2],
  "layers": [
    [["dirt", "dirt", "air"], ["dirt", "oak_planks", "air"]],
    [["air", "air", "air"], ["air", "torch", "air"]]
  ],
  "events": [],
  "position": {"x": 8.5, "y": 65, "z": -3.5}
}
```

A cell in a chunk the bot hasn't loaded is `null`. `bad_request` (400) unless `origin` is three integers and `size`
is three integers from 1 to 32 with at most 8192 cells in all.

### Error codes (v3.4)

v3.2 codes, plus `missing_item`, `occupied`, `no_support`, `place_failed` and `unbreakable`.
