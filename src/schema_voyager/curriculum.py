"""The life's curriculum: which item to try to obtain next.

The agent picks the cheapest goal that still teaches it something (its plan
uses a rule that isn't certified yet). The frontier only ever holds items from
WHITELIST, a fixed set of vanilla items reachable with wood, stone, coal and
iron, so the LLM can suggest but never invent goals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import normalize
from .planner import PlanningError, make_plan
from .schema_store import SchemaStore

MAX_FAILURES = 2  # failed attempts after which an item is skipped for the rest of the life

WHITELIST: tuple[str, ...] = (
    "#planks", "stick", "crafting_table",
    "wooden_pickaxe", "wooden_axe", "wooden_shovel", "wooden_sword",
    "stone_pickaxe", "stone_axe", "stone_shovel", "stone_sword",
    "furnace", "torch", "chest", "ladder",
    "iron_ingot", "iron_pickaxe", "iron_axe", "iron_shovel", "iron_sword", "bucket", "shears",
    # Leather armor joins in M3, when leather can be obtained.
    "iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots", "shield",
)
SEED_FRONTIER: tuple[str, ...] = (
    "#planks", "stick", "crafting_table", "wooden_pickaxe", "stone_pickaxe", "torch",
)


KEPT_STATIONS = ("crafting_table", "furnace", "chest")
KEPT_TOOL_SUFFIXES = ("_pickaxe", "_axe", "_shovel", "_sword")
KEPT_ARMOR_SUFFIXES = ("_helmet", "_chestplate", "_leggings", "_boots")


def is_kept_item(item: str) -> bool:
    """A station, tool, armor piece or shield: one is enough, so learn mode doesn't craft another while one is held."""
    return (
        item in KEPT_STATIONS
        or item in ("bucket", "shears", "shield")
        or item.endswith(KEPT_TOOL_SUFFIXES + KEPT_ARMOR_SUFFIXES)
    )


@dataclass(frozen=True)
class Goal:
    item: str  # normalized name
    count: int  # normalized inventory count to reach: current + 1
    plan_length: int
    uncertified: tuple[str, ...]  # rule ids in the plan that aren't certified
    reason: str  # one line, logged to life.jsonl


@dataclass
class Curriculum:
    frontier: list[str] = field(default_factory=lambda: list(SEED_FRONTIER))
    failures: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)  # item -> why

    def next_goal(
        self, inventory: dict[str, int], store: SchemaStore, world_id: str, visible: dict[str, int] | None = None,
        excluded: frozenset[str] = frozenset(),
    ) -> Goal | None:
        """The cheapest frontier goal whose plan uses an uncertified rule (ties: fewest
        uncertified rules, then name), planned with the producers in `visible` preferred. Skipped items, kept items already held and items that
        can't be planned from here are passed over. Rules in `excluded` are left out of the plans."""
        held = normalize.counts(inventory)
        rules = [rule for rule in store.usable_rules(world_id) if rule.id not in excluded]
        best: Goal | None = None
        for item in self.frontier:
            if item in self.skipped or (is_kept_item(item) and held.get(item, 0) >= 1):
                continue
            count = held.get(item, 0) + 1
            try:
                plan = make_plan(item, count, held, rules, store.skill_for, visible=visible)
            except PlanningError:
                continue
            uncertified = tuple(sorted({step.rule.id for step in plan if step.rule.status != "certified"}))
            if not uncertified:
                continue
            goal = Goal(
                item, count, len(plan), uncertified,
                f"{len(plan)} steps, {len(uncertified)} rules still to test",
            )
            if best is None or _order(goal) < _order(best):
                best = goal
        return best

    def record(self, goal: Goal, success: bool) -> None:
        if success:
            return
        self.failures[goal.item] = self.failures.get(goal.item, 0) + 1
        if self.failures[goal.item] >= MAX_FAILURES:
            self.skipped[goal.item] = f"failed {self.failures[goal.item]} attempts"

    def unused_whitelist(self) -> list[str]:
        return [item for item in WHITELIST if item not in self.frontier]

    def extend(self, items: list[str]) -> list[str]:
        """Add suggested items to the frontier; anything outside the whitelist, or
        already there, is dropped. Returns what was added."""
        added = [item for item in dict.fromkeys(items) if item in WHITELIST and item not in self.frontier]
        self.frontier += added
        return added

    def to_dict(self) -> dict[str, Any]:
        return {"frontier": self.frontier, "failures": self.failures, "skipped": self.skipped}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Curriculum":
        return cls(list(payload["frontier"]), dict(payload["failures"]), dict(payload["skipped"]))


def _order(goal: Goal) -> tuple[int, int, str]:
    return goal.plan_length, len(goal.uncertified), goal.item
