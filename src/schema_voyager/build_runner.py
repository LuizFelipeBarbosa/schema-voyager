"""The build runner (v3.4b): gather a blueprint's materials through the planner, walk back to the site, place and
clear every cell bottom-up checking each one, and end on a final scan that matches the blueprint.

`prepare_build` runs before the episode exists (it sets the budget and the clock); `run_build` is the episode.
Place, clear and goto go through `Episode.act` but are never rule evidence (D1): nothing here reaches
`store.record_transition`. Contract: docs/v3-interfaces.md, v3.4b "The build runner".
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from . import blueprint, builder, normalize
from .adapter import LifeBackend
from .agent import BeforeStep, pursue
from .blueprint import Box, Pos
from .builder import BuildStep
from .episode import Episode
from .goals import goal_label
from .inventory import SCAFFOLD_BLOCKS
from .models import Action, EpisodeResult, GoalSpec, Site
from .planner import PlanningError, make_plan_for
from .schema_store import SchemaStore

BUILD_GOAL = "build"  # a build goal's Task.goal_item (like EXPLORE_GOAL); goal_count is progress total
BUILD_SECONDS_PER_STEP = 10  # a build goal's clock: per build step, plus 30 s per planned gathering step
MAX_BUILD_PASSES = 3  # scan, build, final scan; again while cells don't match
SCAFFOLD_MIN = 16  # SCAFFOLD_BLOCKS to hold before building: the first cell of a layer is placed from a pillar
NEAR_SITE = 12  # horizontal blocks from the footprint's centre; farther, the runner walks back

# The same values as life.py's MIN_GOAL_BUDGET, BUDGET_PER_PLAN_STEP and GOAL_SECONDS_PER_PLAN_STEP (the life
# imports this module, so it can't import them).
_MIN_GOAL_BUDGET = 30
_BUDGET_PER_PLAN_STEP = 4
_GOAL_SECONDS_PER_PLAN_STEP = 30

_MIN_SCAFFOLD_TO_CLIMB = 4  # fewer held and an unreachable cell means the scaffold ran out: gather more
_MAX_REASON_CHARS = 80


@dataclass
class BuildPlan:
    spec: GoalSpec
    origin: Pos
    site: Site | None  # the survey's pick; None when the origin came from the protected list
    steps: list[BuildStep]  # from the scan prepare_build made
    needs: dict[str, int]  # family -> count to hold: needed(steps) by normalize.family, plus scaffold
    gather_steps: int  # planner steps to gather what isn't held (for the budget and the clock)
    protected: list[Box] = field(default_factory=list)  # everything built: scaffold is never cleared inside
    placed: int = 0  # builder.progress of the latest cell states; run_build updates it as it goes

    @property
    def budget(self) -> int:
        return max(_MIN_GOAL_BUDGET, _BUDGET_PER_PLAN_STEP * self.gather_steps + 2 * len(self.steps) + 10)

    @property
    def clock_s(self) -> float:
        return _GOAL_SECONDS_PER_PLAN_STEP * self.gather_steps + BUILD_SECONDS_PER_STEP * len(self.steps)


@dataclass
class BuildOutcome:
    result: EpisodeResult
    reason: str  # "" on success, else at most 80 characters
    placed: int
    total: int


def prepare_build(
    backend: LifeBackend, store: SchemaStore, spec: GoalSpec, *, origin: Pos | None, protected: list[Box],
    world_id: str, excluded: frozenset[str],
) -> BuildPlan | str:
    """Pick the site (unless `origin` is known), scan it, and plan the gathering. A string is why it can't be built."""
    bp = spec.blueprint
    site = None
    if origin is None:
        site = _pick_site(backend, bp, protected)
        if site is None:
            return "no site"
        origin = site.origin

    scan = backend.scan(*blueprint.site_box(bp, origin))
    steps = builder.build_steps(bp, origin, scan)
    observation = backend.observe()
    needs = _material_needs(steps, observation.inventory)
    rules = [rule for rule in store.usable_rules(world_id) if rule.id not in excluded]
    try:
        gathering = make_plan_for(
            needs, normalize.counts(observation.inventory), rules, store.skill_for,
            visible=normalize.counts(observation.nearby),
        )
    except PlanningError as error:
        return f"no plan: {error}"[:_MAX_REASON_CHARS]
    return BuildPlan(spec, origin, site, steps, needs, len(gathering),
                     protected=protected, placed=builder.progress(bp, origin, scan)[0])


