"""Plumbing shared by every system: running one adapter action with the
tolerant explore-and-retry policy, logging it, and building the result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .adapter import Backend
from .evaluator import actions_after_contradiction, repeated_failures
from .llm import LLM
from .models import Action, EpisodeResult, Outcome, Task
from .trace import EpisodeLog

MAX_EXPLORES_PER_TARGET = 3
# Failures of pathing, digging or the bot itself, which say nothing about the
# world's rules: retried after an explore, so the bot tries from somewhere else.
RETRIED_ERRORS = frozenset({"unreachable", "craft_failed", "dig_failed", "timeout"})
MAX_RETRIES = 2

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
    _tokens_at_start: int = 0
    _calls_at_start: int = 0

    def __post_init__(self) -> None:
        self._tokens_at_start = self.llm.total_tokens
        self._calls_at_start = len(self.llm.calls)

    @property
    def budget_left(self) -> int:
        return self.task.budget - self.actions

    @property
    def goal_met(self) -> bool:
        return self.inventory.get(self.task.goal_item, 0) >= self.task.goal_count

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
        while not outcome.ok and self.budget_left >= 2:
            if outcome.error == "not_found" and explores < MAX_EXPLORES_PER_TARGET:
                explores += 1
            elif outcome.error in RETRIED_ERRORS and retries < MAX_RETRIES:
                retries += 1
            else:
                break
            self._act_once(Action("explore"), None)
            outcome = self._act_once(action, annotate)
        return outcome

    def _act_once(self, action: Action, annotate: Annotator | None) -> Outcome:
        outcome = self.backend.act(action)
        self.actions += 1
        self.inventory = outcome.inventory
        annotations = annotate(outcome) if annotate else {}
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
            **annotations,
        )
        return outcome

    def finish(self) -> EpisodeResult:
        final = self.backend.observe()
        self.inventory = final.inventory
        events = self.log.events
        result = EpisodeResult(
            episode_id=self.episode_id,
            system=self.system,
            task_id=self.task.task_id,
            layout_seed=self.layout_seed,
            world_id=self.world_id,
            success=self.goal_met,
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
        return result
