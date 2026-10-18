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

## v3.3 Kill rules and `/attack`

## v3.4 Blueprints and building actions

### v3.4a Blueprints, `place`, `clear` and `scan` (S09: S4.1, S4.2, S4.8)

The parts every build needs. There is no build goal yet: that is S10's v3.4b. The bot side is
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

## v3.5 Places, chests, composite goals
