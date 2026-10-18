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
  - **Junk**: everything else. Of the junk in `SCAFFOLD_BLOCKS` (`inventory.py`), enough is kept that the
    bot still holds `scaffold_reserve` scaffold blocks in total: scaffold blocks kept for other reasons count first,
    then junk scaffold blocks in `SCAFFOLD_BLOCKS` order.
  - Once triggered, it tosses all the junk: junk that isn't a scaffold block, then the scaffold surplus. Then, only
    while `free_slots` plus the slots freed is still below `min_free`, rule-item surplus, the item that frees the most
    slots first, then by name. Freed slots: `slots_used` before minus after, per item.
  - It may fall short of `min_free`: it never tosses a kept item or a rule item's first stack.

Life (`life.py`):

- At the start of each goal (`_Life._run_goal`, right after `begin_episode`, before the `Task` and `Episode` are
  built), if the observation's `free_slots` is below 4: `junk_to_toss(observation.inventory,
  store.usable_rules(world_id), {goal.item}, free_slots)`, then `backend.toss(...)` if that isn't empty, then
  observe again so the goal starts from the inventory after the toss.
- Right after the `goal` event (each episode file still starts with `goal`), log a `tossed` event
  `{"items": {...}, "free_slots_before": n, "free_slots_after": m}` and say "Dropping 40 dirt and 12 granite to make
  room." The goal record gets `"tossed": {...}` (`{}` when nothing was tossed).

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

### v3.0e A tool that wears out on a station dig (S0.7 follow-up, lanes `wear-bot` and `wear-verify`)

Found in `runs/v3-baseline-1` g018: the bot dug its furnace back up with its last pickaxe, a wooden one on its last
durability point. The pickaxe broke, the smelt's delta showed `wooden_pickaxe: -1`, and `smelt_iron_ingot` was
falsely demoted.

- Bot, `retrieveStation` (`lib/station.js`): it picks the tool as `cheapestHarvestTool` (`lib/movements.js`) does:
  the lowest tier that harvests the block, never a tool the dig would break, and the bare hand when the hand harvests
  it. A furnace only drops to a pickaxe, so when no pickaxe has 2 durability left, it digs with the best pickaxe
  anyway (today's behavior). This applies to all three callers: the table in `/craft` and `/probe`, the furnace in
  `/smelt`.
- It measures the break rather than inferring it. When the held tool's count drops across the dig, the tool's name
  goes into the action's `tools_broken: [item, ...]` (`[]` when none). `/craft`, `/smelt` and `/probe` send it.
- Python, `mineflayer_backend.py`: `_smelt_natural` copies `tools_broken` into `outcome.detail["tools_broken"]`, and
  `_craft_natural` concatenates it across its calls. Both skip it when it's empty.
- `verifier.check`, for craft and smelt: each entry in `detail["tools_broken"]` adds back exactly 1 of that item to the
  observed delta, the same way an unretrieved station or burned fuel is added back. The check detail says
  `"<item> wore out (tolerant)"`. Mine checks read drops, not the inventory, so they need nothing.

### v3.0f Retesting a demoted rule (S0.4, lanes `retest-store` and `retest-life`)

A demotion lasts for the rest of the life. Twice a false one cost the life its iron:

- `runs/m4-2024` g014: a fuel bug. `smelt_iron_ingot` had 0 confirmations, so no epoch opened.
- `runs/v3-baseline-1` g018: a pickaxe wore out (v3.0e). The rule had 3 confirmations, so epoch 1 of
  `smelt:iron_ingot` opened at the contradicting transition.

Both times every revision was rejected (`kept: false`), so nothing produced `iron_ingot` afterwards. m4-2024 spent 27
repair calls on its consumers. A false demotion can have causes nobody has found yet, so a demoted rule gets one
experiment: when nothing else produces an item it needs, the agent runs the rule again.

**Evidence: a retest is a new rule with its own evidence window.**

The options:

- **B, rejected:** when a retest matches, mark the contradicting transition suspect, leave it out of replay and
  restore the original rule. A retest can match for a reason other than a false contradiction. `r2` changes coal
  only for a wooden pickaxe, so a retest with a stone pickaxe matches. B would then:
  - erase a real contradiction;
  - change the replay of every rule that transition confirmed or contradicted;
  - leave the epoch the transition opened with nothing behind it.
- **A with a store epoch opened at the retest, rejected.** Epochs mean "the world changed here", and the change
  trigger and `change_report` read them. A retest epoch would cause three problems:
  - it would stamp later revisions;
  - it could swallow a real change's epoch opened at the same seq (`_open_epoch` dedupes by `start_seq`);
  - it would move a certified revision's later confirmations out of that revision's epoch, so `change_report`
    (c), "all in that epoch", would fail.
- **Chosen: A, with a per-rule window.** The retest is a new rule (`source = "retest"`, `revision_of` the
  original), and its replay starts at the retest. Starting a retest never changes `store.epochs`.

Store and models (`retest-store`):

- `Rule.source: Literal["prior", "revision", "retest"]`.
- `Rule.since_seq: int = 0` (`models.py`): replay ignores transitions with `seq < since_seq`. It is 0 for every
  rule except a retest. A store saved before v3.0f loads with 0.
- `SchemaStore.replay` also skips `transition.seq < rule.since_seq`. Nothing else in replay changes.
- `SchemaStore.retest(rule_id: str, world_id: str) -> Rule` adds the retest rule and returns it:
  - `id = f"{rule_id}.retest@{world_id}"`, the full id, like `.rev1@` and `.fix1@`. For example,
    `smelt_iron_ingot.retest@life-2024-...`. Cutting the id at `@` would give `x.rev1@w` and `x.rev1@w.rev1@w` the
    same retest id: the second copy overwrote the first, and the trigger's passes never ended (found by the
    advisor after the merge);
  - it deep-copies `action`, `preconditions`, `effects` and `check` from the original, so editing the copy never
    changes the original;
  - `source="retest"`, `revision_of=rule_id`, `status="hypothesis"`, `scope=world_id`, `rank=0`,
    `rationale=f"retest of {rule_id}, demoted by {transition id}"`;
  - `evidence` starts empty (`{"confirmed": [], "contradicted": [], "counterexamples": []}`), never copied from the
    original: `ChangeTrigger._bites` reads `evidence["confirmed"]` for `r2`;
  - `epoch = current_epoch(epoch_key(world_id, kind, subject))`;
  - `since_seq = len(self.transitions)`: the next transition recorded is the first one it sees;
  - it raises `ValueError` when the rule is not demoted in `world_id` (status `demoted`, or listed in
    `demoted_in[world_id]`), when the rule is itself a retest, or when the rule already has a retest (a rule with
    `source == "retest"` and `revision_of == rule_id`), or when a rule with the retest's id already exists. So each
    rule is retested at most once per life, a retest is never retested, and a retest never overwrites a rule.
- `add_rule` stamps only `source == "revision"`, so it leaves a retest's epoch alone.
- The original rule stays demoted for the rest of the life: its status, its `demoted_in` entry and its evidence
  don't change. Only the copy can come back.
- After that, the retest is an ordinary hypothesis:
  - it is usable and planned;
  - `certify` certifies it with 2 confirmations from 2 episodes inside its window;
  - `demote` treats it like any rule, so its contradiction opens an epoch only if it had a confirmation in its window.
    A mismatch on the first step opens none. A restored rule that later meets a real change opens one, which
    the change needs in order to be caught.

**The trigger (`retest-life`: `_repair_dead_ingredients` and its helper `_retest_demoted`).**

`_repair_dead_ingredients` first calls `_retest_demoted()`, then goes on as today from a fresh `usable_rules`. A
retested item is no longer dead, so its consumers aren't sent to Claude. It returns `True` when it added a retest or
a repair. The life never reads `changes.jsonl` or the change trigger to decide a retest. `_retest_demoted` does two
things:

1. **Report the outcome of each retest that is still pending**, the ones not in `self.retests`. Check mismatch
   first:
   - **mismatch**: the retest is demoted in this world. `transition` is `store.demoted_in[world][retest id]`.
   - **match**: otherwise, its replay has a confirmation. `transition` is the first confirmation.
   - Otherwise it is still pending, because no step has used it yet.
   - The outcome is reported once. A retest that matched and is contradicted in a later goal is an ordinary
     demotion from then on.
2. **Start retests**, in passes, until a pass starts none.
   - A candidate is an item that no usable rule produces and that is in `self.asked`. It must also be needed, which
     means one of these:
     - a usable rule consumes it (today's dead items);
     - a usable rule names it as its `station`;
     - it is first in a usable rule's `tool_any` and no tool in that list is produced (as in `_unasked_families`);
     - it is on the curriculum's frontier.
   - Its demoted producers are the rules that:
     - yield it;
     - are in scope (`train` or this world);
     - are demoted in this world;
     - aren't retests and have no retest yet.
   - Take the producer with the most confirmations (`len(store.replay(rule)[0])`), with ties broken by store order.
     Call `store.retest(rule.id, world_id)`. Start at most one retest per item per pass. After each retest, its
     yields count as produced, so a later item in the same pass that the new retest also yields isn't retested.
   - The passes handle chains. Say `smelt_iron_ingot` and `mine_iron_ore` are both demoted. The first pass
     retests the smelt, which makes `raw_iron` needed again, so the second pass retests the mine. The repairs run
     only after the last pass, so they never ask Claude about an item a retest could bring back. The passes end,
     because each retest uses up one demoted rule.

Life state and events:

- `self.retests: dict[str, str]` maps a retest id to `"match"` or `"mismatch"`. It is saved in `life_state.json` as
  `"retests"` and read with `state.get("retests", {})`. Whether a rule was retested is read from the store, so a
  resumed life neither retests a rule again nor reports an outcome twice.
- Event `retest {"rule": original id, "retest": retest id, "outcome": "started" | "match" | "mismatch",
  "transition": id, "item": item}`. On `started`, `transition` is the demoting one and `item` is the
  candidate; on the other outcomes `item` is `null`.
- Retest events are queued like repairs, in `self._retests_unreported`. Both queues are flushed by two helpers,
  and these two calls in `_run_goal` are the only edit outside `_repair_dead_ingredients` and its helpers. S06's
  rewrite of `_run_goal` keeps one call to each:
  - `_log_unreported(log)` replaces today's `repair` event loop. It writes the `repair` events, then the `retest`
    events, into the next goal's trace.
  - `_attach_unreported(record)` replaces today's `record["repairs"]` lines. It moves `"repairs"` and `"retests"`
    into the goal record, each only when it isn't empty, and empties both queues.
- Chat and console (`[retest]`):
  - started: "Trying smelt iron_ingot again: nothing else I know gives iron_ingot."
  - match: "smelt iron_ingot works again."
  - mismatch: "smelt iron_ingot really changed; I won't use it again."
- A retest with no outcome when the life ends is not reported. The store still holds its state.

**What the change trigger and `change_report` see.**

- `ChangeTrigger._open_change` counts only `source == "revision"`, so a retest never closes a change. Its code
  doesn't change; a test pins this.
- `ChangeTrigger._bites` treats a retest like any rule. A restored, certified retest can be the step a change fires
  on, and its contradiction then opens an epoch, as described above.
- The agent handles a retest mismatch the normal way: demote it (no epoch, since it has no confirmations) and make
  one revise call. That revision is a real `revision`, stamped with the current epoch, so it must also fit the
  original contradiction. Like any revision, it may close the change. `agent.py` doesn't change.
- `change_report`:
  - (a) A retest of a rule the change contradicted comes after that contradiction. But a retest started before the
    change, after an earlier false demotion, can be the first rule the change contradicts. That is fine: a change
    only fires on a rule with evidence in its window (`r2` needs a confirmation, `stick2` and `planks2` a certified
    rule), so that contradiction opens an epoch and the story goes on as for any rule. A test pins this.
  - (b) It still passes or fails on the `revision` events of the first contradicted rule. The text adds the revise
    calls a retest of that rule caused, "; N more after its retest", without failing on them.
  - (c) and (d) read `_revisions_of` (`source == "revision"`), so a retest is never the certified revision or "the
    revised rule".
  - (e) **changes**: a step by a retest of the contradicted rule (`source == "retest"` and `revision_of` that rule),
    with the same action kind and tool, counts as a repeat. The text reads "... attempted again after the
    contradiction: N times (M by a retest)". A retest after a real change does repeat the mistake once, and the
    report says so.

**Tests.**

Life tests drive `_Life` with store fixtures and single `run_schemavoyager` episodes, not long `run_life` runs: those
are slow and S06 rewrites the loop. The fake LLM's revise already rejects the `wooden_pickaxe: -1` case. The `r2`
test overrides revise to return `[]`.

- `tests/test_v3_retest_store.py`:
  - `retest()` builds the copy as specified. The copy is usable, the original stays demoted, and `store.epochs` is
    unchanged. Editing the copy's preconditions leaves the original's unchanged.
  - It refuses a rule that isn't demoted, a retest, and a second retest of the same rule.
  - Ids: a retest of `smelt_iron_ingot` in world `w` is `smelt_iron_ingot.retest@w`, and one of `x.rev1@w` is
    `x.rev1@w.retest@w`. Retests of `x.rev1@w` and `x.rev1@w.rev1@w` get different ids. An existing id raises.
  - The copy's replay ignores the contradicting transition and the confirmations before it.
  - One matching transition after the retest gives 1 confirmation and 0 contradictions. Two from two episodes
    certify it.
  - The v3-baseline-1 case: 3 confirmations, a contradiction, epoch 1. After the retest there is still one epoch
    and the copy's `epoch` is 1.
  - The m4-2024 case: 0 confirmations, no epoch. The copy's `epoch` is 0, and its window excludes the
    contradiction.
  - Demotion opens epochs as follows:
    - a first-step mismatch opens none;
    - matched then contradicted opens one;
    - a revision added after a retest is stamped with the current epoch and has `since_seq` 0.
  - `save` and `load` round-trip `since_seq` and `source`; a store without `since_seq` loads with 0.
- `tests/test_v3_retest_life.py` (simulator, fake LLM):
  - **The false-demotion case (v3-baseline-1):**
    - `smelt_iron_ingot` is confirmed and then contradicted once by a corrupted outcome (an extra
      `wooden_pickaxe: -1`). Epoch 1 opens, and the revision is rejected or absent.
    - The smelt retest starts, with no repair call for `iron_ingot`'s consumers. The next smelt step uses the
      retest and matches. The next `_repair_dead_ingredients` reports `match`.
    - The copy is usable, the original is demoted, and there is one epoch.
  - **The genuine-change case:**
    - `r2` via `set_world_rules`; the bot holds only a wooden pickaxe. `mine_coal_ore` is demoted, and nothing
      else produces coal.
    - The retest runs and mismatches; the outcome is `mismatch`; no new epoch opens.
    - A later call starts no second retest, and the repair path goes on as before.
  - **A chain:** with `smelt_iron_ingot` and `mine_iron_ore` both demoted, one `_repair_dead_ingredients` call
    retests both and makes no repair call.
  - `ChangeTrigger._open_change`, with the change's `t` set to now:
    - a certified retest of `mine_coal_ore` with `epoch` 1 or more keeps an `r2` change open;
    - the control: a certified revision closes it.
  - `ChangeTrigger` doesn't fire `r2` on a step by a retest that hasn't run yet (its evidence is empty).
  - On resume, `retests` is read back. No retest starts twice and no outcome is reported twice.
  - The `retest` events and the record's `"retests"` follow the shape above. A goal with no retest has no
    `"retests"` key.
  - `change_report`:
    - (e) counts a retest step that repeats the contradicted action and tool, and ignores one that used a stone
      pickaxe;
    - (c) ignores a certified retest;
    - a second contradiction and revision caused by the retest leave (a) and (b) passing, with the extra calls in
      (b)'s text;
    - a retest started before the change can be the first rule the change contradicts, and all five criteria
      still pass.
- These pass unchanged: `tests/test_v2_change.py`, `tests/test_v2_scramble.py`, `tests/test_v2_worlds.py`,
  `tests/test_v2_repair.py`, `tests/test_v3_repair_dedupe.py`.

### v3.0g Reaching a `/mine` target (S13, lane `unreachable-retry`)

- Bot side: `contracts/bot_api.md` v3.0g (progress-based candidate attempts, a 60 s call, `reach` with the reason,
  digging down). `MineflayerBackend.mine` copies the answer's top-level `body["reach"]` into `outcome.detail["reach"]`
  when present.
  The simulator's `mine` never answers `unreachable` and is unchanged.
- `Episode.act`, natural world, a `mine` step answering `unreachable`:
  - `detail.reach.reason == "approaching"` (the call's time ran out while the bot was still getting closer): retry
    at once, without an explore. At most once per `act` (`MAX_APPROACH_RETRIES = 1`), not counted in `retries`; a
    second `approaching` in the same `act` is handled as below.
  - Any other reason, or no `reach` (an older bot): counted in `retries` (`MAX_RETRIES` 2) as before, but the explore
    before the retry is `_explore_toward(target, ...)` (shared `went_up` and `previous_direction`, so `up` at most once
    per `act`), not a blind `Action("explore")`. Underground with the target above (a hit more than 4 blocks up, or
    nothing seen) that asks for `explore up`; ore below gets a directional explore. When `_explore_toward` returns
    `None` (every direction dead), `_search_exhausted` is set and the step fails, as for `not_found`.
  - Other actions and non-natural worlds keep the blind explore. `not_found`, `hazard` and `interrupted` are unchanged.
- The S13 live metric: per goal, the longest run of consecutive ore `mine` steps (target ending `_ore`) whose error
  is `unreachable`, any reason; explores and other steps between them don't break the run, any other mine outcome
  does. Reported with the reasons from `reach`.

### v3.0h Polish: a lost bot, which life the panel talks to, scaffold, door planks (S14)

Plan S0.9. Lanes: `backend-health`, `life-robust`, `panel-live`, `door-species`, then `life-polish`,
`trace-interrupted`, `bot-polish`, `build-scaffold`. Reviewed by S14's advisor before the lanes started.

**Losing the bot** (`backend-health`, `life-robust`)

- `LifeBackend.connected() -> bool` (stub on `main`): the bot is connected to the server and takes actions. It never
  raises. `MineflayerBackend`: `GET /health` with a 5 s timeout, `body["connected"] is True`; any error is `False`.
  `NaturalWorld`: `True`. (Not `health()`: `NaturalWorld.health` is the bot's health points.)
- `MineflayerBackend._post` raises `BackendError` for every request that gets no answer, not only `URLError` and
  `TimeoutError`: also any `OSError` (`ConnectionResetError`, ...), `http.client.HTTPException`
  (`RemoteDisconnected`: the bot died mid-request) and a body that isn't JSON. It also raises it for a 200 answer
  `{"ok": false, "error": "not_connected"}`: a disconnected bot (a reconnect, or the seconds after a restart before
  the spawn) is a lost bot, not a failed step. The 409 `busy` retry is unchanged (`BackendError` after 60 s).
- `goals.InterruptReason` gains `"bot"`; `LifeConfig.bot_wait_s = 120.0` and `bot_poll_s = 2.0` (stubs on `main`).
- In the life, a `BackendError` anywhere in a pass of `_Life.run` means the bot is down: the poll on the idle path,
  priors and repairs, starting a goal (`begin_episode`, the junk toss, `prepare_build`), the goal and its ending.
  `_wait_for_bot`:
  - logs `bot_down {"goal_id" | null, "error"}` (the message, at most 200 characters) in `life_events.jsonl` and
    prints one line;
  - calls `backend.connected()` every `bot_poll_s` for up to `bot_wait_s`;
  - when it's back, logs `bot_back {"goal_id" | null, "down_s"}`. The next event poll sees `last_seq` below
    `bot_seq` after a restart and polls from 0, as today (v3.1d).
- The before_step hook still only logs a poll error: the step's own action raises if the bot is gone.
- Mid-goal, `_run_until_interrupted` catches `BackendError` next to `GoalInterrupted`. It waits first, then ends the
  goal through `_end_goal` (the one ending, v3.1d):
  - episode log `goal_interrupted {"goal_id", "reason": "bot"}`, `episode.finish(success=False)`,
    `store.certify()` and `certified`; life record `outcome: "interrupted"`, `interrupt: "bot"`;
  - queue: a user, system or build goal goes back to `queued` and keeps its `target_count` (like `preempted`); a
    curriculum goal isn't recorded in the curriculum. If the queue says `cancelled` by then, it ends `cancelled`;
  - a goal lost 3 times in one session of the life (counted in memory) ends `failed` "lost the bot 3 times";
  - no chat line for the loss itself.
- A goal that was marked `running` but lost the bot outside `_run_until_interrupted` (`begin_episode`, the toss,
  `prepare_build`) is caught by the pass: after the wait, `queue.requeue_running()`, so no goal is left `running`.
- `Episode.finish` works without the bot: if its `observe()` raises `BackendError`, the final inventory is the
  episode's last known one. `_Life._say` is best-effort (catches `BackendError`).
- Not back within `bot_wait_s`: the goal, if any, still ends `interrupted` with `bot` and is requeued, and `run()`
  returns `stop_reason="bot_down"`. Nothing on the way out needs the bot.
- Bot-side state the life sets once at its start, if any, is sent again after `bot_back` (`protect` and `keep_out`
  already go with every request; `set_protected` is re-sent anyway).
- Known limit: a loss outside `_run_until_interrupted` doesn't count toward the 3, so a call that keeps failing while
  `connected()` is True (a `409` past 60 s on every try) loops through `bot_down`/`bot_back` until a stop check ends it.
- The build's scaffold cleanup catches `BackendError` per cell (a lost bot leaves the cell): the build has matched, so
  its success stands.

**Which life the panel talks to** (`life-robust` writes, `panel-live` reads)

- `run_life` writes `<run_dir>/live.json` `{"pid": <os.getpid()>, "started": "<ISO 8601 local time>", "mode":
  "learn" | "play"}` before the first pass and removes it in a `finally`, on an exception too. A killed process
  leaves the file behind; the panel checks the pid.
- The panel, for a request without `run` (`POST /goals`, `/goals/<id>/cancel|approve|discard`, `GET /goals.json`):
  the live runs are the run dirs whose `live.json` names a pid that is alive (`os.kill(pid, 0)`, `PermissionError`
  counts as alive; an unreadable `live.json` counts as not live).
  - One live run: that run.
  - Several: `409 {"ok": false, "error": "ambiguous_run", "runs": [<names, newest "started" first>]}`, on
    `GET /goals.json` too.
  - None: v3.1f's rule (the newest `goals.jsonl`), so a goal can still be queued for a life about to resume.
  - An explicit `run` is unchanged.
- `site/dist/goals.js` shows the run it talks to ("Run: life-20261018-0912", or "No live run"). A `?run=<name>` in the
  page's URL goes on every GET and POST and wins over the run of the last GET. On `ambiguous_run` it shows "Several
  lives are running: a, b. Open the panel with ?run=<name>." and keeps the form.

**Small fixes** (`life-polish`, `trace-interrupted`, `bot-polish`)

- Play mode: `_needed_items` leaves the curriculum frontier out, as `_unasked_families` does.
- A goal whose final `mark(id, "done")` (or `"failed"`) is refused because it is `cancelled` says "Cancelled: <label>"
  (once: not if the chat already said it), not "Done: <label>" or "Couldn't ...".
- `!status` with no goal running and goals queued: "Next: u004: 10 torches. Queued: 2." (`Queued` counts `queued`
  goals; the "Waiting for approval" part is unchanged). "Idle." only when nothing is queued.
- The terminal trace: the `episode_end` line of an episode that logged `goal_interrupted` reads
  `INTERRUPTED (<reason>) actions=N ...` where it read `FAIL actions=N ...`.
- Bot: `lib/place.js`'s build `Movements` set `allowSprinting = sprintAllowed(bot)`; the `autoeat_error` listener
  doesn't log "No food found."; `test/v3_guard_live.js` builds its arena at fixed coordinates and teleports the bot
  onto its floor over RCON before the checks.

**Scaffold left beside a build** (`build-scaffold`)

- Bot: every `travel` gains `placed_cells: [[x, y, z, "dirt"], ...]`, each block the recorded walk placed, in order,
  from mineflayer's `blockPlaced` (position and the new block's name); `placed` counts are unchanged and
  `emptyTravel()` has `[]` (`contracts/bot_api.md` v3.0h). Readers use `.get("placed_cells", [])`, as older bots and
  the simulator omit it. `_merge_travel` concatenates the lists.
- `Action.scaffold` (stub on `main`): `False` on `place` or `clear` sends `"scaffold": []`, so the walk can't
  place anything; `None` sends the usual scaffold blocks.
- `Episode.placed_cells`: every attempt's `travel["placed_cells"]` appended in order, retries included (a failed
  tower attempt is the usual case).
- `build_runner`: the cells recorded from the start of the build phase (after gathering) to the final scan. After
  the final scan matches, each distinct recorded cell outside the site box and every protected box, highest first:
  a 1-cell `scan`; if it still holds the recorded block, `Action("clear", pos=cell, scaffold=False)`. A failed scan
  or clear leaves that cell. `build_done` gains `scaffold_cleared` and `scaffold_left`. The cleanup never changes
  the build's result.
- Gaps: an earlier attempt's pillars (a build requeued or resumed) aren't known to a later attempt (memory only),
  and scaffold placed while gathering (trees, ore) is not cleared.

**Door planks of one species** (`door-species`)

- `build_runner.gather_materials`, between the pursuit of the other needs and the door's: when `#door` is needed and
  no plank species is held 6 times (`DOOR_PLANKS`), pick the species S with the most `planks_S + 4 * logs_S` (ties
  by name). If that reaches 6, craft `S_planks` directly, `times = ceil((6 - planks_S) / 4)` (an `Episode.act`
  step, not evidence for a rule, D1). Otherwise pursue 2 more `#log` and check again, at most 2 rounds; then the
  door is pursued as before and fails with its own reason if it still can't be made.
