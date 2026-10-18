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
