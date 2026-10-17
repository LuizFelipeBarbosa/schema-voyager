from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Direction = Literal["north", "south", "east", "west"]


@dataclass(frozen=True)
class Position:
    x: int
    z: int

    def moved(self, direction: Direction) -> "Position":
        offsets: dict[Direction, tuple[int, int]] = {
            "north": (0, -1),
            "south": (0, 1),
            "east": (1, 0),
            "west": (-1, 0),
        }
        dx, dz = offsets[direction]
        return Position(self.x + dx, self.z + dz)


@dataclass(frozen=True)
class Action:
    name: str
    target: str | None = None


@dataclass
class TaskSpec:
    task_id: str
    split: Literal["train", "holdout"]
    goal_item: str
    goal_count: int
    start: Position
    start_inventory: dict[str, int]
    resources: dict[Position, str]
    action_budget: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start"] = asdict(self.start)
        payload["resources"] = [
            {"x": position.x, "z": position.z, "block": block}
            for position, block in self.resources.items()
        ]
        return payload


@dataclass
class Observation:
    task_id: str
    position: Position
    inventory: dict[str, int]
    visible_blocks: dict[str, int]
    action_count: int
    action_budget: int
    goal_item: str
    goal_count: int
    done: bool
    success: bool
    last_message: str = ""


@dataclass
class ActionResult:
    ok: bool
    message: str
    observation: Observation


@dataclass
class StepRecord:
    action: Action
    ok: bool
    message: str
    predicted_effects: list[str] = field(default_factory=list)
    verified_effects: list[str] = field(default_factory=list)


@dataclass
class Episode:
    task_id: str
    mode: str
    success: bool
    steps: list[StepRecord]
    final_observation: Observation
    used_skills: list[str] = field(default_factory=list)
    estimated_output_tokens: int = 0

    @property
    def action_count(self) -> int:
        return self.final_observation.action_count

    @property
    def failed_actions(self) -> int:
        return sum(1 for step in self.steps if not step.ok)


@dataclass
class WorldRule:
    rule_id: str
    preconditions: list[str]
    predicted_effects: list[str]
    evidence: list[str] = field(default_factory=list)
    confidence: int = 1


@dataclass
class Skill:
    skill_id: str
    goal_item: str
    preconditions: list[str]
    steps: list[str]
    postconditions: list[str]
    validator: str
    version: int = 1
    evidence: list[str] = field(default_factory=list)

