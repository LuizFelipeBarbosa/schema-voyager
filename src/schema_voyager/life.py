"""One continuous life: the agent picks its own goals, one after another, in the
same world, and learns rules from every step it takes.

Each goal is an Episode. Nothing resets between goals, so the inventory carries
over. After every goal the schema store, the curriculum and a line of
life.jsonl are saved, so a stopped life can be resumed (`resume=True`) in the
same world. Design: docs/v2-interfaces.md, "The life loop".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager, Protocol

from . import normalize
from .adapter import DIRECTIONS, LifeBackend
from .agent import run_schemavoyager
from .curriculum import Curriculum, Goal
from .episode import EXPLORE_GOAL, Episode
from .llm import LLM, SpendCapReached
from .models import Action, EpisodeResult, Observation, Task
from .planner import PlanStep
from .schema_store import SchemaStore
from .trace import EpisodeLog

MIN_GOAL_BUDGET = 30
BUDGET_PER_PLAN_STEP = 4
MAX_CHAT_CHARS = 240
EXPLORES_PER_ROUND = 3
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
    keep_going: bool = False  # explore for new blocks instead of stopping on an empty frontier


@dataclass
class LifeHooks:
    before_step: Callable[[Episode, PlanStep, list[PlanStep]], None] | None = None
    after_goal: Callable[[dict[str, Any]], None] | None = None
    # Called once, with the life's schema store (loaded, on resume), before the first goal.
    bind_store: Callable[[SchemaStore], None] | None = None


# -- the M3 trigger -----------------------------------------------------------

# Each rule change, with the planned step it first bites: (action kind, subject) in a plan step.
CHANGE_STEPS = {"r2": ("mine", "coal_ore"), "stick2": ("craft", "stick"), "planks2": ("craft", "#planks")}
CHANGE_PACKS = {"r2": "file/v2_coal_tool", "stick2": "file/v2_stick2", "planks2": "file/v2_planks2"}
SCRAMBLE = ("stick2", "planks2", "r2")
CHANGE_OPEN_S = 120.0  # a fired change blocks the next one for at most this long
DEMO_RCON_PORT = 25575
PACK_POLL_TIMEOUT_S = 10.0


class RconClient(Protocol):
    def run(self, command: str) -> str: ...


RconFactory = Callable[[], ContextManager[RconClient]]


def rcon_factory(port: int) -> RconFactory:
    """Opens a client on this instance's RCON port; never the live demo's."""
    if port == DEMO_RCON_PORT:
        raise ValueError(f"RCON port {DEMO_RCON_PORT} belongs to the live demo")
    from rcon.source import Client

    return lambda: Client("127.0.0.1", port, passwd=os.environ["RCON_PASSWORD"], timeout=10)


def check_pack_ready(change: str, connect: RconFactory) -> None:
    """The pack must exist and still be disabled, or the trigger has nothing to switch on."""
    pack = CHANGE_PACKS[change]
    with connect() as client:
        enabled, available = client.run("datapack list enabled"), client.run("datapack list available")
    hint = "run scripts/v2-change.sh install"
    if _listed(pack, enabled):
        raise ValueError(f"datapack {pack} is already enabled; {hint} to reset it")
    if not _listed(pack, available):
        raise ValueError(f"datapack {pack} is not installed; {hint}")


def rcon_switch(connect: RconFactory, settle_s: float = 1.0) -> Callable[[str], None]:
    """A `switch_on(change)` that enables the change's datapack, waits until the server
    lists it as enabled, then lets the reload settle."""

    def switch_on(change: str) -> None:
        pack = CHANGE_PACKS[change]
        with connect() as client:
            client.run(f'datapack enable "{pack}"')
            deadline = time.monotonic() + PACK_POLL_TIMEOUT_S
            while not _listed(pack, client.run("datapack list enabled")):
                if time.monotonic() > deadline:
                    raise TimeoutError(f"datapack {pack} not enabled after {PACK_POLL_TIMEOUT_S:.0f} s")
                time.sleep(0.5)
        time.sleep(settle_s)

    return switch_on


def _listed(pack: str, reply: str) -> bool:
    """Whether a `datapack list` reply names the pack: the server prints world packs
    as "[file/v2_coal_tool (world)]" and built-in ones as "[vanilla (built-in)]"."""
    return f"[{pack} (" in reply or f"[{pack}]" in reply


def parse_changes(text: str) -> list[str]:
    """"stick2,r2" -> ["stick2", "r2"]; "scramble" -> every change, in priority order."""
    names = [name.strip() for name in text.split(",") if name.strip()]
    names = [change for name in names for change in (SCRAMBLE if name == "scramble" else (name,))]
    unknown = [name for name in names if name not in CHANGE_STEPS]
    if unknown or not names:
        raise ValueError(f"unknown change {', '.join(unknown) or repr(text)}; use scramble or a list of {', '.join(CHANGE_STEPS)}")
    return list(dict.fromkeys(names))


class ChangeTrigger:
    """A `LifeHooks.before_step` that switches rule changes on, each right before the first
    planned step it changes, once per life, and logs each to changes.jsonl. The agent is not told.

    r2: mining coal ore with a wooden pickaxe, by a rule that already has a confirmation.
    stick2, planks2: crafting sticks / planks, by a certified rule.

    `changes` is in priority order. A change may fire only while no earlier-fired change is
    open: open from firing until a revised rule for its (action, subject) is certified, or
    CHANGE_OPEN_S have passed. At most one change fires per step. It reads the certified
    revisions from the store `bind_store` gives it."""

    def __init__(self, changes: str | list[str], run_dir: Path, switch_on: Callable[[str], None], resume: bool = False):
        self.changes = parse_changes(changes) if isinstance(changes, str) else list(changes)
        self.switch_on = switch_on
        self.log_path = run_dir / "changes.jsonl"
        self.store: SchemaStore | None = None
        if not resume:
            self.log_path.unlink(missing_ok=True)
        self.logged = self._read_log()

    @property
    def fired(self) -> list[str]:
        return [line["change"] for line in self.logged]

    def bind_store(self, store: SchemaStore) -> None:
        self.store = store

    def __call__(self, episode: Episode, step: PlanStep, plan: list[PlanStep]) -> None:
        change = next((c for c in self.changes if c not in self.fired and self._bites(c, step)), None)
        if change is None or self._open_change() is not None:
            return
        self.switch_on(change)
        kind, subject = CHANGE_STEPS[change]
        line = {"t": time.time(), "change": change, "action": kind, "subject": subject, "goal": episode.task.task_id}
        self.logged.append(line)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        episode.log.event("world_change", **{k: v for k, v in line.items() if k != "t"})

    def _bites(self, change: str, step: PlanStep) -> bool:
        kind, subject = CHANGE_STEPS[change]
        action = step.action
        if action.kind != kind or normalize.family(action.target or "") != subject:
            return False
        if change == "r2":
            # A contradiction opens an epoch only for a rule with confirmations (certify() keeps them in evidence).
            return action.tool == "wooden_pickaxe" and bool(step.rule.evidence.get("confirmed"))
        return step.rule.status == "certified"

    def _open_change(self) -> str | None:
        """The first fired change that is still open, if any."""
        for line in self.logged:
            revised = self.store is not None and any(
                rule.source == "revision" and rule.status == "certified" and rule.epoch >= 1
                and (rule.kind, rule.subject) == (line["action"], line["subject"])
                for rule in self.store.rules.values()
            )
            if not revised and time.time() - line["t"] < CHANGE_OPEN_S:
                return line["change"]
        return None

    def _read_log(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_life(
    backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks | None = None, resume: bool = False
) -> dict[str, Any]:
    """Run goals until a stop condition: spend_cap, wall_clock, empty_frontier (not with
    `config.keep_going`, which explores instead) or max_goals.
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
            self.explores_done: int = state.get("explores_done", 0)
            self.repaired: set[str] = set(state.get("repaired", []))
            self.earlier_tokens: int = state["tokens"]
            llm.carried_usd = state["spend_usd"]
        else:
            self.world_id = config.world_id
            self.store = SchemaStore()
            self.curriculum = Curriculum()
            self.asked, self.observed_blocks = set(), set()
            self.goals_done, self.earlier_tokens, self.explores_done = 0, 0, 0
            self.repaired = set()
            self.log_path.write_text("", encoding="utf-8")
        llm.spend_cap_usd = config.spend_cap_usd
        self._calls_marked = len(llm.calls)
        self._certified_marked = self._certified_ids()
        self._repairs_unreported: list[dict[str, Any]] = []  # shown in the next goal's trace and record
        if hooks.bind_store is not None:
            hooks.bind_store(self.store)

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
                if self._repair_dead_ingredients(observation):
                    observation = self._learn_priors()  # the repairs may name families nobody asked about
                goal = self.curriculum.next_goal(
                    normalize.counts(observation.inventory), self.store, self.world_id, normalize.counts(observation.nearby)
                )
                if goal is None:
                    if self._expand_frontier():
                        continue
                    if not self.config.keep_going:
                        return "empty_frontier"
                    self._run_exploration_round()
                    continue
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

    def _repair_dead_ingredients(self, observation: Observation) -> bool:
        """A rule that consumes an item nothing the agent knows produces can never be executed,
        so it is never contradicted. Once the item's family has been asked about, ask for
        alternatives to each such rule, once per rule per life. Returns whether any rule was added."""
        rules = self.store.usable_rules(self.world_id)
        produced = {item for rule in rules for item in rule.yields()}
        dead = {item for rule in rules for item in rule.preconditions.get("consumes", {})} - produced
        dead &= self.asked
        obtainable = sorted(produced | set(normalize.counts(observation.inventory)))
        added = False
        for rule in rules:
            dead_items = sorted(dead & set(rule.preconditions.get("consumes", {})))
            if not dead_items or rule.id in self.repaired:
                continue
            proposed = self.llm.repair_rule_v2(rule, dead_items, obtainable, self.world_id)
            self.repaired.add(rule.id)
            for repair in proposed:
                self.store.add_rule(repair)
            added |= bool(proposed)
            dead_text = " or ".join(item.lstrip("#") for item in dead_items)
            message = f"Rethinking {rule.kind} {rule.subject.lstrip('#')}: nothing I know gives {dead_text}."
            print(f"[repair] {rule.id}: no source for {', '.join(dead_items)}; proposed {[r.id for r in proposed]}")
            self.backend.say(message[:MAX_CHAT_CHARS])
            self._repairs_unreported.append(
                {"rule": rule.id, "dead_items": dead_items, "proposed": [r.id for r in proposed]}
            )
        return added

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
        life_clock_limits_goal = self._remaining_s() <= self.config.goal_wall_clock_s
        episode = Episode(
            episode_id=f"D/life/{name}-{goal.item}", system="D", task=task, layout_seed=0, world_id=self.world_id,
            backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
            deadline=time.monotonic() + min(self.config.goal_wall_clock_s, max(self._remaining_s(), 0.0)),
        )
        log.event("goal", index=index, item=goal.item, count=goal.count, reason=goal.reason, budget=budget)
        for repair in self._repairs_unreported:
            log.event("repair", **repair)

        result = run_schemavoyager(episode, self.store, self.hooks.before_step)
        # Cut short by the life's own clock (not the goal's deadline or budget): no attempt to count.
        interrupted = not result.success and life_clock_limits_goal and episode.out_of_time and episode.budget_left > 0
        if not interrupted:
            self.curriculum.record(goal, result.success)
        self.goals_done = index
        record = self._goal_record(index, goal, budget, result, started_at, interrupted)
        if self._repairs_unreported:
            record["repairs"], self._repairs_unreported = self._repairs_unreported, []
        self._save(record)
        if self.hooks.after_goal is not None:
            self.hooks.after_goal(record)

    def _run_exploration_round(self) -> None:
        """Nothing left to learn here: walk a few compass directions (cycling on across
        rounds) so the next `_learn_priors` sees new blocks, and give skipped goals another try."""
        index = self.goals_done + 1
        started_at = time.time()
        name = f"g{index:03d}"
        observation = self.backend.begin_episode()
        task = Task(
            task_id=name, split="train", goal_item=EXPLORE_GOAL, goal_count=1,
            start_inventory=dict(observation.inventory), world_rules=(), budget=EXPLORES_PER_ROUND,
            description="Explore for new blocks",
        )
        log = EpisodeLog(
            self.config.run_dir / "episodes" / f"D_life_{name}_explore.jsonl", label=f"{name} explore", say=self.backend.say
        )
        episode = Episode(
            episode_id=f"D/life/{name}-explore", system="D", task=task, layout_seed=0, world_id=self.world_id,
            backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
            deadline=time.monotonic() + min(self.config.goal_wall_clock_s, max(self._remaining_s(), 0.0)),
        )
        goal = Goal(EXPLORE_GOAL, 1, 0, (), "nothing left to learn here")
        log.event("goal", index=index, item=goal.item, count=goal.count, reason=goal.reason, budget=task.budget)
        self.backend.say("Nothing left to learn here; exploring for new blocks.")

        all_ok = True
        while episode.can_act and episode.actions < EXPLORES_PER_ROUND:
            direction = DIRECTIONS[self.explores_done % len(DIRECTIONS)]
            self.explores_done += 1
            all_ok &= episode.act(Action("explore", direction=direction)).ok
        result = episode.finish(success=all_ok)

        self.curriculum.skipped.clear()
        self.curriculum.failures.clear()
        self.goals_done = index
        record = self._goal_record(index, goal, task.budget, result, started_at, interrupted=False)
        self._save(record)
        if self.hooks.after_goal is not None:
            self.hooks.after_goal(record)

    def _goal_record(
        self, index: int, goal: Goal, budget: int, result: EpisodeResult, started_at: float, interrupted: bool
    ) -> dict[str, Any]:
        certified = self._certified_ids()
        calls = self.llm.calls[self._calls_marked:]
        record = {
            "index": index, "goal": goal.item, "count": goal.count, "reason": goal.reason,
            "plan_length": goal.plan_length, "budget": budget,
            "outcome": "success" if result.success else "interrupted" if interrupted else "failed", "actions": result.actions,
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
            "world_id": self.world_id, "goal_index": self.goals_done, "explores_done": self.explores_done, "curriculum": self.curriculum.to_dict(),
            "asked": sorted(self.asked), "repaired": sorted(self.repaired), "observed_blocks": sorted(self.observed_blocks),
            "tokens": self.earlier_tokens + self.llm.total_tokens, "spend_usd": self.llm.spent_usd,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _certified_ids(self) -> set[str]:
        return {rule.id for rule in self.store.rules.values() if rule.status == "certified"}

    def _remaining_s(self) -> float:
        return self.config.wall_clock_s - (time.monotonic() - self.started)
