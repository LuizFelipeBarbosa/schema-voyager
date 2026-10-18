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
```

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
  2. parse every `pending()` goal with `llm.parse_goal` (v3.1e): `accept` it, or `mark(id, "unsupported",
     reason=reply)` and say the reply. A parse that raises `LLMError` (`SpendCapReached` is one) leaves the goal
     `submitted` and the pass goes on; the stop checks then see `spend_cap_hit`;
  3. `_learn_priors()` and `_repair_dead_ingredients()` as now;
  4. pick the goal: `queue.next()` (system, then user), else in learn mode the curriculum as now, else idle: poll
     the queue and the bot every 2 s (steps 1–2 and, from M2, the hunger check), stand still, and keep checking
     the wall clock and the spend cap.
- `_run_goal(spec)`. For a queued goal it first does `mark(id, "running")`. For `obtain`, the `Task` is the v2 one
  with `goal_count = target_count`. The first time a goal runs, `target_count = held + spec.count` (D5), held being
  `normalize.counts` of the current inventory, and it is saved with `mark(id, "running", target_count=...)`. A
  goal that runs again (after a preemption or a resume) keeps its `target_count`.
- When it ends: `done` on success; `failed` with a `reason` (at most 80 characters: "no plan", "out of time",
  "not found: iron ore", ...). A failed user goal isn't skipped for the rest of the life. User and system goals
  never feed `Curriculum.record`.
- `_unasked_families()` also returns the items of `queued` and `running` obtain goals (minus `self.asked`), so the
  life asks Claude for prior rules about an item it has never seen. In play mode the curriculum frontier part is
  left out.
- The junk toss at the start of a goal (v3.0c) gets as `wanted` the running goal's item plus the items of every
  `queued` obtain goal, so the bot never drops what a queued goal asks for. From M2 (S07 `death-handling`), `wanted`
  also holds every edible food (v3.2e), so the toss never drops what auto-eat needs.
- **Cancel and preemption.** `_Life` composes its own check in front of `hooks.before_step`. Before each step it:
  1. polls the bot's events and handles chat commands, then parses pending goals, catching `LLMError` as in the
     pass above (so `!cancel` and a new goal are seen mid-goal);
  2. raises `GoalInterrupted(id, "cancelled")` if the running goal's status is now `cancelled`;
  3. raises `GoalInterrupted(id, "death")` if the bot died since the goal started (v3.2d);
  4. raises `GoalInterrupted(id, "preempted")` if a queued goal outranks the running one (system over user, user
     over curriculum);
  5. then calls the original `hooks.before_step` (the `ChangeTrigger`). It runs last, so a hidden change is never
     switched on for a step that doesn't run.
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
  ignored. A `chat_command` is handled only from this poll, never from an action response's `events`, so it is
  never handled twice. A `BackendError` from the poll is logged and the pass goes on.
- Stop reasons: `max_goals` (counts every goal run, whatever its source), `spend_cap`, `wall_clock`,
  `empty_frontier` (learn mode only), and `deaths` (v3.2d).
- Tests use `--llm fake` in the simulator. They inject `chat_command` events with a test double around
  `NaturalWorld.status`; S06 doesn't edit `environment.py` (S07's `sim-threats` lane owns it).

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
  own key). New event `goal_interrupted {"goal_id", "reason"}`, where reason is `cancelled`, `preempted` or `death`.
- `life.jsonl` records gain `goal_id`, `source`, `text` and `goal_kind`. An `interrupted` record also has
  `interrupt`: `wall_clock` (v2's case), `cancelled`, `preempted` or `death`. Readers treat a record without
  `source` as a curriculum goal.
- New `runs/<life>/life_events.jsonl`: events outside any episode, in `EpisodeLog`'s line format
  (`{"t", "kind", ...}`). A fresh life empties it and `--resume` appends to it. `EpisodeLog` empties its file when
  it opens it, so the life writes this one with its own small appender. Kinds: `chat_command {"player", "command", "args"}`, `goal_parsed {"goal_id",
  "goal_kind", "item", "count", "target"}`, `goal_unsupported {"goal_id", "reply"}`, and from M2 `death` and
  `hungry` (v3.2). The panel doesn't serve it.
- Chat, at most 3 messages of 240 characters with no `#` (display names drop `#` and `_`):
  - when a user goal's first plan is made: "On it: 10 torches (7 steps)";
  - "Done: 10 torches", "Couldn't get torches: <reason>", "Cancelled: 10 torches";
  - a preemption: "Pausing 10 torches for hunger: 4 bread".
- The panel's goal card shows "Your goal: get me 10 torches" for `source: user`.

### v3.1i Who owns what (S06 and S07 run at the same time)

| File | Owner | Note |
|---|---|---|
| `models.py` | S05 stubs on `main` | Neither session edits it. A missing field goes on `main` as its own commit first |
| `goals.py` | S06 `goal-queue` | S07 only calls `submit`, `accept`, `mark` and `GoalInterrupted` |
| `llm.py`, `data/items_1_21_1.json`, `scripts/export-items.js` | S06 `goal-parse` | S07 reads `foods` |
| `life.py` | S06 `life-queue`, then S07 `death-handling` | One after the other: `death-handling` starts after `life-queue` is merged |
| `agent.py` | S06 `goal-narration` | The `interrupted` line in `_NEVER_BLOCKED_ERRORS` is S05's stub |
| `episode.py` | S05 stub, then S07 `python-survival` | The stub adds `interrupted` to `RETRIED_ERRORS`; S07 makes that retry skip the explore (v3.2b) |
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
  `RETRIED_ERRORS` (`episode.py`, stub on `main`), so the step is retried, at most `MAX_RETRIES` times. S07's
  `python-survival` lane makes `Episode.act` retry an `interrupted` step in place, without the explore that the other
  retried errors get: the fight already moved the bot, and an explore would walk it away from its furnace or table.
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

1. **An aborted handler never runs alongside another action.** On a guard abort the wrapper calls `interruptBot()`
   and aborts `session.signal` (an `AbortSignal` that every handler gets). It waits for the handler to settle, at
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
2. **No item is left in a container for a later step to count.** `interruptBot()` closes the furnace window, so an
   aborted smelt leaves its items in the furnace. Before its shortfall check, `/smelt` drains a placed furnace it
   is about to use: anything in it goes back into the inventory, and the step records `interference {"block":
   "furnace", "position"}`, so that step is quarantined and the next one is clean. Draining first also gives the
   in-place retry its ingredients back. A craft already clears the grids before it measures
   `inventory_before_step`.
3. **While an action holds the lock, the survival plugins click nothing.**
   - `onActionStart` is async. It disables auto-eat and waits until no eat is in progress (at most the plugin's
     `eatingTimeout`, 3 s) before the handler runs.
   - auto-eat runs with `equipOldItem: false`, so it never swaps the held item back later.
   - armor-manager's own `playerCollect` listener is removed. `equipAll()` runs in `onActionEnd` and on idle ticks
     instead.
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

## v3.3 Kill rules and `/attack`

## v3.4 Blueprints and building actions

## v3.5 Places, chests, composite goals
