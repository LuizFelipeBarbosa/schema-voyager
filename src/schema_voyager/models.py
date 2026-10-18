from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ActionKind = Literal["mine", "craft", "explore", "smelt", "place", "clear", "goto", "kill", "withdraw"]
RuleStatus = Literal["hypothesis", "certified", "demoted"]
RuleCheck = Literal["exact", "stochastic"]
# v3 goals (docs/v3-interfaces.md, v3.1)
GoalKind = Literal["obtain", "build", "kill", "guard", "composite"]  # v3.5: composite is a parent goal, never run
GoalSource = Literal["user", "curriculum", "system"]  # system: hunger, later threats
GoalStatus = Literal["submitted", "queued", "running", "done", "failed", "cancelled", "unsupported", "awaiting_approval"]


@dataclass(frozen=True)
class Action:
    """One adapter call. A walk to the target is part of the action."""

    kind: ActionKind
    # mine: a block, or (v2) a group or variant family such as "#log" or "coal_ore";
    # craft and smelt: the output item, which may be a group such as "#planks"; kill (v3.3): a mob type such as "cow".
    target: str | None = None
    tool: str | None = None  # mine: None means bare hand; kill: the weapon, None means the best one held
    times: int = 1  # craft and smelt: how many times the recipe runs
    fuel: str | None = None  # smelt only; may be a group
    ingredient: str | None = None  # smelt only: the input item; may be a group
    direction: str | None = None  # explore only: N, NE, E, SE, S, SW, W, NW; None is the v1 spiral
    # v3.4: place, clear and goto only, the cell in world coordinates. place's target is a raw block id.
    # v3.5: withdraw too: the chest's cell; target is a raw item id and times the count.
    pos: tuple[int, int, int] | None = None
    # v3.0h: place and clear only. False sends "scaffold": [], so the walk places nothing; None sends the usual blocks.
    scaffold: bool | None = None

    def __post_init__(self) -> None:
        if self.pos is not None:  # an action read back from JSON has a list, and Action must stay hashable
            pos = tuple(self.pos)
            if len(pos) != 3 or not all(isinstance(v, int) and not isinstance(v, bool) for v in pos):
                raise ValueError(f"pos must be three integers, got {self.pos!r}")
            object.__setattr__(self, "pos", pos)

    def describe(self) -> str:
        if self.kind == "mine":
            return f"mine {self.target} with {self.tool or 'hand'}"
        if self.kind == "craft":
            return f"craft {self.target}" + (f" x{self.times}" if self.times > 1 else "")
        if self.kind == "smelt":
            times = f" x{self.times}" if self.times > 1 else ""
            return f"smelt {self.ingredient} -> {self.target}{times} with {self.fuel}"
        if self.kind == "place":
            return f"place {self.target} at {_cell(self.pos)}"
        if self.kind == "clear":
            return f"clear {_cell(self.pos)}"
        if self.kind == "goto":
            return f"goto {_cell(self.pos)}"
        if self.kind == "kill":
            return f"kill {self.target}" + (f" with {self.tool}" if self.tool else "")
        if self.kind == "withdraw":
            return f"withdraw {self.target}" + (f" x{self.times}" if self.times > 1 else "") + f" from {_cell(self.pos)}"
        return f"explore {self.direction}" if self.direction else "explore"

    def to_dict(self) -> dict[str, Any]:
        """v2 fields are left out while unset, so v1 actions serialise (and key
        the recorded LLM responses) exactly as before."""
        payload = asdict(self)
        for name in _V2_ACTION_FIELDS:
            if payload[name] is None:
                del payload[name]
        return payload


_V2_ACTION_FIELDS = ("fuel", "ingredient", "direction", "pos", "scaffold")


def _cell(pos: tuple[int, int, int] | None) -> str:
    return " ".join(str(v) for v in pos or ())


@dataclass
class Observation:
    position: tuple[float, float, float]
    inventory: dict[str, int]
    nearby: dict[str, int]  # block type -> count within the 16-block view
    blocks_walked: float = 0.0
    free_slots: int | None = None  # v3: empty main and hotbar slots (0-36); None when not reported
    # v3.2: entity type -> {"count": int, "nearest": float, "kind": str} within 16 blocks
    entities: dict[str, dict] = field(default_factory=dict)


