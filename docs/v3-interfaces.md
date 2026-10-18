# SchemaVoyager v3 interfaces

Each milestone's contract is added here as a `v3.N` section before its code is
written, as `docs/v2-interfaces.md` was for v2. New bot endpoints also go in
`contracts/bot_api.md` as v3.x sections. A section another session already
relies on changes only with the user's approval.

## v3.0 M0 fixes (blocked rules, keep items, `/toss`)

### v3.0a Blocked rules (S0.2, lanes `retry-block` and `blocked-memory`)

Within a goal (`agent.py`):

- `run_schemavoyager(episode, store, before_step=None, excluded: frozenset[str] = frozenset())`.
- `Episode.blocked_rules: dict[str, str]`, rule id → the error that blocked it, empty at the start of each goal.
- Each `failed` step is remembered as `(step.rule.id, outcome.error or "", normalize.counts(inventory))`, the
  inventory as it was when the step failed. The second identical failure in the goal blocks the rule.
- Planning uses `[r for r in store.usable_rules(world_id) if r.id not in excluded and r.id not in episode.blocked_rules]`.
  When nothing can be planned, the goal ends with the existing `plan_failed` event.
- Event `rule_blocked {"rule": "<id>", "error": "<code>", "missing": {...} | null}`, where `missing` is the bot's
  `detail.missing` from the second failure.
- Never blocked: `not_found`, `unreachable`, `hazard`, `timeout`, `not_connected`. Search and travel failures stay
  with `Episode.act` and `_explore_toward`, and `_Life._run_goal` reads the last step's error to start an exploration
  round.

Across goals (`life.py`):

- `_Life.blocked: dict[str, int]`, rule id → the number of goals that blocked it with `missing_ingredients`. Read from
  `episode.blocked_rules` after each goal. Saved in `life_state.json` under `"blocked"`, read with
  `state.get("blocked", {})`.
- A rule blocked in 2 goals is excluded for the rest of the life: it is passed as `excluded` to `run_schemavoyager`
  and to `Curriculum.next_goal(inventory, store, world_id, visible=None, excluded: frozenset[str] = frozenset())`,
  which plans without it, so it never picks a goal whose only plan uses it.

### v3.0b Repairs and kept items (S0.3, S0.5, lanes `repair-dedupe` and `keep-items`)

- `_repair_dead_ingredients` adds each proposal's id to `self.repaired` as soon as the proposal is added, so a repair
  is never repaired. It skips a proposal that equals a rule in `store.rules` (same `action`, `preconditions` and
  `effects`), or that still consumes an item nothing produces.
- The `repair` record: `{"rule", "dead_items", "proposed": [added ids], "skipped": [{"id", "why"}]}`, where `why`
  is `"same as <rule id>"` or `"still consumes <item>"`.
- Learn mode: `Curriculum.next_goal` skips an item to keep while at least one is held. Kept items
  (`curriculum.is_kept_item(item) -> bool`): the stations `crafting_table`, `furnace`, `chest`; tools, meaning names
  ending `_pickaxe`, `_axe`, `_shovel` or `_sword`, plus `bucket` and `shears`. The skip is per call, from the
  inventory, and isn't saved in the curriculum's `skipped`. Rules for kept items stay hypotheses longer (plan S0.5).

### v3.0c Inventory space (S0.6, lanes `toss-bot`, `toss-backend`, `junk-policy`, `toss-sim`, `junk-life`)

The bot has 36 slots (27 main, 9 hotbar). Before each goal, a life whose bot has fewer than 4 free slots drops
junk. Bot side: `contracts/bot_api.md` v3.0 (`POST /toss`, `free_slots` on `/observe`).

Models and backends:

- `Observation.free_slots: int | None = None` (`models.py`): empty main and hotbar slots, 0 to 36. `None` when the
  backend doesn't report it (the v1 simulator, test fakes); nothing is tossed then.
- `LifeBackend.toss(items: dict[str, int]) -> dict[str, int]` (`adapter.py`): raw id → the count to drop. Returns
  what left the inventory (raw ids, counts above 0). An empty request returns `{}` without a call.
  - `MineflayerBackend` posts `/toss`, returns the response's `tossed` (`{}` when `ok` is false and there is
    none), and refreshes its cached inventory from the response. Transport errors raise `BackendError`, as for every
    call. It parses `free_slots` wherever it builds an `Observation`; a missing one (an older bot) is `None`.
  - `NaturalWorld` (simulator) removes the items. They are gone: the simulator has no items on the ground.

