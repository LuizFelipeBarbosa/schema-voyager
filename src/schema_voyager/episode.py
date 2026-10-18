"""Plumbing shared by every system: running one adapter action with the
tolerant explore-and-retry policy, logging it, and building the result."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import normalize
from .adapter import DIRECTIONS, Backend
from .evaluator import actions_after_contradiction, repeated_failures
from .llm import LLM
from .models import Action, EpisodeResult, Outcome, Task
from .trace import EpisodeLog

MAX_EXPLORES_PER_TARGET = 3
MAX_NATURAL_EXPLORES_PER_TARGET = 4
LOOK_RADIUS = 64
# A hit this far above the bot's feet is reached by climbing to open sky, not by walking toward it.
HIT_ABOVE_BLOCKS = 4
# Failures of pathing, digging or the bot itself, which say nothing about the
# world's rules: retried after an explore, so the bot tries from somewhere else.
RETRIED_ERRORS = frozenset({"unreachable", "craft_failed", "dig_failed", "timeout"})
MAX_RETRIES = 2
# The goal item of a life's exploration round: nothing to hold, so its success is reported by the life.
EXPLORE_GOAL = "explore"

Annotator = Callable[[Outcome], dict[str, Any]]


@dataclass
class Episode:
    """State of one running episode, whichever system drives it."""

    episode_id: str
    system: str
    task: Task
    layout_seed: int
    world_id: str
    backend: Backend
    llm: LLM
    log: EpisodeLog
    actions: int = 0
    inventory: dict[str, int] = field(default_factory=dict)
    used_skills: list[str] = field(default_factory=list)
    revisions: int = 0
    # v2: a natural world (backend.look exists, rules use normalized names) and
    # the time.monotonic() moment after which the episode may not act.
    natural: bool = False
    deadline: float | None = None
    _next_direction: int = 0
    _tokens_at_start: int = 0
    _calls_at_start: int = 0

    def __post_init__(self) -> None:
        self._tokens_at_start = self.llm.total_tokens
        self._calls_at_start = len(self.llm.calls)

    @property
    def budget_left(self) -> int:
        return self.task.budget - self.actions

    @property
    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    @property
    def can_act(self) -> bool:
        return self.budget_left > 0 and not self.out_of_time

    @property
    def counts(self) -> dict[str, int]:
        """The inventory in the names rules use: normalized in a natural world."""
        return normalize.counts(self.inventory) if self.natural else self.inventory

    @property
    def goal_met(self) -> bool:
        return self.counts.get(self.task.goal_item, 0) >= self.task.goal_count

    @property
    def llm_tokens(self) -> int:
        return self.llm.total_tokens - self._tokens_at_start

    def act(self, action: Action, annotate: Annotator | None = None) -> Outcome:
        """Run one action with the tolerant policy every system shares, within
        the budget: a target out of view gets up to 3 explores, and an error in
        RETRIED_ERRORS up to 2 more attempts, each after an explore. Every
        adapter call is logged and counted as an action; `annotate` adds fields
        (such as the verifier's verdict) to each attempt's step."""
        outcome = self._act_once(action, annotate)
        explores = retries = 0
        went_up = False
        previous_direction: str | None = None
        max_explores = MAX_NATURAL_EXPLORES_PER_TARGET if self.natural else MAX_EXPLORES_PER_TARGET
        while not outcome.ok and self.budget_left >= 2 and not self.out_of_time:
            # A natural world's hazard (lava or water next to the target) means "look elsewhere" too.
            searching = outcome.error == "not_found" or (self.natural and outcome.error == "hazard")
            if searching and explores < max_explores:
                explores += 1
                if self.natural and action.kind == "mine":
                    explore = self._explore_toward(action.target, not went_up, previous_direction)
                    went_up = went_up or explore.direction == "up"
                    previous_direction = explore.direction
                else:
                    explore = Action("explore")
            elif outcome.error in RETRIED_ERRORS and retries < MAX_RETRIES:
                retries += 1
                explore = Action("explore")
            else:
                break
            self._act_once(explore, None)
            outcome = self._act_once(action, annotate)
        return outcome

    def _explore_toward(self, target: str | None, may_go_up: bool, previous: str | None) -> Action:
        """Natural mode: look for the target's raw ids and explore toward the nearest
        hit, or up when that hit is well above the bot (it is out of a mine's reach
        until the bot is back near the surface). Never straight back where the
        previous explore of this target came from, unless that is the only hit.
        With none in sight, first back up to open sky (once per target: most
        resources are at the surface), then try the next compass direction."""
        hits = self.backend.look(normalize.members(target), LOOK_RADIUS)  # type: ignore[attr-defined]
        seen = [(found["distance"], direction, found) for direction, blocks in hits.items() for found in blocks.values()]
        not_behind = [hit for hit in seen if hit[1] != _opposite(previous)]
        nearest = min(not_behind or seen, key=lambda hit: hit[0], default=None)
        if nearest is not None:
            above = "y" in nearest[2] and nearest[2]["y"] - self.backend.observe().position[1] > HIT_ABOVE_BLOCKS
            direction = "up" if above and may_go_up else nearest[1]
        elif may_go_up:
            direction = "up"
        else:
            direction = DIRECTIONS[self._next_direction % len(DIRECTIONS)]
            self._next_direction += 1
        self.log.event("look", target=target, direction=direction, seen=nearest is not None)
        return Action("explore", direction=direction)

    def _act_once(self, action: Action, annotate: Annotator | None) -> Outcome:
        outcome = self.backend.act(action)
        self.actions += 1
        self.inventory = outcome.inventory
        annotations = annotate(outcome) if annotate else {}
        natural_fields: dict[str, Any] = {}
        if self.natural:
            natural_fields = {"travel": outcome.travel, "events": outcome.events}
            if _moved(outcome.travel):
                self.log.event("travel", index=self.actions, action=action.describe(), **outcome.travel)
        self.log.event(
            "step",
            index=self.actions,
            action=action.describe(),
            action_obj=action.to_dict(),
            ok=outcome.ok,
            error=outcome.error,
            block_removed=outcome.block_removed,
            drops=outcome.drops,
            picked_up=outcome.picked_up,
            inventory_delta=outcome.inventory_delta,
            inventory=outcome.inventory,
            blocks_walked=outcome.blocks_walked,
            detail=outcome.detail,
            **natural_fields,
            **annotations,
        )
        return outcome

    def finish(self, success: bool | None = None) -> EpisodeResult:
        """`success` overrides the goal check, for a goal that isn't an item to hold."""
        final = self.backend.observe()
        self.inventory = final.inventory
        events = self.log.events
        result = EpisodeResult(
            episode_id=self.episode_id,
            system=self.system,
            task_id=self.task.task_id,
            layout_seed=self.layout_seed,
            world_id=self.world_id,
            success=self.goal_met if success is None else success,
            actions=self.actions,
            llm_tokens=self.llm_tokens,
            llm_calls=len(self.llm.calls) - self._calls_at_start,
            blocks_walked=final.blocks_walked,
            repeated_failures=repeated_failures(events),
            contradictions=sum(1 for e in events if e["kind"] == "contradiction"),
            actions_after_contradiction=actions_after_contradiction(events),
            revisions=self.revisions,
            final_inventory=final.inventory,
            used_skills=self.used_skills,
        )
        self.log.event("episode_end", **result.to_dict())
        if result.success:
            reached = "Exploration done." if self.task.goal_item == EXPLORE_GOAL else f"Goal reached: {self.task.goal_count} {self.task.goal_item.replace('#', '')}."
            self.backend.say(reached)
        return result


def _opposite(direction: str | None) -> str | None:
    if direction not in DIRECTIONS:
        return None
    return DIRECTIONS[(DIRECTIONS.index(direction) + len(DIRECTIONS) // 2) % len(DIRECTIONS)]


def _moved(travel: dict[str, Any]) -> bool:
    """Whether getting into position walked, dug or placed anything."""
    return bool(travel.get("distance") or travel.get("dug") or travel.get("placed"))
