"""The runners of kill and guard goals (v3.3i): neither obtains an item, so neither goes down the obtain path.

Like the build runner, they run inside one episode of the life, which hands them its interrupt check and the bot
events it polled during the goal. Their acts are not checked against rules (D1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import normalize
from .agent import BeforeStep, pursue
from .episode import Episode
from .llm import vanilla_mobs
from .mineflayer_backend import BackendError
from .models import Action, EpisodeResult, GoalSpec
from .planner import PlanningError, make_plan
from .schema_store import SchemaStore

ARM_SWORDS = ("iron_sword", "stone_sword", "wooden_sword")  # the arming choices, best first (v3.2e)
NEED_SWORD = "I need a sword first."
WEAPON_TIERS = ("netherite", "diamond", "iron", "stone", "golden", "wooden")
UNARMED_KINDS = frozenset({"animal", "passive"})  # mobs a kill goal fights bare-handed
KILL_SECONDS_PER_KILL = 60  # a kill goal's clock is at least this per kill
FAILED_ACTS_TO_GIVE_UP = 3  # acts in a row that fail with no new kill
GUARD_DISTANCE = 3.0
GUARD_POLL_S = 2.0

_monotonic = time.monotonic
_sleep = time.sleep


@dataclass
class RunnerOutcome:
    """What a runner tells the life besides its result; filled in as it goes, so a cancelled goal has it too."""

    reason: str = ""  # why it failed, in words; empty leaves it to the life's own reading of the episode
    record: dict[str, Any] = field(default_factory=dict)  # fields for the goal's record: `kills`, `guard`


def run_kill(
    episode: Episode, store: SchemaStore, spec: GoalSpec, *, bot_events: list[dict[str, Any]],
    check_interrupts: Callable[[], None], before_step: BeforeStep, excluded: frozenset[str], clock_s: float,
    outcome: RunnerOutcome,
) -> EpisodeResult:
    """Kill `spec.count` of `spec.target`. A kill is a `fight_end` that killed the target after the goal started,
    from the acts' events and the life's poll, counted once by the entity id (so the guard's count too)."""
    outcome.record["kills"] = 0
    if not _arm_if_needed(episode, store, spec, before_step, excluded, always=_needs_weapon(spec.target)):
        outcome.reason = NEED_SWORD
        return episode.finish(success=False, announce=False)

    deadline = _monotonic() + clock_s
    kills = failed_in_a_row = 0
    while kills < spec.count:
        check_interrupts()
        if _monotonic() >= deadline:
            outcome.reason = "out of time"
            break
        if not episode.can_act:
            break  # out of steps: the life's reading of the episode says so
        act = episode.act(Action("kill", spec.target, tool=_best_weapon(episode.inventory)))
        kills_before, kills = kills, len(_unique(_all_events(episode, bot_events), "fight_end", target=spec.target, outcome="killed"))
        outcome.record["kills"] = kills
        failed_in_a_row = 0 if act.ok or kills > kills_before else failed_in_a_row + 1
        if failed_in_a_row == FAILED_ACTS_TO_GIVE_UP:
            break
    return episode.finish(success=kills >= spec.count, announce=False)


def run_guard(
    episode: Episode, store: SchemaStore, spec: GoalSpec, *, bot_events: list[dict[str, Any]],
    check_interrupts: Callable[[], None], before_step: BeforeStep, excluded: frozenset[str], clock_s: float,
    outcome: RunnerOutcome,
) -> EpisodeResult:
    """Follow the player with the guard defending them until the clock, a `follow_end` that says they died or
    were lost, or an interrupt. Following always stops at the end."""
    if not _arm_if_needed(episode, store, spec, before_step, excluded, always=True):
        outcome.reason = NEED_SWORD
        return episode.finish(success=False, announce=False)

    player = episode.backend.follow(spec.target, GUARD_DISTANCE)
    if player is None:
        outcome.reason = f"I can't see {spec.target or 'a player'}."
        return episode.finish(success=False, announce=False)

    started = _monotonic()
    ended_by = None  # "died" or "gone"
    try:
        while True:
            check_interrupts()
            ended_by = next((e["reason"] for e in bot_events if e["type"] == "follow_end" and e.get("reason") in ("died", "gone")), None)
            if ended_by is not None or _monotonic() - started >= clock_s:
                break
            _sleep(GUARD_POLL_S)
    finally:
        outcome.record["guard"] = {
            "player": player, "alive": ended_by != "died", "duration_s": round(_monotonic() - started, 1),
            "fights": len(_unique(bot_events, "fight_start")),
            "kills": len(_unique(bot_events, "fight_end", outcome="killed")),
        }
        try:
            episode.backend.stop_follow()
        except BackendError as error:  # never mask what ended the goal
            print(f"[bot] could not stop following: {error}")

    outcome.reason = {"died": f"{player} died.", "gone": f"I lost sight of {player}."}.get(ended_by, "")
    return episode.finish(success=ended_by is None, announce=False)


def _needs_weapon(mob: str | None) -> bool:
    return vanilla_mobs().get(mob) not in UNARMED_KINDS


def _arm_if_needed(
    episode: Episode, store: SchemaStore, spec: GoalSpec, before_step: BeforeStep, excluded: frozenset[str], *, always: bool
) -> bool:
    """With no sword or axe held, pursue the first of ARM_SWORDS the usable rules reach. Returns whether the bot is
    armed, or needs no weapon (`always` False)."""
    if not always or _best_weapon(episode.inventory) is not None:
        return True
    held = normalize.counts(episode.inventory)
    rules = [rule for rule in store.usable_rules(episode.world_id) if rule.id not in excluded]
    for sword in ARM_SWORDS:
        try:
            make_plan(sword, 1, held, rules, store.skill_for)
        except PlanningError:
            continue
        pursue(episode, store, {sword: 1}, before_step=before_step, excluded=excluded, narrate=False)
        return episode.counts.get(sword, 0) >= 1
    return False


def _best_weapon(inventory: dict[str, int]) -> str | None:
    """The best sword held, else the best axe."""
    for kind in ("sword", "axe"):
        for tier in WEAPON_TIERS:
            if inventory.get(f"{tier}_{kind}", 0) > 0:
                return f"{tier}_{kind}"
    return None


def _all_events(episode: Episode, bot_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bot events of the goal: the life's poll, and those the episode's acts brought back (the poll's may
    not have run yet). The same event can be in both."""
    return bot_events + [e for step in episode.log.events if step["kind"] == "step" for e in step.get("events", [])]


def _unique(events: list[dict[str, Any]], kind: str, **fields: Any) -> set[Any]:
    """The distinct events of this type with these fields, by entity id (the event's `seq` restarts with the bot)."""
    return {
        event.get("id", ("seq", event.get("seq")))
        for event in events
        if event.get("type") == kind and all(event.get(name) == value for name, value in fields.items())
    }