- Not covered: an `obtain` goal for a door (outside a build).

## v3.1 Goals

Written by S05 for S06 (M1: plan steps S1.2–S1.7 and play mode). The bot side is in `contracts/bot_api.md`, v3.1.
The user's answers: `BOT_OWNERS` is `Otopo`; D2 (panel first, then chat) and D5 ("get me 20" means 20 more) stand;
a play-mode life with nothing queued stands still, and from M2 on the bot's guard defends it.

### v3.1a `GoalSpec` (`models.py`, stub on `main`)

```python
GoalKind = Literal["obtain", "build", "kill", "guard"]
GoalSource = Literal["user", "curriculum", "system"]   # system: hunger (v3.2e), later threats
GoalStatus = Literal["submitted", "queued", "running", "done", "failed", "cancelled", "unsupported", "awaiting_approval"]

@dataclass(frozen=True)
class GoalSpec:
    id: str                   # "u003" user, "s001" system, "g012" curriculum (the life's goal name)
    kind: GoalKind
    source: GoalSource
    text: str = ""            # what the user typed; a short label for system goals ("hunger: 4 bread")
    item: str | None = None   # obtain: normalized name (normalize.family): "#planks", "torch"
    count: int = 1            # obtain: how many more than held (D5); kill: how many
    target: str | None = None # kill: mob type; guard: player name
    blueprint: dict | None = None  # build (M4)

@dataclass(frozen=True)
class Unsupported:
    reply: str                # what the bot says back, at most 240 characters, no "#"
```

