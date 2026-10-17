from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ActionKind = Literal["mine", "craft", "explore"]
RuleStatus = Literal["hypothesis", "certified", "demoted"]


@dataclass(frozen=True)
class Action:
    """One adapter call. A walk to the target is part of the action."""

    kind: ActionKind
    target: str | None = None  # block for mine, item for craft
    tool: str | None = None  # mine only; None means bare hand
    times: int = 1  # craft only: how many times the recipe runs

    def describe(self) -> str:
        if self.kind == "mine":
            return f"mine {self.target} with {self.tool or 'hand'}"
        if self.kind == "craft":
            return f"craft {self.target}" + (f" x{self.times}" if self.times > 1 else "")
        return "explore"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Observation:
    position: tuple[float, float, float]
    inventory: dict[str, int]
    nearby: dict[str, int]  # block type -> count within the 16-block view
    blocks_walked: float = 0.0


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


@dataclass
class Rule:
    """A STRIPS-style operator with the evidence for it.

    Mine rules: preconditions {"tool_any": [...]} (empty list = any tool or bare hand),
    effects {"block_removed": true, "drops": {...}}.
    Craft rules: preconditions {"consumes": {...}, "station": "crafting_table" | None},
    effects {"gains": {...}} per recipe run.
    """

    id: str
    action: dict[str, str]  # {"type": "mine", "block": ...} or {"type": "craft", "item": ...}
    preconditions: dict[str, Any]
    effects: dict[str, Any]
    status: RuleStatus = "hypothesis"
    source: Literal["prior", "revision"] = "prior"
    scope: str = "train"  # "train" or the holdout world id that proposed it
    rank: int = 0  # order among sibling revision hypotheses
    rationale: str = ""
    revision_of: str | None = None
    # Transition ids. confirmed/contradicted come from replay within the rule's
    # scope; counterexamples are contradictions seen in other worlds.
    evidence: dict[str, list[str]] = field(
        default_factory=lambda: {"confirmed": [], "contradicted": [], "counterexamples": []}
    )

    @property
    def kind(self) -> str:
        return self.action["type"]

    @property
    def subject(self) -> str:
        return self.action["block"] if self.kind == "mine" else self.action["item"]

    def yields(self) -> dict[str, int]:
        if self.kind == "mine":
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
