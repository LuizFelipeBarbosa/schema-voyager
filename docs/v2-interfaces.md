# v2 Python interfaces (Wave 0 contract)

Owned by the orchestrator. Lanes code against this file and
`contracts/bot_api.md` (v2 section), not against each other's branches. If a
lane needs a change here, it reports back instead of editing.

`models.py` and `adapter.py` are frozen after Wave 0. `normalize.py`,
`verifier.py` and `schema_store.py` carry Wave 0 signatures (stubs raise
`NotImplementedError`) that the `rules` lane implements without changing them.

## Module layout

| Module | Lane | Role |
|---|---|---|
| `models.py` | orchestrator | `Action` (+`smelt`, `fuel`, `ingredient`, `direction`), `Outcome` (+`travel`, `events`), `Rule` (+`check`, `epoch`), `Status` |
| `adapter.py` | orchestrator | `Backend` (v1) and `LifeBackend` (v2) protocols |
| `normalize.py` | rules | the only place that knows species, groups and variant families |
| `verifier.py` | rules | kinds of check, the status vocabulary, predict / check / evaluate |
| `schema_store.py` | rules | epochs, replay by epoch and kind, persistence |
| `environment.py` | backends | v1 `SimulatedWorld` unchanged, plus `NaturalWorld` (natural-world simulator) |
| `mineflayer_backend.py` | backends | v2 endpoints, `natural=True` mode |
| `planner.py` | life | smelt rules (station `furnace`, fuel) |
| `curriculum.py` | life | next goal, skips, expansion from the whitelist |
| `life.py` | life (then `change` adds the M3 trigger) | the life loop, persistence, resume, spend cap, stop conditions |
| `agent.py` | life | step loop: status dispatch, never records quarantined steps, budget hook |
| `episode.py` | life | natural-mode explore policy (look, then explore toward), travel logging |
| `evaluator.py` | life | ignore stochastic and quarantined steps in failure signatures |
| `llm.py` | life | v2 prompts and schema, per-family prior calls, curriculum expansion, spend cap |
| `tasks.py` | life | v1 vocabulary (`MINEABLE_BLOCKS`, `CRAFTABLE_ITEMS`, moved here in Wave 0) |
| `demo.py` | life | `schema-voyager life` command and the demo-port guard |
| `trace.py` | life | formatting of the new events and statuses |

## Normalization (`normalize.py`)

Vocabulary. A *group* is a name starting with `#` that stands for every species
of one kind of item or block. A *variant family* is a block id that also covers
its variants. Each raw id belongs to at most one group or family.

| Name | Members (raw ids) |
|---|---|
| `#log` | `<s>_log`, `<s>_wood`, `stripped_<s>_log`, `stripped_<s>_wood` for every overworld species `<s>` in 1.21.1 (oak, spruce, birch, jungle, acacia, dark_oak, mangrove, cherry) |
| `#planks` | `<s>_planks` |
| `#leaves` | `<s>_leaves`, `azalea_leaves`, `flowering_azalea_leaves` |
| `#sapling` | `<s>_sapling` (and `mangrove_propagule`) |
| `#cobblestone` | `cobblestone`, `cobbled_deepslate`, `blackstone`: what vanilla accepts in stone tools and the furnace |
| `coal_ore` | `coal_ore`, `deepslate_coal_ore` |
| `iron_ore` | `iron_ore`, `deepslate_iron_ore` |
| `copper_ore`, `gold_ore`, `diamond_ore`, `redstone_ore`, `lapis_ore`, `emerald_ore` | the ore and its `deepslate_` variant |

`stone` and `deepslate` are different blocks with different drops, not variants.