Curriculum goals become a `GoalSpec` with `source="curriculum"` and the life's goal name as `id`. They never go
into the queue file, and their `Task` is built exactly as in v2 (`goal_count` is the curriculum's count), so learn
mode and its tests don't change.

`count` means two things (S06): for a curriculum goal it is the count to hold (v2's held + 1); for a user or
system `obtain` goal it is how many more (D5), and the count to hold is the entry's `target_count`.

Statuses and the transitions `GoalQueue` allows (anything else is refused):

| From | To |
|---|---|
| `submitted` (text only, waiting for the life to parse it) | `queued`, `unsupported`, `cancelled` |
| `queued` | `running`, `cancelled`, `awaiting_approval` (M4) |
| `awaiting_approval` (M4: a blueprint waiting for Approve) | `queued`, `cancelled` |
| `running` | `done`, `failed`, `cancelled`, `queued` (preempted, a death, or `--resume`) |
| `done`, `failed`, `cancelled`, `unsupported` | nothing: these are final |

### v3.1b `runs/<life>/goals.jsonl`

- Append-only. A fresh life creates it empty (truncating any old one, as it empties `life.jsonl`); `--resume` keeps it.
  The panel and the life are separate processes and both write to it.
- Each line is the goal's full state after one change:

  ```json
  {"id": "u003", "t": 1760731200.512, "status": "queued", "source": "user", "text": "get me 10 torches",
   "kind": "obtain", "item": "torch", "count": 10, "target": null, "blueprint": null,
   "reason": "", "target_count": null, "deaths": 0}
  ```

  `t` is `time.time()` of the change. `kind`, `item`, `count`, `target` and `blueprint` are `null` until the goal is
  parsed. `reason` is why a goal is `failed`, `cancelled` or `unsupported` (for `unsupported`, the reply).
  `target_count` is the count to hold, fixed the first time an `obtain` goal runs (v3.1d). `deaths` counts the bot's
  deaths during this goal (v3.2d).
- Rebuilding the state: read every line in order and skip any line that isn't valid JSON or has no `id` or `status`
  (a crash mid-write leaves a corrupt last line). The latest valid line per id is that goal's state. Goals are
  ordered by their first line, which is the submission order.
- Writing: open the file in `a+b` mode, take `fcntl.flock(LOCK_EX)`, rebuild the state, check the transition, write
  a `"\n"` first if the file doesn't end with one, append the new line in one `write`, flush, then unlock. Readers
  don't lock.
- Ids: `u` (user) or `s` (system), then a 3-digit number, one more than the highest number with that prefix, chosen
  under the lock.

### v3.1c `goals.py` (stub on `main`)

```python
InterruptReason = Literal["cancelled", "preempted", "death"]

class GoalInterrupted(Exception):
    """Raised by the life's before_step hook: the running goal ends before its next step."""
    def __init__(self, goal_id: str, reason: InterruptReason) -> None

@dataclass(frozen=True)
class GoalEntry:              # one goal as rebuilt from goals.jsonl
    id: str
    status: GoalStatus
    source: GoalSource
    text: str
    t: float                  # time of its latest line
    spec: GoalSpec | None = None   # set once parsed
    reason: str = ""
    target_count: int | None = None
    deaths: int = 0
    def to_json(self) -> dict[str, Any]   # the goals.jsonl line, also served by GET /goals.json

class GoalQueue:
    def __init__(self, path: Path) -> None
    def submit(self, text: str, source: GoalSource = "user") -> str      # new id, status "submitted"
    def accept(self, spec: GoalSpec) -> bool       # submitted -> queued, with the parsed fields; spec.id is the goal's id
    def pending(self) -> list[GoalEntry]           # "submitted" goals, oldest first: what the life must parse
    def next(self) -> GoalSpec | None              # the queued goal to run: system before user, then oldest first
    def mark(self, goal_id: str, status: GoalStatus, **fields: Any) -> bool
    def cancel(self, goal_id: str) -> bool         # -> cancelled, from any status that isn't final
    def get(self, goal_id: str) -> GoalEntry | None
    def goals(self) -> list[GoalEntry]             # every goal, in submission order
    def requeue_running(self) -> list[str]         # --resume: every running goal goes back to queued

def goal_label(spec: GoalSpec | None, fallback: str = "") -> str   # S06: what chat calls a goal
```

- `goal_label` (S06): for an `obtain` spec, the count and the item's display name: "10 torches", "4 sticks",
  "1 iron ingot", "2 iron ingots", "4 planks", "1 plank", "3 coal", "2 glass", "1 shears". The name drops `#` and
  turns `_` into spaces. A count above 1 takes the plural (`+es` after `ch`, `sh`, `x`; nothing after a final `s`;
  nothing for a few uncountable names such as coal, charcoal, redstone, glass, sand, gravel, dirt, cobblestone,
  stone, flint; else `+s`), and a count of 1 takes the singular (a group name loses its final `s`: `#planks` →
  "plank"). For no spec, or a kind other than `obtain`, it is `fallback` (the typed text) or else `spec.text`.
- `mark` and `cancel` return `False`, and write nothing, for an unknown id or a transition the table refuses (a goal
  the panel cancelled stays cancelled when the life later tries to mark it `done`). `mark` takes only the fields
  `reason`, `target_count` and `deaths`; any other name raises `ValueError`.
- `next()` and `pending()` don't write. The life marks the goal `running` itself.
- A system goal goes in with `submit(text, "system")` and then `accept(spec)`, both from the life, so nothing ever
  parses it.

### v3.1d The life takes goals from the queue (`life.py`, `demo.py`)

- `LifeConfig.mode: Literal["learn", "play"] = "learn"`, and `--mode {learn,play}` on `schema-voyager life`
  (default `learn`). In play mode the curriculum never picks a goal: no exploration rounds, no `empty_frontier`.
- `_Life` holds `self.queue = GoalQueue(run_dir / "goals.jsonl")`. On `--resume` it calls `requeue_running()`.
- Each pass of `_Life.run`, after the existing stop checks:
  1. poll the bot's events (below) and handle chat commands (v3.1g);
  2. parse every `pending()` goal with `llm.parse_goal` (v3.1e), with `known_items=vanilla_items()` and the last
     inventory the life saw: `accept` it, or `mark(id, "unsupported", reason=reply)` and say the reply. Say
     "Queued u003: 10 torches" only if `accept` returned `True` (the panel may have cancelled it meanwhile). A
     parse that raises `LLMError` (`SpendCapReached` is one) leaves the goal `submitted` and the pass goes on; the
     stop checks then see `spend_cap_hit`;
  3. pick the goal: `queue.next()` (system, then user), else in learn mode the curriculum as now. When there is a
     goal to run (or in learn mode), `_learn_priors()` and `_repair_dead_ingredients()` run first as now;
  4. otherwise (play mode, nothing queued) idle: sleep `LifeConfig.idle_poll_s` (2.0) and start the next pass. An
     idle pass calls only `backend.status()` and reads the queue (steps 1–2 and, from M2, the hunger check). It
     never calls `observe` (that takes the bot's lock), and it keeps checking the wall clock and the spend cap.
- `_run_goal(spec)`. Every source gets a curriculum `Goal` next to its spec, because `_goal_record` and
  `Curriculum.record` read `plan_length` and `reason`; a user or system goal's `Goal` is built by the life. For a
  queued goal, after `begin_episode` and the junk toss, it does one `mark`: the first time the goal runs,
  `mark(id, "running", target_count=held + spec.count)` (D5), held being `normalize.counts` of that observation;
  a goal that runs again (after a preemption or a resume) keeps its `target_count` and does `mark(id, "running")`.
  If `mark` returns `False` (cancelled meanwhile), the goal doesn't run and uses no goal index.
- A user or system `obtain` goal's `Task` is the v2 one with `goal_count = target_count`. Its plan (for the budget
  and the clock) is `make_plan(item, target_count, held, usable rules minus _excluded_rules(), store.skill_for,
  visible=nearby)`, and `run_schemavoyager` gets the same `excluded` and `goal=spec`. Budget:
  `max(MIN_GOAL_BUDGET, BUDGET_PER_PLAN_STEP * plan_length)`. Goal clock: `max(goal_wall_clock_s,
  GOAL_SECONDS_PER_PLAN_STEP * plan_length)` with `GOAL_SECONDS_PER_PLAN_STEP = 30`, capped by the life's remaining
  time; `life_clock_limits_goal` compares the remaining time with this clock. On `PlanningError` the episode still
  runs (plan length 0) so the trace shows `plan_failed`, and the goal ends `failed` "no plan".
- When it ends: `done` on success; `failed` with a `reason` (at most 80 characters), the first that applies:
  1. "no plan": the last event before `episode_end` is `plan_failed` (a failed revision also logs `plan_failed`,
     but the loop goes on after it);
  2. the last step failed with `not_found`, `unreachable` or `hazard`: "<error>: <target>" in words ("not found:
     iron ore");
  3. "out of time" (the episode's `out_of_time`, read when the loop ends), then "out of steps" (no budget left);
  4. the last failed step's "<error>: <target>"; else "failed".

  A failed user goal isn't skipped for the rest of the life. User and system goals never feed `Curriculum.record`.
  A user goal cut short by the life's own clock (v2's `interrupted`) goes back to `queued`, with `interrupt:
  "wall_clock"` in the record, so `--resume` runs it again and the panel doesn't show it `running` after the life.
- **One ending.** A goal ends one of six ways: done, failed, no plan, cancelled, preempted, cut short by the life's
  clock (S07 adds a death). Each does exactly once, in one place (`_end_goal`): the queue mark, the life record
  (with `interrupt` for an interrupted goal), `goals_done`, the `blocked` count (not for an interrupted goal),
  `Curriculum.record` (curriculum goals only, not interrupted) and the chat line (v3.1h).
- In play mode there are no exploration rounds, after any goal.
- `_unasked_families()` also returns the items of `queued` and `running` obtain goals (minus `self.asked`), so the
  life asks Claude for prior rules about an item it has never seen. In play mode the curriculum frontier part is
  left out.
- The junk toss at the start of a goal (v3.0c) gets as `wanted` the running goal's item plus the items of every
  `queued` obtain goal and every `done` user obtain goal, so the bot never drops what a queued goal asks for or
  what the user already asked for (S06, orchestrator: torches made for the user aren't junk the next time the bot
  is nearly full). `wanted` grows over the life, so a toss may then free fewer than `min_free` slots. From M2 (S07 `death-handling`), `wanted`
  also holds every edible food (v3.2e), so the toss never drops what auto-eat needs.
- **Cancel and preemption.** `_Life` composes its own check in front of `hooks.before_step`. Before each step it:
  1. polls the bot's events and handles chat commands, then parses pending goals, catching `LLMError` and
     `BackendError` as in the pass above (so `!cancel` and a new goal are seen mid-goal);
  2. raises `GoalInterrupted(id, "cancelled")` if the running goal's status is now `cancelled`;
  3. raises `GoalInterrupted(id, "death")` if the bot died since the goal started (v3.2d);
  4. raises `GoalInterrupted(id, "preempted")` if a queued goal outranks the running one (system over user, user
     over curriculum);
  5. then calls the original `hooks.before_step` (the `ChangeTrigger`). It runs last, so a hidden change is never
     switched on for a step that doesn't run.

  The hook runs only between plan steps. One step (an explore, a walk, `Episode.act`'s retries) can take a minute
  or more live, so a cancel takes effect when the current step ends.
- `_run_goal` catches `GoalInterrupted` (`run_schemavoyager` doesn't, and `agent.py` isn't changed for it):
  - the episode log gets `goal_interrupted {"goal_id", "reason"}`;
  - it then does what `run_schemavoyager` does at a normal end: `episode.finish(success=False)`, `store.certify()`
    and the `certified` event. No skill is compiled;
  - the life record's `outcome` is `"interrupted"`, and the goal's `episode.blocked_rules` don't count toward
    `_Life.blocked` (v3.0a);
  - the queue: `cancelled` stays; `preempted` goes back to `queued`; for `death` see v3.2d;
  - the goal isn't counted as a failure, and a curriculum goal isn't recorded in the curriculum.
- **Polling the bot's events.** `self.bot_seq` starts at `backend.status().last_seq` (fresh or resumed), so events
  from before the life are skipped. A poll is `backend.status(since=self.bot_seq)`, then
  `self.bot_seq = status.last_seq`. If `status.last_seq < self.bot_seq` the bot restarted: poll again with
  `since=0`. Events are handled in seq order: `chat_command` (v3.1g) and, from M2, `death` (v3.2d); others are
  ignored. Outside a bot restart, an event with `seq <= self.bot_seq` is skipped. A `chat_command` is handled only from this poll, never from an action response's `events`, so it is
  never handled twice. A `BackendError` from the poll is logged and the pass goes on.
- Stop reasons: `max_goals` (counts every goal run, whatever its source), `spend_cap`, `wall_clock`,
  `empty_frontier` (learn mode only), and `deaths` (v3.2d).
- Tests use `--llm fake` in the simulator. They inject `chat_command` events through the simulator's own
  `world._emit({...})` (from a test double around `NaturalWorld.status` or a hook), so seqs stay consecutive; a
  made-up seq above the world's would look like a bot restart. S06 doesn't edit `environment.py` (S07's
  `sim-threats` lane owns it).

### v3.1e Parsing a goal (`llm.py`, `data/items_1_21_1.json`)

```python
SUPPORTED_GOAL_KINDS: frozenset[GoalKind] = frozenset({"obtain"})  # M3 adds kill and guard, M4 build
MAX_GOAL_COUNT = 256

def vanilla_items() -> frozenset[str]   # every item id in data/items_1_21_1.json, read once
def vanilla_foods() -> dict[str, int]   # food item -> food points, from the same file (v3.2e)

class LLM:
    def parse_goal(self, text: str, inventory: dict[str, int], known_items: frozenset[str], *,
                   goal_id: str) -> GoalSpec | Unsupported
```

- Live and replay go through `_json_call("parse_goal", system, user, PARSE_GOAL_SCHEMA)`, so replay and the spend
  cap work as for every other call. The prompt gets the text and the inventory, not the item list.

  ```python
  PARSE_GOAL_SCHEMA = {
      "type": "object",
      "properties": {
          "kind": {"type": "string", "enum": ["obtain", "build", "kill", "guard", "unsupported"]},
          "item": {"type": "string"},    # obtain: a vanilla item id, or "#planks" / "#log" for any wood; else ""
          "count": {"type": "integer"},  # obtain: how many more (D5); kill: how many; else 1
          "target": {"type": "string"},  # kill: mob id; guard: player name; else ""
          "reply": {"type": "string"},   # unsupported: a short, friendly reply saying what it can do; else ""
      },
      "required": ["kind", "item", "count", "target", "reply"],
      "additionalProperties": False,
  }
  ```

- Fake mode (no call; `_meter_fake("parse_goal", text)`): lowercase the text. If its first word is `build`,
  `kill` or `guard`, that is the kind and the rest is the target. Otherwise it's `obtain`, with
  `re.search(r"(\d+)?\s*([a-z_]+)\s*$", text)`: the number is the count (1 if absent), and the item is the first of
  `word`, `word[:-1]`, `word[:-2]`, `"#" + word`, `"#" + word[:-1]` that is a known item or a group
  ("torches" → `torch`, "sticks" → `stick`, "planks" → `#planks`, "logs" → `#log`).
- Every mode, after the model or the regex, in this order:
  1. kind `unsupported`: `Unsupported(reply)`, with a default reply if it's empty;
  2. a kind not in `SUPPORTED_GOAL_KINDS`: `Unsupported` with a fixed reply for that kind, for example "I can't
     build yet. I can fetch items: try \"get me 10 torches\".";
  3. `obtain`: the item must be in `known_items` or be a group name from `normalize` (`#planks`, `#log`, ...),
     else `Unsupported("I don't know an item called <item>.")`. Then `item = normalize.family(item)`;
  4. the count: below 1 becomes 1; above `MAX_GOAL_COUNT` is `Unsupported("That's more than I can carry. Ask for
     256 or fewer.")`.
- The result is `GoalSpec(id=goal_id, source="user", text=text, ...)`.
- `src/schema_voyager/data/items_1_21_1.json`, written once by `scripts/export-items.js` from minecraft-data
  (version 1.21.1, read from `mc/bot/node_modules`) and committed:
  `{"version": "1.21.1", "minecraft_data": "3.117.0", "items": ["acacia_boat", ...], "foods": {"apple": 4, ...}}`.
  `items` is every item id, sorted. `foods` maps each food item to its food points (S07's hunger goal reads it
  through `vanilla_foods()`).

### v3.1f Panel routes (`panel.py`)

The panel only appends to `goals.jsonl` through `GoalQueue`. It never calls Claude and needs no API key. It still
binds 127.0.0.1 only, since anyone who can reach it can steer the bot.

- Which life: the `run` field of the body (or `?run=` on GET), a directory name under `runs/` matching
  `[A-Za-z0-9._-]+`. Without it, the run whose `goals.jsonl` was modified last. No such run: `404
  {"ok": false, "error": "no_live_run"}`.
- POST routes accept only the media type `application/json` (the `Content-Type` before any `;`, so
  `application/json; charset=utf-8` is fine; otherwise 415). A request with an `Origin` header other than the
  panel's own (`http://127.0.0.1:<port>` or `http://localhost:<port>`) gets 403; one without `Origin` (curl, tests)
  is allowed. A browser page on another site therefore can't post goals.
- `POST /goals {"text": "get me 10 torches", "run"?}`: `text` stripped, 1 to 200 characters, else `400
  {"ok": false, "error": "bad_request", "detail": "..."}`. It calls `submit(text, "user")` and answers `201
  {"ok": true, "id": "u003", "run": "<run>", "status": "submitted"}`.
- `GET /goals.json?run=<run>`: `200 {"run": "<run>" | null, "goals": [GoalEntry.to_json(), ...]}` in submission
  order. With no run: `{"run": null, "goals": []}`.
- `POST /goals/<id>/cancel {"run"?}`: `200 {"ok": true, "id": "u003", "status": "cancelled"}`; unknown id 404;
  a final goal `409 {"ok": false, "error": "not_cancellable", "status": "done"}`.
- Other POST paths are 404. `do_HEAD` stays 405.
- The page (`site/dist/goals.js`) polls `GET /goals.json` every 2 s and shows a chip per status. `submitted` and
  `queued` both show as "queued". Cancel is shown on goals that are `submitted`, `queued`, `running` or
  `awaiting_approval`.
- Port: 8765 belongs to the v1 demo, and 8766 is the usual v2 trace panel (screen `sv2-panel`, running old code).
  A live check starts its own panel from its worktree on a free port. The bot's CORS already allows any localhost
  port.

### v3.1g Chat commands (life side)

The bot records `chat_command {"player", "command", "args"}` for owners only (`contracts/bot_api.md`, v3.1). The
life turns it into queue operations and answers through `backend.say`:

| Command | Effect | Reply (examples) |
|---|---|---|
| `!goal <text>` | `submit(text, "user")`, then parse at once (v3.1d) | "Queued u003: 10 torches", or the `Unsupported` reply. Without text: "Usage: !goal <what>, like !goal 10 torches" |
| `!cancel` | cancel the running user goal | "Cancelled u003: 10 torches", or "Nothing to cancel." |
| `!cancel <id>` | cancel that goal | the same, or "No goal u009." |
| `!stop` | cancel every user goal that isn't final | "Stopped: cancelled 2 goals." |
| `!status` | nothing | "Doing u003: 10 torches. Queued: 1." or "Idle." or "Learning: 2 stone pickaxe." |
| anything else | nothing | "Commands: !goal <what>, !cancel [id], !stop, !status" |

The life logs each one as `chat_command` in `life_events.jsonl` (v3.1h). M4 adds `!yes` and `!no`.

### v3.1h Trace, records and narration

- Episode log: the `goal` event gains `goal_id`, `source`, `text` and `goal_kind` (`kind` is already the record's
  own key). Its `count` is the count to hold (the Task's `goal_count`); a user or system goal's event also has
  `asked`, its `spec.count` (how many more). New event `goal_interrupted {"goal_id", "reason"}`, where reason is
  `cancelled`, `preempted` or `death`.
- `life.jsonl` records gain `goal_id`, `source`, `text` and `goal_kind`. An `interrupted` record also has
  `interrupt`: `wall_clock` (v2's case), `cancelled`, `preempted` or `death`. Readers treat a record without
  `source` as a curriculum goal.
- New `runs/<life>/life_events.jsonl`: events outside any episode, in `EpisodeLog`'s line format
  (`{"t", "kind", ...}`). A fresh life empties it and `--resume` appends to it. `EpisodeLog` empties its file when
  it opens it, so the life writes this one with its own small appender. Kinds: `chat_command {"player", "command", "args"}`, `goal_parsed {"goal_id",
  "goal_kind", "item", "count", "target"}`, `goal_unsupported {"goal_id", "reply"}`, and from M2 `death` and
  `hungry` (v3.2). The panel doesn't serve it.
- Chat, at most 3 messages of 240 characters with no `#` (display names drop `#` and `_`), with `goal_label`
  (v3.1c) for the goal:
  - `agent.py` (S06 `goal-narration`), when a user goal's first plan is made: "On it: 10 torches (7 steps)", in
    place of v2's "Goal: 28 torch (...)". `run_schemavoyager(..., goal: GoalSpec | None = None)` gets the spec from
    the life (`None` for curriculum goals, which narrate as in v2);
  - `life.py` (S06 `life-queue`), when the goal ends: "Done: 10 torches", "Couldn't get 10 torches: <reason>",
    "Cancelled: 10 torches", and a preemption: "Pausing 10 torches for hunger: 4 bread";
  - `Episode.finish(success=None, announce=True)` (stub on `main`, S06): `announce=False` skips v2's "Goal reached:
    28 torch." `run_schemavoyager` passes `announce=goal is None`, so a user goal says "Done: 10 torches" once.
- The panel's goal card shows "Your goal: get me 10 torches" for `source: user`.

### v3.1i Who owns what (S06 and S07 run at the same time)

| File | Owner | Note |
|---|---|---|
| `models.py` | S05 stubs on `main` | Neither session edits it. A missing field goes on `main` as its own commit first |
| `goals.py` | S06 `goal-queue` | S07 only calls `submit`, `accept`, `mark` and `GoalInterrupted` |
| `llm.py`, `data/items_1_21_1.json`, `scripts/export-items.js` | S06 `goal-parse` | S07 reads `foods` |
| `life.py` | S06 `life-queue`, then S07 `death-handling` | One after the other: `death-handling` starts after `life-queue` is merged |
| `agent.py` | S06 `goal-narration` | The `interrupted` line in `_NEVER_BLOCKED_ERRORS` is S05's stub; the `goal` keyword of `run_schemavoyager` is S06's stub |
| `episode.py` | S05 stub, then S07 `python-survival` | The stub adds `interrupted` to `RETRIED_ERRORS`; S07 makes that retry skip the explore (v3.2b). S06's stub adds `finish(announce=)`, far from S07's edits in `act` |
| `panel.py`, `trace.py`, `site/dist/*` | S06 | |
| `mineflayer_backend.py`, `environment.py`, `curriculum.py` | S07 | S06 uses the existing `status(since)` |
| `mc/bot/server.js` | both, in separate places | S06 adds one line, `attachChatCommands(current, eventLog)`, right after the `current.on('error', ...)` listener in `connect()`. S07 adds its plugin loading right after `current.loadPlugin(pathfinder)`, the pvp movements inside `once('spawn')`, and everything else. Neither touches the `config` block: each lib module reads its own env var |
| `mc/bot/lib/events.js` | S07 | S06 calls the existing `eventLog.record` |
| `scripts/v2-up.sh` | S06 `chat-bot` | Only the bot's command line gains `BOT_OWNERS='${BOT_OWNERS:-Otopo}'` |
| `scripts/v2-env.sh` | S07 `difficulty-env` | All server settings go in `sv2_apply_gamerules` and the `server.properties` writer, so S07 never edits `v2-up.sh` (v3.2f) |
| `mc/bot/lib/smelt.js` | S07 `guard-wire` | The abort check and the furnace drain (v3.2c) |

S04 (running at the same time) also edits `observe()` in `server.js`, `mineflayer_backend.py`, `environment.py` and
`life.py`. Whoever merges second rebases.

## v3.2 Survival

Written by S05 for S07 (M2: plan steps S2.1–S2.8). The bot side (guard, entities, events, plugins) is in
`contracts/bot_api.md`, v3.2.

### v3.2a The bot sees creatures

- `Observation.entities: dict[str, dict] = field(default_factory=dict)` (`models.py`, stub on `main`): entity
  type → `{"count": int, "nearest": float, "kind": str}`, parsed from `/observe`'s `nearby_entities`. The
  simulator's `observe()` fills it too.

### v3.2b Error codes and `409 busy`

- `interrupted`: the guard cut into an action to fight or flee (`detail: {"threat", "outcome"}`). It is in
  `RETRIED_ERRORS` (`episode.py`, stub on `main`), so the step is retried. S07's `python-survival` lane makes
  `Episode.act` retry an `interrupted` step in place, without the explore that the other retried errors get: the
  fight already moved the bot, and an explore would walk it away from its furnace or table. Interrupts have a budget
  of their own, `MAX_INTERRUPTS = 4` per step, separate from `MAX_RETRIES` (S07: at night several fights can land
  on one step, and the shared budget of 2 would fail it). The usual `budget_left` and `out_of_time` checks still
  apply.
  **Resting after a flight** (S07, from `runs/v3-soak-2`, where in-place retries walked a bot at 4.8 health back
  into a skeleton's arrows twice): after an `interrupted` whose outcome is `fled` and whose events show health below
  10 (the `fled` event's `health`, else the last `damage` event's `health`), `Episode.act` doesn't retry at once. If
  `backend.status().food` is 18 or more (health only regenerates then), it polls `status()` every 2 s until health is
  14 or more, at most 60 s, then retries in place. With food below 18 it can't heal, so the step ends `interrupted`
  with no more in-place retries.
  It is never evidence:
  - a step whose last attempt is `interrupted` is a `failed` check, which records no transition;
  - each retry is judged only on its own response window;
  - `interrupted` is in `_NEVER_BLOCKED_ERRORS` (`agent.py`, stub on `main`), so fights never block a rule (v3.0a);
  - it isn't in `MISSING_RESOURCE_ERRORS`, so it never starts an exploration round.
- A death mid-action still answers `quarantined`, as in v2, even when it happens during the guard's fight.
- `409 {"ok": false, "error": "busy"}`: an action is running, the guard is fighting while the bot is idle
  (`detail: "guard"`), or an aborted handler hasn't settled yet (`detail: "orphan"`).
  `MineflayerBackend._post` waits and retries: after 0.25 s, then every 0.5 s, until 60 s after the first try (a
  longer wait could keep missing the guard's 1 s gaps between fights). After that it raises `BackendError` as now.
  `REQUEST_TIMEOUT_S` goes from 150 to 180 s, because an interrupted action can take its own 120 s, then up to 10 s
  for the handler to settle and 30 s for the fight. `say` (`/chat`) is never retried: a busy bot
  drops the message. `/status` and `/guard` are outside the lock and never answer 409.

### v3.2c Fights and evidence

The guard must never turn a fight into evidence. These rules hold (S07 tests the bot-side ones with a fake bot):

1. **An aborted handler never runs alongside another action.** On a guard abort the wrapper stops the bot's pathing
   and digging (not its window: `contracts/bot_api.md`, v3.2, "Cutting into an action") and aborts `session.signal`
   (an `AbortSignal` that every handler gets). Every handler that walks or waits checks it at its choke points and
   throws (S07 `handler-abort`; in v2 none of them stopped: a cancelled walk looked like `unreachable`, so `/mine`
   walked to the next candidate and remembered a good block as unreachable). It waits for the handler to settle, at
   most 10 s, before the guard fights; past 10 s it logs `orphaned_handler {"path"}` and fights anyway. `busy` is
   released only once the handler has settled, however long that takes: until then every request gets `409
   {"error": "busy", "detail": "orphan"}`, even after the response was sent. A timeout and a death release `busy`
   the same way (in v2, `busy` was released while an aborted handler could still run, and a late
   `retrieveStation` fed its pickup into the next action's own drops). `/smelt` checks the signal in its furnace
   wait loop and before it retrieves the furnace. On an abort it stops there and leaves the furnace standing.
   Backstop: a handler still unsettled 45 s after the abort makes the bot reconnect (`bot.quit()`, then the
   existing reconnect). The wrapper records `orphaned_handler {"path", "reconnected": true}` and releases `busy`.
   The old handler holds the old bot object, so it can't touch the new one, and Python's 60 s of 409 retries end
   before it gives up.
2. **No item is left in a container for a later step to count.** On a guard abort `/smelt` takes its items back out
   of the furnace and closes the window itself. A timeout or a death closes the window first (`interruptBot()`), so
   the items stay in the furnace. Before its shortfall check, `/smelt` drains a placed furnace it
   is about to use: anything in it goes back into the inventory, and the step records `interference {"block":
   "furnace", "position"}`, so that step is quarantined and the next one is clean. Draining first also gives the
   in-place retry its ingredients back. A craft already clears the grids before it measures
   `inventory_before_step`.
3. **While an action holds the lock, the survival plugins click nothing.**
   - `await survival.hold()` (S07 changed `onActionStart`/`onActionEnd` to a counted hold that returns `release()`)
     disables auto-eat and waits until no eat and no `equipAll()` is in progress (at most the plugin's
     `eatingTimeout`, 3 s) before the handler runs. The guard's idle fight holds them too.
   - auto-eat runs with `equipOldItem: false`, so it never swaps the held item back later, and `offhand: false`, so
     it never pushes the shield out.
   - armor-manager's own `playerCollect` listener is removed. `equipAll()` runs on the last `release()` and on idle
     ticks instead, and so does `bot.autoEat.eat()`, since an eat trigger that came during a hold is dropped.
   - Eating mid-action would change a craft's delta or swap the tool mid-dig. Eating at low health happens only
     inside the guard's flee, after the action was aborted.
4. **The guard cuts only into `/mine`, `/craft`, `/smelt` and `/explore`** (M3 and M4 add `/attack`, `/place` and
   `/clear`). Every other endpoint that holds the lock (`/observe`, `/chat`, `/look`, `/probe`, `/reset_episode`,
   `/toss`) runs to its end, and the guard acts after it. An interrupted `/observe` would report an empty inventory,
   which would set a wrong D5 target.
5. **The inventory counts armor and off-hand slots** (window slots 5..45, already so in `inventoryCounts`), so
   putting armor on or holding a shield in the off-hand changes no count.
6. **Fights while idle (between steps) happen outside every action's event window.** A craft or smelt measures
   `inventory_before_step` inside its own call, and a mine its drops per cell. What changes between steps (pickups,
   position, health) is never evidence.
7. **Kills by the guard are never kill-rule evidence (D1).** M3's kill rules come only from `/attack`.
8. **The verifier needs no change.** `quarantined` and `failed` already record nothing, and damage, death and
   `stray_pickup` in a step window already quarantine.

### v3.2d Deaths (`life.py`, S07 `death-handling`)

- `sv2_apply_gamerules` sets `keepInventory true` on every up, and that stays. A death normally loses no items; it
  moves the bot to the world spawn and refills health and food.
- A `death` event from the poll (v3.1d) does three things:
  - it adds `time.time()` to `self.deaths`, saved in `life_state.json` under `"deaths"` (read with
    `state.get("deaths", [])`);
  - it logs `death {"goal_id" | null, "position", "inventory_lost"}` in `life_events.jsonl`. `inventory_lost` is the
    last inventory the life saw before the death minus the inventory it observes after the respawn (normally `{}`);
  - it marks the running goal, if any, as having died.
- The before_step hook then raises `GoalInterrupted(id, "death")`. When `_run_goal` returns or raises, a goal
  during which the bot died ends `interrupted` with `interrupt: "death"`, whatever the episode returned, so a
  death never ends a goal `failed`. The life then re-observes and plans again from where the bot respawned.
- After a death:
  - a user goal's `deaths` goes up by one. At 2 it is marked `failed` with reason "died twice trying". Otherwise it
    goes back to `queued` and keeps its `target_count`, since the inventory was kept;
  - a system goal is `cancelled` with reason "the bot died", since the respawn refilled food.
- Three deaths within 600 s (counting deaths from before a `--resume`) stop the life with `stop_reason="deaths"`.
  This is checked with the other stop checks and on idle ticks.

### v3.2e The hunger goal (`life.py`)

- Between goals and on idle ticks, read `backend.status().food`. When it is below 8, the bot holds no edible food,
  and no system goal is `queued` or `running`, the life queues a system goal. Edible food is the `foods` of the
  items file minus the auto-eat ban list in v3.2g.
- The food is the first one, highest food points first, that `make_plan` (`planner.py`) can reach with the usable
  rules (minus `excluded`) from the current inventory. The goal is `submit("hunger: 4 <food>", "system")` then
  `accept(GoalSpec(kind="obtain", source="system", item=food, count=4, ...))`.
- A food whose hunger goal ended `failed` isn't chosen again for 900 s (S07: in `runs/v3-soak-1` the planner reached
  `apple` through a "mine leaves → apple" rule, and apples are a rare drop, so the life queued 15 hunger goals in a
  row, 14 failed, about half the life).
- With no such food, which is the normal case until M3's hunting, it logs `hungry {"food"}` in `life_events.jsonl`
  at most once per 300 s and carries on.
- The bot's auto-eat eats the food when food is 14 or less and no action holds the lock. Some may be eaten before
  the goal counts them. The goal then gathers a little more, which is fine.
- M3 makes this `obtain cooked_beef 4`, planned as kill cow then smelt beef.
- **The arming goal** (S07, from `runs/v3-soak-2`, where both deaths came from fights with bare hands). When the poll
  sees a `threat` event while the bot holds no sword or axe (by the last inventory the life saw) and no system goal
  is `queued` or `running`, the life queues `arm: 1 <sword>` (`submit` then `accept`, `kind="obtain"`,
  `source="system"`, `count=1`) for the first of `iron_sword`, `stone_sword`, `wooden_sword` that `make_plan` can
  reach with the usable rules (minus `excluded`). A sword whose arming goal failed is skipped for 900 s, as a hunger
  food is. With none reachable it does nothing. Like any system goal it preempts a running user or curriculum goal at
  its next step, and a death cancels it.

### v3.2f Difficulty and daylight (`scripts/v2-env.sh`, S07 `difficulty-env`)

Both are applied by `sv2_apply_gamerules` in `v2-env.sh`, which `v2-up.sh`, `v2-new-world.sh` and `world-restore.sh`
already call, so S07 doesn't edit `v2-up.sh`.

- `SV2_DIFFICULTY`: `peaceful` (the default, used by the learning demo), `easy`, `normal` or `hard`. The
  `server.properties` writer writes `difficulty=<value>`. Because a world's saved difficulty can win over the file,
  `sv2_apply_gamerules` also sends `difficulty <value>` over RCON.
- `SV2_DAYLIGHT`: `off` (the default, today's behavior: `gamerule doDaylightCycle false` and `time set day` on every
  up) or `on` (`gamerule doDaylightCycle true`). It is applied on every up, because a gamerule persists in the world.
- Both values are checked when `v2-env.sh` is sourced, so any other value makes the script exit with an error
  before it starts anything (`sv2_apply_gamerules` only runs once the server is up).
- The other gamerules (`keepInventory true`, no weather, `spawnRadius 0`) don't change.
- S07's survival checks and soak use `SV2_DIFFICULTY=easy SV2_DAYLIGHT=on`.

### v3.2g Plugins (exact versions; S07 installs these)

Checked on 2026-10-17 against mineflayer 4.39.0, mineflayer-pathfinder 2.4.5 and Node 26.5.0. A lockfile-only
resolve in a scratch directory gave no peer warnings and a single copy of mineflayer-pathfinder.

| Package | Version | Module | Dependencies | Notes |
|---|---|---|---|---|
| `mineflayer-pvp` | `1.3.2` (latest, 2022-07-03) | CommonJS, `require('mineflayer-pvp').plugin` | mineflayer ^4.0.0, mineflayer-pathfinder ^2.0.0, mineflayer-utils ^0.1.4 | Unmaintained since 2022. mineflayer-utils nests an older mineflayer of its own, which is unused (it only uses `TaskQueue`) but adds install size |
| `mineflayer-armor-manager` | `2.0.1` (latest, 2023-07-01) | CommonJS, `require('mineflayer-armor-manager')` | minecraft-data ^3.38.0; peer mineflayer ^4.10.0 | Equips on the bot's own pickup (`playerCollect`) and on `bot.armorManager.equipAll()`. No options |
| `mineflayer-auto-eat` | `3.3.6` (2023-04-22, the last CommonJS release) | CommonJS, `require('mineflayer-auto-eat').plugin` | none | 4.0.0 and later are ESM-only with another API; 5.0.3 is the latest. Not taken |

- Load order: `pathfinder` first (pvp throws without it), then pvp, armor-manager and auto-eat.
- **pvp and movements.** pvp builds its own default `Movements` (which may dig and place), and every `attack()`
  installs `bot.pvp.movements` on the pathfinder. Set `bot.pvp.movements` to the bot's `worldPreservingMovements`
  on every spawn, right after `setMovements`. Otherwise the first fight leaves the bot pathing with digging on. Its
  `stop()` doesn't restore the previous goal or movements: after a fight, set the movements back and clear the
  goal. Its `GoalFollow` stays until `stop()`, so a fight ends with `stop()` before any other pathing, and rule 1 of
  v3.2c keeps a handler from pathing during a fight.
- **auto-eat 3.3.6.** Options are in `bot.autoEat.options`. Set `startAt: 14` (it eats at `food <= startAt`). Keep
  the default `bannedFood`, which is the ban list Python mirrors: `pufferfish`, `spider_eye`, `poisonous_potato`,
  `rotten_flesh`, `chorus_fruit`, `chicken`, `suspicious_stew`, `golden_apple`. It eats on `health` events and on
  pickup, so `disable()` / `enable()` follow the lock, and `equipOldItem` is `false` (v3.2c, rule 3). Check in its
  source how to tell that an eat is in progress. Events: `autoeat_started`, `autoeat_finished`, `autoeat_error`.
  Always add an `autoeat_error` listener.
- **armor-manager 2.0.1.** It equips on the bot's own pickup (its `playerCollect` listener, which S07 removes) or
  on `equipAll()`. `equipAll()` runs in `onActionEnd` and on idle ticks, which also covers crafted armor, since that
  is never picked up (v3.2c, rule 3).

### v3.2h Who owns what

S07's lanes own the files listed in its prompt. The shared seams are in the v3.1i table: `models.py` (stubs),
`life.py` (after S06's `life-queue`), `server.js` (separate places in `connect()`) and the goal queue (S07 calls
`submit`, `accept` and `mark` only). Some lanes need more than the S06 and S07 prompts list. S05 asked the
orchestrator to add these:

- S06 `chat-bot`: `scripts/v2-up.sh`, only the bot's command line (`BOT_OWNERS`).
- S07 `guard-wire`: `mc/bot/lib/smelt.js`, for the abort check and the furnace drain (v3.2c, rules 1 and 2).
- S07 `python-survival`: `src/schema_voyager/episode.py`, only the retry of `interrupted` in place, and
  `REQUEST_TIMEOUT_S = 180` in `mineflayer_backend.py` (v3.2b).
- S07 `survival-plugins`: the lock rule (v3.2c, rule 3) replaces "never while a window is open or a dig is in
  progress". `attachSurvival(bot)` returns `{onActionStart, onActionEnd}`, and `guard-wire` calls them from the
  `action()` wrapper.
- S07 `difficulty-env`: the settings go in `sv2_apply_gamerules` and the properties writer (v3.2f). `SV2_DAYLIGHT`
  defaults to `off`.
- S07 `death-handling`: a system goal is cancelled on a death, and a user goal keeps its `target_count` (v3.2d).
  Edible foods join the junk toss's `wanted` (v3.1d).
- S07 `guard-wire`: the 45 s reconnect backstop for a hung handler (v3.2c, rule 1).

S07 re-split two lanes (2026-10-18), after finding that no handler stops after `interruptBot()`:

- `handler-abort` (new): `session.signal` through `lib/world.js`, `mine.js`, `explore.js`, `craft.js` and
  `station.js`, and the memory of stations left standing.
- `smelt-abort` (new, after `handler-abort`): `lib/smelt.js`, which therefore leaves `guard-wire`: the abort in the
  furnace loop (take the items back, close, throw), the furnace drain and `interference`.

### v3.2i The simulator (`environment.py`, S07 `sim-threats`)

- `NaturalWorld(seed, *, world_rules=(), species=None, difficulty="peaceful")`. On `peaceful` nothing below
  happens, and every existing seeded result stays the same: threats and food roll on their own seeded streams,
  never on the damage stream.
- On `easy`, `normal` or `hard`, each `mine`, `craft`, `smelt` and `explore` may be cut into by a threat. It then
  answers `interrupted` with `detail {"threat", "outcome"}` and the inventory unchanged, and its `events` carry
  `threat`, `fight_start`, `damage` (phase `step`) and `fight_end`, shaped as in `contracts/bot_api.md` v3.2.
- Food falls during actions (never on `peaceful`). The simulator eats held edible food at 14 or less, between
  actions, and emits `ate`. `status().food` reports it.
- `observe()` fills `Observation.entities`.
- `die()` is public: the bot dies now. It emits `death` then `respawn`, moves the bot to the spawn, refills health
  and food, and keeps the inventory (`keepInventory`). A fight that takes health to 0 calls it, and that action
  answers `quarantined`. Tests use it to put a death where they want one.

## v3.3 Kill rules, `/attack`, `/follow`

Written by S08 (M3: plan steps S3.1–S3.4; duels are S11). The bot side is in `contracts/bot_api.md`, v3.3.
Decision D1 holds: combat is a built-in behavior, and only mob drops become rules.

### v3.3a Kill actions and kill rules (`models.py`, stubs on `main`)

- `ActionKind` gains `"kill"`. `Action("kill", target="cow", tool="stone_sword" | None)`:
  - `target` is a mob type, the entity name (`cow`, `zombie`), never `player`;
  - `tool` is the weapon. `None` means the bot's best held weapon (a sword, else an axe, else a bare hand).
  - `describe()`: `"kill cow with stone_sword"`, or `"kill cow"` when `tool` is `None`.
- A kill rule:

  ```jsonc
  {"id": "kill_cow",
   "action": {"type": "kill", "mob": "cow"},
   "preconditions": {"tool_any": []},            // always [] (vanilla drops don't depend on the weapon)
   "effects": {"killed": true,
               "drops": {"beef": 1},              // what the planner expects per kill
               "allowed": {"beef": [1, 3], "leather": [0, 2]}},  // [min, max] per kill (vanilla, no Looting)
   "check": "stochastic"}
  ```

  - `allowed` holds the drop ranges. `drops` is the low end of each range, for items whose low end is 1 or more.
    Leather (`[0, 2]`) is in `allowed`, but not in `drops`, so the planner never plans leather through a cow.
  - The prompt for S08 asked for `effects.drops` as ranges. That isn't done here: stochastic mine rules already keep
    integer `drops` plus `allowed` ranges, and `Rule.yields()`, `counts()`, the planner and `_items_used` all read
    `drops` as integers.
  - `Rule.subject` is `action["mob"]` for a kill rule, and `Rule.yields()` is `effects["drops"]` for mine and kill
    rules (stubs on `main`).
  - `tool_any` is always `[]`: `_to_rule_v2` forces it. So the planner's kill steps have `tool=None`, and the bot
    fights with its best held weapon.
  - `normalize.family` leaves mob names alone (`family("cow") == "cow"`).

### v3.3b The check (`verifier.py`, `schema_store.py`; lane `kill-rules`)

- `kind_of(rule, action)` is `"stochastic"` for every kill rule, whatever `rule.check` says. No kill rule is ever
  exact, so random drops can't contradict one.
- `predict` for a kill rule: `{"killed": True, "allowed": _allowed(rule)}`.
- `check` for a kill rule:
  - quarantine and `not outcome.ok` work as for any step (`quarantined`, then `failed`);
  - the observation is `{"killed": bool(outcome.detail.get("killed")), "drops": counts(outcome.drops)}`;
  - `killed` false on an ok outcome is a `failed` check ("not killed"), never a mismatch;
  - otherwise it's `stochastic`, with detail "drops in range" or "drops out of range (neutral)";
  - the wrong-tool check stays mine-only: a weapon doesn't change vanilla drops.
- `_in_range` treats `killed` like `block_removed`, so `evaluate` replays a kill transition the same way: `confirm`
  in range, `neutral` otherwise.
- Quarantine events are unchanged: `damage`, `death`, `respawn`, `target_despawned` or `interference` in the step
  phase quarantine a kill. `stray_pickup` doesn't (as with a mine): a kill's drops are measured where the target
  died (`contracts/bot_api.md`, v3.3), not from the inventory.
- `schema_store.applies(rule, action)`: a kill rule applies to a kill action on its mob, if the action's weapon is in
  `tool_any` or `tool_any` is empty. This is the same as a mine rule's tool check.
- `escaped` joins `_NEVER_BLOCKED_ERRORS` (`agent.py`), so a cow that runs away twice doesn't block `kill_cow` for
  the goal (v3.0a).
- So a kill step is always `stochastic`, `failed` or `quarantined`, never `mismatch`. It never demotes a rule, starts
  a revision or opens an epoch. A kill rule is certified like any stochastic rule: no contradictions, plus 2 in-range
  confirmations from 2 episodes.

### v3.3c The planner (`planner.py`, `agent.py`; lane `kill-rules`)

- `_Planner._mine` serves both mine and kill rules: it emits `Action(rule.kind, rule.subject, tool=...)`.
  - The weapon comes from `tool_any`, like a tool: one already held first, else the cheapest to obtain. An empty
    list gives `tool=None`.
  - The number of kills is `ceil(need / drops[item])`. A kill that drops more than its low end makes
    `_deviates` replan, so the plan needs fewer kills.
- `_sightings` for a kill rule is `visible.get(mob, 0)`. `agent.py` merges `observation.entities` counts into
  `visible` (`{"cow": 3}`).
- `agent.py`'s narration and summary helpers (`_revision_description`, `_goal_line`, `_predicts`, `_summarize`)
  and `seed_report.py` handle kill rules ("kill cow"), with no `KeyError` and no craft wording.

### v3.3d Prior rules for mobs (`llm.py`, `src/schema_voyager/data/prior_rules_v2.json`; lane `kill-llm`)

- `propose_prior_rules_v2(families, known_blocks, world_id, mobs: Sequence[str] = ())`.
  - With no mobs, the request (system prompt, user prompt and schema) is byte-identical to today's, so every
    recorded response still replays (`_cache_key` covers all three).
  - With mobs, the prompt asks for one kill rule per mob, using a schema whose `action_type` enum also has `kill`.
    For a kill rule, `subject` is the mob, `consumes`, `station` and `fuel_any` are empty, `check` is
    `"stochastic"`, and `allowed` is required. Its `yields` and `tool_any` are ignored.
  - The prompt says that an item's `min` is the fewest of it one kill always drops (cow: beef 1, leather 0) and
    `max` the most (no Looting).
- `_to_rule_v2` builds kill rules as in v3.3a:
  - `drops` holds the low ends of `allowed` that are 1 or more;
  - with no `allowed`, it uses `{item: [0, n]}` from `yields`, which plans nothing.
- `vanilla_mobs() -> dict[str, str]` (`llm.py`, next to `vanilla_items`): every entity name mapped to its kind
  (`hostile`, `animal`, `mob`, `passive`, `water_creature`, `ambient`, `player`, ...). It reads a new file,
  `src/schema_voyager/data/entities_1_21_1.json`, generated once from `mc/bot/node_modules/minecraft-data` (pc 1.21.1 `entities.json`,
  `name` → `type`).
- Fake priors (`src/schema_voyager/data/prior_rules_v2.json`, read by `_fake_priors_v2`):
  - kill rules with vanilla ranges for `cow` (beef 1-3, leather 0-2), `pig` (porkchop 1-3), `chicken` (chicken 1,
    feather 0-2), `sheep` (mutton 1-2, white_wool 1), `zombie` (rotten_flesh 0-2), `skeleton` (bone 0-2, arrow
    0-2), `spider` (string 0-2, spider_eye 0-1) and `creeper` (gunpowder 0-2);
  - smelt rules `beef → cooked_beef`, `porkchop → cooked_porkchop`, `chicken → cooked_chicken` and
    `mutton → cooked_mutton`, each shaped like the iron smelt rule (station `furnace`, the same `fuel_any`);
  - a kill rule comes in when its mob is asked, or when one of its yields is, as for every fake prior today.
- The v2 revision and repair paths (`_fake_revisions_v2`, `_fake_repairs_v2`, the filters on `rule.subject`) skip
  kill rules without error. A kill rule never mismatches, so nothing revises it.

### v3.3e Backends (`adapter.py` stubs; lanes `kill-backend` and `kill-sim`)

- `LifeBackend.look(blocks, radius=64, mobs=())`: mob hits come back in the same `LookResult`, keyed by the mob
  name, as `{"x", "y", "z", "distance"}`. With no mobs, the request is unchanged.
- `LifeBackend.follow(player: str | None, distance: float = 3.0) -> str | None` starts following and returns the
  name of the player followed, or `None` when that player isn't found. `None` as the argument means the nearest
  `BOT_OWNERS` player in view. `LifeBackend.stop_follow() -> None` ends it.
- `Status.follow: dict | None = None`, set to `{"player", "distance"}` while following (stub on `main`).
- `MineflayerBackend` (lane `kill-backend`):
  - In natural mode, `act(Action("kill"))` calls `_kill_natural`, which sends `POST /attack {"mob", "weapon",
    "max_distance": 32, "protect"}`.
  - The `Outcome` has `ok`, `error`, `drops`, `picked_up`, `inventory_delta = picked_up`, `inventory`,
    `blocks_walked`, `block_removed = False`, `travel` and `events`. `detail` holds `killed`, `mob`, `target`,
    `weapon_used`, `death_position`, `inventory_before_step`, `position` and the bot's `detail`.
  - In non-natural mode a kill answers `Outcome(ok=False, error="unknown_action")`.
  - `look` sends `mobs` only when it's non-empty. `follow` calls `POST /follow`, `stop_follow` calls
    `POST /follow/stop`, and `status()` parses `follow`.

### v3.3f `Episode.act` for a kill (`episode.py`; lane `kill-backend`)

- `not_found` takes the search branch. It explores toward the nearest sighting, as for a natural mine:
  `_explore_toward` asks `look([], LOOK_RADIUS, mobs=[target])`, with the same dead-direction and `up` rules. A mob
  has no "below the surface", so going `up` only happens for a hit more than 4 blocks above.
- `escaped` is retried once in place (another mob of that type is usually close, and the chase already moved the
  bot). It counts toward `MAX_RETRIES`.
- `unreachable` is retried after an explore toward the mob. `timeout` is retried in place. Both count toward
  `MAX_RETRIES`.
- `interrupted` works as in v3.2b. Rest after a flight also covers `/attack`'s own `fled` (`detail.outcome`).
- `MISSING_RESOURCE_ERRORS` doesn't change.

### v3.3g The simulator (`environment.py`; lane `kill-sim`)

- `NaturalWorld` adds `kill`, using the animals `_entities` already places in each region (`cow`, `pig`, `chicken`,
  `sheep`). Hostiles (`zombie`, `skeleton`, `spider`) appear off peaceful only.
  - A kill on a mob that has none left within `MAX_DISTANCE` (the region's count minus the kills made there) answers
    `not_found`.
  - Otherwise the bot travels to the mob (`blocks_walked`, seeded) and kills it. The drops are rolled within the
    vanilla ranges of v3.3d, on a new stream `Random(f"{seed}:kills")`, so existing seeds give the same results.
  - Animals escape with probability 0.05 (`escaped`).
  - A hostile kill costs `FIGHT_DAMAGE` and adds a step-phase `damage` event, so it's quarantined, as on the bot.
  - Events are `fight_start` and `fight_end` (`killed`). `detail` has `killed`, `mob` and `weapon_used`.
- `kill` joins `THREATENED_ACTIONS`.
- `SMELTS` gains beef, porkchop, chicken and mutton → their cooked forms.
- `look(blocks, radius, mobs=())` returns seeded positions for the region's mobs.
- `observe().entities` counts go down after kills.
- `follow(player, distance)` returns `player`, or `"Otopo"` for `None`. `stop_follow()` ends it, and `status().follow`
  reports it. There are no player threats in the simulator.

### v3.3h Food from hunting (`life.py`, `curriculum.py`; lane `hunt-food`)

- `WHITELIST` gains `cooked_beef`, `cooked_porkchop`, `cooked_chicken` and `cooked_mutton`. Two things stay out:
  - leather and leather armor, because a kill's low end of leather is 0, so the planner never reaches it;
  - `bread`, because no rule reaches wheat.
- The hunger goal (v3.2e) ranks the candidate foods by:
  1. whether the plan relies on a stochastic mine rule (any step whose rule is a mine rule and whose `kind_of` is
     `stochastic`, such as apples from leaves), later if it does;
  2. whether the plan kills a mob that hasn't been seen in the last 600 s (by `observed_mobs`), later if it does;
  3. food points, highest first;
  4. the name.
  Cooked beef through kill and smelt then comes before apples. The goal is `hunger: 4 cooked_beef`. The 900 s skip
  after a failure is unchanged.
- **Mobs the bot has seen join the priors.**
  - The names in `observation.entities` whose `kind` is `animal`, `hostile` or `mob`, plus the targets of kill
    goals, are kept in `observed_mobs` in `life_state.json` as `{mob: last seen time}`.
  - Every prior call sends the observed mobs not asked yet as `propose_prior_rules_v2(..., mobs=...)`. A family
    such as `beef` can only come back as a kill rule when its mob is sent: without mobs, the schema has no `kill`.
  - Asked mobs are remembered the way asked families are, so no mob is asked twice. With mobs to ask and no family,
    the life still makes the call.
  - For each kill rule's `allowed` item `x` where `cooked_x` is a vanilla item, `cooked_x` joins the wanted
    families, so its smelt rule gets asked. In play mode nothing else would ask for `cooked_beef`.
- Auto-eat may eat raw meat (it isn't on the ban list) before it's smelted. The plan then deviates and replans with
  more kills. That's accepted.

### v3.3i Kill and guard goals (`llm.py` `parse_goal`, lane `parse-kill-guard`; `goal_runners.py`, lane `goal-runners`)

- **Parsing.**
  - `SUPPORTED_GOAL_KINDS` gains `kill` and `guard`.
  - `kill`: `target` is the mob, singular and checked against `vanilla_mobs()` (`"zombies"` → `zombie`). `count` is
    the number of kills, 1 to 16.
    - A name that isn't a mob gets `Unsupported("I don't know a creature called <x>.")`.
    - `player` gets `Unsupported("I won't fight players.")`.
  - `guard`: `target` is a player name or `"me"`, `count` 1.
  - The fake parse reads "kill 3 zombies" as `kill zombie 3`, "guard me" as `guard me` and "guard Tester" as
    `guard Tester`.
  - `PARSE_GOAL_SYSTEM` and its schema stay byte-identical: they already describe `kill` and `guard`.
- **"me".** When the goal is accepted, the life replaces it in `GoalSpec.target` with the player whose `!goal` chat
  command submitted it. So it lasts across `--resume`. A panel goal gets `None`, and the bot follows the nearest
  `BOT_OWNERS` player it sees.
- The runners live in a new `goal_runners.py`, like `build_runner.py`. `life.py` only dispatches to them, passes
  them bot events, and calls `backend.stop_follow()` at the start of a life.
- **Arming first** (both runners). A kill goal on a mob whose `vanilla_mobs()` kind isn't `animal` or `passive`,
  and every guard goal, first checks the last inventory. If it holds no sword and no axe, the runner pursues the first
  of `ARM_SWORDS` that `make_plan` can reach, inside the goal. If it can't, the goal fails: "I need a sword first."
- **The kill runner.**
  - It repeats `Episode.act(Action("kill", mob, tool=<the best sword or axe held, else None>))` until the kills
    reach `count`.
  - A kill is a `fight_end` event with `target == mob` and `outcome == "killed"` that arrived after the goal
    started. Events come from the step outcomes and from the life's bot poll. Each kill counts once, by the event's
    `id` (the target entity's id): `seq` restarts when the bot does. Kills the guard makes count too, since the bot
    killed the mob; they often arrive inside an `interrupted` act.
  - The goal is `done` at `count` kills. It's `failed` after 3 acts in a row that fail with no new kill (any new
    kill resets the streak), giving the last act's failure reason. It's also `failed` at its clock,
    `max(goal_wall_clock_s, 60 s × count)`. Cancel, death and preemption are checked
    between acts, as for any goal.
  - The runner's acts are not checked against rules (as with build steps): only planned kill steps in obtain goals
    are evidence. The goal record gains `kills`.
- **The guard runner.**
  - It calls `backend.follow(player, 3.0)`. `None` fails the goal: "I can't see <player>."
  - Then, every 2 s, it checks for interrupts and polls the bot, until its clock (`goal_wall_clock_s`).
    - A `follow_end` with reason `died` fails the goal: "<player> died."
    - A `follow_end` with reason `gone` fails it: "I lost sight of <player>."
    - Reaching the clock ends it `done`: the player stayed alive.
  - `!stop` and `!cancel` cancel it like any user goal (v3.1g), and that is how the user ends a guard. The
    cancelled goal's record still gets its `guard` stats.
  - `backend.stop_follow()` always runs at the end, whatever ended the goal: an interrupt, an error or the bot going
    down.
  - The goal record gains `guard {"player", "alive", "duration_s", "fights", "kills"}`.

### v3.3j Guard tactics (`lib/guard.js`; lane `follow-bot`)

S07 already made the unarmed bot flee from every threat (92c5331), and added rest after a flight and the arming goal
(ddcab3a). This section adds the armed tactics:

- Fights sprint. `fight()` paths with a copy of the fight movements that has `allowSprinting = true`, for the
  guard's fights and for `/attack`. Walking otherwise keeps v3.2's rule: sprint on peaceful only.
- Ranged mobs (`skeleton`, `stray`, `bogged`, `pillager`, `witch`) get 8 s out of reach before a fight ends `lost`,
  not 5 s, so the bot closes in on a skeleton that keeps its range.
- Deferred: breaking line of sight.

### v3.3k Fights and evidence (adds to v3.2c)

1. `/attack`'s own fight is the step:
   - damage taken during it quarantines the kill;
   - drops count only where the target died, within 0.5 s before and 1.5 s after its death;
   - another living entity dying within 4 blocks in that window is `interference`, which also quarantines.
2. The guard cutting into `/attack` for another threat makes the step `interrupted`, a `failed` check. `/attack`'s
   own target is never the guard's threat while `/attack` runs.
3. Kills by the guard, idle or cutting in, are never evidence (v3.2c rule 7). They count only toward a kill goal.
4. Acts by the kill and guard runners aren't checked against rules.
5. A kill rule is never exact (v3.3b). Babies (`/attack` skips them), fire kills (cooked drops) and Looting can't
   contradict one; at worst they're `neutral`.

### v3.3l Who owns what (S08)

| Group | Lane | Files |
|---|---|---|
| 1 | `attack-bot` | `mc/bot/lib/attack.js` (new), `mc/bot/server.js` (`/attack` route and validator, the `/look` validator), `mc/bot/lib/targets.js`, `mc/bot/lib/guard.js` (`setQuarry`, the `fight()` options `flee` and `sprint`, `id` on `fight_end`), `mc/bot/test/unit/attack.test.js` |
| 1 | `kill-rules` | `planner.py`, `verifier.py`, `schema_store.py`, `agent.py`, `seed_report.py`, `tests/test_v3_kill_rules.py` |
| 1 | `kill-sim` | `environment.py`, `tests/test_v3_kill_sim.py` |
| 1 | `kill-llm` | `llm.py` (priors, `vanilla_mobs`), `src/schema_voyager/data/prior_rules_v2.json`, `src/schema_voyager/data/entities_1_21_1.json` (new), `tests/test_v3_kill_llm.py` |
| 2 | `kill-backend` | `mineflayer_backend.py`, `episode.py`, `tests/test_v3_kill_backend.py` |
| 2 | `follow-bot` | `mc/bot/lib/follow.js` (new), `mc/bot/server.js` (the two routes, `/status.follow`, `/reset_episode` ends following), `mc/bot/lib/guard.js` (the followed player's threats, v3.3j tactics), `mc/bot/test/unit/follow.test.js` |
| 2 | `parse-kill-guard` | `llm.py` (`parse_goal`, `_checked_goal`, the fake parse), `tests/test_v3_parse_kill_guard.py` |
| 2 | `hunt-food` | `life.py` (hunger ranking, `observed_mobs`, mobs and `cooked_x` in priors), `curriculum.py`, `tests/test_v3_hunt_food.py` |
| 3 | `goal-runners` | `goal_runners.py` (new), `life.py` (dispatch, "me", events to the runners, `stop_follow` at start), `tests/test_v3_goal_runners.py` |
| live | `hunt-live` | `mc/bot/test/v3_hunt_live.js` |

- Deferred: a datapack that changes a mob's loot table, as a new hidden change in `CHANGE_STEPS`. With kill rules
  always stochastic, noticing it needs an out-of-range count, which M3 doesn't keep.

## v3.4 Blueprints and building actions

### v3.4a Blueprints, `place`, `clear` and `scan` (S09: S4.1, S4.2, S4.8)

The parts every build needs. The build goal itself is S10's v3.4b, which changes `matches` for doors (any
wooden door) and adds `Box`/`site_box`. The bot side is
`contracts/bot_api.md` v3.4 (`/place`, `/clear`, `/scan`).

D1 holds: a placed or cleared block is checked exactly (is the cell what we asked for afterwards?) but is never
evidence for a rule. No rule has the kind `place` or `clear`, the LLM's kind enums don't gain them, and S10's
build runner keeps these steps out of `store.record_transition`.

#### The blueprint format (`blueprint.py`, lane `blueprint`)

A blueprint is a JSON object, kept as a plain `dict` in Python. The example house is 7 wide (x), 4 high (y) and
5 deep (z), with log corners, a glass window in three walls, a torch on the inside of the west wall, a door in the
south wall, and a roof whose corners are left as they are:

```json
{
  "name": "small house",
  "size": [7, 4, 5],
  "palette": {"L": "#log", "P": "#planks", "G": "glass", "D": "oak_door", "T": "torch"},
  "layers": [
    ["LPPPPPL", "P.....P", "P.....P", "P.....P", "LPPDPPL"],
    ["LPPGPPL", "PT....P", "G.....G", "P.....P", "LPPDPPL"],
    ["LPPPPPL", "P.....P", "P.....P", "P.....P", "LPPPPPL"],
    [" PPPPP ", "PPPPPPP", "PPPPPPP", "PPPPPPP", " PPPPP "]
  ]
}
```

Its `materials` are `{"#planks": 74, "#log": 12, "glass": 3, "torch": 1, "oak_door": 1}`.

**Axes.** `layers[y][z][x]`: layers go bottom-up, rows go north to south, characters go west to east. The origin
`(ox, oy, oz)` is the world position of `layers[0][0][0]`, the north-west bottom corner, and cell `layers[y][z][x]`
is at `(ox + x, oy + y, oz + z)` (north is -z, east is +x). Layer 0 stands on the ground: S10 picks the origin's y
as the first air layer above the site's surface, so the ground is directly below layer 0.

**Keys.** Exactly these four; any other key is an error.

- `name`: a string of 1 to 60 characters.
- `size`: `[width, height, depth]`, the x, y and z extents: integers with `1 <= width <= 16`, `1 <= height <= 12`,
  `1 <= depth <= 16` (`MAX_SIZE = (16, 12, 16)`).
- `palette`: one-character keys to values. A value is a placeable block or group (below), `"air"` or `"any"`.
  Two keys are built in: `.` is always air and a space is always any. They needn't be listed; if listed, only with
  those values. `any` means "whatever is there": never placed, cleared or checked.
- `layers`: `height` layers; each has `depth` rows; each row is a string of `width` characters, every one a palette
  key or a built-in.

**What the first version places** (`PLACEABLE` in `blueprint.py`, a frozenset; anything else is an error, such as
stairs, slabs, fences, beds, chests, `iron_door`, leaves, and blocks that fall like sand and gravel):

- full blocks (`FULL_BLOCKS`): the members of `#planks`, `#log` and `#cobblestone` (`normalize.members`: the
  logs, woods and stripped ones; `blackstone`, `cobblestone`, `cobbled_deepslate`), `mossy_cobblestone`, `stone`,
  `smooth_stone`, `stone_bricks`, `bricks`, `sandstone`, `polished_andesite`, `polished_diorite`, `polished_granite`.
  Not `dirt`, which turns into `grass_block` and then fails the scan, and not the other scaffold blocks
  (`andesite`, `diorite`, `granite`, `tuff`), which the walks spend. `cobbled_deepslate` is in `#cobblestone` and
  is also scaffold, so S10 budgets the two together;
- `glass` and `glass_pane`;
- doors (`DOORS`): `<species>_door` for each of `normalize.SPECIES`;
- `torch`;
- groups (`GROUPS = ("#planks", "#log", "#cobblestone")`), which mean any member. S10's runner places whichever
  member the bot holds. A group is a full block for the rules below.

A log's axis comes from the face it is placed against; it isn't part of the blueprint and isn't checked.

`REPLACEABLE` (public): the plants a placement replaces, `short_grass`, `tall_grass`, `fern`, `large_fern`,
`dead_bush` and `snow` (the layer). A cell is free for a placement when it is one of `AIR_BLOCKS` or of these. The
bot (`/place`) and the simulator use the same list.

**Claude's form (S10).** Structured output can't describe a map with free keys, so `design_blueprint` asks for the
palette as a list, `[{"key": "P", "block": "#planks"}, ...]`, with `block` an enum of `PLACEABLE`, the groups,
`"air"` and `"any"`. It turns the list into the map before `validate`, and reports a key listed twice as
`"palette: key 'P' is listed twice"`.

**Doors, torches and support.** "A full block" below means a `FULL_BLOCKS` member or a group: not glass, a pane, a
door or a torch.

- A door takes two cells: the same door block in one cell and in the cell straight above it. A vertical run of the
  same door block must be exactly 2 cells. Its lower cell is in layer 0 or has a full block below. The bot places
  the lower cell and the door fills the upper one (`door_tops`).
- A torch is in layer 0 (on the ground), or has a full block below it or beside it (north, south, east or west). It
  never hangs from above. A torch placed against a side is a `wall_torch` in the world; `matches` accepts both.
- Every other placed cell (a full block, glass or a pane) connects to layer 0, face to face, through other full
  blocks, glass or panes. Doors and torches carry nothing. A floating block can't be built: the bot needs a face to
  place against.

**For the builder (S10).** Pathfinder never opens doors here (`canOpenDoors` stays off: it misbehaves on vanilla
servers), and the walks never dig, so a placed door is a wall. Torches go before doors, every door goes last, and
`/place` of a door with a `keep_out` box ends its walk outside that box. During a build the runner sets the
backend's `keep_out` to the blueprint's box (`set_keep_out`, below), so scaffold never lands in a blueprint cell.

**Functions.** All but `validate` assume a valid blueprint. `Pos = tuple[int, int, int]`.

- `validate(bp: Any) -> list[str]`: every error, `[]` when valid. Each message starts with the JSON path it is
  about, so that Claude can fix it (S10 sends them back). At most 20 messages: when there are more, the 20th is
  `"... and N more"`.
  - The shape comes first: the keys, `name`, `size`, the palette, and the layer, row and row-length counts. When
    the shape is wrong, the support and connection checks are skipped, since they would only add noise. Unknown
    characters are always checked, one message per character (naming its first cell).
  - When the layers are a clean box (every layer has the same number of rows, every row the same length) but
    `size` disagrees, there is one message giving the size the layers have, not one per layer or row.
  - The messages:
    - `"blueprint: must be a JSON object"`, `"blueprint: missing key 'size'"`, `"blueprint: unknown key 'notes'"`
    - `"name: must be a string of 1 to 60 characters"`
    - `"size: must be [width, height, depth], three integers"`,
      `"size: [20, 5, 7] is over the limit 16 x 12 x 16"`
    - `"size: says [7, 5, 7] but the layers are 7 wide, 4 high and 5 deep: [7, 4, 5]"`
    - `"palette: key 'AB' must be one character"`, `"palette['.']: '.' always means air"`,
      `"palette[' ']: ' ' always means any"`
    - `"palette['X']: 'oak_stairs' is not a block this version can place"`
    - `"layers: 4 layers, size says 5"`, `"layers[1]: 6 rows, size says 7"`
    - `"layers[1][3]: 'P....P' has 6 characters, size says 7"` (the row quoted as written)
    - `"layers[1][3][2]: 'X' is not in the palette"`
    - `"layers[0][4][3]: the door 'D' needs the same door in the cell above"` (a one-cell door)
    - `"layers[0][4][3]: the door 'D' is 3 cells tall, a door is 2"` (the run's lower cell)
    - `"layers[1][4][3]: the door 'D' needs a full block below"`
    - `"layers[1][1][1]: the torch 'T' needs a full block below or beside it"`
    - `"layers[3][2][2]: 'P' and 4 other cells are not connected to the ground"` (one message per unconnected
      group of cells, naming its first cell in layer, row, character order)
    - `"layers: the blueprint places no blocks"`
- `materials(bp) -> dict[str, int]`: palette value → count over the placed cells. Keys that share a value are
  summed. Air and any are left out. A door counts once per two-cell door.
- `cells(bp, origin: Pos) -> Iterator[tuple[Pos, str]]`: every cell whose value isn't `any`, in layer, row, then
  character order. That includes air cells and both cells of a door. The value is the palette value: a raw id, a
  group, or `"air"`.
- `door_tops(bp, origin: Pos) -> set[Pos]`: the upper cell of each door. A builder never places these:
  `/place` on the lower cell fills them.
- `matches(expected: str, found: str | None) -> bool`: whether a scanned raw id satisfies a palette value. `any`
  matches everything, `None` included (a cell the bot can't see). `air` matches `air`, `cave_air` and `void_air`
  (`AIR_BLOCKS`). A group matches its members. `torch` matches `torch` and `wall_torch`. Anything else must be
  equal.
- `render(bp) -> list[list[str]]`: the layer slices for the panel, bottom-up, each a list of rows from north to
  south. Air keys become `.`, any keys become a space, and other characters stay as written. The panel draws its
  legend from the palette.
- `is_full(value: str) -> bool`: a `FULL_BLOCKS` member or a group, the support a door or a torch needs.
- `carries(value: str) -> bool`: whether a placed cell with this value carries its neighbours: a full block, glass
  or a pane, not a door, a torch, air or any. S10's support search uses `carries` and `is_full`, so it agrees with
  `validate`.

Constants, public: `MAX_SIZE`, `GROUPS`, `FULL_BLOCKS`, `DOORS`, `PLACEABLE`, `AIR_BLOCKS`, `REPLACEABLE`. The
stub on `main` has them, with the function signatures.

#### The `place` and `clear` actions (`models.py`, stub on `main`)

- `ActionKind` gains `"place"` and `"clear"`.
- `Action.pos: tuple[int, int, int] | None = None`: the cell for `place` and `clear`, in world coordinates. A list
  (an action read back from JSON) becomes a tuple, so the frozen `Action` stays hashable. `pos` is in
  `_V2_ACTION_FIELDS`, so `to_dict()` leaves it out while it is unset, and every recorded LLM key stays the same.
- `Action("place", target="oak_planks", pos=(10, 64, -3))`. `target` is a raw block id: the caller (S10's runner)
  resolves a group from the inventory first. `describe()` gives `"place oak_planks at 10 64 -3"`.
- `Action("clear", pos=(10, 64, -3))`, with `target` `None`. `describe()` gives `"clear 10 64 -3"`.

#### Backends (`adapter.py` stub, lanes `build-backend` and `build-sim`)

- `LifeBackend.scan(origin: Pos, size: tuple[int, int, int]) -> dict[Pos, str | None]`: the block in every cell
  of the box, keyed by world position. `None` is a cell the bot can't see (an unloaded chunk; never in the
  simulator).
- `LifeBackend.set_keep_out(box: tuple[Pos, tuple[int, int, int]] | None) -> None`: an `(origin, size)` box the
  walks of `place` and `clear` never put scaffold in, sent with every `/place` and `/clear` until it is set back to
  `None`. The simulator only stores it (it has no scaffold).
- `act(Action("place" | "clear"))` returns an `Outcome`, in both of `MineflayerBackend`'s modes:
  - `ok`, `error`, `inventory`, `travel`, `events` as for the other kinds; `blocks_walked` is `travel.distance`;
  - `inventory_delta` is the step's own: `inventory` minus `inventory_before_step`, without zeros;
  - `block_removed`, `drops` and `picked_up` stay at their defaults;
  - `detail` for `place`: `block`, `placed`, `found`, `target` (`{"x", "y", "z"}`), `inventory_before_step`;
  - `detail` for `clear`: `cleared`, `removed`, `found`, `target`, `inventory_before_step`;
  - on an error, `detail` keeps what the bot sent (`found` for `occupied`, `place_failed` and `unbreakable`), and
    `interrupted` is flattened as for the other kinds.
- `MineflayerBackend` posts `/place {"block", "x", "y", "z", "scaffold"}` and `/clear {"x", "y", "z", "scaffold"}`,
  with `scaffold` set to `list(SCAFFOLD_BLOCKS)`, plus `"keep_out": {"origin": [x, y, z], "size": [w, h, d]}` while
  one is set. `scan()` posts `/scan {"origin", "size"}` and turns
  `layers[y][z][x]` into `{(ox + x, oy + y, oz + z): name}`. A 400 raises `BackendError`, as every HTTP error does.
- `NaturalWorld` (the simulator) gives the same `Outcome` shapes:
  - `place`, checked in this order:
    - the cell already holds the target (or `wall_torch` for a torch): `ok`, `placed: false`, nothing spent;
    - none of the target held: `missing_item`;
    - the cell isn't free (free: `blueprint.AIR_BLOCKS` or `blueprint.REPLACEABLE`; a door needs the cell above
      free too): `occupied`, with `found`;
    - no support: `no_support`. Support is a neighbour that isn't free, a liquid, a door or a torch. A torch takes
      one below or beside it, a door one below.
    - Otherwise the cell gets the target and one is spent. A torch with nothing below becomes `wall_torch`; a door
      fills both cells.
  - `clear`:
    - an air cell: `ok`, `cleared: false`, `found: "air"`;
    - bedrock (at or below `TERRAIN_BOTTOM`) or a liquid: `unbreakable`, with `found`;
    - otherwise the cell becomes air (a door: both cells), with no drops, since the real bot doesn't collect them.
  - The simulator reaches any cell with an open neighbour (`_exposed`), with travel as `_mine` has it
    (`_travel_to`). A cell with none is `unreachable` for both: the walks never dig.
  - `place` and `clear` join `THREATENED_ACTIONS`, so the simulator's threats interrupt them as they do a mine.
  - `scan()` reads `block_at` for each cell.

#### Who owns what (S09)

| File | Owner | Note |
|---|---|---|
| `models.py`, `adapter.py`, `blueprint.py` (stub) | S09 stubs on `main` | No lane edits `models.py` or `adapter.py`. S08 adds `kill` to the same `ActionKind` line: whoever merges second rebases |
| `blueprint.py`, `tests/test_v3_blueprint.py` | `blueprint` | Fills in the stub; keeps its signatures and constants |
| `mc/bot/lib/place.js`, `mc/bot/lib/scan.js`, `mc/bot/server.js` (three routes and validators), `mc/bot/test/unit/place.test.js`, `mc/bot/test/unit/scan.test.js` | `build-bot` | S08 adds `/attack` and its validator to `server.js` at the same time: small edits, rebase |
| `environment.py`, `tests/test_v3_build_sim.py` | `build-sim` | Imports only the constants from the `blueprint.py` stub |
| `mineflayer_backend.py`, `tests/test_v3_build_backend.py` | `build-backend` | Fake HTTP |
| `mc/bot/test/v3_place_live.js` | `place-live` (live, instance a) | After `build-bot` is merged |

### v3.4b Build goals (S10: S4.3–S4.7, S4.9)

"Build a small wooden house with a door": Claude designs a blueprint, the panel shows it, and the user approves it.
Then the life picks a site, gathers the materials through the planner, builds bottom-up checking every block, and
the goal is `done` when a final scan matches the blueprint. The bot side is `contracts/bot_api.md` v3.4b
(`/survey` and `protect`). D1 holds as in v3.4a: `place` and `clear` steps go through `Episode.act` and are
checked exactly, but never reach `store.record_transition`; gathering the materials does, as for any obtain goal.

#### Protected boxes (everything the bot built)

A built or half-built thing must survive the next goal: a `#log` mine must never pick a placed log, a digging walk
must never tunnel through a wall, and scaffold must never land inside.

- `Box = tuple[Pos, tuple[int, int, int]]`, an `(origin, size)` box (`blueprint.py`, public). A build's box is
  `blueprint.site_box(bp, origin)`: the blueprint's box plus the layer under it, `((ox, oy - 1, oz), (w, h + 1, d))`,
  so the foundation the builder filled is protected too.
- The life keeps the list in `life_state.json`, key `"protected"`: `[{"goal_id": "u003", "name": "small house",
  "origin": [x, y, z], "size": [w, h, d], "status": "building" | "built" | "partial"}]`, where `origin` and `size`
  are the blueprint's (not the site box's). An entry is added, and `life_state.json` saved at once, when a build
  goal's site is picked (`status: building`). When the goal ends `done` it becomes `built`. When it ends `failed` or
  `cancelled` (a cancel, a second death, any failure) it becomes `partial` if any of its cells was placed
  (`BuildPlan.placed > 0`, which `run_build` keeps up to date as it goes, so it is known even when
  `GoalInterrupted` ends the run; half-built walls stay protected), else it is removed. A preempted, died-in or clock-cut goal keeps its `building` entry and runs
  again on the same origin. The goal's origin lives here (keyed by `goal_id`), not in `goals.jsonl`. M5 (S12)
  builds "home" on this list.
- `LifeBackend.set_protected(boxes: list[Box]) -> None`: the site boxes of every entry. The life calls it at start
  (fresh: `[]`; `--resume`: from the loaded list) and after every change to the list. It is backend state on the
  Python side only: `MineflayerBackend` sends `"protect": [{"origin", "size"}, ...]` with every `/mine`, `/explore`,
  `/look`, `/place`, `/clear`, `/goto` and `/survey`, so a bot restart or reconnect loses nothing and needs no
  re-sending. The same goes for `keep_out` (`set_keep_out`), which is now only the running build's own box.
- What `protect` means, in the bot (`contracts/bot_api.md` v3.4b) and the simulator alike:
  - `/mine` never targets a block inside a protected box (the bot's candidate filter; `NaturalWorld._choose_target`);
  - a walk never breaks a block inside one (`DiggingMovements`' `exclusionAreasBreak`, any other dig the bot makes
    outside `/clear`; the simulator's `_dig_path` and `_explore_up`); a buried target whose dig path crosses a box
    is skipped;
  - a walk never places scaffold inside one (`exclusionAreasPlace` in `DiggingMovements` and in `/place`/`/clear`'s
    build movements, as `keep_out` does);
  - `/look` leaves out blocks inside one, so an explore toward a `#log` never heads for the house;
  - `/survey` rules out a site whose footprint, grown by one block on each side, overlaps a box in x and z;
  - `/place` and `/clear` still act on their own target cell, inside a box or not: the builder clears and fills
    cells of its own box. Only their walks obey `protect`.
- The test that matters (`tests/test_v3_build_life.py`): a simulator life builds the fake hut next to trees, then a
  user goal "get me 10 logs" runs, and a `scan` of the site box before and after is identical.

#### `/survey`: where to build (`LifeBackend.survey`, lanes `survey-bot` and `survey-backend`)

```python
@dataclass(frozen=True)
class Site:                  # models.py
    origin: tuple[int, int, int]  # the north-west corner of layer 0: the first air layer above the ground
    cut: int                 # solid blocks at or above origin y inside the footprint (dug by the builder)
    fill: int                # free cells in the layer under origin y inside the footprint (filled by the builder)
    distance: float          # from the bot's feet to the footprint's centre, horizontally

    @property
    def cost(self) -> int: return self.cut + self.fill

def survey(self, size: tuple[int, int], radius: int = 24) -> list[Site]   # LifeBackend; size is (width, depth)
```

- One ranking, the same in the bot and the simulator. For every column `(x, z)` within `radius` of the bot (a
  square), find its **ground**: going down from the bot's feet y + 12 to feet y - 12, the first block that is solid
  (not air, not `blueprint.REPLACEABLE`, not a flower or another block without a collision box). The column is
  **ruled out** when the window's top cell (feet y + 12) isn't exactly `air` (the bot is underground: rock or
  `cave_air` there means a cave floor, not ground), when the ground block is water or lava, a log, wood or leaves (`*_log`, `*_wood`, `*_leaves`, the
  stripped ones, mushroom blocks), or when there is water or lava above it, or no ground in the window, or the chunk
  isn't loaded. `ground(x, z)` is that block's y.
- A candidate is a footprint of `width x depth` columns with its north-west corner at `(x0, z0)`. The floor is
  `fy = median(ground) + 1` over the footprint (the lower median of the sorted grounds, so it is an existing
  height). It is ruled out when any of its columns is, when any column of the one-block ring around it is water or
  lava, when its footprint plus the ring overlaps a protected box in x and z, or when a column is **steep**: its
  ground is above `fy - 1 + SURVEY_MAX_STEP` (`SURVEY_MAX_STEP = 3`) or below `fy - 2` (the builder fills only the
  layer under layer 0). Then `cut = sum(max(0, ground - fy + 1))` and `fill = sum(max(0, fy - 1 - ground))` over
  its columns.
- Ranked by `cost`, then `distance`, then `(x0, z0)`. The answer has the first `SURVEY_MAX_SITES = 10`, which may
  overlap one another. `origin = (x0, fy, z0)`.
- The simulator uses `NaturalWorld`'s own columns (`_column`), features (trees) and `block_at`; it has no unloaded
  chunks.
- `MineflayerBackend.survey` posts `/survey {"size": [w, d], "radius": r, "protect": [...]}` and turns `sites` into
  `Site`s. A 400 raises `BackendError`.

#### `goto`: back to the site (`models.py` stub, lanes `survey-bot` and `survey-backend`)

After gathering, a death or `--resume`, the bot may be far from the site, and a far site's chunks may not be
loaded (the scan has `None` cells). The runner walks back first.

- `ActionKind` gains `"goto"`: `Action("goto", pos=(x, y, z))`, `describe()` gives `"goto 10 64 -3"`. Like `place`
  and `clear`, it is never a rule kind, the LLM's enums don't gain it, and it is never recorded (D1).
- The bot: `POST /goto {"x", "y", "z", "range": 3, "protect"?}` walks with the world-preserving movements (no
  digging, no scaffold) to pathfinder's `GoalNear(x, y, z, range)`, under the action lock with the guard's cut-in,
  60 s. `{"ok": true, "arrived": true, "travel": {...}, "events": [...]}`; not there in time or no path:
  `unreachable` (with `travel`), as `/mine`'s walk reports it.
- `act(Action("goto"))` returns an `Outcome` with `travel`, `blocks_walked`, an empty `inventory_delta`, and
  `detail: {"arrived", "target"}`. The simulator teleports the bot to the nearest standable cell within `range` and
  counts the walk as `_travel_to` does; it joins `THREATENED_ACTIONS`.

#### The approval flow (`goals.py`, `llm.py`, `panel.py`, `life.py`)

- `parse_goal` (lane `build-life`): `SUPPORTED_GOAL_KINDS` gains `"build"`. A `build` kind (from the model, or the
  fake parser's first word) calls `design_blueprint(text)` and returns `GoalSpec(kind="build", source="user",
  text=text, blueprint=bp)` with `item=None`, `count=1`, `target=None`. A `BlueprintDesignError` becomes
  `Unsupported("I couldn't design that. Try \"build a small wooden house with a door\".")`, so a design that never
  validates isn't retried every pass. `SpendCapReached` (an `LLMError`) still leaves the goal `submitted`.
- `GoalQueue.accept(spec)`: a `build` spec goes from `submitted` straight to `awaiting_approval` (one line, under
  the lock, so a crash can't leave an unapproved build `queued`). The table gains `submitted -> awaiting_approval`;
  `queued -> awaiting_approval` stays allowed but unused. A queued build goal has therefore been approved.
- The life, on accepting a build: logs `goal_parsed` (with `goal_kind: "build"` and the blueprint's `name`) and
  says "Plan for u003: small house, 5 x 4 x 5: 55 planks, 12 logs, 1 torch, 1 oak door. Approve it in the panel,
  or say !yes or !no." (the materials as `goal_label` would name them, largest first).
- Approve: `awaiting_approval -> queued`, by `POST /goals/<id>/approve` or `!yes`. Discard: `awaiting_approval ->
  cancelled` with `reason: "discarded"`, by `POST /goals/<id>/discard` or `!no`. Cancel (panel or `!cancel`) still
  works on it too.
- `goal_label(spec)` for a build is the blueprint's `name` ("small house").
- Chat (v3.1g gains two commands; the help line lists them):

  | Command | Effect | Reply |
  |---|---|---|
  | `!yes` | approve the oldest `awaiting_approval` user goal | "Approved u003: small house." or "Nothing to approve." |
  | `!yes <id>` | approve that goal | the same, or "No goal u009." / "u003 isn't waiting for approval." |
  | `!no` / `!no <id>` | discard it | "Discarded u003: small house." and the same misses |

- `!status` adds "Waiting for approval: u003." when one is.

#### Panel routes and the preview (`panel.py`, `site/dist/build_preview.js`, lane `panel-preview`)

- `POST /goals/<id>/approve {"run"?}`: `200 {"ok": true, "id": "u003", "status": "queued"}`. Unknown id `404
  unknown_goal`; any status but `awaiting_approval` `409 {"ok": false, "error": "not_awaiting_approval", "status":
  "<status>"}` (also when `mark` loses a race). Same media type, Origin and run rules as v3.1f.
- `POST /goals/<id>/discard {"run"?}`: `mark(id, "cancelled", reason="discarded")`, `200 {"ok": true, "id",
  "status": "cancelled"}`; the same 404 and 409.
- `GET /goals.json`: each goal whose `blueprint` isn't null also gets `"preview": {"name", "size", "layers":
  blueprint.render(bp), "palette": {key: value}, "materials": blueprint.materials(bp)}` (computed by the panel, not
  stored in `goals.jsonl`). A blueprint that fails `validate` gets `"preview": null`.
- `site/dist/build_preview.js` (`SvBuildPreview`, a classic script like `goals.js`, mounted in
  `<section class="build-preview" id="build-preview">` in `trace.html`): it polls `GET /goals.json` every 2 s, as
  `goals.js` does, and shows the newest goal with a `preview` that is `awaiting_approval` (else the newest
  `running` one, else nothing). It draws one small grid per layer, bottom-up, a colour per palette value with a
  legend, the materials list, and Approve and Discard buttons only while `awaiting_approval`. Tested with node's
  `vm` like `goals.test.mjs` (`node site/dist/build_preview.test.mjs`, part of the offline suite from S10 on).

#### `design_blueprint` (`llm.py`, lane `blueprint-llm`)

```python
@dataclass(frozen=True)
class BlueprintLimits:
    max_size: tuple[int, int, int] = (7, 4, 7)   # within blueprint.MAX_SIZE; a roof 4 up is one the bot can
                                                 # reach from inside and drop from (maxDropDown 4)
    max_placed: int = 120                        # placed cells, both door cells counted

class BlueprintDesignError(Exception):           # not an LLMError: the parse turns it into Unsupported
    def __init__(self, errors: list[str]) -> None

DESIGN_RETRIES = 2
FAKE_HUT: dict                                    # the fixed blueprint fake mode returns

class LLM:
    def design_blueprint(self, text: str, limits: BlueprintLimits = BlueprintLimits()) -> dict
```

- Live and replay: `_json_call("design_blueprint", DESIGN_BLUEPRINT_SYSTEM, user, BLUEPRINT_SCHEMA)`. The schema is
  v3.4a's "Claude's form": `name` (string), `size` (array of integers), `palette` (array of `{"key", "block"}`,
  `block` an enum of `sorted(PLACEABLE) + list(GROUPS) + ["air", "any"]`), `layers` (array of arrays of strings);
  every object `additionalProperties: false` with every key required.
- The palette list becomes the map (a repeated key: `"palette: key 'P' is listed twice"`), then
  `blueprint.validate`, then the limits: `"size: [9, 5, 9] is over this build's limit 7 x 4 x 7"`,
  `"layers: 130 placed cells, this build's limit is 120"`. Any errors go back to Claude in a new call whose user
  message has the request, the previous answer as JSON and the error list. After `DESIGN_RETRIES` failed retries
  (3 calls in all) it raises `BlueprintDesignError(errors)`. Each call is metered and replayed as usual.
- The system prompt explains the axes (`layers[y][z][x]`, bottom-up, north to south, west to east), that layer 0
  stands on the ground, `.` for every cell that must be air (**the interior too**: a space keeps whatever grass or
  flowers were there), a space only where anything goes (such as roof corners), doors as two cells of the same
  door in one column with a full block or the ground below, a torch with a full block below or beside it, every
  other block connected to the ground, `#planks` and `#log` for wood (the bot places whichever species it holds),
  glass only when asked for windows, torches only when asked for light (coal needs a pickaxe and a search), and the
  limits.
- Fake mode (no call; `_meter_fake("design_blueprint", text)`): a deep copy of `FAKE_HUT`, whatever the text:

  ```json
  {"name": "small hut", "size": [5, 4, 5],
   "palette": {"L": "#log", "P": "#planks", "D": "oak_door", "T": "torch"},
   "layers": [
     ["LPPPL", "P...P", "P...P", "P...P", "LPDPL"],
     ["LPPPL", "PT..P", "P...P", "P...P", "LPDPL"],
     ["LPPPL", "P...P", "P...P", "P...P", "LPPPL"],
     [" PPP ", "PPPPP", "PPPPP", "PPPPP", " PPP "]]}
  ```

  Its `materials` are `{"#planks": 55, "#log": 12, "torch": 1, "oak_door": 1}`.

#### Doors are any wooden door (`normalize.py`, `blueprint.py`, `environment.py`, lane `doors`)

A door needs six planks of one species, so an `oak_door` can't be made from birch. A door is treated like
`#planks`:

- `normalize` gains the group `#door`: `<species>_door` for each of `SPECIES`. `family("oak_door")` is `#door`, and
  `resolve("#door", inventory)` is the door of a species with at least 6 planks held (the most held such species;
  failing that, the species whose planks are held most; failing that, the door held most), as `#planks` follows the logs. A rule about a raw door normalizes to `#door` like any member.
- `blueprint.matches(door, found)` (v3.4a change): a door value matches any member of `DOORS`. `materials` still
  names the door as written (`"oak_door": 1`); the runner gathers `normalize.family` of each material.
- The simulator's `WHITELIST_RECIPES` gains `"#door": ({"#planks": 6}, 3, True)` (crafted per species, like
  planks), and `data/prior_rules_v2.json` a fake prior rule for it, so a fake-mode life plans a door.
- `resolve` is for crafting. The runner places a door value as the `#door` member held most, as it places any
  group.

#### Build order (`builder.py`, lane `build-order`)

```python
@dataclass(frozen=True)
class BuildStep:
    kind: Literal["clear", "place"]
    pos: Pos
    value: str        # place: the palette value (a raw id or a group); clear: "air"
    phase: Literal["clear", "fill", "place", "torch", "door"]

def build_steps(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[BuildStep]
def fill_value(bp: dict) -> str                       # the value that fills holes under layer 0
def progress(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> tuple[int, int]   # (placed, total)
def mismatches(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[Pos]
def needed(steps: list[BuildStep]) -> dict[str, int]  # place steps' values -> count: what is left to place
```

`blueprint.py` gains `Box` and `site_box(bp, origin) -> Box` (the box above). `scan` is a backend scan of
`site_box`: layer -1 (`oy - 1`) and the blueprint's layers. A cell missing from `scan`, or `None`, is unknown.

- **Free**: `blueprint.AIR_BLOCKS` or `blueprint.REPLACEABLE`. **Solid** (gives support): known, not free, not a
  liquid (`water`, `lava`), not a door, a torch or `wall_torch`, and not another block without a collision box
  (a private list in `builder.py`: flowers, saplings, mushrooms, sugar cane, sweet berry bushes, vines, pink
  petals).
- The steps, in this order, every phase skipping a cell that already matches (`blueprint.matches`) and a cell
  that is unknown:
  1. **clear**, top-down (y descending, then z, then x): every blueprint cell whose value is `air` and doesn't
     match; every cell to be placed (a placed value, or a door's upper cell) whose block is neither free nor a
     match. In layer -1, only a block that is neither free nor solid (a flower: dug, then filled).
  2. **fill**: every cell of layer -1 under the footprint that isn't solid, with `fill_value(bp)`: the most used full-block
     value of the blueprint (`blueprint.is_full`), ties broken by `GROUPS` order, then by name.
  3. **place**: every cell whose value is a full block, glass or a pane (`blueprint.carries`), lowest first as
     far as support allows. A cell is ready when a face neighbour is solid in the world as it will be after the
     earlier steps (the scan, minus what phase 1 clears, plus what phases 2 and 3 placed so far). Ready cells come
     out of a heap keyed by `(y, z, x)`; placing one may make its neighbours ready. Cells never ready (only
     possible on a site that doesn't hold) are appended at the end in `(y, z, x)` order.
  4. **torch**, in `(y, z, x)` order, after every full block.
  5. **door**, the lower cell of each door only (`door_tops` are filled by the door), in `(y, z, x)` order, last.
- `progress`: `total` counts the blueprint's placed cells with each door once (no door tops, no fill); `placed`
  counts those that match in `scan`. `build_progress` is always counted this way, from a scan or from the cell
  states the runner keeps, never from `placed: true` (a retry answers `placed: false`).
- `mismatches`: every non-`any` blueprint cell that doesn't match, plus every layer -1 cell under the footprint
  that isn't solid, in `(y, z, x)` order. A build is done when it is empty.
- Tests first: the example house (v3.4a) on flat ground orders clear, then place bottom-up, then the torch, then
  the door; an overhang is placed after its support; a half-built scan resumes with only the missing cells; grass
  in an interior air cell is cleared, grass under a placed cell isn't; a hole under layer 0 is filled first.

#### The build runner (`build_runner.py`, lane `build-run`)

```python
BUILD_GOAL = "build"             # a build goal's Task.goal_item (like EXPLORE_GOAL); goal_count is progress total
BUILD_SECONDS_PER_STEP = 10      # a build goal's clock: per build step, plus 30 s per planned gathering step
MAX_BUILD_PASSES = 3             # scan, build, final scan; again while cells don't match
SCAFFOLD_MIN = 16                # SCAFFOLD_BLOCKS to hold before building: the first cell of a layer is placed
                                 # from a pillar outside the box
NEAR_SITE = 12                   # horizontal blocks from the footprint's centre; farther, the runner walks back
BUILD_KINDS = frozenset({"place", "clear", "goto"})   # episode.py

@dataclass
class BuildPlan:
    spec: GoalSpec
    origin: Pos
    site: Site | None            # the survey's pick; None when the origin came from the protected list
    steps: list[BuildStep]       # from the scan prepare_build made
    needs: dict[str, int]        # family -> count to hold: needed(steps) by normalize.family, plus scaffold
    gather_steps: int            # planner steps to gather what isn't held (for the budget and the clock)
    placed: int = 0              # builder.progress of the latest cell states; run_build updates it as it goes
    @property
    def budget(self) -> int      # max(MIN_GOAL_BUDGET, BUDGET_PER_PLAN_STEP * gather_steps + 2 * len(steps) + 10)
    @property
    def clock_s(self) -> float   # GOAL_SECONDS_PER_PLAN_STEP * gather_steps + BUILD_SECONDS_PER_STEP * len(steps)

@dataclass
class BuildOutcome:
    result: EpisodeResult
    reason: str                  # "" on success, else at most 80 characters
    placed: int
    total: int

def prepare_build(backend: LifeBackend, store: SchemaStore, spec: GoalSpec, *, origin: Pos | None,
                  protected: list[Box], world_id: str, excluded: frozenset[str]) -> BuildPlan | str
def run_build(episode: Episode, store: SchemaStore, plan: BuildPlan, *, before_step: BeforeStep,
              check_interrupts: Callable[[], None], excluded: frozenset[str]) -> BuildOutcome
```

`planner.py` gains `make_plan_for(needs: dict[str, int], inventory, rules, skill_for, visible) -> list[PlanStep]`:
the planner's `_gather` (every need held at once, deeper items first, a need that spends another repeated), made
public. `agent.py` gains `pursue(episode, store, needs, *, before_step, excluded, goal=None, narrate=True) ->
list[tuple[Action, str]]`: `run_schemavoyager`'s plan-and-run loop moved out of it, looping until every need is
held (not `episode.goal_met`), planning with `make_plan` for one need (so `run_schemavoyager`, now
`pursue({task.goal_item: task.goal_count})` plus its start and end, behaves exactly as before) and `make_plan_for`
for several. It returns the completed steps (for the skill), and records, revises and blocks exactly as before.

- `prepare_build` runs before the episode exists, like an obtain goal's `make_plan`. With no `origin` it surveys
  (`survey((w, d))`) and drops sites overlapping `protected`; with none left it explores `up` once
  (`backend.act(Action("explore", direction="up"))`, as the junk toss acts outside an episode) and surveys again;
  still none: the string "no site". It takes the first site, scans `site_box`, computes `build_steps`, and the
  needs: `needed(steps)` by `normalize.family`, plus `dirt` up to `SCAFFOLD_MIN` scaffold blocks held in all.
  `gather_steps` is `len(make_plan_for(needs, held, usable rules minus excluded, ...))`; a `PlanningError` is the
  string "no plan: <error>". The scan may have `None` cells when the site is far; the steps then cover only what is
  known, and `run_build` rescans on site.
- The life, on a string: the episode is still made (budget `MIN_GOAL_BUDGET`), logs `build_failed {"reason"}`,
  finishes failed, and `_end_goal` fails the goal with that reason, as an obtain goal's "no plan" does. On a plan:
  it adds the protected entry, saves `life_state.json` and calls `set_protected` before making the episode with
  `plan.budget` and `plan.clock_s` (capped by the life's remaining time).
- `run_build`, in that one episode:
  1. logs `build_site {"goal_id", "origin", "size", "cut", "fill"}` and sets `set_keep_out(site_box)`. Says
     "Building small hut at 12 64 -30: gathering 55 planks, 12 logs, 1 torch, 1 door." (only what isn't held).
  2. **Materials**: `pursue(episode, store, needs, before_step=before_step, excluded=excluded, narrate=False)`.
     A `#door` need is gathered last: first `pursue` of the needs without it, then of all of them, since a door
     takes 6 planks of one species and planks crafted early from mixed logs may not have them (S10: simulator seeds
     1, 4, 5, 7, 8). Not all held when it returns: the goal fails "couldn't get 1 door, 12 logs" (every need not
     held, in the needs' order).
  3. **On site**: if the bot is more than `NEAR_SITE` blocks (horizontally) from the footprint's centre, or the
     last scan had a `None` cell, `goto` (`range` 3) a point 2 blocks out from the middle of the footprint's south
     side at the origin's y, then on `unreachable` the east, north and west sides: up to 3 walks. The walk only
     needs the chunks loaded, and the centre may be inside finished walls. Then a fresh
     scan of `site_box` and `build_steps` again. A pass whose scan still has `None` cells after the walks fails
     "can't see the site"; `None` cells never use up a pass otherwise.
  4. **Build**: the steps in order. Before each, `check_interrupts()` (the life's poll, parse, cancel, death and
     preemption checks, without the `ChangeTrigger`, which is for rule steps). A place resolves a group to the
     member the bot holds most of (a door value: the `#door` member held most); holding none is `missing_item`. Each runs through
     `episode.act(Action("place", target=raw, pos=pos))` or `episode.act(Action("clear", pos=pos))` with no
     `annotate`, so nothing is checked against a rule and nothing is recorded. `Episode.act` treats `BUILD_KINDS`
     differently: never an explore; `interrupted` retried in place as for every kind; `unreachable` and `timeout`
     retried once in place; any other error returned at once.
  5. A step's outcome updates the runner's cell states (`found` from `detail`); `build_progress {"goal_id",
     "placed", "total"}` (`builder.progress` of those states) is logged after each step that changed `placed`,
     and once after each scan.
  6. **A failed step**: `missing_item`, or `unreachable` while the bot holds fewer than 4 scaffold blocks (pillars
     are left standing, so scaffold runs out), reruns the materials (2) once per pass, without counting toward
     "stuck". Otherwise the cell is remembered
     with its error; if that cell had already failed in this goal the build stops "stuck at 12 65 -28:
     unreachable". Else the runner rescans, recomputes the steps, moves the steps of every failed cell to the end,
     and goes on.
  7. **Final scan**: `mismatches` of a fresh scan. Empty: success. Else another pass from 3, up to
     `MAX_BUILD_PASSES`, then "3 cells don't match". Out of budget or time ends it with the life's own reasons
     ("out of steps", "out of time").
  8. `episode.finish(success=..., announce=False)`, `store.certify()` and the `certified` event, as
     `run_schemavoyager` ends; no skill. A `finally` sets `set_keep_out(None)`. `GoalInterrupted` from
     `check_interrupts` or `before_step` propagates to the life unchanged.
- Resuming (a preemption, a death, `--resume`) is just running again: the life passes the saved origin,
  `prepare_build` rescans, and matching cells are skipped. A cell placed before a crash is in the scan.
- `/place`'s walk for a `keep_out` box (lane `protect-bot`): besides ending outside the box for a door, the stance
  never ends on top of the box (feet at or above the box's top y, inside its x and z), where the bot could be stuck:
  it can drop at most 4 on build walks and 3 on digging walks, and can't dig the protected roof.
- The life (lane `build-life`): `_run_goal` sends a `build` spec to `prepare_build`/`run_build` instead of
  `make_plan`/`run_schemavoyager`, inside the same `try/except GoalInterrupted` and through the same `_end_goal`
  (the six endings and a death, v3.1d/v3.2d). `_before_step` becomes `_check_interrupts()` (keeping `_poll_bot`
  and `_parse_pending`) plus `hooks.before_step`. A failed build's reason is `BuildOutcome.reason`. The life
  record gains `"build": {"name", "origin", "placed", "total"}`. "Done: small hut" / "Couldn't build small hut:
  <reason>". The junk toss's `wanted` and `_unasked_families` include the materials (by family) of
  `awaiting_approval`, `queued` and `running` build goals, so glass brings its sand and smelting priors.
- Tests (`tests/test_v3_build_run.py`, simulator, `--llm fake`): `prepare_build` on a flat site; a run that gathers
  and builds a door-less hut and ends with no mismatches and no `place`/`clear`/`goto` transition in the store; a
  preemption mid build that resumes and finishes with the same origin; a `missing_item` rerun; a far bot walking
  back. `tests/test_v3_build_life.py`: the whole flow with `FAKE_HUT`, submit, `awaiting_approval`, approve, gather,
  build, `done`, plus the protected-box mine test above and `--resume` re-sending `set_protected`.

#### Who owns what (S10)

| File | Owner | Note |
|---|---|---|
| `models.py` (`Site`, `goto` in `ActionKind`), `adapter.py` (`survey`, `set_protected`), `blueprint.py` (`Box`, `site_box`), `builder.py` (stub) | S10 stubs on `main` | S08 may add `kill` to the same `ActionKind` line: whoever merges second rebases |
| `builder.py`, `tests/test_v3_builder.py` | `build-order` | |
| `mc/bot/lib/survey.js`, `mc/bot/lib/goto.js` (new), `mc/bot/server.js` (two routes), `mc/bot/test/unit/survey.test.js`, `mc/bot/test/unit/goto.test.js` | `survey-bot` | |
| `mineflayer_backend.py` (`survey`, `goto`), `environment.py` (`survey`, `goto`), `tests/test_v3_survey.py` | `survey-backend` | |
| `llm.py` (`design_blueprint` only), `tests/test_v3_blueprint_llm.py` | `blueprint-llm` | |
| `site/dist/build_preview.js`, `site/dist/build_preview.test.mjs`, `site/dist/trace.html`, `site/dist/trace.css`, `site/dist/trace.js` (only to render `place`/`clear`/`goto` steps and `build_*` events), `panel.py`, `tests/test_panel.py` | `panel-preview` | |
| `mc/bot/lib/protect.js` (new), `lib/targets.js`, `lib/mine.js`, `lib/movements.js`, `lib/explore.js`, `lib/place.js`, `lib/survey.js`, `lib/goto.js`, `mc/bot/server.js` (validators), `mc/bot/test/unit/protect.test.js` | `protect-bot` | After `survey-bot` |
| `mineflayer_backend.py`, `environment.py` (`set_protected`), `tests/test_v3_protect.py` | `protect-backend` | After `survey-backend` |
| `normalize.py` (`#door`), `blueprint.py` (`matches`), `environment.py` (the door recipe), `data/prior_rules_v2.json`, `tests/test_v3_doors.py` | `doors` | After `protect-backend` |
| `build_runner.py`, `agent.py` (`pursue`), `planner.py` (`make_plan_for`), `episode.py` (`BUILD_KINDS`), `tests/test_v3_build_run.py` | `build-run` | After group 1 and the design check; alongside `doors` |
| `life.py`, `llm.py` (`parse_goal`), `goals.py` (`accept`, `goal_label`), `tests/test_v3_build_life.py` | `build-life` | After `build-run` and `doors` |
## v3.5 Places, chests, composite goals
