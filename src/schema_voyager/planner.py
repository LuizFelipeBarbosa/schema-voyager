"""Backward-chaining planner over rules.

The planner sees only rules from the schema store. It never reads the
simulator's recipe tables, so a wrong rule produces a wrong plan, which the
step check then catches.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from .models import Action, Rule, Skill

MAX_DEPTH = 12


class PlanningError(Exception):
    pass


@dataclass
class PlanStep:
    action: Action
    rule: Rule
    expected_inventory: dict[str, int]

    @property
    def experimental(self) -> bool:
        """Steps that rely on an uncertified rule are marked experiments."""
        return self.rule.status != "certified"


def make_plan(
    goal_item: str,
    goal_count: int,
    inventory: dict[str, int],
    rules: list[Rule],
    skill_for: Callable[[str], Skill | None] = lambda item: None,
) -> list[PlanStep]:
    planner = _Planner(rules, skill_for)
    return planner.achieve(goal_item, goal_count, Counter(inventory), [])


class _Planner:
    def __init__(self, rules: list[Rule], skill_for: Callable[[str], Skill | None]):
        self.producers: dict[str, list[Rule]] = {}
        for rule in rules:
            for item, count in rule.yields().items():
                if count > 0:
                    self.producers.setdefault(item, []).append(rule)
        self.skill_for = skill_for

    def achieve(self, item: str, count: int, inv: Counter, stack: list[str]) -> list[PlanStep]:
        if inv[item] >= count:
            return []
        if item in stack or len(stack) > MAX_DEPTH:
            raise PlanningError(f"circular need for {item}")

        candidates = self.producers.get(item, [])
        if not candidates:
            raise PlanningError(f"no usable rule produces {item}")
        skill = self.skill_for(item)
        preferred = set(skill.rule_ids) if skill else set()
        candidates = sorted(
            candidates,
            key=lambda r: (r.id not in preferred, r.status != "certified", r.rank, r.id),
        )

        reasons = []
        for rule in candidates:
            trial = inv.copy()
            try:
                if rule.kind == "craft":
                    steps = self._craft(rule, item, count, trial, stack + [item])
                else:
                    steps = self._mine(rule, item, count, trial, stack + [item])
            except PlanningError as error:
                reasons.append(f"{rule.id}: {error}")
                continue
            _replace(inv, trial)
            return steps
        raise PlanningError(f"cannot obtain {item} ({'; '.join(reasons)})")

    def _craft(self, rule: Rule, item: str, count: int, inv: Counter, stack: list[str]) -> list[PlanStep]:
        runs = math.ceil((count - inv[item]) / rule.yields()[item])
        needs = {ingredient: n * runs for ingredient, n in rule.preconditions.get("consumes", {}).items()}
        station = rule.preconditions.get("station")
        if station:
            needs_with_station = {station: 1, **needs}
        else:
            needs_with_station = needs

        # Gathering one ingredient can spend another (sticks spend planks), so
        # repeat until every need holds at once.
        steps: list[PlanStep] = []
        for _ in range(4):
            for ingredient in sorted(needs_with_station, key=self._depth, reverse=True):
                steps += self.achieve(ingredient, needs_with_station[ingredient], inv, stack)
            if all(inv[i] >= n for i, n in needs_with_station.items()):
                break
        else:
            raise PlanningError(f"could not hold every ingredient for {item} at once")

        for ingredient, n in needs.items():
            inv[ingredient] -= n
        for gained, n in rule.yields().items():
            inv[gained] += n * runs
        steps.append(PlanStep(Action("craft", rule.subject, times=runs), rule, _snapshot(inv)))
        return steps

    def _mine(self, rule: Rule, item: str, count: int, inv: Counter, stack: list[str]) -> list[PlanStep]:
        tools = rule.preconditions.get("tool_any", [])
        steps: list[PlanStep] = []
        tool = None
        if tools:
            tool = next((t for t in tools if inv[t] > 0), None)
            if tool is None:
                tool, steps = self._cheapest_tool(tools, inv, stack)

        mines = math.ceil((count - inv[item]) / rule.yields()[item])
        for _ in range(mines):
            for gained, n in rule.yields().items():
                inv[gained] += n
            steps.append(PlanStep(Action("mine", rule.subject, tool=tool), rule, _snapshot(inv)))
        return steps

    def _cheapest_tool(self, tools: list[str], inv: Counter, stack: list[str]) -> tuple[str, list[PlanStep]]:
        best: tuple[str, list[PlanStep], Counter] | None = None
        for tool in tools:
            trial = inv.copy()
            try:
                steps = self.achieve(tool, 1, trial, stack)
            except PlanningError:
                continue
            if best is None or len(steps) < len(best[1]):
                best = (tool, steps, trial)
        if best is None:
            raise PlanningError(f"no obtainable tool among {tools}")
        _replace(inv, best[2])
        return best[0], best[1]

    def _depth(self, item: str, seen: frozenset[str] = frozenset()) -> int:
        """How many crafts deep an item is; gathered first when deeper."""
        crafts = [r for r in self.producers.get(item, []) if r.kind == "craft"]
        if not crafts or item in seen:
            return 0
        ingredients = crafts[0].preconditions.get("consumes", {})
        return 1 + max((self._depth(i, seen | {item}) for i in ingredients), default=0)


def _replace(target: Counter, source: Counter) -> None:
    target.clear()
    target.update(source)


def _snapshot(inv: Counter) -> dict[str, int]:
    return {item: count for item, count in sorted(inv.items()) if count > 0}