```python
SPECIES: tuple[str, ...]

def family(name: str) -> str:
    """Raw id -> its group or family name; groups, families and unknown ids map to
    themselves (idempotent). "birch_log" -> "#log", "deepslate_coal_ore" -> "coal_ore",
    "stick" -> "stick", "#log" -> "#log"."""

def members(name: str) -> list[str]:
    """Group or family -> its raw ids, sorted; any other id -> [id].
    "coal_ore" -> ["coal_ore", "deepslate_coal_ore"]; "stone" -> ["stone"]."""

def counts(raw: dict[str, int]) -> dict[str, int]:
    """Sum counts by family(), drop zeros, sort keys. Idempotent.
    {"birch_planks": 4, "oak_planks": 2, "stick": 1} -> {"#planks": 6, "stick": 1};
    {"birch_log": -1, "birch_planks": 4} -> {"#log": -1, "#planks": 4}."""

def species(name: str) -> str | None:
    """"birch_planks" -> "birch"; "stick" -> None; "#planks" -> None."""

def species_mismatch(raw: dict[str, int]) -> str | None:
    """For one step's raw counts (a craft's inventory delta, or a mine's
    {mined block: -1, **drops}): when species-bearing ids appear both among the
    negative and the positive counts, every species on the positive side must
    also be on the negative side. Returns a short description when not
    ("birch_log -> oak_planks"), else None.
    {"birch_log": -1, "birch_planks": 4} -> None;
    {"oak_log": -1, "birch_log": -1, "oak_planks": 4, "birch_planks": 4} -> None;
    {"oak_planks": -3, "stick": -2, "wooden_pickaxe": 1} -> None (one side only);
    {"birch_log": -1, "oak_planks": 4} -> "birch_log -> oak_planks"."""

def resolve(name: str, inventory: dict[str, int]) -> str:
    """A concrete raw id to send to the bot for a craft, smelt or fuel argument.
    "#planks" -> "<s>_planks" for the log species held most (ties by name); with no
    log held, the planks species held most; with neither, raise ValueError (no
    species is ever assumed). "#log" -> the log held most. A raw id -> itself."""
```

Both directions: rules are written in normalized names (`#planks +4`); the
check normalizes the observed raw ids (`birch_planks +4` -> `#planks +4`) and
separately requires `species_mismatch(raw) is None`.

## Kinds of check and the status vocabulary (`verifier.py`)

```python
CheckStatus = Literal["match", "mismatch", "failed", "quarantined", "stochastic"]
CheckKind = Literal["exact", "stochastic", "tolerant", "quarantined"]

EXACT_FAMILIES: frozenset[str]          # mine subjects with deterministic drops
STOCHASTIC_FAMILIES: frozenset[str]     # mine subjects with random drops
QUARANTINE_EVENTS: frozenset[str]       # {"damage", "death", "respawn", "target_despawned", "interference"}
                                        # plus "stray_pickup" for craft and smelt steps only

@dataclass
class Check:
    status: CheckStatus
    predicted: dict[str, Any]
    observed: dict[str, Any]
    detail: str = ""
    kind: CheckKind = "exact"

def kind_of(rule: Rule, action: Action) -> CheckKind
def predict(rule: Rule, action: Action) -> dict[str, Any]          # normalized names
def check(rule: Rule, action: Action, outcome: Outcome) -> Check
def evaluate(rule: Rule, action: Action, observed: dict[str, Any]) -> Literal["confirm", "contradict", "neutral"]
```

**Kinds.**
- *exact*: every craft and smelt; mines whose family is in `EXACT_FAMILIES`
  (at least `#log`, `stone`, `deepslate`, `#cobblestone`, `dirt`, `grass_block`,
  `sand`, `andesite`, `diorite`, `granite`, `coal_ore`, `iron_ore`, `gold_ore`,
  `diamond_ore`, `emerald_ore`, `crafting_table`, `furnace`); and a mine whose
  family is in neither whitelist when the rule declares `check="exact"`.
  Mines are assumed not to use Fortune or Silk Touch.
- *stochastic*: mines whose family is in `STOCHASTIC_FAMILIES` (at least
  `gravel`, `#leaves`, `short_grass`, `tall_grass`, `fern`, `large_fern`,
  `dead_bush`, the small and tall flowers, and `copper_ore`, `redstone_ore`,
  `lapis_ore`, whose drop counts are random), and a non-whitelisted mine whose
  rule declares `check="stochastic"`. The whitelist wins over the rule's
  declaration.
