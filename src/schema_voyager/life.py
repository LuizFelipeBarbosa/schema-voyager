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
from typing import Any, Callable, ContextManager, Literal, Protocol

from . import normalize
from .adapter import DIRECTIONS, LifeBackend
from .agent import run_schemavoyager
from .curriculum import Curriculum, Goal
from .episode import EXPLORE_GOAL, Episode
from .goals import GoalInterrupted, GoalQueue, goal_label
from .inventory import junk_to_toss
from .llm import LLM, LLMError, SpendCapReached, vanilla_items
from .mineflayer_backend import BackendError
from .models import Action, EpisodeResult, GoalSpec, Observation, Rule, Task, Unsupported
from .planner import PlanningError, PlanStep, make_plan
from .schema_store import TRAIN_WORLD, SchemaStore
from .trace import EpisodeLog

MIN_GOAL_BUDGET = 30
GOAL_SECONDS_PER_PLAN_STEP = 30  # a user goal's clock: at least this per planned step
BUDGET_PER_PLAN_STEP = 4
MAX_CHAT_CHARS = 240
EXPLORES_PER_ROUND = 3
MIN_FREE_SLOTS = 4  # a goal starts with at least this many free slots, if junk can make them
BLOCKED_GOALS_TO_EXCLUDE = 2  # goals that block a rule with missing_ingredients before the life leaves it out
# A goal that fails with one of these is followed by an exploration round, to start the next one elsewhere.
MISSING_RESOURCE_ERRORS = frozenset({"not_found", "unreachable"})
# Blocks around the bot that no rule is worth asking about.
AMBIENT_BLOCKS = frozenset({"air", "cave_air", "void_air", "water", "lava", "bedrock"})
FINAL_GOAL_STATUSES = frozenset({"done", "failed", "cancelled", "unsupported"})
GOAL_RANK = {"system": 0, "user": 1, "curriculum": 2}  # a queued goal of a lower rank preempts the running one
CHAT_COMMANDS_HELP = "Commands: !goal <what>, !cancel [id], !stop, !status"


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
    stop_reason = life.run()
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
            self.blocked: dict[str, int] = dict(state.get("blocked", {}))
            self.retests: dict[str, str] = dict(state.get("retests", {}))  # retest id -> "match" | "mismatch"
            self.earlier_tokens: int = state["tokens"]
            llm.carried_usd = state["spend_usd"]
        else:
            self.world_id = config.world_id
            self.store = SchemaStore()
            self.curriculum = Curriculum()
            self.asked, self.observed_blocks = set(), set()
            self.goals_done, self.earlier_tokens, self.explores_done = 0, 0, 0
            self.repaired = set()
            self.blocked = {}
            self.retests = {}
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
        self._cancelled_in_chat: set[str] = set()  # their ending doesn't say "Cancelled" a second time
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
            self._poll_bot()
            self._parse_pending()
            spec = self.queue.next()
            if spec is None and self.config.mode == "play":
                time.sleep(self.config.idle_poll_s)
                continue
            goal = None
            try:
                observation = self._learn_priors()
                if self._repair_dead_ingredients(observation):
                    observation = self._learn_priors()  # the repairs may name families nobody asked about
                if spec is None:
                    goal = self.curriculum.next_goal(
                        normalize.counts(observation.inventory), self.store, self.world_id, normalize.counts(observation.nearby),
                        excluded=self._excluded_rules(),
                    )
                    if goal is None:
                        if self._expand_frontier():
                            continue
                        if not self.config.keep_going:
                            return "empty_frontier"
                        self._run_exploration_round()
                        continue
                    spec = GoalSpec(
                        id=f"g{self.goals_done + 1:03d}", kind="obtain", source="curriculum", item=goal.item, count=goal.count
                    )
            except SpendCapReached:
                return "spend_cap"
            if self._run_goal(spec, goal) and self.config.mode == "learn" and spec.source == "curriculum":
                self._run_exploration_round(reason="the resource was not found here", reset_skips=False)

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
        needed = set() if self.config.mode == "play" else set(self.curriculum.frontier)
        needed |= self._obtain_items("queued", "running")
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
        first tool (when it has no producible tool), or that are on the frontier."""
        needed = set(self.curriculum.frontier)
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

    def _poll_bot(self) -> None:
        """Handle the chat commands the bot recorded since the last poll (v3.1g)."""
        if not self._polls_bot:
            return
        since = self.bot_seq
        try:
            status = self.backend.status(since=since)
            if status.last_seq < since:  # the bot restarted: its events start over
                since = 0
                status = self.backend.status(since=0)
        except BackendError as error:
            print(f"[bot] could not poll events: {error}")
            return
        self.bot_seq = status.last_seq
        for event in sorted(status.events, key=lambda e: e["seq"]):
            if event["seq"] > since and event["type"] == "chat_command":
                self._handle_chat_command(event)

    def _handle_chat_command(self, event: dict[str, Any]) -> None:
        command = str(event.get("command", "")).lstrip("!").lower()
        args = str(event.get("args") or "").strip()
        self._log_event("chat_command", player=event.get("player"), command=command, args=args)
        if command == "goal":
            if args:
                self.queue.submit(args, "user")  # _parse_pending answers it
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

    def _status_line(self) -> str:
        running = self._running
        if running is None:
            return "Idle."
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
                parsed = self.llm.parse_goal(entry.text, self._inventory_seen, vanilla_items(), goal_id=entry.id)
            except LLMError as error:
                print(f"[goal] could not parse {entry.id}: {error}")
                return  # it stays submitted; the next pass tries again
            if isinstance(parsed, Unsupported):
                if self.queue.mark(entry.id, "unsupported", reason=parsed.reply):
                    self._log_event("goal_unsupported", goal_id=entry.id, reply=parsed.reply)
                    self._say(parsed.reply)
            elif self.queue.accept(parsed):
                self._log_event(
                    "goal_parsed", goal_id=entry.id, goal_kind=parsed.kind, item=parsed.item, count=parsed.count, target=parsed.target
                )
                self._say(f"Queued {entry.id}: {goal_label(parsed)}")

    def _log_event(self, kind: str, **fields: Any) -> None:
        """An event outside any episode, in EpisodeLog's line format."""
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "kind": kind, **fields}) + "\n")

    def _say(self, message: str) -> None:
        self.backend.say(message.replace("#", "")[:MAX_CHAT_CHARS])

    def _obtain_items(self, *statuses: str, source: str | None = None) -> set[str]:
        return {
            g.spec.item for g in self.queue.goals()
            if g.status in statuses and g.spec is not None and g.spec.kind == "obtain" and g.spec.item and source in (None, g.source)
        }

    def _before_step(self, episode: Episode, step: PlanStep, plan: list[PlanStep]) -> None:
        """Runs before each plan step: sees new chat commands and goals, ends the running goal if it was
        cancelled or outranked, then calls the caller's hook (which runs last, so a hidden change is never
        switched on for a step that doesn't run)."""
        self._poll_bot()
        self._parse_pending()
        running = self._running
        if running is not None:
            entry = self.queue.get(running.id)  # curriculum goals are not in the queue
            if entry is not None and entry.status == "cancelled":
                raise GoalInterrupted(running.id, "cancelled")
            # S07 (death-handling): raise GoalInterrupted(running.id, "death") here if the bot died since the goal started.
            waiting = self.queue.next()
            if waiting is not None and GOAL_RANK[waiting.source] < GOAL_RANK[running.source]:
                raise GoalInterrupted(running.id, "preempted")
        if self.hooks.before_step is not None:
            self.hooks.before_step(episode, step, plan)

    # -- one goal ---------------------------------------------------------

    def _run_goal(self, spec: GoalSpec, goal: Goal | None = None) -> bool:
        """Run one goal: `goal` is the curriculum's for a curriculum spec; a queued spec gets one built here.
        Returns whether it failed for want of a resource that was not found or reachable."""
        index = self.goals_done + 1
        started_at = time.time()
        name = f"g{index:03d}"
        observation = self.backend.begin_episode()
        self._inventory_seen = observation.inventory
        free_slots_before = observation.free_slots
        observation, tossed = self._make_room(spec.item, observation)
        if goal is None:
            goal = self._start_queued_goal(spec, observation)
            if goal is None:
                return False  # cancelled meanwhile: no goal index used, nothing recorded
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
            deadline=time.monotonic() + min(goal_clock_s, max(self._remaining_s(), 0.0)),
        )
        asked = {} if spec.source == "curriculum" else {"asked": spec.count}
        log.event(
            "goal", index=index, item=goal.item, count=task.goal_count, reason=goal.reason, budget=budget,
            goal_id=spec.id, source=spec.source, text=spec.text, goal_kind=spec.kind, **asked,
        )
        if tossed:
            log.event("tossed", items=tossed, free_slots_before=free_slots_before, free_slots_after=observation.free_slots)
            names = [f"{count} {item.lstrip('#')}" for item, count in tossed.items()]
            listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
            self.backend.say(f"Dropping {listed} to make room."[:MAX_CHAT_CHARS])
        self._log_unreported(log)

        interrupt: str | None = None
        self._running = spec
        try:
            # `goal` is left out for a curriculum goal (its default None): v2's tests stub run_schemavoyager without it.
            result = run_schemavoyager(
                episode, self.store, self._before_step, excluded=self._excluded_rules(),
                **({} if spec.source == "curriculum" else {"goal": spec}),
            )
        except GoalInterrupted as stop:
            log.event("goal_interrupted", goal_id=stop.goal_id, reason=stop.reason)
            result = episode.finish(success=False)  # as run_schemavoyager does at a normal end, but no skill is compiled
            log.event("certified", rules=self.store.certify())
            interrupt = stop.reason
        finally:
            self._running = None
        # Cut short by the life's own clock (not the goal's deadline or budget): no attempt to count.
        if interrupt is None and not result.success and life_clock_limits_goal and episode.out_of_time and episode.budget_left > 0:
            interrupt = "wall_clock"
        self._end_goal(spec, goal, index, budget, started_at, episode, result, tossed, interrupt)
        steps = [event for event in log.events if event["kind"] == "step"]
        return not result.success and interrupt is None and bool(steps) and steps[-1]["error"] in MISSING_RESOURCE_ERRORS

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
        result: EpisodeResult, tossed: dict[str, int], interrupt: str | None,
    ) -> None:
        """The one ending of a goal (v3.1d): done, failed, cancelled, preempted or cut short by the life's clock."""
        queued_goal = spec.source != "curriculum"
        label = goal_label(spec)
        chat = ""
        if queued_goal:
            if interrupt == "cancelled":  # the queue already says cancelled
                chat = "" if spec.id in self._cancelled_in_chat else f"Cancelled: {label}"
            elif interrupt == "preempted":
                chat = self._pausing_line(label)
                self.queue.mark(spec.id, "queued")
            elif interrupt is not None:  # wall_clock: --resume runs it again
                self.queue.mark(spec.id, "queued")
            elif result.success:
                self.queue.mark(spec.id, "done")
                chat = f"Done: {label}"
            else:
                reason = self._failure_reason(episode)
                self.queue.mark(spec.id, "failed", reason=reason)
                chat = f"Couldn't get {label}: {reason}"
            # S07 (death-handling): a "death" interrupt goes back to queued with its deaths count (v3.2d).
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
        if interrupt is not None:
            record["interrupt"] = interrupt
        self._attach_unreported(record)
        if chat:
            self._say(chat)
        self._save(record)
        if self.hooks.after_goal is not None:
            self.hooks.after_goal(record)

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

    def _make_room(self, goal_item: str, observation: Observation) -> tuple[Observation, dict[str, int]]:
        """Toss junk when the bot is nearly full. Returns the observation to start the goal from
        (a new one after a toss) and what was tossed. The goal's item and what the user asked for are never junk."""
        if observation.free_slots is None or observation.free_slots >= MIN_FREE_SLOTS:
            return observation, {}
        wanted = {goal_item} | self._obtain_items("queued") | self._obtain_items("done", source="user")
        junk = junk_to_toss(
            observation.inventory, self.store.usable_rules(self.world_id), wanted, observation.free_slots,
            min_free=MIN_FREE_SLOTS,
        )
        if not junk:
            return observation, {}
        tossed = self.backend.toss(junk)
        return self.backend.observe(), tossed

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
            deadline=time.monotonic() + min(self.config.goal_wall_clock_s, max(self._remaining_s(), 0.0)),
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
            "asked": sorted(self.asked), "repaired": sorted(self.repaired), "blocked": self.blocked, "retests": self.retests, "observed_blocks": sorted(self.observed_blocks),
            "tokens": self.earlier_tokens + self.llm.total_tokens, "spend_usd": self.llm.spent_usd,
        }
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _excluded_rules(self) -> frozenset[str]:
        return frozenset(rule for rule, goals in self.blocked.items() if goals >= BLOCKED_GOALS_TO_EXCLUDE)

    def _certified_ids(self) -> set[str]:
        return {rule.id for rule in self.store.rules.values() if rule.status == "certified"}

    def _remaining_s(self) -> float:
        return self.config.wall_clock_s - (time.monotonic() - self.started)