@dataclass
class Outcome:
    """What the world reported for one action, read from server-confirmed state."""

    ok: bool
    error: str | None = None
    block_removed: bool | None = None  # mine only
    drops: dict[str, int] = field(default_factory=dict)  # mine only, exact
    picked_up: dict[str, int] = field(default_factory=dict)  # mine only, tolerant
    inventory_delta: dict[str, int] = field(default_factory=dict)
    inventory: dict[str, int] = field(default_factory=dict)
    blocks_walked: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    # v2: getting into position before the step itself (tolerant):
    # {"distance": float, "dug": {...}, "placed": {...}, "picked_up": {...}}
    travel: dict[str, Any] = field(default_factory=dict)
    # v2: bot events during the call, e.g. {"seq": 41, "type": "damage", "amount": 2.0}
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Status:
    """The bot's condition (v2 /status) and its events after a given seq."""

    health: float
    food: int
    position: tuple[float, float, float]
    alive: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    last_seq: int = 0
    follow: dict[str, Any] | None = None  # v3.3: {"player", "distance"} while following a player
    # v3.0i: /status's nearby_entities, as Observation.entities: {type: {"count", "nearest", "kind"}} within 16 blocks
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Site:
    """A place to build (v3.4b, `/survey`): a footprint's north-west corner on the first air layer above the ground."""

    origin: tuple[int, int, int]
    cut: int  # solid blocks at or above origin y inside the footprint
    fill: int  # free cells in the layer under origin y inside the footprint
    distance: float  # horizontal, from the bot's feet to the footprint's centre

    @property
    def cost(self) -> int:
        return self.cut + self.fill


HOME_RANGE = 48.0  # v3.5: blocks (3D) from the bot within which a home station or the home chest is planned as usable


@dataclass(frozen=True)
class HomeStock:
    """v3.5: what the bot can use at home without carrying it. See docs/v3-interfaces.md, v3.5d."""

    stations: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # "crafting_table"/"furnace" -> pos
    chest: tuple[int, int, int] | None = None
    stored: dict[str, int] = field(default_factory=dict)  # the chest's contents, raw ids
    lost: frozenset[str] = frozenset()  # kinds ("chest" too) found gone this goal: the life drops them from places
    skipped: frozenset[str] = frozenset()  # kinds the bot couldn't reach this goal: not planned again in it
    # v3.0i: kinds whose walk stalled near their own cell this goal (the slot is to blame): the life moves or drops them
    unreachable: frozenset[str] = frozenset()

    def near(
        self, position: tuple[float, float, float], reach: float = HOME_RANGE
    ) -> tuple[frozenset[str], dict[str, int]]:
        """The station kinds within reach of position, and the stored items if the chest is within reach; never a
        lost, skipped or unreachable kind. Distances are 3D Euclidean, from position to the centre of the station's or chest's cell."""
        unusable = self.lost | self.skipped | self.unreachable

        def within_reach(cell: tuple[int, int, int]) -> bool:
            return math.dist(position, (cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5)) <= reach

        stations = frozenset(
            kind for kind, cell in self.stations.items() if kind not in unusable and within_reach(cell)
        )
        chest_in_reach = self.chest is not None and "chest" not in unusable and within_reach(self.chest)
        return stations, dict(self.stored) if chest_in_reach else {}


@dataclass(frozen=True)
class ChestResult:
    """v3.5: a /deposit (or a chest read) as the life sees it. See docs/v3-interfaces.md, v3.5d."""

    moved: dict[str, int]  # deposited or withdrawn, raw ids
    contents: dict[str, int]  # the chest after the call, raw ids
    error: str | None = None  # not_found, unreachable, interrupted, timeout; a lost bot raises BackendError instead
    detail: dict[str, Any] = field(default_factory=dict)  # chest_full, inventory_full, travel


@dataclass
class Task:
    task_id: str  # "t1", "t2", "t3", "h3"
    split: Literal["train", "holdout"]
    goal_item: str
    goal_count: int
    start_inventory: dict[str, int]
    world_rules: tuple[str, ...]  # changed rules switched on, e.g. ("r2",)
    budget: int  # adapter actions
    description: str


