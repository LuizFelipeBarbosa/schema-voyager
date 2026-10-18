"""One continuous life: the agent picks its own goals, one after another, in the
same world, and learns rules from every step it takes.

Each goal is an Episode. Nothing resets between goals, so the inventory carries
over. After every goal the schema store, the curriculum and a line of
life.jsonl are saved, so a stopped life can be resumed (`resume=True`) in the
same world. Design: docs/v2-interfaces.md, "The life loop".
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ContextManager, Literal, Protocol

from . import blueprint as blueprints
from . import builder, normalize
from .adapter import DIRECTIONS, LifeBackend
from .agent import run_schemavoyager
from .build_runner import BUILD_GOAL, BuildOutcome, prepare_build, run_build
from .curriculum import Curriculum, Goal
from .episode import EXPLORE_GOAL, Episode
from .goal_runners import ARM_SWORDS, KILL_SECONDS_PER_KILL, RunnerOutcome, run_guard, run_kill
from .goals import GoalEntry, GoalInterrupted, GoalQueue, goal_label
from .inventory import SCAFFOLD_BLOCKS, junk_to_store, junk_to_toss
from .llm import LLM, LLMError, SpendCapReached, vanilla_foods, vanilla_items
from .mineflayer_backend import BackendError
from .models import HOME_RANGE, Action, EpisodeResult, GoalSpec, HomeStock, Observation, Rule, Task, Unsupported
from .places import HOME_STATION_KINDS, Home, Places, blames_slot, home_box, home_from_build, home_from_protected, yard_scan_box, yard_slots
from .planner import PlanningError, PlanStep, make_plan
from .schema_store import TRAIN_WORLD, SchemaStore
from .trace import EpisodeLog
from .verifier import kind_of

MIN_GOAL_BUDGET = 30
GOAL_SECONDS_PER_PLAN_STEP = 30  # a user goal's clock: at least this per planned step
BUDGET_PER_PLAN_STEP = 4
MAX_CHAT_CHARS = 240
EXPLORES_PER_ROUND = 3
SLOTS_TRIED_PER_STATION = 3  # yard cells furnishing tries for one station before giving up on it (v3.5c)
MIN_FREE_SLOTS = 4  # a goal starts with at least this many free slots, if junk can make them
BLOCKED_GOALS_TO_EXCLUDE = 2  # goals that block a rule with missing_ingredients before the life leaves it out
# A goal that fails with one of these is followed by an exploration round, to start the next one elsewhere.
MISSING_RESOURCE_ERRORS = frozenset({"not_found", "unreachable"})
# Blocks around the bot that no rule is worth asking about.
AMBIENT_BLOCKS = frozenset({"air", "cave_air", "void_air", "water", "lava", "bedrock"})
FINAL_GOAL_STATUSES = frozenset({"done", "failed", "cancelled", "unsupported"})
GOAL_RANK = {"system": 0, "user": 1, "curriculum": 2}  # a queued goal of a lower rank preempts the running one
CHAT_COMMANDS_HELP = "Commands: !goal <what>, !yes [id], !no [id], !cancel [id], !stop, !status"
BOT_LOSSES_TO_FAIL = 3  # a goal that loses the bot this many times in a life fails (v3.0h)
BOT_STUCK_LOSSES = 3  # this many bot losses within BOT_STUCK_WINDOW_S, none with an action answered between, stop the life (v3.0i)
BOT_STUCK_WINDOW_S = 300.0
DEATHS_TO_STOP = 3  # this many deaths within DEATH_WINDOW_S stop the life (v3.2d)
DEATH_WINDOW_S = 600.0
USER_DEATHS_TO_FAIL = 2
HUNGER_BELOW = 8  # a food level under this, with nothing edible held, queues a hunger goal (v3.2e)
HUNGER_FOOD_COUNT = 4
HUNGRY_LOG_EVERY_S = 300.0
HUNGER_RETRY_S = 900.0  # a food whose hunger goal failed isn't chosen again for this long
UNSEEN_MOB_S = 600.0  # a mob not seen for this long makes a hunger plan that kills it rank later (v3.3h)
ARM_BUDGET = 40  # steps a kill or guard goal may spend arming itself, and the guard's whole budget
KILL_BUDGET_PER_KILL = 20
ARM_RETRY_S = 900.0  # a sword whose arming goal failed isn't chosen again for this long
# Foods the bot's auto-eat never eats (v3.2g), so they don't count as edible.
AUTO_EAT_BANNED = frozenset(
    {"pufferfish", "spider_eye", "poisonous_potato", "rotten_flesh", "chorus_fruit", "chicken", "suspicious_stew", "golden_apple"}
)


@dataclass
class LifeConfig:
    run_dir: Path
    world_id: str  # "life-<seed>-<yyyymmdd-hhmm>"
    max_goals: int | None = None  # goals in the whole life, resumed sessions included
    wall_clock_s: float = 1800.0  # per session (a resumed life gets a fresh allowance)
    goal_wall_clock_s: float = 300.0
    spend_cap_usd: float = 10.0
    keep_going: bool = False  # explore for new blocks instead of stopping on an empty frontier
    mode: Literal["learn", "play"] = "learn"  # play: only queued goals, idle otherwise (v3.1d)
    idle_poll_s: float = 2.0  # play mode, nothing queued: sleep between idle passes
    bot_wait_s: float = 120.0  # v3.0h: how long a lost bot is waited for before stop_reason "bot_down"
    bot_poll_s: float = 2.0  # v3.0h: between connected() checks while waiting


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


def _rule_name(rule: Rule) -> str:
    return f"{rule.kind} {rule.subject.lstrip('#')}"


def run_life(
    backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks | None = None, resume: bool = False
) -> dict[str, Any]:
    """Run goals until a stop condition: spend_cap, wall_clock, empty_frontier (not with
    `config.keep_going`, which explores instead) or max_goals.
    Returns a summary: world id, goals done, stop reason, certified rules, tokens, spend.

    `llm` should be a fresh object: on resume its spend starts from the saved total."""
    life = _Life(backend, llm, config, hooks or LifeHooks(), resume)
    live_path = config.run_dir / "live.json"  # v3.0h: tells the panel which life is running
    live_path.write_text(
        json.dumps({"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds"), "mode": config.mode}) + "\n",
        encoding="utf-8",
    )
    try:
        stop_reason = life.run()
    finally:
        live_path.unlink(missing_ok=True)
    return life.summary(stop_reason)


class _Life:
    def __init__(self, backend: LifeBackend, llm: LLM, config: LifeConfig, hooks: LifeHooks, resume: bool):
        self.backend, self.llm, self.config, self.hooks = backend, llm, config, hooks
        self.started = time.monotonic()
        self.state_path = config.run_dir / "life_state.json"
        self.store_path = config.run_dir / "store.json"
        self.log_path = config.run_dir / "life.jsonl"
        self.events_path = config.run_dir / "life_events.jsonl"
        config.run_dir.mkdir(parents=True, exist_ok=True)
        self.queue = GoalQueue(config.run_dir / "goals.jsonl")
        self._ring_scans: dict[str, tuple[Any, dict]] = {}  # build goal id -> its first attempt's ring scan (v3.0i)

        if resume:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.world_id: str = state["world_id"]
            self.store = SchemaStore.load(self.store_path)
            self.curriculum = Curriculum.from_dict(state["curriculum"])
            self.asked: set[str] = set(state["asked"])
            self.observed_blocks: set[str] = set(state["observed_blocks"])
            self.observed_mobs: dict[str, float] = dict(state.get("observed_mobs", {}))  # mob -> time.time() last seen
            self.asked_mobs: set[str] = set(state.get("asked_mobs", []))
            self.goals_done: int = state["goal_index"]
            self.explores_done: int = state.get("explores_done", 0)
            self.repaired: set[str] = set(state.get("repaired", []))
            self.blocked: dict[str, int] = dict(state.get("blocked", {}))
            self.retests: dict[str, str] = dict(state.get("retests", {}))  # retest id -> "match" | "mismatch"
            self.deaths: list[float] = list(state.get("deaths", []))  # time.time() of each death
            self.protected: list[dict[str, Any]] = list(state.get("protected", []))  # what the life built (v3.4b)
            self.places = Places.from_json(state.get("places"))  # the home, its stations and chest (v3.5b)
            self.earlier_tokens: int = state["tokens"]
            llm.carried_usd = state["spend_usd"]
        else:
            self.world_id = config.world_id
            self.store = SchemaStore()
            self.curriculum = Curriculum()
            self.asked, self.observed_blocks = set(), set()
            self.observed_mobs, self.asked_mobs = {}, set()
            self.goals_done, self.earlier_tokens, self.explores_done = 0, 0, 0
            self.repaired = set()
            self.blocked = {}
            self.retests = {}
            self.deaths = []
            self.protected = []
            self.places = Places()
            for path in (self.log_path, self.events_path, self.queue.path):
                path.write_text("", encoding="utf-8")
        if resume:
            self.queue.requeue_running()
        llm.spend_cap_usd = config.spend_cap_usd
        self._calls_marked = len(llm.calls)
        self._certified_marked = self._certified_ids()
        self._repairs_unreported: list[dict[str, Any]] = []  # shown in the next goal's trace and record
        self._retests_unreported: list[dict[str, Any]] = []
        self._polls_bot = hasattr(backend, "status")  # v2's scripted test backends have none
        self.bot_seq: int = backend.status().last_seq if self._polls_bot else 0  # events from before the life are skipped
        self._running: GoalSpec | None = None  # only while _run_goal runs it
        self._inventory_seen: dict[str, int] = {}  # the last inventory the life observed, for parsing goals
        self._food: int | None = None  # the food level of the latest poll
        self._died_in_goal = False  # the bot died since the running goal started
        self._goal_events: list[dict[str, Any]] = []  # fight and follow events polled since the running goal started
        self._goal_speakers: dict[str, str | None] = {}  # goal id -> the player whose !goal submitted it
        self._last_hungry_log: float | None = None  # time.monotonic()
        self._system_failed_at: dict[str, float] = {}  # item of a failed hunger or arming goal -> time.monotonic()
        self._cancelled_in_chat: set[str] = set()  # their ending doesn't say "Cancelled" a second time
        self._bot_gone = False  # the bot did not come back within bot_wait_s: nothing may call it any more
        self._bot_losses: dict[str, int] = {}  # goal id -> times it lost the bot (v3.0h)
        self._loss_times: list[float] = []  # time.monotonic() of the bot losses since the bot last answered an action (v3.0i)
        self._bot_stuck = False  # the bot is connected but keeps answering busy: the life stops with "bot_stuck"
        if hooks.bind_store is not None:
            hooks.bind_store(self.store)
        if resume and "places" not in state:  # a life saved before v3.5: its first house is home
            def blueprint_of(goal_id: str) -> dict | None:
                goal = self.queue.get(goal_id)
                return goal.spec.blueprint if goal is not None and goal.spec is not None else None

            home = home_from_protected(self.protected, blueprint_of)
            if home is not None:
                self._set_home(home)
                self._save_state()
        self._send_places()
        if hasattr(backend, "stop_follow"):  # v2's scripted test backends have none
            try:
                backend.stop_follow()  # a bot left following by an earlier life
            except BackendError as error:
                print(f"[bot] could not stop following at the start: {error}")  # the first pass waits for the bot

    # -- the loop ---------------------------------------------------------

    def run(self) -> str:
        while True:
            if self.config.max_goals is not None and self.goals_done >= self.config.max_goals:
                return "max_goals"
            if self.llm.spend_cap_hit:
                return "spend_cap"
            if self._remaining_s() <= 0:
                return "wall_clock"
            if sum(time.time() - died <= DEATH_WINDOW_S for died in self.deaths) >= DEATHS_TO_STOP:
                return "deaths"
            try:
                stop_reason = self._run_pass()
            except BackendError as error:  # the bot is down (v3.0h): wait for it, then try the pass again
                running = next((g.id for g in self.queue.goals() if g.status == "running"), None)
                back = self._wait_for_bot(running, error)
                self.queue.requeue_running()
                stop_reason = None if back else ("bot_stuck" if self._bot_stuck else "bot_down")
            if stop_reason is not None:
                return stop_reason

    def _run_pass(self) -> str | None:
        """One pass of the loop: poll, queue and run a goal (or idle). Returns the stop reason, else None."""
        self._poll_bot(raises=True)
        self._queue_hunger_goal()
        self._parse_pending()
        self._settle_parents()
        spec = self.queue.next()
        if spec is None and self.config.mode == "play":
            time.sleep(self.config.idle_poll_s)
            return None
        goal = None
        try:
            observation = self._learn_priors()
            if self._repair_dead_ingredients(observation):
                observation = self._learn_priors()  # the repairs may name families nobody asked about
            if spec is None:
                held = normalize.counts(observation.inventory)
                held |= {kind: max(held.get(kind, 0), 1) for kind in self.places.stations}  # a home station counts as held
                goal = self.curriculum.next_goal(
                    held, self.store, self.world_id, normalize.counts(observation.nearby),
                    excluded=self._excluded_rules(),
                )
                if goal is None:
                    if self._expand_frontier():
                        return None
                    if not self.config.keep_going:
                        return "empty_frontier"
                    self._run_exploration_round()
                    return None
                spec = GoalSpec(
                    id=f"g{self.goals_done + 1:03d}", kind="obtain", source="curriculum", item=goal.item, count=goal.count
                )
        except SpendCapReached:
            return "spend_cap"
        if self._run_goal(spec, goal) and self.config.mode == "learn" and spec.source == "curriculum":
            self._run_exploration_round(reason="the resource was not found here", reset_skips=False)
        if self._bot_stuck:
            return "bot_stuck"
        return "bot_down" if self._bot_gone else None

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
        self._inventory_seen = observation.inventory
        self.observed_blocks |= {normalize.family(block) for block in observation.nearby}
        self._observe_mobs(observation.entities, with_kill_targets=True)
        while True:
            families = self._unasked_families()
            mobs = sorted(set(self.observed_mobs) - self.asked_mobs)
            if not families and not mobs:
                return observation
            rules = self.llm.propose_prior_rules_v2(families, sorted(self.observed_blocks), self.world_id, mobs=mobs)
            self.asked.update(families)
            self.asked_mobs.update(mobs)
            for rule in rules:
                if rule.id not in self.store.rules:
                    self.store.add_rule(rule)

    def _observe_mobs(self, entities: dict[str, dict[str, Any]], with_kill_targets: bool = False) -> None:
        """Remember the mobs in view (an observation's or a status's entities), and with `with_kill_targets` the
        targets of the kill goals in the queue, as seen now (v3.3h)."""
        now = time.time()
        in_view = [name for name, info in entities.items() if info.get("kind") in ("animal", "hostile", "mob")]
        targets = [
            g.spec.target for g in self.queue.goals()
            if g.status in ("queued", "running") and g.spec is not None and g.spec.kind == "kill" and g.spec.target
        ] if with_kill_targets else []
        self.observed_mobs.update({mob: now for mob in in_view + targets})

    def _unasked_families(self) -> list[str]:
        """Observed blocks, plus every frontier item, rule ingredient and (the first listed)
        tool that no usable rule produces."""
        rules = self.store.usable_rules(self.world_id)
        produced = {item for rule in rules for item in rule.yields()}
        needed = set() if self.config.mode == "play" else set(self.curriculum.frontier)
        needed |= self._obtain_items("queued", "running") | self._build_families("awaiting_approval", "queued", "running")
        for rule in rules:
            if rule.kind == "kill":  # the meat a kill drops gets smelted: ask for the cooked item
                needed.update(f"cooked_{item}" for item in rule.effects.get("allowed", {}) if f"cooked_{item}" in vanilla_items())
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
        so it is never contradicted. Once the item's family has been asked about, a demoted rule that
        produces the item is retested first; then ask for alternatives to each such rule, once per rule
        per life. Returns whether a retest was started or a rule was added."""
        started = self._retest_demoted()
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
            kept, skipped = [], []
            for repair in proposed:
                why = self._useless_repair_reason(repair, dead)
                if why:
                    skipped.append({"id": repair.id, "why": why})
                    continue
                self.store.add_rule(repair)
                self.repaired.add(repair.id)  # a repair is never repaired
                kept.append(repair)
            proposed = kept
            added |= bool(proposed)
            dead_text = " or ".join(item.lstrip("#") for item in dead_items)
            message = f"Rethinking {rule.kind} {rule.subject.lstrip('#')}: nothing I know gives {dead_text}."
            print(f"[repair] {rule.id}: no source for {', '.join(dead_items)}; proposed {[r.id for r in proposed]}")
            self.backend.say(message[:MAX_CHAT_CHARS])
            self._repairs_unreported.append(
                {"rule": rule.id, "dead_items": dead_items, "proposed": [r.id for r in proposed], "skipped": skipped}
            )
        return started or added

    def _retest_demoted(self) -> bool:
        """Report the outcome of retests that have one, then retest demoted rules whose product is needed
        and produced by nothing else, in passes (a retest can make another item needed). Each rule is
        retested at most once per life (v3.0f). Returns whether any retest started."""
        self._report_retest_outcomes()
        started = False
        while True:
            rules = self.store.usable_rules(self.world_id)
            produced = {item for rule in rules for item in rule.yields()}
            pass_started = False
            for item in sorted(self._needed_items(rules, produced) & self.asked):
                if item in produced:
                    continue  # a retest started in this pass already yields it
                original = self._best_demoted_producer(item)
                if original is None:
                    continue
                retest = self.store.retest(original.id, self.world_id)
                demoted_by = self.store.demoted_in[self.world_id][original.id]
                self._announce_retest(
                    f"Trying {_rule_name(original)} again: nothing else I know gives {item.lstrip('#')}.",
                    {"rule": original.id, "retest": retest.id, "outcome": "started", "transition": demoted_by, "item": item},
                )
                produced |= set(retest.yields())
                pass_started = True
            if not pass_started:
                return started
            started = True

    def _report_retest_outcomes(self) -> None:
        for retest in list(self.store.rules.values()):
            if retest.source != "retest" or retest.id in self.retests:
                continue
            name = _rule_name(retest)
            demoted_by = self.store.demoted_in.get(self.world_id, {}).get(retest.id)
            confirmed = self.store.replay(retest)[0]
            if demoted_by is not None:
                outcome, transition, message = "mismatch", demoted_by, f"{name} really changed; I won't use it again."
            elif confirmed:
                outcome, transition, message = "match", confirmed[0].id, f"{name} works again."
            else:
                continue  # no step has used it yet
            self.retests[retest.id] = outcome
            self._announce_retest(
                message, {"rule": retest.revision_of, "retest": retest.id, "outcome": outcome, "transition": transition, "item": None}
            )

    def _needed_items(self, rules: list[Rule], produced: set[str]) -> set[str]:
        """Items nothing usable produces that a usable rule consumes, uses as a station or needs as its
        first tool (when it has no producible tool), or that are on the frontier (not in play mode)."""
        needed = set() if self.config.mode == "play" else set(self.curriculum.frontier)
        for rule in rules:
            needed.update(rule.preconditions.get("consumes", {}))
            if rule.preconditions.get("station"):
                needed.add(rule.preconditions["station"])
            tools = rule.preconditions.get("tool_any", [])
            if tools and not produced.intersection(tools):
                needed.add(tools[0])
        return needed - produced

    def _best_demoted_producer(self, item: str) -> Rule | None:
        """The demoted rule with the most confirmations that yields `item` and has not been retested."""
        demoted_here = self.store.demoted_in.get(self.world_id, {})
        retested = {rule.revision_of for rule in self.store.rules.values() if rule.source == "retest"}
        producers = [
            rule for rule in self.store.rules.values()
            if item in rule.yields() and rule.scope in (TRAIN_WORLD, self.world_id) and rule.id in demoted_here
            and rule.source != "retest" and rule.id not in retested
        ]
        return max(producers, key=lambda rule: len(self.store.replay(rule)[0]), default=None)  # first of the ties

    def _announce_retest(self, message: str, event: dict[str, Any]) -> None:
        print(f"[retest] {event['retest']}: {event['outcome']}")
        self.backend.say(message[:MAX_CHAT_CHARS])
        self._retests_unreported.append(event)

    def _useless_repair_reason(self, repair: Rule, dead: set[str]) -> str | None:
        """Why a proposed repair is not worth adding: it repeats a known rule or still consumes a dead item."""
        for known in self.store.rules.values():
            if (known.action, known.preconditions, known.effects) == (repair.action, repair.preconditions, repair.effects):
                return f"same as {known.id}"
        still_dead = sorted(dead & set(repair.preconditions.get("consumes", {})))
        return f"still consumes {still_dead[0]}" if still_dead else None

    def _expand_frontier(self) -> bool:
        candidates = self.curriculum.unused_whitelist()
        if not candidates:
            return False
        suggested = self.llm.expand_frontier(self.curriculum.frontier, candidates, sorted(self._certified_ids()))
        return bool(self.curriculum.extend(suggested))

    # -- the bot's chat and the goal queue --------------------------------

    def _poll_bot(self, raises: bool = False) -> None:
        """Handle the chat commands (v3.1g) and deaths (v3.2d) the bot recorded since the last poll, and arm the
        bot if it met a threat (v3.2e). A lost bot (v3.0h) raises `BackendError` for the pass to wait on if `raises`;
        else it is only printed, as the step's own action will raise."""
        if not self._polls_bot or self._bot_gone:
            return
        since = self.bot_seq
        try:
            status = self.backend.status(since=since)
            if status.last_seq < since:  # the bot restarted: its events start over
                since = 0
                status = self.backend.status(since=0)
        except BackendError as error:
            if raises:
                raise
            print(f"[bot] could not poll events: {error}")
            return
        self.bot_seq = status.last_seq
        self._food = status.food
        self._observe_mobs(status.entities)
        threat_seen = False
        for event in sorted(status.events, key=lambda e: e["seq"]):
            if event["seq"] <= since:
                continue
            if event["type"] == "chat_command":
                self._handle_chat_command(event)
            elif event["type"] == "death":
                self._handle_death(event)
            elif event["type"] == "threat":
                threat_seen = True
            elif event["type"] == "mob_seen" and event.get("kind") in ("animal", "hostile", "mob"):
                # a mob passed during a long action (v3.0i); never older than a sighting already recorded
                self.observed_mobs[event["mob"]] = max(self.observed_mobs.get(event["mob"], 0.0), event["t"])
            if event["type"] in ("fight_start", "fight_end", "follow_end") and self._running is not None:
                self._goal_events.append(event)
        if threat_seen:
            self._queue_arming_goal()

    def _handle_death(self, event: dict[str, Any]) -> None:
        """Count the death, log what it cost, and remember that the running goal, if any, died (v3.2d)."""
        self.deaths.append(time.time())
        self._died_in_goal |= self._running is not None
        before = self._inventory_seen
        try:
            after = self.backend.observe().inventory
        except BackendError as error:
            print(f"[bot] could not observe after a death: {error}")
            after = before
        self._inventory_seen = after
        lost = {item: count - after.get(item, 0) for item, count in before.items() if count > after.get(item, 0)}
        self._save_state()  # an idle death must survive a --resume
        self._log_event(
            "death", goal_id=self._running.id if self._running else None, position=event.get("position"), inventory_lost=lost
        )

    def _queue_hunger_goal(self) -> None:
        """When food is low, nothing edible is held and no system goal is open, queue a goal for the best food
        the usable rules can reach, or log that the bot is hungry (v3.2e)."""
        if self._food is None or self._food >= HUNGER_BELOW:
            return
        if self._system_goal_is_open():
            return
        foods = self._edible_foods()
        inventory = self._inventory_seen  # an idle pass never observes: that takes the bot's lock
        if any(inventory.get(food, 0) > 0 for food in foods):
            return
        plans = {food: self._reachable_plan(food, HUNGER_FOOD_COUNT, HUNGER_RETRY_S) for food in foods}
        reachable = [food for food, plan in plans.items() if plan is not None]
        if reachable:
            best = min(reachable, key=lambda food: self._hunger_rank(food, plans[food], foods[food]))
            self._queue_goal("hunger", best, HUNGER_FOOD_COUNT)
            return
        if self._last_hungry_log is None or time.monotonic() - self._last_hungry_log >= HUNGRY_LOG_EVERY_S:
            self._last_hungry_log = time.monotonic()
            self._log_event("hungry", food=self._food)

    def _hunger_rank(self, food: str, plan: list[PlanStep], points: int) -> tuple[int, int, int, str]:
        """Lowest first: no stochastic mining, then no kill of a mob not seen in the last 600 s, then the most points (v3.3h)."""
        stochastic_mine = any(step.rule.kind == "mine" and kind_of(step.rule, step.action) == "stochastic" for step in plan)
        now = time.time()
        unseen_kill = any(
            step.action.kind == "kill" and now - self.observed_mobs.get(step.action.target, -UNSEEN_MOB_S) >= UNSEEN_MOB_S
            for step in plan
        )
        return (int(stochastic_mine), int(unseen_kill), -points, food)

    def _queue_arming_goal(self) -> None:
        """When a threat finds the bot with no sword or axe and no system goal open, queue a goal for the best
        sword the usable rules can reach (v3.2e)."""
        if any(item.endswith(("_sword", "_axe")) and count > 0 for item, count in self._inventory_seen.items()):
            return
        if self._system_goal_is_open():
            return
        for sword in ARM_SWORDS:
            if self._queue_system_goal("arm", sword, 1, ARM_RETRY_S):
                return

    def _system_goal_is_open(self) -> bool:
        return any(g.source == "system" and g.status in ("queued", "running") for g in self.queue.goals())

    def _queue_system_goal(self, prefix: str, item: str, count: int, retry_s: float) -> bool:
        """Queue `<prefix>: <count> <item>` unless its last goal failed under `retry_s` ago or the usable rules
        can't reach it. Returns whether it was queued."""
        if self._reachable_plan(item, count, retry_s) is None:
            return False
        self._queue_goal(prefix, item, count)
        return True

    def _reachable_plan(self, item: str, count: int, retry_s: float) -> list[PlanStep] | None:
        """The plan for `count` more of `item`, or None when its last system goal failed under `retry_s` ago or the
        usable rules can't reach it."""
        failed_at = self._system_failed_at.get(item)
        if failed_at is not None and time.monotonic() - failed_at < retry_s:
            return None
        excluded = self._excluded_rules()
        rules = [rule for rule in self.store.usable_rules(self.world_id) if rule.id not in excluded]
        held = normalize.counts(self._inventory_seen)
        try:
            return make_plan(item, held.get(item, 0) + count, held, rules, self.store.skill_for)
        except PlanningError:
            return None

    def _queue_goal(self, prefix: str, item: str, count: int) -> None:
        text = f"{prefix}: {count} {item}"
        goal_id = self.queue.submit(text, "system")
        self.queue.accept(GoalSpec(id=goal_id, kind="obtain", source="system", text=text, item=item, count=count))

    @staticmethod
    def _edible_foods() -> dict[str, int]:
        """Food item -> food points, without the foods auto-eat never eats."""
        return {food: points for food, points in vanilla_foods().items() if food not in AUTO_EAT_BANNED}

    def _handle_chat_command(self, event: dict[str, Any]) -> None:
        command = str(event.get("command", "")).lstrip("!").lower()
        args = str(event.get("args") or "").strip()
        self._log_event("chat_command", player=event.get("player"), command=command, args=args)
        if command == "goal":
            if args:
                self._goal_speakers[self.queue.submit(args, "user")] = event.get("player")  # _parse_pending answers it
            else:
                self._say("Usage: !goal <what>, like !goal 10 torches")
        elif command == "cancel":
            goal_id = args.split()[0].lower() if args else self._running.id if self._running and self._running.source == "user" else None
            self._say(self._cancel_from_chat(goal_id) if goal_id else "Nothing to cancel.")
        elif command == "stop":
            open_goals = [g.id for g in self.queue.goals() if g.source == "user" and g.status not in FINAL_GOAL_STATUSES]
            cancelled = [goal_id for goal_id in open_goals if self.queue.cancel(goal_id)]
            self._cancelled_in_chat.update(cancelled)
            self._say(f"Stopped: cancelled {len(cancelled)} goal{'s' if len(cancelled) > 1 else ''}." if cancelled else "Nothing to cancel.")
        elif command in ("yes", "no"):
            self._say(self._answer_from_chat(command, args.split()[0].lower() if args else None))
        elif command == "status":
            self._say(self._status_line())
        else:
            self._say(CHAT_COMMANDS_HELP)

    def _cancel_from_chat(self, goal_id: str) -> str:
        entry = self.queue.get(goal_id)
        if entry is None:
            return f"No goal {goal_id}."
        if entry.status in FINAL_GOAL_STATUSES:
            return f"{goal_id} is already {entry.status}."
        self.queue.cancel(goal_id)
        self._cancelled_in_chat.add(goal_id)
        return f"Cancelled {goal_id}: {goal_label(entry.spec, entry.text)}"

    def _answer_from_chat(self, command: str, goal_id: str | None) -> str:
        """!yes approves and !no discards a build goal waiting for approval: the one named, else the oldest."""
        if goal_id is None:
            waiting = self._awaiting_approval()
            if not waiting:
                return f"Nothing to {'approve' if command == 'yes' else 'discard'}."
            entry = waiting[0]
        else:
            entry = self.queue.get(goal_id)
            if entry is None:
                return f"No goal {goal_id}."
            if entry.status != "awaiting_approval":
                return f"{goal_id} isn't waiting for approval."
        if command == "yes":
            self.queue.mark(entry.id, "queued")
            return f"Approved {entry.id}: {goal_label(entry.spec, entry.text)}."
        self.queue.mark(entry.id, "cancelled", reason="discarded")
        return f"Discarded {entry.id}: {goal_label(entry.spec, entry.text)}."

    def _awaiting_approval(self) -> list[GoalEntry]:
        return [g for g in self.queue.goals() if g.source == "user" and g.status == "awaiting_approval"]

    def _plan_line(self, spec: GoalSpec) -> str:
        """"Plan for u003: small hut, 5 x 4 x 5: 55 planks, 1 door. Approve it ..." for a parsed build."""
        bp = spec.blueprint
        needs: dict[str, int] = {}
        for value, count in blueprints.materials(bp).items():
            needs[normalize.family(value)] = needs.get(normalize.family(value), 0) + count
        listed = ", ".join(
            goal_label(GoalSpec(spec.id, "obtain", "user", item=item, count=count))
            for item, count in sorted(needs.items(), key=lambda need: (-need[1], need[0]))
        )
        width, height, depth = bp["size"]
        return (f"Plan for {spec.id}: {goal_label(spec)}, {width} x {height} x {depth}: {listed}. "
                "Approve it in the panel, or say !yes or !no.")

    def _status_line(self) -> str:
        waiting_approval = ", ".join(g.id for g in self._awaiting_approval())
        approval = f" Waiting for approval: {waiting_approval}." if waiting_approval else ""
        return self._doing_line() + approval

    def _doing_line(self) -> str:
        running = self._running
        if running is None:
            next_goal = self.queue.next()
            if next_goal is None:
                return "Idle."
            queued = sum(1 for g in self.queue.goals() if g.status == "queued")
            return f"Next: {next_goal.id}: {goal_label(next_goal)}. Queued: {queued}."
        if running.source == "curriculum":
            return f"Learning: {goal_label(running)}."
        waiting = sum(1 for g in self.queue.goals() if g.source != "curriculum" and g.status in ("queued", "submitted"))
        return f"Doing {running.id}: {goal_label(running)}. Queued: {waiting}."

    def _parse_pending(self) -> None:
        """Parse the goals typed since the last pass: queue each one, or say why it can't be done."""
        for entry in self.queue.pending():
            if entry.source != "user":
                continue
            try:
                parsed = self.llm.parse_goal(
                    entry.text, self._inventory_seen, vanilla_items(), goal_id=entry.id, home=self.places.home is not None
                )
            except LLMError as error:
                print(f"[goal] could not parse {entry.id}: {error}")
                return  # it stays submitted; the next pass tries again
            if isinstance(parsed, list):
                self._accept_children(entry, parsed)
                continue
            parsed = self._resolve_me(parsed)
            if isinstance(parsed, Unsupported):
                if self.queue.mark(entry.id, "unsupported", reason=parsed.reply):
                    self._log_event("goal_unsupported", goal_id=entry.id, reply=parsed.reply)
                    self._say(parsed.reply)
            elif self.queue.accept(parsed):
                self._log_event(
                    "goal_parsed", goal_id=entry.id, goal_kind=parsed.kind, item=parsed.item, count=parsed.count, target=parsed.target,
                    **({"name": parsed.blueprint["name"]} if parsed.kind == "build" else {}),
                )
                self._say(self._plan_line(parsed) if parsed.kind == "build" else f"Queued {entry.id}: {goal_label(parsed)}")

    def _accept_children(self, parent: GoalEntry, specs: list[GoalSpec]) -> None:
        """Queue the children of a composite goal, in order, and say what comes (v3.5f)."""
        resolved = [self._resolve_me(replace(spec, id=parent.id)) for spec in specs]  # "me" is the parent's speaker
        children = self.queue.accept_children(parent.id, resolved)
        if not children:
            return
        labels = [goal_label(child) for child in children]
        self._log_event("goal_children", goal_id=parent.id, children=[{"id": c.id, "label": label} for c, label in zip(children, labels)])
        listed = ", then ".join(f"{child.id} {label}" for child, label in zip(children, labels))
        self._say(f"Queued {parent.id}: {parent.text}: {listed}.")
        for child in children:
            if child.kind == "build":
                self._say(self._plan_line(child))

    def _advance_parent(self, child_id: str) -> None:
        """After a child's own ending: a failed child gets one replan of its parent's remaining children, or ends the
        parent; then the parent settles (v3.5f)."""
        child = self.queue.get(child_id)
        parent = self.queue.parent_of(child_id)
        if parent is None or parent.status in FINAL_GOAL_STATUSES:
            return
        if child.status == "failed":
            reason = f"{goal_label(child.spec, child.text)}: {child.reason}"
            replanned = [] if parent.replanned else self._replan_children(parent, child)
            if replanned:
                self.queue.mark(parent.id, parent.status, replanned=True)
                self._cancel_unstarted(parent.id, "replaced")
                accepted = self.queue.accept_children(parent.id, replanned)
                self._log_event(
                    "goal_replanned", goal_id=parent.id, failed=child.id, reason=child.reason,
                    children=[{"id": c.id, "label": goal_label(c)} for c in accepted],
                )
            else:
                self.queue.mark(parent.id, "failed", reason=reason)
                self._cancel_unstarted(parent.id, "parent failed")
                self._end_parent(parent, "failed", reason)
                return
        self._settle_parent(parent)

    def _replan_children(self, parent: GoalEntry, failed: GoalEntry) -> list[GoalSpec]:
        """Claude's new children for the ones still waiting after `failed`; [] when it gives up or can't be asked."""
        children = self.queue.children_of(parent.id)
        done = [c.spec for c in children if c.status == "done" and c.spec is not None]
        remaining = [c.spec for c in children if c.status in ("queued", "awaiting_approval") and c.spec is not None]
        try:
            replanned = self.llm.replan_children(
                parent.text, done, failed.spec, failed.reason, remaining, self._inventory_seen, vanilla_items(),
                home=self.places.home is not None,
            )
        except LLMError as error:
            print(f"[goal] could not replan {parent.id}: {error}")
            return []
        return [self._resolve_me(replace(spec, id=parent.id)) for spec in replanned] if isinstance(replanned, list) else []

    def _cancel_unstarted(self, parent_id: str, reason: str) -> None:
        for child in self.queue.children_of(parent_id):
            if child.status in ("queued", "awaiting_approval"):
                self.queue.mark(child.id, "cancelled", reason=reason)

    def _settle_parents(self) -> None:
        """Children cancelled while queued never reach `_end_goal`, so settle every open parent once per pass."""
        for entry in self.queue.goals():
            if entry.children and entry.status not in FINAL_GOAL_STATUSES:
                self._settle_parent(entry)

    def _settle_parent(self, parent: GoalEntry) -> None:
        status = self.queue.settle(parent.id)
        if status is not None:
            self._end_parent(parent, status, "")

    def _end_parent(self, parent: GoalEntry, status: str, reason: str) -> None:
        self._log_event("goal_parent_end", goal_id=parent.id, status=status)
        self._queue_chest_goal()  # a cancelled parent settles outside any goal's end
        if status == "done":
            self._say(f"Done {parent.id}: {parent.text}.")
        elif status == "failed":
            self._say(f"Couldn't finish {parent.id}: {reason}")
        else:
            self._say(f"Cancelled {parent.id}: {parent.text}.")

    def _resolve_me(self, parsed: GoalSpec | Unsupported) -> GoalSpec | Unsupported:
        """A guard goal for "me" is for the player who typed it, if known (else None: the bot follows the nearest owner)."""
        if isinstance(parsed, GoalSpec) and parsed.kind == "guard" and (parsed.target or "").lower() == "me":
            return replace(parsed, target=self._goal_speakers.get(parsed.id))
        return parsed

    def _log_event(self, kind: str, **fields: Any) -> None:
        """An event outside any episode, in EpisodeLog's line format."""
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "kind": kind, **fields}) + "\n")

    def _say(self, message: str) -> None:
        """Best-effort chat: a lost bot (v3.0h) doesn't hear it."""
        if self._bot_gone:
            return
        try:
            self.backend.say(message.replace("#", "")[:MAX_CHAT_CHARS])
        except BackendError as error:
            print(f"[bot] could not say it: {error}")

    def _wait_for_bot(self, goal_id: str | None, error: Exception) -> bool:
        """Wait up to `bot_wait_s` for the bot to be connected again (v3.0h). Returns whether it is. A bot that is
        connected but keeps failing (busy) is stuck (v3.0i): the third loss in a row stops the life without a wait."""
        now = time.monotonic()
        self._loss_times = (self._loss_times + [now])[-BOT_STUCK_LOSSES:]
        if len(self._loss_times) == BOT_STUCK_LOSSES and now - self._loss_times[0] <= BOT_STUCK_WINDOW_S:
            window_s = round(now - self._loss_times[0], 1)
            self._log_event("bot_stuck", goal_id=goal_id, losses=BOT_STUCK_LOSSES, window_s=window_s, error=str(error)[:200])
            print(f"[bot] the bot is stuck ({str(error)[:200]}): {BOT_STUCK_LOSSES} losses in {window_s} s; stopping the life")
            self._bot_stuck = self._bot_gone = True  # chat stays silent, as for a gone bot
            return False
        self._log_event("bot_down", goal_id=goal_id, error=str(error)[:200])
        print(f"[bot] lost the bot ({str(error)[:200]}); waiting up to {self.config.bot_wait_s:.0f} s")
        went_down = time.monotonic()
        while not self.backend.connected():
            if time.monotonic() - went_down >= self.config.bot_wait_s:
                return False
            time.sleep(self.config.bot_poll_s)
        self._log_event("bot_back", goal_id=goal_id, down_s=round(time.monotonic() - went_down, 1))
        try:
            self._send_places()  # a restarted bot has forgotten the boxes and the stations
        except BackendError:
            pass  # lost again: the next call finds out
        return True

    def _obtain_items(self, *statuses: str, source: str | None = None) -> set[str]:
        return {
            g.spec.item for g in self.queue.goals()
            if g.status in statuses and g.spec is not None and g.spec.kind == "obtain" and g.spec.item and source in (None, g.source)
        }

    def _build_families(self, *statuses: str) -> set[str]:
        """The material families of the build goals in these statuses, and dirt for their scaffold."""
        materials = {
            normalize.family(value)
            for g in self.queue.goals()
            if g.status in statuses and g.spec is not None and g.spec.kind == "build"
            for value in blueprints.materials(g.spec.blueprint)
        }
        return materials | {"dirt"} if materials else materials

    def _before_step(self, episode: Episode, step: PlanStep, plan: list[PlanStep]) -> None:
        """Runs before each plan step: the interrupt checks, then the caller's hook (which runs last, so a hidden
        change is never switched on for a step that doesn't run)."""
        self._inventory_seen = dict(episode.inventory)  # what a death in this poll may have cost
        if episode.actions > 0:
            self._loss_times.clear()  # the bot answered an action since it was last lost
        self._check_interrupts()
        if self.hooks.before_step is not None:
            self.hooks.before_step(episode, step, plan)

    def _check_interrupts(self) -> None:
        """Sees new chat commands and goals, and ends the running goal if it was cancelled, the bot died or
        something outranks it."""
        self._poll_bot()
        self._parse_pending()
        running = self._running
        if running is not None:
            entry = self.queue.get(running.id)  # curriculum goals are not in the queue
            if entry is not None and entry.status == "cancelled":
                raise GoalInterrupted(running.id, "cancelled")
            if self._died_in_goal:
                raise GoalInterrupted(running.id, "death")
            waiting = self.queue.next()
            if waiting is not None and GOAL_RANK[waiting.source] < GOAL_RANK[running.source]:
                raise GoalInterrupted(running.id, "preempted")

    # -- one goal ---------------------------------------------------------

    def _run_goal(self, spec: GoalSpec, goal: Goal | None = None) -> bool:
        """Run one goal: `goal` is the curriculum's for a curriculum spec; a queued spec gets one built here.
        Returns whether it failed for want of a resource that was not found or reachable."""
        index = self.goals_done + 1
        started_at = time.time()
        name = f"g{index:03d}"
        observation = self.backend.begin_episode()
        self._inventory_seen = observation.inventory
        observation = self._furnish_home(observation)
        free_slots_before = observation.free_slots
        observation, tossed, stored = self._make_room(spec.item, observation)
        if spec.kind == "build":
            self._run_build_goal(spec, index, started_at, observation, tossed, stored)
            return False
        if spec.kind in ("kill", "guard"):
            self._run_kill_or_guard_goal(spec, index, started_at, observation, tossed, stored)
            return False
        if goal is None:
            goal = self._start_queued_goal(spec, observation)
            if goal is None:
                return False  # cancelled meanwhile: no goal index used, nothing recorded
        kept_food = self._kept_food_for(spec)
        if kept_food:
            self.backend.keep_food(kept_food)  # before the first action; a lost bot goes the way any goal's call does
        try:
            budget = max(MIN_GOAL_BUDGET, BUDGET_PER_PLAN_STEP * goal.plan_length)
            goal_clock_s = self.config.goal_wall_clock_s
            if spec.source != "curriculum":
                goal_clock_s = max(goal_clock_s, GOAL_SECONDS_PER_PLAN_STEP * goal.plan_length)
            task = Task(
                task_id=name, split="train", goal_item=goal.item, goal_count=goal.count,
                start_inventory=dict(observation.inventory), world_rules=(), budget=budget,
                description=f"Obtain {goal.count} {goal.item}",
            )
            log = EpisodeLog(
                self.config.run_dir / "episodes" / f"D_life_{name}_{goal.item.replace('#', '')}.jsonl",
                label=f"{name} {goal.item}", say=self.backend.say,
            )
            life_clock_limits_goal = self._remaining_s() <= goal_clock_s
            episode = Episode(
                episode_id=f"D/life/{name}-{goal.item}", system="D", task=task, layout_seed=0, world_id=self.world_id,
                backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
                deadline=time.monotonic() + min(goal_clock_s, max(self._remaining_s(), 0.0)), home=self._home_stock(),
            )
            self._log_goal_start(log, spec, goal, task, index, observation, free_slots_before, tossed, stored)

            # `goal` is left out for a curriculum goal (its default None): v2's tests stub run_schemavoyager without it.
            goal_arg = {} if spec.source == "curriculum" else {"goal": spec}
            result, interrupt = self._run_until_interrupted(
                spec, episode,
                lambda: run_schemavoyager(episode, self.store, self._before_step, excluded=self._excluded_rules(), **goal_arg),
                life_clock_limits_goal,
            )
            self._end_goal(spec, goal, index, budget, started_at, episode, result, tossed, interrupt, stored=stored)
            steps = [event for event in log.events if event["kind"] == "step"]
            return not result.success and interrupt is None and bool(steps) and steps[-1]["error"] in MISSING_RESOURCE_ERRORS
        finally:
            if kept_food and not self._bot_gone:
                try:
                    self.backend.keep_food([])
                except BackendError as error:
                    print(f"life: could not clear keep_food: {error}")

    def _kept_food_for(self, spec: GoalSpec) -> list[str]:
        """v3.0i: the items auto-eat must leave alone for an obtain goal for a food: the food itself (the hungry bot
        would eat what the goal just made) and the ingredients of every usable smelt rule that yields it. Empty for
        any other goal, or a backend without `keep_food`."""
        if spec.item not in vanilla_foods() or not hasattr(self.backend, "keep_food"):
            return []
        ingredients = {
            ingredient
            for rule in self.store.usable_rules(self.world_id)
            if rule.kind == "smelt" and spec.item in rule.yields()
            for ingredient in rule.preconditions.get("consumes", {})
        }
        return sorted({spec.item} | ingredients)

    def _run_build_goal(
        self, spec: GoalSpec, index: int, started_at: float, observation: Observation, tossed: dict[str, int],
        stored: dict[str, Any],
    ) -> None:
        """Run an approved build goal in one episode: pick and protect its site, then the build runner (v3.4b).
        A site that can't be had still gets an episode, so the trace shows `build_failed`."""
        if not self.queue.mark(spec.id, "running"):
            return  # cancelled meanwhile: no goal index used, nothing recorded
        name = f"g{index:03d}"
        free_slots_before = observation.free_slots
        entry = self._protected_entry(spec.id)
        plan = prepare_build(
            self.backend, self.store, spec, origin=tuple(entry["origin"]) if entry else None,
            protected=self._protected_boxes(except_goal=spec.id), world_id=self.world_id, excluded=self._excluded_rules(),
        )
        total = builder.progress(spec.blueprint, (0, 0, 0), {})[1]
        if isinstance(plan, str):
            goal, budget, goal_clock_s = Goal(BUILD_GOAL, 1, 0, (), plan), MIN_GOAL_BUDGET, self.config.goal_wall_clock_s
        else:
            if entry is None:
                self._add_protected_entry(spec, plan.origin)
            goal = Goal(BUILD_GOAL, total, plan.gather_steps, (), f"{spec.source} goal {spec.id}")
            budget, goal_clock_s = plan.budget, plan.clock_s
        task = Task(
            task_id=name, split="train", goal_item=BUILD_GOAL, goal_count=goal.count,
            start_inventory=dict(observation.inventory), world_rules=(), budget=budget,
            description=f"Build {goal_label(spec)}",
        )
        log = EpisodeLog(
            self.config.run_dir / "episodes" / f"D_life_{name}_{BUILD_GOAL}.jsonl", label=f"{name} {BUILD_GOAL}", say=self.backend.say
        )
        episode = Episode(
            episode_id=f"D/life/{name}-{BUILD_GOAL}", system="D", task=task, layout_seed=0, world_id=self.world_id,
            backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
            deadline=time.monotonic() + min(goal_clock_s, max(self._remaining_s(), 0.0)), home=self._home_stock(),
        )
        self._log_goal_start(log, spec, goal, task, index, observation, free_slots_before, tossed, stored)

        if isinstance(plan, str):
            log.event("build_failed", reason=plan)
            outcome, interrupt = BuildOutcome(episode.finish(success=False, announce=False), plan, 0, total), None
        else:
            reason = ""

            def check_interrupts() -> None:
                self._inventory_seen = dict(episode.inventory)  # what a death in this poll may have cost
                self._check_interrupts()

            def run() -> EpisodeResult:
                nonlocal reason
                done = run_build(
                    episode, self.store, plan, before_step=self._before_step, check_interrupts=check_interrupts,
                    excluded=self._excluded_rules(), ring_scans=self._ring_scans,
                )
                reason = done.reason
                return done.result

            result, interrupt = self._run_until_interrupted(spec, episode, run, self._remaining_s() <= goal_clock_s)
            outcome = BuildOutcome(result, reason, plan.placed, total)
        self._end_goal(spec, goal, index, budget, started_at, episode, outcome.result, tossed, interrupt, build=outcome, stored=stored)

    def _run_kill_or_guard_goal(
        self, spec: GoalSpec, index: int, started_at: float, observation: Observation, tossed: dict[str, int],
        stored: dict[str, Any],
    ) -> None:
        """Run a kill or guard goal in one episode (v3.3i): arm if need be, then the kill or guard runner."""
        if not self.queue.mark(spec.id, "running"):
            return  # cancelled meanwhile: no goal index used, nothing recorded
        name = f"g{index:03d}"
        free_slots_before = observation.free_slots
        if spec.kind == "kill":
            item, budget = f"kill_{spec.target}", ARM_BUDGET + KILL_BUDGET_PER_KILL * spec.count
            goal_clock_s = max(self.config.goal_wall_clock_s, KILL_SECONDS_PER_KILL * spec.count)
        else:
            item, budget, goal_clock_s = "guard", ARM_BUDGET, self.config.goal_wall_clock_s
        goal = Goal(item, spec.count, 0, (), f"{spec.source} goal {spec.id}")
        task = Task(
            task_id=name, split="train", goal_item=item, goal_count=goal.count,
            start_inventory=dict(observation.inventory), world_rules=(), budget=budget, description=spec.text,
        )
        log = EpisodeLog(self.config.run_dir / "episodes" / f"D_life_{name}_{item}.jsonl", label=f"{name} {item}", say=self.backend.say)
        episode = Episode(
            episode_id=f"D/life/{name}-{item}", system="D", task=task, layout_seed=0, world_id=self.world_id,
            backend=self.backend, llm=self.llm, log=log, inventory=dict(observation.inventory), natural=True,
            deadline=time.monotonic() + min(goal_clock_s, max(self._remaining_s(), 0.0)), home=self._home_stock(),
        )
        self._log_goal_start(log, spec, goal, task, index, observation, free_slots_before, tossed, stored)

        def check_interrupts() -> None:
            self._inventory_seen = dict(episode.inventory)  # what a death in this poll may have cost
            self._check_interrupts()

        runner = run_kill if spec.kind == "kill" else run_guard
        outcome = RunnerOutcome()
        result, interrupt = self._run_until_interrupted(
            spec, episode,
            lambda: runner(
                episode, self.store, spec, bot_events=self._goal_events, check_interrupts=check_interrupts,
                before_step=self._before_step, excluded=self._excluded_rules(), clock_s=goal_clock_s, outcome=outcome,
            ),
            self._remaining_s() <= goal_clock_s,
        )
        self._end_goal(spec, goal, index, budget, started_at, episode, result, tossed, interrupt, runner=outcome, stored=stored)

    def _run_until_interrupted(
        self, spec: GoalSpec, episode: Episode, run: Callable[[], EpisodeResult], life_clock_limits_goal: bool
    ) -> tuple[EpisodeResult, str | None]:
        """Run the goal's episode with the interrupts armed. Returns its result and why it was cut short: cancelled,
        preempted, death, bot (lost, v3.0h) or wall_clock (the life's own clock, not the goal's deadline or budget),
        else None."""
        interrupt: str | None = None
        self._running = spec
        self._died_in_goal = False
        self._goal_events.clear()
        try:
            result = run()
        except (GoalInterrupted, BackendError) as stop:
            if isinstance(stop, BackendError):
                self._bot_gone = not self._wait_for_bot(spec.id, stop)
                stop = GoalInterrupted(spec.id, "bot")
            episode.log.event("goal_interrupted", goal_id=stop.goal_id, reason=stop.reason)
            result = episode.finish(success=False)  # as a normal end does, but no skill is compiled
            episode.log.event("certified", rules=self.store.certify())
            interrupt = stop.reason
        finally:
            self._inventory_seen = dict(episode.inventory)
            self._poll_bot()  # a death in the goal's last step has no later check to see it
            self._running = None
        if self._died_in_goal and interrupt != "cancelled":
            interrupt = "death"
        # Cut short by the life's own clock: no attempt to count.
        if interrupt is None and not result.success and life_clock_limits_goal and episode.out_of_time and episode.budget_left > 0:
            interrupt = "wall_clock"
        return result, interrupt

    def _log_goal_start(
        self, log: EpisodeLog, spec: GoalSpec, goal: Goal, task: Task, index: int, observation: Observation,
        free_slots_before: int | None, tossed: dict[str, int], stored: dict[str, Any] | None = None,
    ) -> None:
        """The `goal` event, what was stored in the home chest and what was tossed to make room (also said in chat),
        and the repairs and retests so far."""
        asked = {} if spec.source == "curriculum" else {"asked": spec.count}
        log.event(
            "goal", index=index, item=goal.item, count=task.goal_count, reason=goal.reason, budget=task.budget,
            goal_id=spec.id, source=spec.source, text=spec.text, goal_kind=spec.kind, **asked,
        )
        if stored:
            log.event("stored", **stored)
            listed = ", ".join(f"{item.lstrip('#')} x{count}" for item, count in stored["items"].items())
            self.backend.say(f"Putting {listed} in my chest."[:MAX_CHAT_CHARS])
        if tossed:
            log.event("tossed", items=tossed, free_slots_before=free_slots_before, free_slots_after=observation.free_slots)
            names = [f"{count} {item.lstrip('#')}" for item, count in tossed.items()]
            listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
            self.backend.say(f"Dropping {listed} to make room."[:MAX_CHAT_CHARS])
        self._log_unreported(log)

    def _start_queued_goal(self, spec: GoalSpec, observation: Observation) -> Goal | None:
        """Mark a queued goal running, fixing its target count the first time it runs (D5), and plan it for its
        budget and clock. None if the goal was cancelled meanwhile."""
        entry = self.queue.get(spec.id)
        held = normalize.counts(observation.inventory)
        if entry is not None and entry.target_count is not None:
            target_count, fields = entry.target_count, {}
        else:
            target_count = held.get(spec.item, 0) + spec.count
            fields = {"target_count": target_count}
        if not self.queue.mark(spec.id, "running", **fields):
            return None
        excluded = self._excluded_rules()
        rules = [rule for rule in self.store.usable_rules(self.world_id) if rule.id not in excluded]
        try:
            plan = make_plan(spec.item, target_count, held, rules, self.store.skill_for, visible=normalize.counts(observation.nearby))
        except PlanningError:
            plan = []  # the episode still runs, so the trace shows plan_failed
        uncertified = tuple(sorted({step.rule.id for step in plan if step.rule.status != "certified"}))
        return Goal(spec.item, target_count, len(plan), uncertified, f"{spec.source} goal {spec.id}")

    def _end_goal(
        self, spec: GoalSpec, goal: Goal, index: int, budget: int, started_at: float, episode: Episode,
        result: EpisodeResult, tossed: dict[str, int], interrupt: str | None, build: BuildOutcome | None = None,
        runner: RunnerOutcome | None = None, stored: dict[str, Any] | None = None,
    ) -> None:
        """The one ending of a goal (v3.1d): done, failed, cancelled, preempted or cut short by the life's clock.
        A build goal also settles its protected entry (v3.4b)."""
        if getattr(episode, "actions", 0) > 0:  # (some test episodes are stubs) the bot answered this goal's actions:
            self._loss_times = self._loss_times[-1:] if interrupt == "bot" else []  # only a loss that ended it still counts
        queued_goal = spec.source != "curriculum"
        label = goal_label(spec)
        couldnt = {"build": "Couldn't build", "kill": "Couldn't do", "guard": "Couldn't do"}.get(spec.kind, "Couldn't get")
        entry = self._protected_entry(spec.id)
        chat = ""
        if queued_goal:
            cancelled_chat = "" if spec.id in self._cancelled_in_chat else f"Cancelled: {label}"
            if interrupt == "cancelled" or (interrupt == "bot" and self.queue.get(spec.id).status == "cancelled"):
                chat = cancelled_chat  # the queue already says cancelled
            elif interrupt == "preempted":
                chat = self._pausing_line(label)
                self.queue.mark(spec.id, "queued")
            elif interrupt == "bot":
                self._bot_losses[spec.id] = self._bot_losses.get(spec.id, 0) + 1
                if self._bot_losses[spec.id] < BOT_LOSSES_TO_FAIL:
                    self.queue.mark(spec.id, "queued")  # keeps target_count, like preempted
                else:
                    reason = f"lost the bot {BOT_LOSSES_TO_FAIL} times"
                    self.queue.mark(spec.id, "failed", reason=reason)
                    chat = f"{couldnt} {label}: {reason}"
            elif interrupt == "death":
                chat = self._end_after_death(spec, f"{couldnt} {label}")
            elif interrupt is not None:  # wall_clock: --resume runs it again
                self.queue.mark(spec.id, "queued")
            elif result.success:
                marked = self.queue.mark(spec.id, "done")
                chat = f"Done: {label}"
                if not marked and self.queue.get(spec.id).status == "cancelled":  # cancelled after its last step
                    chat = cancelled_chat
            else:
                reason = build.reason if build is not None else (runner.reason if runner is not None else "") or self._failure_reason(episode)
                marked = self.queue.mark(spec.id, "failed", reason=reason)
                chat = f"{couldnt} {label}: {reason}"
                if not marked and self.queue.get(spec.id).status == "cancelled":
                    chat = cancelled_chat
                if spec.source == "system" and spec.text.startswith(("hunger:", "arm:")):
                    self._system_failed_at[spec.item] = time.monotonic()
        if self.places.home is not None:
            self._write_back_home(episode.home)
        if build is not None:
            self._settle_protected_entry(spec.id, build.placed)
            if entry is not None and entry["status"] == "built" and self.places.home is None:
                self._set_home(home_from_build(entry, spec.blueprint))
                self._update_protected()  # the home's box grows by the yard
        if interrupt is None:
            for rule, error in episode.blocked_rules.items():
                if error == "missing_ingredients":
                    self.blocked[rule] = self.blocked.get(rule, 0) + 1
                    if self.blocked[rule] == BLOCKED_GOALS_TO_EXCLUDE:
                        print(f"[blocked] {rule}: blocked in {BLOCKED_GOALS_TO_EXCLUDE} goals, left out for the rest of the life")
            if not queued_goal:
                self.curriculum.record(goal, result.success)
        self.goals_done = index
        record = self._goal_record(index, goal, budget, result, started_at, interrupted=interrupt is not None)
        record |= {"goal_id": spec.id, "source": spec.source, "text": spec.text, "goal_kind": spec.kind, "tossed": tossed}
        record["stored"] = stored["items"] if stored else {}
        if interrupt is not None:
            record["interrupt"] = interrupt
        parent = self.queue.parent_of(spec.id) if queued_goal else None
        if parent is not None:
            record["parent"] = parent.id
        if runner is not None:
            record |= runner.record
        if build is not None:
            record["build"] = {
                "name": spec.blueprint["name"], "origin": entry["origin"] if entry else None,
                "placed": build.placed, "total": build.total,
            }
        self._attach_unreported(record)
        if chat:
            self._say(chat)
        if parent is not None:
            self._advance_parent(spec.id)
        self._queue_chest_goal()
        self._save(record)
        if self.hooks.after_goal is not None:
            self.hooks.after_goal(record)

    def _end_after_death(self, spec: GoalSpec, couldnt: str) -> str:
        """A user goal goes back to queued, or fails at its second death; a system goal is cancelled (v3.2d).
        Returns the chat line."""
        if spec.source == "system":
            self.queue.mark(spec.id, "cancelled", reason="the bot died")
            return ""
        deaths = self.queue.get(spec.id).deaths + 1
        if deaths >= USER_DEATHS_TO_FAIL:
            self.queue.mark(spec.id, "failed", deaths=deaths, reason="died twice trying")
            return f"{couldnt}: died twice trying"
        self.queue.mark(spec.id, "queued", deaths=deaths)
        return ""

    def _log_unreported(self, log: EpisodeLog) -> None:
        for repair in self._repairs_unreported:
            log.event("repair", **repair)
        for retest in self._retests_unreported:
            log.event("retest", **retest)

    def _attach_unreported(self, record: dict[str, Any]) -> None:
        """Move the repairs and retests reported since the last goal into its record, then empty both queues."""
        if self._repairs_unreported:
            record["repairs"] = self._repairs_unreported
        if self._retests_unreported:
            record["retests"] = self._retests_unreported
        self._repairs_unreported, self._retests_unreported = [], []

    def _pausing_line(self, label: str) -> str:
        """"Pausing 10 torches for hunger: 4 bread": a system goal is named by its text, a user goal by its label."""
        preemptor = self.queue.next()
        if preemptor is None:
            return ""
        return f"Pausing {label} for {preemptor.text if preemptor.source == 'system' else goal_label(preemptor)}"

    @staticmethod
    def _failure_reason(episode: Episode) -> str:
        """Why a user goal failed, at most 80 characters (v3.1d): no plan, the last step's missing resource, out of
        time or steps, the last failed step, else just "failed"."""
        events = episode.log.events
        end = max(i for i, event in enumerate(events) if event["kind"] == "episode_end")
        if end > 0 and events[end - 1]["kind"] == "plan_failed":
            return "no plan"
        steps = [event for event in events if event["kind"] == "step"]

        def in_words(step: dict[str, Any]) -> str:
            target = (step["action_obj"].get("target") or "").lstrip("#").replace("_", " ")
            return f"{step['error'].replace('_', ' ')}: {target}"[:80]

        if steps and steps[-1]["error"] in ("not_found", "unreachable", "hazard"):
            return in_words(steps[-1])
        if episode.out_of_time:
            return "out of time"
        if episode.budget_left <= 0:
            return "out of steps"
        failed = [step for step in steps if step["error"]]
        return in_words(failed[-1]) if failed else "failed"

    def _make_room(
        self, goal_item: str | None, observation: Observation
    ) -> tuple[Observation, dict[str, int], dict[str, Any]]:
        """Make room when the bot is nearly full: store junk and spare materials in the home chest if it is within
        range, then toss junk if still short (v3.5c). Returns the observation to start the goal from (a new one after a
        store or toss), what was tossed, and the `stored` event's fields (empty when nothing was stored). The goal's
        item and what the user asked for are never junk; a build goal has no item (None), and keeps its materials
        and the scaffold blocks."""
        if observation.free_slots is None or observation.free_slots >= MIN_FREE_SLOTS:
            return observation, {}, {}
        wanted = (
            self._obtain_items("queued") | self._obtain_items("done", source="user") | self._build_families("queued", "running")
            | set(self._edible_foods())
        )
        wanted |= {goal_item} if goal_item else set(SCAFFOLD_BLOCKS)
        rules = self.store.usable_rules(self.world_id)

        stored: dict[str, Any] = {}
        chest = self.places.stations.get("chest")
        if chest is not None and math.dist(observation.position, _cell_centre(chest)) <= HOME_RANGE:
            items = junk_to_store(
                observation.inventory, rules, wanted, observation.free_slots, min_free=MIN_FREE_SLOTS
            )
            if items:
                observation, stored = self._store_in_chest(chest, items, observation)
        junk = junk_to_toss(
            observation.inventory, rules, wanted, observation.free_slots, min_free=MIN_FREE_SLOTS
        )
        if not junk:
            return observation, {}, stored
        tossed = self.backend.toss(junk)
        return self.backend.observe(), tossed, stored

    def _store_in_chest(
        self, chest: tuple[int, int, int], items: dict[str, int], observation: Observation
    ) -> tuple[Observation, dict[str, Any]]:
        """Deposit items in the home chest. Returns a fresh observation and the `stored` event's fields (empty when
        nothing went in). A chest that is not there any more is dropped from the home's stations."""
        result = self.backend.deposit(chest, items)
        if result.error == "not_found":
            self._drop_station("chest")
        elif result.error == "unreachable" and blames_slot(chest, result.detail.get("position")):
            self._home_unreachable("chest")
        elif result.error is None:
            self.places.stored = dict(result.contents)
            self._save_state()
        after = self.backend.observe()
        if result.error is not None or not result.moved:
            return after, {}
        return after, {
            "items": result.moved, "free_slots_before": observation.free_slots, "free_slots_after": after.free_slots
        }

    def _run_exploration_round(self, reason: str = "nothing left to learn here", reset_skips: bool = True) -> None:
        """Walk a few compass directions (cycling on across rounds) so the next goal starts
        somewhere new and the next `_learn_priors` sees new blocks. With `reset_skips`
        (an empty frontier), skipped goals also get another try."""
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
            deadline=time.monotonic() + min(self.config.goal_wall_clock_s, max(self._remaining_s(), 0.0)), home=self._home_stock(),
        )
        goal = Goal(EXPLORE_GOAL, 1, 0, (), reason)
        log.event("goal", index=index, item=goal.item, count=goal.count, reason=goal.reason, budget=task.budget)
        self.backend.say("Nothing left to learn here; exploring for new blocks." if reset_skips else "Moving on to find new ground.")

        all_ok = True
        while episode.can_act and episode.actions < EXPLORES_PER_ROUND:
            direction = DIRECTIONS[self.explores_done % len(DIRECTIONS)]
            self.explores_done += 1
            all_ok &= episode.act(Action("explore", direction=direction)).ok
        result = episode.finish(success=all_ok)

        if reset_skips:
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
            "outcome": "interrupted" if interrupted else "success" if result.success else "failed", "actions": result.actions,
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
        self._save_state()

    def _protected_entry(self, goal_id: str) -> dict[str, Any] | None:
        return next((entry for entry in self.protected if entry["goal_id"] == goal_id), None)

    def _protected_boxes(self, except_goal: str | None = None) -> list[blueprints.Box]:
        """The site box of every protected entry (v3.4b), but the one of `except_goal`; the home's is its box with the
        yard (v3.5e). At most 32 boxes: the closest ones are merged."""
        home = self.places.home
        boxes = [
            home_box(home) if home is not None and entry["goal_id"] == home.goal_id
            else blueprints.site_box({"size": entry["size"]}, tuple(entry["origin"]))
            for entry in self.protected if entry["goal_id"] != except_goal
        ]
        return blueprints.merge_boxes(boxes)

    def _add_protected_entry(self, spec: GoalSpec, origin: tuple[int, int, int]) -> None:
        self.protected.append(
            {"goal_id": spec.id, "name": spec.blueprint["name"], "origin": list(origin), "size": list(spec.blueprint["size"]), "status": "building"}
        )
        self._update_protected()

    def _settle_protected_entry(self, goal_id: str, placed: int) -> None:
        """After a build goal's ending: built when done; partial when it failed or was cancelled with something placed,
        else removed; unchanged (still building) when it will run again."""
        entry = self._protected_entry(goal_id)
        status = self.queue.get(goal_id).status
        if entry is None or status not in ("done", "failed", "cancelled"):
            return
        if status == "done":
            entry["status"] = "built"
        elif placed > 0:
            entry["status"] = "partial"
        else:
            self.protected.remove(entry)
        self._update_protected()

    # -- the home (v3.5) ---------------------------------------------------

    def _home_stock(self) -> HomeStock | None:
        return self.places.stock() if self.places.home is not None else None

    def _set_home(self, home: Home) -> None:
        """The first house built is home. The caller saves and re-sends the protected boxes."""
        self.places.home = home
        self._log_event("home_set", home=self.places.to_json()["home"])
        x, y, z = home.origin
        self._say(f"This is my home now: {home.name} at {x} {y} {z}.")

    def _furnish_home(self, observation: Observation) -> Observation:
        """At the start of a goal, put the chest, crafting table and furnace the bot holds in the home's yard (v3.5c).
        Returns the observation to go on from (a new one if anything was scanned or placed)."""
        home = self.places.home
        stations = self.places.stations
        unplaced = [
            kind for kind in HOME_STATION_KINDS
            if observation.inventory.get(kind, 0) >= 1 and kind not in stations and kind not in self.places.retired
        ]
        if home is None or not unplaced or math.dist(observation.position, home.origin) > HOME_RANGE:
            return observation

        scan = self.backend.scan(*yard_scan_box(home))
        for kind in unplaced:
            failure = None
            for cell in yard_slots(home, scan, stations.values(), self.places.bad_slots)[:SLOTS_TRIED_PER_STATION]:
                if hasattr(self.backend, "reach"):  # v2's scripted test backends have none
                    walk = self.backend.reach(cell)
                    if not walk.ok and blames_slot(cell, walk.detail.get("position")):
                        self.places.bad_slots.append(cell)
                        self._save_state()
                        self._log_event("home_slot_bad", station=kind, pos=list(cell))
                        continue
                    if not walk.ok:
                        self._log_event("home_place_failed", station=kind, pos=list(cell), error="unreachable")
                        return self._after_furnishing()
                outcome = self.backend.act(Action("place", target=kind, pos=cell, scaffold=False))
                if outcome.ok:
                    stations[kind] = cell
                    if kind == "chest":
                        self.places.stored = {}
                    self._save_state()
                    self._send_places()
                    self._log_event("home_placed", station=kind, pos=list(cell))
                    self._say(f"I put a {kind.replace('_', ' ')} by my house at {cell[0]} {cell[1]} {cell[2]}.")
                    failure = None
                    break
                failure = {"station": kind, "pos": list(cell), "error": outcome.error}
                if outcome.error not in ("occupied", "no_support"):
                    break
            if failure is not None:
                self._log_event("home_place_failed", **failure)
        return self._after_furnishing()

    def _after_furnishing(self) -> Observation:
        observation = self.backend.observe()
        self._inventory_seen = observation.inventory
        return observation

    def _write_back_home(self, home: HomeStock | None) -> None:
        """After a goal: the chest's contents as the agent last saw them, and the stations it found gone dropped."""
        if home is None:
            return
        if "chest" in self.places.stations:
            self.places.stored = dict(home.stored)
        for kind in HOME_STATION_KINDS:
            if kind in home.lost and kind in self.places.stations:
                self._drop_station(kind)
            elif kind in home.unreachable and kind in self.places.stations:
                self._home_unreachable(kind)

    def _drop_station(self, kind: str) -> None:
        """A station or chest found gone is forgotten (a lost chest empties `stored`)."""
        pos = self.places.stations.pop(kind)
        if kind == "chest":
            self.places.stored = {}
        self._save_state()
        self._send_places()
        self._log_event("home_lost", station=kind, pos=list(pos))

    def _home_unreachable(self, kind: str) -> None:
        """A station or chest whose slot the bot can't walk to (v3.0i): the cell is a bad slot. The first time the kind
        is moved (forgotten, so furnishing puts one elsewhere; a chest's contents are abandoned and one more chest
        goal is allowed), the second time it is dropped for good."""
        places = self.places
        cell = places.stations[kind]
        moving = kind not in places.moved
        self._log_event(
            "home_unreachable", station=kind, pos=list(cell), action="moved" if moving else "dropped",
            stored=dict(places.stored) if kind == "chest" else {},
        )
        places.bad_slots.append(cell)
        if moving:
            places.moved.add(kind)
            del places.stations[kind]
            if kind == "chest":
                places.stored = {}
                places.chest_goal = None
            self._save_state()
            self._send_places()
        else:
            places.retired.add(kind)
            self._drop_station(kind)

    def _queue_chest_goal(self) -> None:
        """When a goal ends and the home has no chest, none is held and none was ever asked for, queue one system
        goal to get one, once per home (v3.5c). Not while a composite parent is open, nor once the chest is retired."""
        places = self.places
        if places.home is None or "chest" in places.stations or self._inventory_seen.get("chest", 0) > 0 or places.chest_goal:
            return
        if "chest" in places.retired or any(
            entry.children and entry.status not in FINAL_GOAL_STATUSES for entry in self.queue.goals()
        ):
            return
        text = "a chest for my home"
        goal_id = self.queue.submit(text, "system")
        self.queue.accept(GoalSpec(id=goal_id, kind="obtain", source="system", text=text, item="chest", count=1))
        places.chest_goal = goal_id

    def _update_protected(self) -> None:
        """Save the list and send its boxes to the backend, after every change."""
        self._save_state()
        self._send_places()

    def _send_places(self) -> None:
        """Send the protected boxes and the home stations to the backend."""
        if hasattr(self.backend, "set_protected") and not self._bot_gone:  # v2's scripted test backends have none
            self.backend.set_protected(self._protected_boxes())
        if hasattr(self.backend, "set_stations") and not self._bot_gone:
            self.backend.set_stations({kind: pos for kind, pos in self.places.stations.items() if kind != "chest"})

    def _save_state(self) -> None:
        state = {
            "world_id": self.world_id, "goal_index": self.goals_done, "explores_done": self.explores_done, "curriculum": self.curriculum.to_dict(),
            "asked": sorted(self.asked), "repaired": sorted(self.repaired), "blocked": self.blocked, "retests": self.retests, "deaths": self.deaths, "protected": self.protected, "places": self.places.to_json(), "observed_blocks": sorted(self.observed_blocks),
            "observed_mobs": self.observed_mobs, "asked_mobs": sorted(self.asked_mobs),
            "tokens": self.earlier_tokens + self.llm.total_tokens, "spend_usd": self.llm.spent_usd,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _excluded_rules(self) -> frozenset[str]:
        return frozenset(rule for rule, goals in self.blocked.items() if goals >= BLOCKED_GOALS_TO_EXCLUDE)

    def _certified_ids(self) -> set[str]:
        return {rule.id for rule in self.store.rules.values() if rule.status == "certified"}

    def _remaining_s(self) -> float:
        return self.config.wall_clock_s - (time.monotonic() - self.started)


def _cell_centre(cell: tuple[int, int, int]) -> tuple[float, float, float]:
    return cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5