def run_build(
    episode: Episode, store: SchemaStore, plan: BuildPlan, *, before_step: BeforeStep,
    check_interrupts: Callable[[], None], excluded: frozenset[str],
) -> BuildOutcome:
    """Run the build in `episode`: materials, walk back, build passes, final scan. `GoalInterrupted` from the
    hooks propagates; the life finishes the episode then."""
    run = _BuildRun(episode, store, plan, before_step, check_interrupts, excluded)
    episode.log.event(
        "episode_start", system=episode.system, task=episode.task.task_id, description=episode.task.description,
        world_id=episode.world_id, budget=episode.task.budget, layout=episode.layout_seed,
    )
    episode.backend.set_keep_out(run.box)
    try:
        reason = run.execute()
    finally:
        episode.backend.set_keep_out(None)

    episode.log.event(
        "build_done", matched=not reason, mismatched=[list(pos) for pos in run.mismatched[:10]],
        scaffold_cleared=run.scaffold_cleared, scaffold_left=run.scaffold_left,
    )
    result = episode.finish(success=not reason, announce=False)
    episode.log.event("certified", rules=store.certify())
    return BuildOutcome(result, reason[:_MAX_REASON_CHARS], plan.placed, run.total)


# -- preparing ----------------------------------------------------------------------


def _pick_site(backend: LifeBackend, bp: dict, protected: list[Box]) -> Site | None:
    """The survey's first site clear of every protected box; with none, explore up once and survey again."""
    width, _, depth = bp["size"]
    for attempt in range(2):
        if attempt:
            backend.act(Action("explore", direction="up"))
        site = next((s for s in backend.survey((width, depth)) if not _touches(s.origin, (width, depth), protected)), None)
        if site is not None:
            return site
    return None


def _inside(pos: Pos, box: Box) -> bool:
    """Whether `pos` is in the half-open box (origin, size)."""
    return all(low <= value < low + extent for value, low, extent in zip(pos, *box))


def _touches(origin: Pos, size: tuple[int, int], protected: list[Box]) -> bool:
    """Whether a footprint, grown by one block on each side, overlaps a protected box in x and z."""
    (x0, _, z0), (width, depth) = origin, size
    return any(
        x0 - 1 < bx + bw and bx < x0 + width + 1 and z0 - 1 < bz + bd and bz < z0 + depth + 1
        for (bx, _, bz), (bw, _, bd) in protected
    )


def _material_needs(steps: list[BuildStep], inventory: dict[str, int]) -> dict[str, int]:
    """Family -> count to hold for the steps' place values, plus dirt up to SCAFFOLD_MIN scaffold blocks in all."""
    needs: dict[str, int] = {}
    for value, count in builder.needed(steps).items():
        needs[normalize.family(value)] = needs.get(normalize.family(value), 0) + count
    shortfall = SCAFFOLD_MIN - sum(inventory.get(block, 0) for block in SCAFFOLD_BLOCKS)
    if shortfall > 0:
        needs["dirt"] = needs.get("dirt", 0) + inventory.get("dirt", 0) + shortfall
    return needs


# -- running ------------------------------------------------------------------------


