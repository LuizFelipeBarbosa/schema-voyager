"""One continuous life: the agent picks its own goals, one after another, in the
same world, and learns rules from every step it takes.

Each goal is an Episode. Nothing resets between goals, so the inventory carries
over. After every goal the schema store, the curriculum and a line of
life.jsonl are saved, so a stopped life can be resumed (`resume=True`) in the
same world. Design: docs/v2-interfaces.md, "The life loop".
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import normalize
from .adapter import LifeBackend
from .agent import run_schemavoyager
from .curriculum import Curriculum, Goal
from .episode import Episode
from .llm import LLM, SpendCapReached
from .models import EpisodeResult, Observation, Task
from .planner import PlanStep
from .schema_store import SchemaStore
from .trace import EpisodeLog

MIN_GOAL_BUDGET = 30
BUDGET_PER_PLAN_STEP = 4
MAX_CHAT_CHARS = 240
# Blocks around the bot that no rule is worth asking about.
AMBIENT_BLOCKS = frozenset({"air", "cave_air", "void_air", "water", "lava", "bedrock"})


@dataclass
class LifeConfig:
    run_dir: Path
    world_id: str  # "life-<seed>-<yyyymmdd-hhmm>"
    max_goals: int | None = None  # goals in the whole life, resumed sessions included
    wall_clock_s: float = 1800.0  # per session (a resumed life gets a fresh allowance)
    goal_wall_clock_s: float = 300.0
    spend_cap_usd: float = 10.0


@dataclass
class LifeHooks:
    before_step: Callable[[Episode, PlanStep, list[PlanStep]], None] | None = None
    after_goal: Callable[[dict[str, Any]], None] | None = None


def run_life(
    backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks | None = None, resume: bool = False
) -> dict[str, Any]:
    """Run goals until a stop condition: spend_cap, wall_clock, empty_frontier or max_goals.
    Returns a summary: world id, goals done, stop reason, certified rules, tokens, spend.

    `llm` should be a fresh object: on resume its spend starts from the saved total."""
    life = _Life(backend, llm, config, hooks or LifeHooks(), resume)
    stop_reason = life.run()
    return life.summary(stop_reason)


class _Life:
    def __init__(self, backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks, resume: bool):
        self.backend, self.llm, self.config, self.hooks = backend, llm, config, hooks
        self.started = time.monotonic()
        self.state_path = config.run_dir / "life_state.json"
        self.store_path = config.run_dir / "store.json"
        self.log_path = config.run_dir / "life.jsonl"
        config.run_dir.mkdir(parents=True, exist_ok=True)

        if resume:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.world_id: str = state["world_id"]
            self.store = SchemaStore.load(self.store_path)
            self.curriculum = Curriculum.from_dict(state["curriculum"])
            self.asked: set[str] = set(state["asked"])
            self.observed_blocks: set[str] = set(state["observed_blocks"])
            self.goals_done: int = state["goal_index"]
            self.earlier_tokens: int = state["tokens"]
            llm.carried_usd = state["spend_usd"]
        else:
            self.world_id = config.world_id
            self.store = SchemaStore()
            self.curriculum = Curriculum()
            self.asked, self.observed_blocks = set(), set()
            self.goals_done, self.earlier_tokens = 0, 0
            self.log_path.write_text("", encoding="utf-8")
        llm.spend_cap_usd = config.spend_cap_usd
        self._calls_marked = len(llm.calls)
        self._certified_marked = self._certified_ids()

    # -- the loop ---------------------------------------------------------

    def run(self) -> str:
        while True:
            if self.config.max_goals is not None and self.goals_done >= self.config.max_goals:
                return "max_goals"
            if self.llm.spend_cap_hit:
                return "spend_cap"
            if self._remaining_s() <= 0:
                return "wall_clock"
            try:
                observation = self._learn_priors()
                goal = self.curriculum.next_goal(
                    normalize.counts(observation.inventory), self.store, self.world_id, normalize.counts(observation.nearby)
                )
                if goal is None:
                    if self._expand_frontier():
                        continue
                    return "empty_frontier"
            except SpendCapReached:
                return "spend_cap"
            self._run_goal(goal)

    def summary(self, stop_reason: str) -> dict[str, Any]:
        return {
            "world_id": self.world_id,
            "goals": self.goals_done,
            "stop_reason": stop_reason,
            "certified": sorted(self._certified_ids()),
            "tokens": self.earlier_tokens + self.llm.total_tokens,
            "spend_usd": round(self.llm.spent_usd, 4),
        }

    # -- what the LLM is asked ---------------------------------------------

    def _learn_priors(self) -> Observation:
        """Ask for prior rules, in one batched call per round, until every family the
        agent needs has been asked about once. Returns the observation it learned from."""
        observation = self.backend.observe()
        self.observed_blocks |= {normalize.family(block) for block in observation.nearby}
        while True:
            families = self._unasked_families()
            if not families:
                return observation
            rules = self.llm.propose_prior_rules_v2(families, sorted(self.observed_blocks), self.world_id)
            self.asked.update(families)
            for rule in rules:
                if rule.id not in self.store.rules:
                    self.store.add_rule(rule)

    def _unasked_families(self) -> list[str]:
        """Observed blocks, plus every frontier item, rule ingredient and (the first listed)
        tool that no usable rule produces."""
        rules = self.store.usable_rules(self.world_id)
        produced = {item for rule in rules for item in rule.yields()}
        needed = set(self.curriculum.frontier)
        for rule in rules:
            needed.update(rule.preconditions.get("consumes", {}))
            if rule.preconditions.get("station"):
                needed.add(rule.preconditions["station"])
            tools = rule.preconditions.get("tool_any", [])
            if tools and tools[0] not in produced:
                needed.add(tools[0])
        wanted = (needed - produced) | (self.observed_blocks - AMBIENT_BLOCKS)
        return sorted(wanted - self.asked)

    def _expand_frontier(self) -> bool:
        candidates = self.curriculum.unused_whitelist()
        if not candidates:
            return False
        suggested = self.llm.expand_frontier(self.curriculum.frontier, candidates, sorted(self._certified_ids()))
        return bool(self.curriculum.extend(suggested))

    # -- one goal ---------------------------------------------------------

    def _run_goal(self, goal: Goal) -> None:
        index = self.goals_done + 1
        started_at = time.time()
        name = f"g{index:03d}"
        observation = self.backend.begin_episode()
        budget = max(MIN_GOAL_BUDGET, BUDGET_PER_PLAN_STEP * goal.plan_length)
        task = Task(
            task_id=name, split="train", goal_item=goal.item, goal_count=goal.count,
            start_inventory=dict(observation.inventory), world_rules=(), budget=budget,
            description=f"Obtain {goal.count} {goal.item}",
        )
        log = EpisodeLog(
            self.config.run_dir / "episodes" / f"D_life_{name}_{goal.item.replace('#', '')}.jsonl",
            label=f"{name} {goal.item}", say=self.backend.say,
        )
        episode = Episode(
            episode_id=f"D/life/{name}-{goal.item}", system="D", task=task, layout_seed=0, world_id=self.world_id,
            backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
            deadline=time.monotonic() + min(self.config.goal_wall_clock_s, max(self._remaining_s(), 0.0)),
        )
        log.event("goal", index=index, item=goal.item, count=goal.count, reason=goal.reason, budget=budget)
        self.backend.say(f"Next goal: {goal.item} ({goal.reason})"[:MAX_CHAT_CHARS])

        result = run_schemavoyager(episode, self.store, self.hooks.before_step)
        self.curriculum.record(goal, result.success)
        self.goals_done = index
        record = self._goal_record(index, goal, budget, result, started_at)
        self._save(record)
        if self.hooks.after_goal is not None:
            self.hooks.after_goal(record)

    def _goal_record(
        self, index: int, goal: Goal, budget: int, result: EpisodeResult, started_at: float
    ) -> dict[str, Any]:
        certified = self._certified_ids()
        calls = self.llm.calls[self._calls_marked:]
        record = {
            "index": index, "goal": goal.item, "count": goal.count, "reason": goal.reason,
            "plan_length": goal.plan_length, "budget": budget,
            "outcome": "success" if result.success else "failed", "actions": result.actions,
            "certified_now": sorted(certified - self._certified_marked), "certified_total": len(certified),
            "llm_calls": len(calls), "tokens": sum(call.tokens for call in calls),
            "spend_usd": round(self.llm.spent_usd, 4), "started": started_at, "ended": time.time(),
        }
        self._calls_marked, self._certified_marked = len(self.llm.calls), certified
        return record

    # -- persistence ------------------------------------------------------

    def _save(self, record: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        self.store.save(self.store_path)
        state = {
            "world_id": self.world_id, "goal_index": self.goals_done, "curriculum": self.curriculum.to_dict(),
            "asked": sorted(self.asked), "observed_blocks": sorted(self.observed_blocks),
            "tokens": self.earlier_tokens + self.llm.total_tokens, "spend_usd": self.llm.spent_usd,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _certified_ids(self) -> set[str]:
        return {rule.id for rule in self.store.rules.values() if rule.status == "certified"}

    def _remaining_s(self) -> float:
        return self.config.wall_clock_s - (time.monotonic() - self.started)