- *tolerant*: explore, travel, pickup, placement. No rule, no check.
- *quarantined*: decided per step, from the events (below).

**`check(rule, action, outcome)`**, in this order:
1. `quarantined` if `outcome.error == "quarantined"`, if any event is `death`
   or `respawn` (either phase), or if any `"phase": "step"` event (an event
   without a phase counts as step) has a type in `QUARANTINE_EVENTS`, with
   `stray_pickup` only for craft and smelt. Travel-phase damage doesn't
   quarantine: the step's own window was clean. `kind="quarantined"`.
2. `failed` if `not outcome.ok` (as v1), or, for a mine, if
   `outcome.detail["tool_used"]` is present and differs from `action.tool`
   (detail `"wrong tool held"`).
3. Observed, normalized:
   - mine: `{"block_removed": bool, "drops": counts(outcome.drops)}`.
   - craft and smelt: `{"inventory_delta": counts(delta)}`, where `delta` is the
     outcome's raw delta with the v1 station correction (`table_retrieved` /
     `furnace_retrieved` False adds the station back) and, for smelt, the fuel
     added back before normalizing: `delta[detail["fuel"]] += detail["fuel_used"]`
     (the raw fuel id the bot used; fuel is tolerant).
   - When `species_mismatch(raw)` returns a description, add
     `"species_mismatch": <description>` to observed. `predict` never contains
     that key, so the step can't match.
4. Exact kind: `match` when observed equals `predict(rule, action)`, else
   `mismatch`. Stochastic kind: status `stochastic`; `detail` says whether the
   drops were in the allowed range. A stochastic step is never `mismatch`.

**`predict`** for exact rules: mine `{"block_removed": True, "drops": counts(drops)}`;
craft and smelt `{"inventory_delta": counts(consumes × -times + gains × times)}`.
For stochastic rules: `{"block_removed": True, "allowed": {item: [min, max]}}`.

**`evaluate`** (used by replay; normalizes `observed` again, so v1 stores with
raw ids still replay): exact: `confirm` if equal to `predict`, else
`contradict`. Stochastic: `confirm` if `block_removed` and every dropped item is
in `allowed` with its count in range (absent items count 0), else `neutral`.

A certified exact rule whose step mismatches is always a contradiction, and
quarantine is the only exception, which keeps the coal case detectable.

## Rules (`models.py`)

```python
@dataclass
class Rule:
    ...v1 fields...
    check: Literal["exact", "stochastic"] = "exact"
    epoch: int = 0   # the epoch of its (world, action, family) key it was proposed in
```

Shapes (all names normalized):
- mine: `action {"type": "mine", "block": "#log"}`, `preconditions {"tool_any": [...]}`,
  `effects {"block_removed": true, "drops": {"#log": 1}}`; stochastic rules add
  `effects["allowed"] = {"gravel": [0, 1], "flint": [0, 1]}`. When a stochastic
  rule has no `allowed`, it is derived from `drops` as `[0, n]` per item.
- craft: `action {"type": "craft", "item": "#planks"}`, `preconditions {"consumes": {"#log": 1}, "station": null}`,
  `effects {"gains": {"#planks": 4}}`.
- smelt: `action {"type": "smelt", "item": "iron_ingot"}`,
  `preconditions {"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "charcoal", "#planks", "#log"]}`,
  `effects {"gains": {"iron_ingot": 1}}`.

`applies(rule, action)` (schema_store): same action type, `family(action.target) == family(rule.subject)`,
and for mines a tool the rule allows.

**Scope in a life.** Every rule a life adds (priors and revisions) has
`scope = config.world_id`, and every transition has `world_id = config.world_id`,
so replay counts the life's own evidence. (v1 keeps `scope="train"` for priors.)

## The whitelist's vanilla rules (shared truth)

