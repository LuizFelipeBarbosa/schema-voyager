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
# An explore that moved the bot less than this got nowhere: its direction is dead for the goal.
MIN_EXPLORE_BLOCKS = 2
# One that moved it at least this far is a new place, where dead directions get another chance.
NEW_PLACE_BLOCKS = 8
# Failures of pathing, digging or the bot itself, which say nothing about the
# world's rules: retried after an explore, so the bot tries from somewhere else.
# interrupted (v3.2): the bot's guard cut in to fight or flee.
RETRIED_ERRORS = frozenset({"unreachable", "craft_failed", "dig_failed", "timeout", "interrupted"})
MAX_RETRIES = 2
# An interrupted step is retried in place, on a budget of its own: several fights can land on one step at night.
MAX_INTERRUPTS = 4
# After fleeing below REST_BELOW_HEALTH the bot rests (polling status every REST_POLL_S, at most REST_MAX_S)
# until REST_UNTIL_HEALTH before the retry; health only regenerates with food at REST_MIN_FOOD or more.
REST_BELOW_HEALTH = 10
REST_UNTIL_HEALTH = 14
REST_MIN_FOOD = 18
REST_MAX_S = 60
REST_POLL_S = 2
_sleep = time.sleep
_monotonic = time.monotonic
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
    # v3: rule id -> the error that blocked it for the rest of this goal (see agent.py).
    blocked_rules: dict[str, str] = field(default_factory=dict)
    # v2: a natural world (backend.look exists, rules use normalized names) and
    # the time.monotonic() moment after which the episode may not act.
    natural: bool = False
    deadline: float | None = None
    _next_direction: int = 0
    # Compass directions whose explore got nowhere, for the rest of the goal.
    _dead_directions: set[str] = field(default_factory=set)
    _search_exhausted: bool = False
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
        return self.budget_left > 0 and not self.out_of_time and not self._search_exhausted

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
        RETRIED_ERRORS up to 2 more attempts, each after an explore. An
        `interrupted` step (the guard fought) is retried at once, in place and
        without an explore, up to MAX_INTERRUPTS times, apart from those; after
        fleeing badly hurt it first rests (see `_rest_before_retry`). Every
        adapter call is logged and counted as an action; `annotate` adds fields
        (such as the verifier's verdict) to each attempt's step."""
        outcome = self._act_once(action, annotate)
        explores = retries = interrupts = 0
        went_up = False
        previous_direction: str | None = None
        max_explores = MAX_NATURAL_EXPLORES_PER_TARGET if self.natural else MAX_EXPLORES_PER_TARGET
        while not outcome.ok and self.budget_left >= 2 and not self.out_of_time:
            # A natural world's hazard (lava or water next to the target) means "look elsewhere" too.
            searching = outcome.error == "not_found" or (self.natural and outcome.error == "hazard")
            if searching and explores < max_explores:
                explores += 1
                if self.natural and action.kind == "mine":
                    toward = self._explore_toward(action.target, not went_up, previous_direction)
                    if toward is None:
                        self._search_exhausted = True  # every direction is dead: fail now, not at the deadline
                        break
                    explore = toward
                    went_up = went_up or explore.direction == "up"
                    previous_direction = explore.direction
                else:
                    explore = Action("explore")
            elif outcome.error == "interrupted":
                if interrupts >= MAX_INTERRUPTS:
                    break
                if _fled_hurt(outcome) and not self._rest_before_retry(outcome):
                    break
                interrupts += 1
                outcome = self._act_once(action, annotate)
                continue
            elif outcome.error in RETRIED_ERRORS and retries < MAX_RETRIES:
                retries += 1
                explore = Action("explore")
            else:
                break
            self._remember_explore(explore, self._act_once(explore, None))
            outcome = self._act_once(action, annotate)
        return outcome

    def _rest_before_retry(self, outcome: Outcome) -> bool:
        """After a hurt flight (v3.2b), wait for the bot to heal before the in-place retry. False means do not
        retry: it can't heal (food below REST_MIN_FOOD). A backend without `status`, or one that fails, is
        retried at once."""
        from .mineflayer_backend import BackendError  # here, not at the top: it pulls in rcon, which instance_lock's importers lack

        status_of = getattr(self.backend, "status", None)
        if status_of is None:
            return True
        started = _monotonic()
        try:
            status = status_of()
            if status.food < REST_MIN_FOOD:
                return False
            health_before = status.health
            while status.health < REST_UNTIL_HEALTH and _monotonic() - started < REST_MAX_S and not self.out_of_time:
                _sleep(REST_POLL_S)
                status = status_of()
        except BackendError:
            return True
        self.log.event(
            "rest", health_before=health_before, health_after=status.health, food=status.food,
            waited_s=_monotonic() - started,
        )
        return True

    def _explore_toward(self, target: str | None, may_go_up: bool, previous: str | None) -> Action | None:
        """Natural mode: look for the target's raw ids and explore toward the nearest
        hit in a live direction, or up when that hit is well above the bot (it is out
        of a mine's reach until the bot is back near the surface). Never straight back
        where the previous explore of this target came from, unless that is the only
        hit, and never into a dead direction (one that got nowhere this goal).
        With no live hit in sight: with nothing seen at all, first back up to open sky
        (once per target: most resources are at the surface), then the live compass direction
        furthest from the dead ones; when every direction is dead, None."""
        hits = self.backend.look(normalize.members(target), LOOK_RADIUS)  # type: ignore[attr-defined]
        seen = [(found["distance"], direction, found) for direction, blocks in hits.items() for found in blocks.values()]
        live = [hit for hit in seen if hit[1] not in self._dead_directions]
        not_behind = [hit for hit in live if hit[1] != _opposite(previous)]
        nearest = min(not_behind or live, key=lambda hit: hit[0], default=None)
        direction: str | None
        if nearest is not None:
            above = "y" in nearest[2] and nearest[2]["y"] - self.backend.observe().position[1] > HIT_ABOVE_BLOCKS
            direction = "up" if above and may_go_up else nearest[1]
        elif not seen and may_go_up:
            direction = "up"
        else:
            direction = self._next_live_compass_direction() or ("up" if may_go_up else None)
        self.log.event(
            "look", target=target, direction=direction, seen=nearest is not None, dead=sorted(self._dead_directions)
        )
        return Action("explore", direction=direction) if direction else None

    def _next_live_compass_direction(self) -> str | None:
        """The live compass direction furthest from the dead ones, the rotation breaking ties."""
        start = self._next_direction % len(DIRECTIONS)
        rotation = DIRECTIONS[start:] + DIRECTIONS[:start]
        dead = [DIRECTIONS.index(d) for d in self._dead_directions if d in DIRECTIONS]

        def gap_to_dead(direction: str) -> int:
            steps = [abs(DIRECTIONS.index(direction) - d) for d in dead]
            return min((min(s, len(DIRECTIONS) - s) for s in steps), default=0)

        live = [d for d in rotation if d not in self._dead_directions]
        if not live:
            return None
        self._next_direction += 1
        return max(live, key=gap_to_dead)

    def _remember_explore(self, explore: Action, outcome: Outcome) -> None:
        """An explore that got nowhere kills its direction; one that moved the bot far clears them all first.
        A neighbouring direction that did the moving also kills the requested one: it stalled."""
        if explore.direction not in DIRECTIONS:  # "up" is limited per target instead, and a blind explore has none
            return
        distance = outcome.travel.get("distance")
        took = outcome.detail.get("took")
        if not outcome.ok or (distance is not None and distance < MIN_EXPLORE_BLOCKS):
            self._dead_directions.add(explore.direction)
        elif distance is not None and distance >= NEW_PLACE_BLOCKS:
            self._dead_directions.clear()
        if took and took.get("direction") != explore.direction:
            self._dead_directions.add(explore.direction)

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

    def finish(self, success: bool | None = None, announce: bool = True) -> EpisodeResult:
        """`success` overrides the goal check, for a goal that isn't an item to hold. `announce=False` skips the
        "Goal reached" chat line (a user goal's life says "Done: ..." instead, v3.1h)."""
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
        if result.success and announce:
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


def _fled_hurt(outcome: Outcome) -> bool:
    """An interrupted step whose guard fled, with health below REST_BELOW_HEALTH (the `fled` event's, else the
    last `damage` event's)."""
    if outcome.detail.get("outcome") != "fled":
        return False
    for kind in ("fled", "damage"):
        healths = [event["health"] for event in outcome.events if event.get("type") == kind and "health" in event]
        if healths:
            return healths[-1] < REST_BELOW_HEALTH
    return False