@dataclass
class _BuildRun:
    """What one `run_build` shares between its phases."""

    episode: Episode
    store: SchemaStore
    plan: BuildPlan
    before_step: BeforeStep
    check_interrupts: Callable[[], None]
    excluded: frozenset[str]
    states: dict[Pos, str | None] = field(default_factory=dict)  # the cells as last scanned or acted on
    steps: list[BuildStep] = field(default_factory=list)
    mismatched: list[Pos] = field(default_factory=list)  # from the latest scan
    failed_cells: dict[Pos, str] = field(default_factory=dict)  # cell -> the error it failed with, this goal
    scaffold_cleared: int = 0
    scaffold_left: int = 0

    @property
    def bp(self) -> dict:
        return self.plan.spec.blueprint

    @property
    def total(self) -> int:
        return builder.progress(self.bp, self.plan.origin, {})[1]

    @property
    def box(self) -> Box:
        return blueprint.site_box(self.bp, self.plan.origin)

    def execute(self) -> str:
        """The whole build; "" on success, else why not."""
        episode, plan = self.episode, self.plan
        size = self.bp["size"]
        episode.log.event(
            "build_site", goal_id=plan.spec.id, origin=list(plan.origin), size=size,
            cut=plan.site.cut if plan.site else 0, fill=plan.site.fill if plan.site else 0,
        )
        self.announce()
        reason = self.gather_materials(plan.needs)
        if reason:
            return reason
        placed_before_build = len(episode.placed_cells)
        for number in range(1, MAX_BUILD_PASSES + 1):
            reason = self.scan_on_site(number)
            if reason:
                return reason
            if number == 1:
                episode.backend.say(f"Placing {sum(step.kind == 'place' for step in self.steps)} blocks.")
            reason = self.build_pass(number)
            if reason:
                return reason
            self.scan(number)  # the final scan
            if not self.mismatched:
                self.clear_scaffold(episode.placed_cells[placed_before_build:])
                return ""
        return f"{len(self.mismatched)} cells don't match"

    def clear_scaffold(self, placed_cells: list[tuple[int, int, int, str]]) -> None:
        """Clear the scaffold the build's walks placed outside the house box and every protected box, highest first,
        where the cell still holds the recorded block. A failed scan or clear leaves the cell. Never evidence."""
        from .mineflayer_backend import BackendError  # here, not at the top: it pulls in rcon (see Episode)

        last_block = {(x, y, z): block for x, y, z, block in placed_cells}  # the last name recorded per cell
        outside = [cell for cell in last_block if not any(_inside(cell, box) for box in [self.box, *self.plan.protected])]
        for cell in sorted(outside, key=lambda pos: -pos[1]):
            try:
                holds_scaffold = self.episode.backend.scan(cell, (1, 1, 1)).get(cell) == last_block[cell]
                cleared = holds_scaffold and self.episode.act(Action("clear", pos=cell, scaffold=False)).ok
            except BackendError:
                cleared = False
            if cleared:
                self.scaffold_cleared += 1
            else:
                self.scaffold_left += 1

    def announce(self) -> None:
        """One chat line: the build's place and, if anything is missing, what it will gather."""
        held = normalize.counts(self.episode.inventory)
        missing = {item: count - held.get(item, 0) for item, count in self.plan.needs.items() if held.get(item, 0) < count}
        x, y, z = self.plan.origin
        line = f"Building {self.bp['name']} at {x} {y} {z}"
        if missing:
            line += ": gathering " + ", ".join(_label(item, count) for item, count in missing.items())
        self.episode.backend.say(line + ".")

    # -- materials ------------------------------------------------------------

    def gather_materials(self, needs: dict[str, int]) -> str:
        """Run the rule planner until every need is held; else the needs not held, as a reason.

        The planner gathers deeper items first, so a door would be crafted from whatever planks the logs make, of
        mixed species. Gathering everything else first leaves the bulk of the planks held, and some species has 6."""
        if "#door" in needs:
            self.pursue_needs({item: count for item, count in needs.items() if item != "#door"})
            if not self.episode.counts.get("#door"):
                self.ensure_door_planks()
        self.pursue_needs(needs)
        held = self.episode.counts
        short = [_label(item, count) for item, count in needs.items() if held.get(item, 0) < count]
        if not short:
            return ""
        if not self.episode.can_act:
            return self.out_of_budget()
        return "couldn't get " + ", ".join(short)

    def ensure_door_planks(self) -> None:
        """Hold 6 planks of one species, which the planner can't see (it counts #planks across species). Craft them
        from the species with the most planks plus 4 per log; if none reaches 6, gather 2 more logs and look again
        (twice at most). A direct step, not evidence for a rule; a failed craft leaves the door to fail."""
        for round_number in range(3):
            planks: Counter[str] = Counter()
            logs: Counter[str] = Counter()
            for raw, count in self.episode.inventory.items():
                if normalize.family(raw) == "#planks":
                    planks[normalize.species(raw)] += count
                elif normalize.family(raw) == "#log":
                    logs[normalize.species(raw)] += count
            if any(count >= normalize.DOOR_PLANKS for count in planks.values()):
                return
            potential = {kind: planks[kind] + 4 * logs[kind] for kind in planks.keys() | logs.keys()}
            if potential:
                best = min(potential, key=lambda kind: (-potential[kind], kind))
                if potential[best] >= normalize.DOOR_PLANKS:
                    times = math.ceil((normalize.DOOR_PLANKS - planks[best]) / 4)
                    self.episode.act(Action("craft", f"{best}_planks", times=times))
                    return
            if round_number < 2:
                self.pursue_needs({"#log": self.episode.counts.get("#log", 0) + 2})

    def pursue_needs(self, needs: dict[str, int]) -> None:
        pursue(
            self.episode, self.store, needs, before_step=self.before_step, excluded=self.excluded, narrate=False
        )

    def remaining_needs(self) -> dict[str, int]:
        """What the cells not yet matching still take, from the states so far."""
        return _material_needs(builder.build_steps(self.bp, self.plan.origin, self.states), self.episode.inventory)

    # -- the site -------------------------------------------------------------

    def scan_on_site(self, number: int) -> str:
        """Walk back if the site is far or was partly unseen, then scan it. "" or "can't see the site"."""
        if self.is_far():
            self.walk_back()
        self.scan(number)
        if self.unknown_cells() and self.walk_back():
            self.scan(number)
        return "can't see the site" if self.unknown_cells() else ""

    def scan(self, number: int) -> None:
        """Rescan the site: the cell states, the steps left, the mismatches, and the progress."""
        origin = self.plan.origin
        self.states = self.episode.backend.scan(*self.box)
        self.steps = builder.build_steps(self.bp, origin, self.states)
        self.mismatched = builder.mismatches(self.bp, origin, self.states)
        self.episode.log.event(
            "build_scan", **{"pass": number}, mismatched=len(self.mismatched), unknown=len(self.unknown_cells()),
        )
        self.log_progress(force=True)

    def unknown_cells(self) -> list[Pos]:
        return [pos for pos, found in self.states.items() if found is None]

    def is_far(self) -> bool:
        (ox, _, oz), (width, _, depth) = self.plan.origin, self.bp["size"]
        x, _, z = self.episode.backend.observe().position
        return math.hypot(x - (ox + width / 2), z - (oz + depth / 2)) > NEAR_SITE

    def walk_back(self) -> bool:
        """`goto` a point 2 blocks out from the middle of a side of the footprint (south, then east, north, west)
        until one is reached. Whether it arrived."""
        (ox, oy, oz), (width, _, depth) = self.plan.origin, self.bp["size"]
        sides = [
            (ox + width // 2, oy, oz + depth + 1),
            (ox + width + 1, oy, oz + depth // 2),
            (ox + width // 2, oy, oz - 2),
            (ox - 2, oy, oz + depth // 2),
        ]
        for point in sides:
            if not self.episode.can_act:
                return False
            if self.episode.act(Action("goto", pos=point)).ok:
                return True
        return False

    # -- building -------------------------------------------------------------

    def build_pass(self, number: int) -> str:
        """Run the steps of the latest scan in order; "" when they are all done, else why the build stops."""
        materials_rerun = False
        steps = list(self.steps)
        index = 0
        while index < len(steps):
            if not self.episode.can_act:
                return self.out_of_budget()
            self.check_interrupts()
            step = steps[index]
            error = self.run_step(step)
            if error is None:
                index += 1
                continue
            if self.scaffold_or_material_ran_out(error) and not materials_rerun:
                materials_rerun = True
                reason = self.gather_materials(self.remaining_needs())
                if reason:
                    return reason
                continue  # the same step again
            reason = self.remember_failure(step, error)
            if reason:
                return reason
            self.scan(number)
            steps = sorted(self.steps, key=lambda s: s.pos in self.failed_cells)  # stable: failed cells last
            index = 0
        return ""

    def run_step(self, step: BuildStep) -> str | None:
        """Place or clear one cell through `Episode.act`; the error, or None when the cell is done."""
        episode = self.episode
        if step.kind == "clear":
            action = Action("clear", pos=step.pos)
        else:
            raw = _held_member(step.value, episode.inventory)
            action = Action("place", target=raw or step.value, pos=step.pos)
            if raw is None:
                self.log_missing_item(action)
                return "missing_item"
        outcome = episode.act(action)
        if not outcome.ok:
            return outcome.error or "failed"
        self.states[step.pos] = outcome.detail.get("found", "air" if step.kind == "clear" else action.target)
        self.log_progress()
        return None

    def log_missing_item(self, action: Action) -> None:
        """A place with nothing held to place never reaches the backend; the trace still shows it failed."""
        self.episode.log.event(
            "step", index=self.episode.actions, action=action.describe(), action_obj=action.to_dict(), ok=False,
            error="missing_item", block_removed=None, drops={}, picked_up={}, inventory_delta={},
            inventory=self.episode.inventory, blocks_walked=0.0, detail={"target": action.target},
        )

    def scaffold_or_material_ran_out(self, error: str) -> bool:
        """`missing_item`, or `unreachable` with the pillars used up: gather again (once per pass)."""
        scaffold = sum(self.episode.inventory.get(block, 0) for block in SCAFFOLD_BLOCKS)
        return error == "missing_item" or (error == "unreachable" and scaffold < _MIN_SCAFFOLD_TO_CLIMB)

    def remember_failure(self, step: BuildStep, error: str) -> str:
        """A cell that failed before in this goal stops the build; else it is remembered. "" or the reason."""
        if step.pos in self.failed_cells:
            x, y, z = step.pos
            return f"stuck at {x} {y} {z}: {error}"
        self.failed_cells[step.pos] = error
        return ""

    def log_progress(self, force: bool = False) -> None:
        placed, total = builder.progress(self.bp, self.plan.origin, self.states)
        if force or placed != self.plan.placed:
            self.plan.placed = placed
            self.episode.log.event("build_progress", goal_id=self.plan.spec.id, placed=placed, total=total)

    def out_of_budget(self) -> str:
        return "out of time" if self.episode.out_of_time else "out of steps"


def _held_member(value: str, inventory: dict[str, int]) -> str | None:
    """The raw id to place for a palette value: the group or door member held most; a raw id as it is. None when
    a group or a door has no member held."""
    if value in blueprint.GROUPS:
        candidates = normalize.members(value)
    elif value in blueprint.DOORS:
        candidates = sorted(blueprint.DOORS)
    else:
        return value
    held = {raw: inventory[raw] for raw in candidates if inventory.get(raw, 0) > 0}
    return min(held, key=lambda raw: (-held[raw], raw)) if held else None


def _label(item: str, count: int) -> str:
    """"55 planks", as chat names an obtain goal."""
    return goal_label(GoalSpec(id="", kind="obtain", source="system", item=item, count=count))