`inventory.py` (new):

- `stack_size(item: str) -> int`: 1 for tools, armor, filled buckets and the other unstackables it lists; 16 for
  `bucket`, `snowball`, `egg`, `ender_pearl`, signs and banners; 64 otherwise.
- `slots_used(inventory: dict[str, int]) -> int`: the sum of ceil(count / stack_size), the packed count. The
  simulator reports `36 - slots_used`. The real bot can use more slots than this.
- `junk_to_toss(inventory, rules, wanted, free_slots, min_free=4, scaffold_reserve=64) -> dict[str, int]`, pure.
  `inventory`: raw id → count. `rules`: the usable rules. `wanted`: normalized names the goals need (today
  `{goal.item}`). Returns raw id → count, or `{}` when `free_slots` is `None` or at least `min_free`.
  - **Kept**, never tossed: an item whose stack size is 1 (a tool, armor); an item `curriculum.is_kept_item` names
    (raw id or `normalize.family`); an item in `wanted` (raw id or family).
  - **Rule items**: those a rule names (raw id or family) in `preconditions.consumes`, `station`, `tool_any` or
    `fuel_any`. The first stack (`stack_size` items) of each raw id is kept; the rest is surplus.
  - **Junk**: everything else. Of the junk in `SCAFFOLD_BLOCKS` (`mineflayer_backend.py`), enough is kept that the
    bot still holds `scaffold_reserve` scaffold blocks in total: scaffold blocks kept for other reasons count first,
    then junk scaffold blocks in `SCAFFOLD_BLOCKS` order.
  - Once triggered, it tosses all the junk: junk that isn't a scaffold block, then the scaffold surplus. Then, only
    while `free_slots` plus the slots freed is still below `min_free`, rule-item surplus, the item that frees the most
    slots first, then by name. Freed slots: `slots_used` before minus after, per item.
  - It may fall short of `min_free`: it never tosses a kept item or a rule item's first stack.

Life (`life.py`):

- At the start of each goal (`_Life._run_goal`, once the episode log is open, before the `Task` and `Episode` are
  built), if the observation's `free_slots` is below 4: `junk_to_toss(observation.inventory,
  store.usable_rules(world_id), {goal.item}, free_slots)`, then `backend.toss(...)` if that isn't empty, then
  observe again so the goal starts from the inventory after the toss.
- Log a `tossed` event `{"items": {...}, "free_slots_before": n, "free_slots_after": m}`, add `"tossed": {...}` to
  the goal record (`{}` when nothing was tossed), and say "Dropping 40 dirt and 12 granite to make room."

Simulator (`environment.py`):

- `NaturalWorld` has 36 slots, with `inventory.stack_size`. Mine drops and travel pickups stop at what fits; the
  rest is left on the ground (gone). A mine step's `drops` still reports what the block dropped, while its
  `picked_up` and `inventory_delta` report what was kept, and `detail["inventory_full"]` is `True`.
- A craft or smelt whose output doesn't fit fails, as the real bot does (`craft_failed`, `partial_delta`): `ok`
  false, the inventory unchanged, `detail["inventory_full"]` `True`. An `ok: true` step with a short delta would read
  as a contradiction.
- `observe()` reports `free_slots = 36 - slots_used(inventory)`; `toss()` as above.

Deferred:

- A goal that fills the inventory halfway through. Nothing tosses between steps. Once full, the real bot leaves new
  drops on the ground, and `emptyHand` (`lib/world.js`) keeps the held stack in hand.
- Tossed stacks stay on the ground for 5 minutes. A later goal that walks back over them picks them up again
  (travel, tolerant) and refills the inventory.

### v3.0d `/explore` reports the attempt that moved (lane `explore-took`)

- A directional `/explore` response gains `took: {"direction", "distance"} | null`: the attempt that moved the bot
  1 block or more (the request, the short hop or a neighbouring direction), with that attempt's distance. `null` when
  nothing moved. Blind and `up` explores don't send it. See `contracts/bot_api.md`, v3.0.
- `_explore_natural` copies it into `outcome.detail["took"]`.
- `Episode._remember_explore`: a move of `NEW_PLACE_BLOCKS` or more clears the dead directions first. Then, when
  `took.direction` differs from the request, the requested direction is marked dead. Without `took` (an older bot),
  the behavior is unchanged.

## v3.1 Goals

## v3.2 Survival

## v3.3 Kill rules and `/attack`

## v3.4 Blueprints and building actions

## v3.5 Places, chests, composite goals