`prior_rules_v2.json` (the fake LLM's priors, owned by `life`) and
`NaturalWorld`'s recipes (owned by `backends`) both implement exactly this
table, in normalized names. Vanilla 1.21.1, no Fortune or Silk Touch.

| Rule | Preconditions | Effect |
|---|---|---|
| mine `#log` | any tool or hand | drops `#log` 1 |
| mine `stone` | any pickaxe | drops `#cobblestone` 1 |
| mine `deepslate` | any pickaxe | drops `#cobblestone` 1 |
| mine `coal_ore` | any pickaxe | drops `coal` 1 |
| mine `iron_ore` | stone, iron, diamond or netherite pickaxe | drops `raw_iron` 1 |
| mine `dirt`, `grass_block` | any tool or hand | drops `dirt` 1 |
| mine `sand` | any tool or hand | drops `sand` 1 |
| mine `gravel` (stochastic) | any tool or hand | allowed `gravel` [0, 1], `flint` [0, 1] |
| craft `#planks` | `#log` 1 | `#planks` 4 |
| craft `stick` | `#planks` 2 | `stick` 4 |
| craft `crafting_table` | `#planks` 4 | 1 |
| craft `wooden_pickaxe`, `wooden_axe` | `#planks` 3, `stick` 2, table | 1 |
| craft `wooden_shovel` | `#planks` 1, `stick` 2, table | 1 |
| craft `wooden_sword` | `#planks` 2, `stick` 1, table | 1 |
| craft `stone_pickaxe`, `stone_axe` | `#cobblestone` 3, `stick` 2, table | 1 |
| craft `stone_shovel` | `#cobblestone` 1, `stick` 2, table | 1 |
| craft `stone_sword` | `#cobblestone` 2, `stick` 1, table | 1 |
| craft `furnace` | `#cobblestone` 8, table | 1 |
| craft `torch` | `coal` 1, `stick` 1 | `torch` 4 |
| craft `chest` | `#planks` 8, table | 1 |
| craft `ladder` | `stick` 7, table | `ladder` 3 |
| craft `iron_pickaxe`, `iron_axe` | `iron_ingot` 3, `stick` 2, table | 1 |
| craft `iron_shovel` | `iron_ingot` 1, `stick` 2, table | 1 |
| craft `iron_sword` | `iron_ingot` 2, `stick` 1, table | 1 |
| craft `bucket` | `iron_ingot` 3, table | 1 |
| craft `shears` | `iron_ingot` 2 | 1 |
| smelt `iron_ingot` | `raw_iron` 1, furnace, fuel `coal` or `#planks` | `iron_ingot` 1 |

The changed rules `NaturalWorld.set_world_rules` can switch on: `"r2"` (mine
`coal_ore` drops coal only with a stone, iron, diamond or netherite pickaxe, at
every depth) and `"stick2"` (craft `stick` gives 2).

## Actions and outcomes (`models.py`)

```python
ActionKind = Literal["mine", "craft", "explore", "smelt"]

@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target: str | None = None      # mine: block, group or family; craft/smelt: output item (may be a group)
    tool: str | None = None        # mine only
    times: int = 1                 # craft and smelt
    fuel: str | None = None        # smelt only (may be a group)
    ingredient: str | None = None  # smelt only: the input item (may be a group)
    direction: str | None = None   # explore only: N, NE, E, SE, S, SW, W, NW, or "up" (v2.1); None = v1 spiral

@dataclass
class Outcome:
    ...v1 fields...
    travel: dict[str, Any] = field(default_factory=dict)        # {"distance", "dug", "placed", "picked_up"}
    events: list[dict[str, Any]] = field(default_factory=list)  # bot events during the call

@dataclass
class Status:
    health: float
    food: int
    position: tuple[float, float, float]
    alive: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    last_seq: int = 0
```

`Outcome.detail` keys added in v2 (when set): `block` (raw id actually mined),
`item` (raw id actually crafted or smelted), `fuel_used`, `furnace_retrieved`,
`inventory_before_step`.

## Backends (`adapter.py`)

```python
DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
LookResult = dict[str, dict[str, dict[str, Any]]]   # direction -> raw id -> {"x","y","z","distance","exposed"}

class LifeBackend(Backend, Protocol):
    """A backend for one continuous life: no world reset between goals."""
    def begin_episode(self) -> Observation: ...   # per-goal bookkeeping only (bot: /reset_episode)
    def status(self, since: int = 0) -> Status: ...
    def look(self, blocks: list[str], radius: int = 64) -> LookResult: ...
    def probe(self, grid: list[list[str | None]]) -> dict[str, Any]: ...   # {"output": {"item","count"} | None}
```

`act()` in a life backend accepts group and family targets: mine targets are
sent as `blocks=normalize.members(target)`; craft and smelt targets, fuels and
ingredients go through `normalize.resolve(name, inventory)` with the latest
known inventory. A group craft that no single species can pay for (`craft
#planks x2` holding 1 oak and 1 birch log) is split into one bot call per
species, and the backend merges the results into one `Outcome` (deltas summed,
events and travel concatenated, `ok` only if every part was). In natural mode,
`MineflayerBackend` sends `scaffold=SCAFFOLD_BLOCKS` (a constant in
`mineflayer_backend.py`: dirt, cobbled_deepslate, netherrack, andesite,
diorite, granite, tuff; not cobblestone, which the plans spend). Both `MineflayerBackend(natural=True)` and `NaturalWorld`
implement `LifeBackend`. In natural mode, `MineflayerBackend` sends
`dig_through: true`, `max_distance: 32` on `/mine` and `distance: 32` on
directional `/explore`, and fills `Outcome.travel` and `Outcome.events`.
`reset()` stays the v1 arena reset; a life never calls it.

**`NaturalWorld(seed: int, *, world_rules: tuple[str, ...] = (), species: tuple[str, ...] | None = None)`**
(environment.py): seeded terrain with a surface, trees of 1–3 species (`species`, or drawn from `normalize.SPECIES`),
stone below the surface with some exposed, coal in exposed and buried spots,
deepslate and `deepslate_*_ore` below y=0, iron ore, gravel, leaves, grass and
flowers, water and lava pools. Random drops (gravel, leaves, grass) come from
a seeded RNG; travel through buried terrain digs, picks up and reports `travel`;
a seeded small chance of a `damage` event exercises quarantine (mostly
travel-phase, occasionally step-phase); recipes are the whitelist table above,
with tag ingredients that accept mixed species; it reports `tool_used`;
targets next to lava are skipped with a `hazard` event. `set_world_rules(rules)`
switches changed rules mid-run: `"r2"` (coal ore and deepslate coal ore drop
coal only for a stone-tier pickaxe) and `"stick2"` (the stick recipe yields 2).
It never imports `layouts` or uses `GROUND_Y`.

## Epochs (`schema_store.py`)

```python
@dataclass
class Transition:
    id: str
    episode_id: str
    world_id: str
    action: Action
    observed: dict[str, Any]
    kind: CheckKind = "exact"   # "exact" or "stochastic"; quarantined steps are never recorded
    seq: int = -1               # assigned by record_transition: its position in the log

@dataclass
class Epoch:
    key: str          # epoch_key(world_id, action type, subject)
    number: int       # 1, 2, ... (epoch 0 is implicit: from the start of the log)
    start_seq: int    # seq of the transition that opened it (that transition belongs to it)
    opened_by: str    # that transition's id
    rule_id: str      # the rule whose contradiction opened it

def epoch_key(world_id: str, kind: str, subject: str) -> str   # f"{world_id}/{kind}:{family(subject)}"

class SchemaStore:
    epochs: dict[str, list[Epoch]]
    def current_epoch(self, key: str) -> int
    def epoch_of(self, transition: Transition) -> int
```

- `record_transition` assigns `seq`. `load` assigns `seq` by list order to
  transitions that lack one, and accepts v1 stores (no epochs, kinds or seqs).
- `demote(rule_id, transition_id, world_id)` keeps its v1 signature and
  return value, and also opens a new epoch for `epoch_key(world_id, rule.kind,
  rule.subject)` at that transition when the rule was `certified`, or had at
  least one confirmation in its own epoch (a hypothesis already confirmed and
  now contradicted is a change too; approved by the user).
- `add_rule` stamps a revision (`source == "revision"`) with
  `current_epoch(epoch_key(rule.scope, rule.kind, rule.subject))`. Priors keep
  `epoch=0`. (In a life, `rule.scope == world_id`; in v1's holdout, a revision's
  scope is the holdout world that `demote` was called with, so the keys agree.)
- `epoch_of(t)` uses `epoch_key(t.world_id, t.action.kind, t.action.target)`.
- `replay(rule)` only considers transitions in the rule's scope that the rule
  applies to, with `epoch_of(t) >= rule.epoch`, and classifies each with
  `verifier.evaluate`: `confirm` -> confirmed, `contradict` -> contradicted,
  `neutral` -> neither.
- Certification is unchanged: 2+ confirmations from 2+ episodes, no
  contradictions (within the epoch).
- `save` / `load` round-trip epochs, kinds and seqs.

## The step loop (`agent.py`)

`run_schemavoyager(episode, store, before_step=None)`. `before_step(episode,
step, plan)` is called before each step runs (the M3 trigger uses it). Status
dispatch:

| status | recorded? | then |
|---|---|---|
| `match` | yes, `kind="exact"` | next step |
| `stochastic` | yes, `kind="stochastic"` | next step (replan if the plan's items deviate) |
| `mismatch` | yes | demote, revise (one LLM call), replan |
| `quarantined` | **never** | log it, replan from the current state (no revision) |
| `failed` | no | replan (tolerant) |

In natural mode the planner gets `normalize.counts(inventory)`, and the
"inventory deviates, replan" check compares only the items the remaining plan
uses, so travel pickups (dirt, cobblestone) don't force a replan after every mine.
`Outcome.travel` goes into the trace (a `travel` event before the step event,
and a `travel` field on the step event) and never into a `Transition`.
In natural mode, `hazard` is handled like `not_found`: look, then explore in
another direction. **v2.1:** when the look sees none of the target, the first
fallback explore for that target is `Action("explore", direction="up")` (back
to open sky, a no-op when already there: logs and most surface resources are
only found at the surface), once per target, then the horizontal directions as
before. `NaturalWorld` implements "up" the
same way (moves the bot to the surface above it). `evaluator.failure_signature` ignores steps whose check is
`stochastic` or `quarantined`, so a grass mine with no drop isn't a "failure".

## Curriculum (`curriculum.py`)

```python
WHITELIST: tuple[str, ...]   # fixed vanilla items reachable with wood, stone, coal and iron, e.g.
# "#planks", "stick", "crafting_table", "wooden_pickaxe", "wooden_axe", "wooden_shovel", "wooden_sword",
# "stone_pickaxe", "stone_axe", "stone_shovel", "stone_sword", "furnace", "torch", "chest", "ladder",
# "iron_ingot", "iron_pickaxe", "iron_axe", "iron_shovel", "iron_sword", "bucket", "shears"
SEED_FRONTIER: tuple[str, ...]   # the initial goals, a subset of WHITELIST

@dataclass(frozen=True)
class Goal:
    item: str                    # normalized name
    count: int                   # normalized inventory count to reach: current + 1
    plan_length: int
    uncertified: tuple[str, ...] # rule ids in the plan that aren't certified
    reason: str                  # one line, logged to life.jsonl

@dataclass
class Curriculum:
    frontier: list[str]
    failures: dict[str, int]
    skipped: dict[str, str]      # item -> why
    def next_goal(self, inventory: dict[str, int], store: SchemaStore, world_id: str) -> Goal | None
    def record(self, goal: Goal, success: bool) -> None
    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Curriculum"
```

- Next goal: among frontier items not skipped, the cheapest (plan length from
  the current normalized inventory) whose plan uses at least one uncertified
  rule; ties by fewest uncertified rules, then by name.
- An item that can't be planned now is passed over for this choice. 2 failed
  attempts (budget or wall clock exhausted, or a plan failure mid-goal) skip it
  for the rest of the life.
- When nothing qualifies, the life loop asks Claude to expand the frontier from
  the unused part of `WHITELIST` (anything outside the whitelist is dropped); if
  that adds nothing, the life stops (`empty_frontier`).

## The life loop (`life.py`)

```python
@dataclass
class LifeConfig:
    run_dir: Path
    world_id: str                   # "life-<seed>-<yyyymmdd-hhmm>"
    max_goals: int | None = None
    wall_clock_s: float = 1800.0
    goal_wall_clock_s: float = 300.0
    spend_cap_usd: float = 10.0

@dataclass
class LifeHooks:
    before_step: Callable[[Episode, PlanStep, list[PlanStep]], None] | None = None
    after_goal: Callable[[dict[str, Any]], None] | None = None

def run_life(backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks | None = None,
             resume: bool = False) -> dict[str, Any]   # summary: goals, stop reason, certified, tokens, spend
```

- One continuous life: no `backend.reset`. Each goal is an `Episode` (id
  `D/life/g<NNN>-<item>`, trace `episodes/D_life_g<NNN>_<item>.jsonl` with any
  `#` dropped from the item, so file names never contain `#` or `/`) with `split="train"`, `world_id=config.world_id`,
  budget `max(30, 4 × plan_length)` actions and a `goal_wall_clock_s` deadline,
  started with `backend.begin_episode()`.
- v2 LLM calls use new prompt and schema constants (`WORLD_MODEL_SYSTEM_V2`,
  `RULES_SCHEMA_V2`, with `smelt`, `check`, `allowed` and `fuel_any`); the v1
  constants stay byte-identical so v1's recorded responses still replay. The
  v2 prompts describe groups ("a log of any species crafts 4 planks of that
  species: write consumes #log 1, yields #planks 4") and variants, and never
  say "wood is oak". The v2 rule parser normalizes every subject and count with
  `normalize.family` / `normalize.counts`, and sets `scope=world_id`.
- **Repair calls (v2.2, approved by the user 18:12):** when a usable rule consumes an
  item that no usable rule produces although its family was already asked about (a
  "dead ingredient", e.g. a prior that smelts `iron_ore` instead of `raw_iron`), the
  life asks Claude once per rule to re-propose it using obtainable items
  (`LLM.repair_rule_v2`). The alternatives are hypotheses like any other.
- LLM calls: prior rules once per new family, batched into one call (life
  start: the seed frontier's items plus the observed block families; later, any
  item no usable rule produces, and newly observed blocks); revisions on
  contradictions; curriculum expansion when the frontier is empty. Never per
  step. The prior-rule prompt always lists the known blocks ("none observed yet"
  rather than an empty list).
- Spend cap: `LLM.spend_cap_usd`; before each live call, if spend so far plus
  a worst-case estimate of this call would pass the cap, raise
  `SpendCapReached` (an `LLMError`). Sonnet 5.5: $2 per million input tokens,
  $10 per million output tokens.
- Stop conditions: `spend_cap`, `wall_clock`, `empty_frontier`, `max_goals`.
- Files under `runs/<run>/`: `episodes/*.jsonl` (the v1 trace format, one per
  goal), `life.jsonl` (one line per goal: index, goal, count, reason, outcome,
  actions, rules certified this goal, certified total, LLM calls, tokens, spend,
  start and end time), `store.json` and `life_state.json` (curriculum, asked
  families, goal index), saved after every goal, `llm_cache.jsonl`, `COMMIT`.
  `resume=True` reloads them and continues the same world id.

## CLI and the instance environment

```
schema-voyager life --backend mc|sim --llm live|replay|fake [--run-dir runs/<run>] [--resume]
                    [--goals N] [--minutes 30] [--cap-usd 10] [--seed <level seed>] [--sim-seed N]
```

Instance settings, read by every v2 script and by `schema-voyager life`
(command-line values win, since `load_dotenv` doesn't override variables already set):

| Variable | Instance a | Instance b |
|---|---|---|
| `SV2_INSTANCE` | `a` (default) | `b` |
| `SV2_MC_PORT` | 25566 | 25567 |
| `SV2_RCON_PORT` | 25576 | 25577 |
| `SV2_BOT_PORT` | 3200 | 3210 |
| `SV2_VIEWER_PORT` | 3207 | 3217 |

Plus `RCON_PASSWORD`, `JAVA_BIN`, `SV2_SEED` (the level seed of the running
world, for the world id) and `SV2_HOME` (the sv2 checkout holding `server-*/`,
`run/`, `logs/` and `mc/bot/node_modules`; scripts default to their own repo
root).

`life --backend mc` builds `MineflayerBackend(api_url=f"http://127.0.0.1:{SV2_BOT_PORT}", natural=True)`
and talks to RCON (M3) on `SV2_RCON_PORT`. **The guard:** it refuses to start
when `SV2_BOT_PORT` or `SV2_RCON_PORT` is unset, when the bot port is 3000, or
when the RCON port is 25575, and says why. `--backend sim` needs none of them.

## v2.3: trace events for the stage (lanes `story` and `lifeview`)

The panel must show, at every moment, the goal, where the bot is in its plan,
and the hypothesis the current step tests. These additions are backwards
compatible (new keys and one new event kind; v1 traces are unchanged).

- **`plan` event**, each entry of `steps` adds:
  `status` (the rule's status: `certified`, `hypothesis`, `prior`, ...),
  `confirmed` (confirmations so far in the rule's current epoch, an int),
  `needed` (confirmations certification needs, an int), and
  `predicts` (one human line, at most 120 characters, for what the rule
  predicts, e.g. `"2 planks -> 4 stick"`, `"coal_ore with wooden_pickaxe -> 1 coal"`,
  `"raw_iron + 1 coal in a furnace -> 1 iron_ingot"`). Existing keys stay.
- **`step_start` event (new)**, logged by the step loop right before each
  backend call: `{"kind": "step_start", "index": <the step event's index>,
  "action": "<describe()>", "rule": "<rule id>", "experimental": bool,
  "status", "confirmed", "needed", "predicts"}`. A `step` event with the same
  `index` follows when the call returns (or the episode ends).
- **`episode_end`** for an exploration goal (`task.goal_item` is the
  exploration marker the life uses) reports `success` = the exploration found a
  new block family; it no longer says FAIL because the goal item isn't held.
- **Chat narration (`/chat`)** stays at most 3 messages of 240 characters per
  burst. The stage moments, each one message: the goal (`Goal: 4 torch, to test
  coal_ore with wooden_pickaxe`), the mismatch (`MISMATCH: expected 1 coal,
  got nothing`), the hypothesis (`Hypothesis: coal_ore needs stone_pickaxe`),
  the recovery (`Recovered: got 1 coal with stone_pickaxe`), and the
  certification (`Certified: mine coal_ore`).
- **Panel server:** besides `runs/<run>/episodes/*.jsonl`, it serves
  `runs/<run>/life.jsonl`, `runs/<run>/changes.jsonl` and
  `runs/<run>/store.json` read-only (404 when missing), and `/runs.json` (run
  directories, newest first, with whether each has a `life.jsonl`).

## v2.3: the instance lock (lane `launch`)

One live owner per instance. `run/lock-<instance>` (under `SV2_HOME`) holds one
JSON line `{"pid", "session", "purpose", "started"}`.

- Shell (`scripts/v2-env.sh`): `sv2_lock_acquire <purpose>` takes the lock or
  dies naming the holder (a lock whose pid is dead is stale and is taken over
  with a warning); `sv2_lock_release` removes it only if this shell's
  session holds it. `SV2_LOCK_SESSION` names the session (default
  `$$-<purpose>`); a child script that inherits the same `SV2_LOCK_SESSION`
  passes through instead of refusing (so `v2-demo.sh` can call `world-restore.sh`).
- Scripts that take it: `v2-up.sh`, `v2-new-world.sh`, `world-restore.sh`,
  `v2-change.sh` (install, enable, disable), `v2-seeds.sh`, `v2-demo.sh`.
  `v2-down.sh` and `v2-change.sh status` don't.
- Python (`src/schema_voyager/instance_lock.py`): `hold(instance, purpose)`, a
  context manager with the same file format and rules, used by
  `life --backend mc` for the whole life.