@dataclass(frozen=True)
class GoalSpec:
    """A goal of any source (v3.1): typed by the user, picked by the curriculum, or queued by the life itself."""

    id: str  # "u003" user, "s001" system, "g012" curriculum (the life's goal name)
    kind: GoalKind
    source: GoalSource
    text: str = ""  # what the user typed; a short label for system goals ("hunger: 4 bread")
    item: str | None = None  # obtain: normalized name (normalize.family), such as "#planks" or "torch"
    count: int = 1  # obtain: how many more than held (D5); kill: how many
    target: str | None = None  # kill: mob type; guard: player name
    blueprint: dict | None = None  # build (M4)


@dataclass(frozen=True)
class Unsupported:
    """parse_goal's answer for a goal the bot can't do (yet)."""

    reply: str  # what the bot says back, at most 240 characters, no "#"


@dataclass
class Rule:
    """A STRIPS-style operator with the evidence for it.

    Mine rules: preconditions {"tool_any": [...]} (empty list = any tool or bare hand),
    effects {"block_removed": true, "drops": {...}}.
    Craft rules: preconditions {"consumes": {...}, "station": "crafting_table" | None},
    effects {"gains": {...}} per recipe run.
    Smelt rules (v2): preconditions {"consumes": {...}, "station": "furnace",
    "fuel_any": [...]}, effects {"gains": {...}} per item smelted.
    Kill rules (v3.3): action {"type": "kill", "mob": ...}, preconditions {"tool_any": []},
    effects {"killed": true, "drops": {...} (low ends), "allowed": {item: [min, max]}}; always stochastic.
    v2 rules use normalized names ("#planks", "coal_ore"); see docs/v2-interfaces.md.
    """

    id: str
    action: dict[str, str]  # {"type": "mine", "block": ...} or {"type": "craft", "item": ...}
    preconditions: dict[str, Any]
    effects: dict[str, Any]
    status: RuleStatus = "hypothesis"
    source: Literal["prior", "revision", "retest"] = "prior"
    scope: str = "train"  # "train" or the holdout world id that proposed it
    rank: int = 0  # order among sibling revision hypotheses
    rationale: str = ""
    revision_of: str | None = None
    # Transition ids. confirmed/contradicted come from replay within the rule's
    # scope; counterexamples are contradictions seen in other worlds.
    evidence: dict[str, list[str]] = field(
        default_factory=lambda: {"confirmed": [], "contradicted": [], "counterexamples": []}
    )
    # v2: the declared kind of check (verifier.py's whitelists take precedence),
    # and the epoch of its (world, action, family) key it was proposed in.
    check: RuleCheck = "exact"
    epoch: int = 0
    # v3: replay ignores transitions with seq < since_seq, which is a retest's evidence window.
    since_seq: int = 0

    @property
    def kind(self) -> str:
        return self.action["type"]

    @property
    def subject(self) -> str:
        if self.kind == "mine":
            return self.action["block"]
        if self.kind == "kill":
            return self.action["mob"]
        return self.action["item"]

    def yields(self) -> dict[str, int]:
        if self.kind in ("mine", "kill"):
            return dict(self.effects.get("drops", {}))
        return dict(self.effects.get("gains", {}))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Rule":
        return cls(**payload)


@dataclass
class Skill:
    """A compiled plan: the rule applications that reached a goal, by rule id."""

    id: str
    goal_item: str
    goal_count: int
    rule_ids: list[str]
    steps: list[dict[str, Any]]
    status: Literal["ok", "flagged"] = "ok"
    flagged_by: str | None = None
    evidence: list[str] = field(default_factory=list)  # episode ids

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Skill":
        return cls(**payload)


@dataclass
class EpisodeResult:
    episode_id: str
    system: str
    task_id: str
    layout_seed: int
    world_id: str
    success: bool
    actions: int
    llm_tokens: int
    llm_calls: int
    blocks_walked: float
    repeated_failures: int
    contradictions: int
    actions_after_contradiction: int | None
    revisions: int
    final_inventory: dict[str, int]
    used_skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
