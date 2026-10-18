"""The build runner (v3.4b): make_plan_for, pursue, prepare_build and run_build in the simulator with the fake LLM,
and Episode.act's policy for place, clear and goto."""

import copy
import time

import pytest

from schema_voyager import blueprint, build_runner, builder, normalize
from schema_voyager.agent import pursue
from schema_voyager.build_runner import BUILD_GOAL, _held_member, prepare_build, run_build
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.goals import GoalInterrupted
from schema_voyager.llm import FAKE_HUT, LLM
from schema_voyager.models import Action, GoalSpec, Outcome, Task
from schema_voyager.planner import make_plan, make_plan_for
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog

SEED = 7
WORLD_ID = "w"
FAMILIES = ["#planks", "#log", "stick", "torch", "dirt", "coal", "coal_ore", "crafting_table", "wooden_pickaxe",
            "grass_block"]
NO_HOOK = lambda *args: None  # noqa: E731


def _door_less_hut():
    hut = copy.deepcopy(FAKE_HUT)
    hut["layers"] = [[row.replace("D", "P") for row in layer] for layer in hut["layers"]]
    del hut["palette"]["D"]
    return hut


HUT = _door_less_hut()
SPEC = GoalSpec(id="u001", kind="build", source="user", text="a small hut", blueprint=HUT)


@pytest.fixture
def world():
    return NaturalWorld(SEED)


@pytest.fixture
def llm(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")


@pytest.fixture
def store(llm):
    store = SchemaStore()
    for rule in llm.propose_prior_rules_v2(FAMILIES, [], WORLD_ID):
        store.add_rule(rule)
    return store


def _prepare(world, store, origin=None, protected=()):
    plan = prepare_build(world, store, SPEC, origin=origin, protected=list(protected), world_id=WORLD_ID,
                         excluded=frozenset())
    assert not isinstance(plan, str), plan
    return plan


def _episode(world, llm, plan=None, goal_item=BUILD_GOAL, goal_count=1, budget=400, seconds=3000):
    """An Episode as the life builds it: natural, with a deadline."""
    budget = plan.budget if plan else budget
    observation = world.observe()
    task = Task("g001", "train", goal_item, goal_count, dict(observation.inventory), (), budget, "test")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id=WORLD_ID, backend=world,
        llm=llm, log=EpisodeLog(None, "test", say=world.say), inventory=dict(observation.inventory), natural=True,
        deadline=time.monotonic() + seconds,
    )


def _run(world, llm, store, plan, check_interrupts=lambda: None):
    episode = _episode(world, llm, plan)
    outcome = run_build(episode, store, plan, before_step=NO_HOOK, check_interrupts=check_interrupts,
                        excluded=frozenset())
    return episode, outcome


def _events(episode, kind):
    return [event for event in episode.log.events if event["kind"] == kind]


def _steps_of(episode, kind):
    return [event for event in _events(episode, "step") if event["action_obj"]["kind"] == kind]


def _site_scan(world, plan):
    return world.scan(*blueprint.site_box(HUT, plan.origin))


# --- make_plan_for and pursue ----------------------------------------------------------


def test_make_plan_for_leaves_two_needs_held_when_one_spends_the_other(store):
    rules = store.usable_rules(WORLD_ID)
    plan = make_plan_for({"stick": 4, "#planks": 8}, {}, rules, store.skill_for)
    final = plan[-1].expected_inventory
    assert final["stick"] >= 4 and final["#planks"] >= 8  # sticks spend planks, so the planks were made again
    crafts = [step.action.target for step in plan if step.action.kind == "craft"]
    assert "stick" in crafts and crafts.count("#planks") >= 2


def test_make_plan_for_a_need_already_held_plans_nothing(store):
    assert make_plan_for({"#planks": 4}, {"#planks": 6}, store.usable_rules(WORLD_ID), store.skill_for) == []


def test_pursue_with_one_need_plans_what_make_plan_plans(world, llm, store):
    episode = _episode(world, llm, goal_item="#log", goal_count=2)
    expected = make_plan("#log", 2, {}, store.usable_rules(WORLD_ID), store.skill_for,
                         visible=normalize.counts(world.observe().nearby))
    completed = pursue(episode, store, {"#log": 2})
    logged = _events(episode, "plan")[0]["steps"]
    assert [step["action"] for step in logged] == [step.action.describe() for step in expected]
    assert episode.goal_met and len(completed) == len(expected)


def test_pursue_without_narrating_logs_the_plan_but_says_nothing(world, llm, store):
    said = []
    world.say = said.append
    episode = _episode(world, llm)
    pursue(episode, store, {"#log": 2, "stick": 4}, narrate=False)
    assert _events(episode, "plan") and said == []
    held = episode.counts
    assert held["#log"] >= 2 and held["stick"] >= 4


# --- prepare_build ----------------------------------------------------------------------


def test_prepare_build_takes_the_surveys_first_site_and_plans_the_materials(world, store):
    plan = _prepare(world, store)
    site = world.survey((5, 5))[0]
    assert plan.site == site and plan.origin == site.origin
    assert plan.needs == {"#log": 12, "#planks": 57, "torch": 1, "dirt": build_runner.SCAFFOLD_MIN}
    assert plan.steps == builder.build_steps(HUT, plan.origin, _site_scan(world, plan))
    assert builder.needed(plan.steps) == {"#log": 12, "#planks": 57, "torch": 1}
    assert plan.gather_steps > 0 and plan.placed == 0
    assert plan.budget == max(30, 4 * plan.gather_steps + 2 * len(plan.steps) + 10)
    assert plan.clock_s == 30 * plan.gather_steps + 10 * len(plan.steps)


def test_prepare_build_counts_scaffold_already_held(world, store):
    world.inventory.update(dirt=10, tuff=3)
    assert _prepare(world, store).needs["dirt"] == 10 + (build_runner.SCAFFOLD_MIN - 13)


def test_prepare_build_keeps_a_given_origin_and_skips_what_is_placed(world, store):
    origin = world.survey((5, 5))[0].origin
    fresh = _prepare(world, store, origin=origin)
    world._changes[(origin[0], origin[1], origin[2])] = "oak_log"  # the corner of layer 0
    resumed = _prepare(world, store, origin=origin)
    assert resumed.site is None and resumed.origin == origin
    assert len(resumed.steps) == len(fresh.steps) - 1 and resumed.placed == 1


def test_prepare_build_skips_sites_next_to_a_protected_box(world, store):
    first = world.survey((5, 5))[0].origin
    protected = [((first[0] + 2, 0, first[2] + 2), (1, 30, 1))]  # the first footprint's middle, one column
    plan = _prepare(world, store, protected=protected)
    assert plan.origin != first
    assert not build_runner._touches(plan.origin, (5, 5), protected)


def test_prepare_build_with_no_site_explores_up_once_then_says_so(world, store):
    everywhere = [((-500, 0, -500), (1000, 100, 1000))]
    actions = []
    act = world.act
    world.act = lambda action: (actions.append(action), act(action))[1]
    result = prepare_build(world, store, SPEC, origin=None, protected=everywhere, world_id=WORLD_ID,
                           excluded=frozenset())
    assert result == "no site"
    assert [action.describe() for action in actions] == ["explore up"]


def test_prepare_build_with_no_rule_for_a_material_is_no_plan(world, llm):
    empty = SchemaStore()
    result = prepare_build(world, empty, SPEC, origin=None, protected=[], world_id=WORLD_ID, excluded=frozenset())
    assert isinstance(result, str) and result.startswith("no plan: ") and len(result) <= 80


# --- run_build --------------------------------------------------------------------------


def test_a_build_from_an_empty_inventory_gathers_builds_and_matches(world, llm, store):
    said = []
    world.say = said.append
    plan = _prepare(world, store)
    episode, outcome = _run(world, llm, store, plan)

    assert outcome.reason == "" and outcome.result.success
    assert builder.mismatches(HUT, plan.origin, _site_scan(world, plan)) == []
    assert outcome.placed == outcome.total == plan.placed == 70
    progress = _events(episode, "build_progress")
    assert (progress[-1]["placed"], progress[-1]["total"], progress[-1]["goal_id"]) == (70, 70, "u001")
    assert _events(episode, "build_done") == [{**_events(episode, "build_done")[0], "matched": True, "mismatched": []}]
    assert [e["pass"] for e in _events(episode, "build_scan")] == [1, 1]
    assert _events(episode, "build_site")[0]["origin"] == list(plan.origin)
    assert _events(episode, "certified")
    assert world._keep_out is None
    assert said[0].startswith("Building small hut at ") and "gathering 12 logs, 57 planks, 1 torch, 16 dirt." in said[0]
    assert said.count("Placing 70 blocks.") == 1


def test_no_place_clear_or_goto_reaches_the_store(world, llm, store):
    plan = _prepare(world, store)
    episode, outcome = _run(world, llm, store, plan)
    assert outcome.result.success
    kinds = {transition.action.kind for transition in store.transitions}
    assert kinds and kinds <= {"mine", "craft", "smelt"}
    assert len(_steps_of(episode, "place")) == 70


def test_build_with_everything_held_does_not_gather(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=12, torch=1, dirt=16)
    said = []
    world.say = said.append
    episode, outcome = _run(world, llm, store, plan)
    assert outcome.result.success and not _events(episode, "plan")
    assert said[0].startswith("Building small hut at ") and said[0].endswith(".") and "gathering" not in said[0]


def test_a_preempted_build_resumes_on_the_same_origin_and_places_only_the_rest(world, llm, store):
    plan = _prepare(world, store)
    build_steps_run = []

    def preempt_after_ten():
        build_steps_run.append(1)
        if len(build_steps_run) > 10:
            raise GoalInterrupted("u001", "preempted")

    first = _episode(world, llm, plan)
    with pytest.raises(GoalInterrupted):
        run_build(first, store, plan, before_step=NO_HOOK, check_interrupts=preempt_after_ten, excluded=frozenset())
    assert len(_steps_of(first, "place")) + len(_steps_of(first, "clear")) == 10
    assert plan.placed == len(_steps_of(first, "place"))
    assert world._keep_out is None

    again = _prepare(world, store, origin=plan.origin)
    assert again.placed == plan.placed and again.site is None
    second, outcome = _run(world, llm, store, again)
    assert outcome.result.success and outcome.reason == ""
    assert len(_steps_of(second, "place")) == 70 - plan.placed
    assert builder.mismatches(HUT, plan.origin, _site_scan(world, plan)) == []


def test_a_missing_item_mid_build_reruns_the_materials(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=40, torch=1, dirt=16)  # spare logs: the rerun crafts, never mines
    seen = []

    def lose_the_planks():
        seen.append(1)
        if len(seen) == 21:
            for item in [item for item in world.inventory if item.endswith("_planks")]:
                del world.inventory[item]

    episode, outcome = _run(world, llm, store, plan, check_interrupts=lose_the_planks)
    assert outcome.result.success and outcome.reason == ""
    failed = [event for event in _steps_of(episode, "place") if event["error"] == "missing_item"]
    assert len(failed) == 1
    plan_after = next(event for event in _events(episode, "plan"))
    assert episode.log.events.index(plan_after) > episode.log.events.index(failed[0])
    assert builder.mismatches(HUT, plan.origin, _site_scan(world, plan)) == []


def test_a_bot_far_from_the_site_walks_back_before_scanning(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=12, torch=1, dirt=16)
    far_x = plan.origin[0] + 40
    world._cell = (far_x, world._column(far_x, plan.origin[2])[0] + 1, plan.origin[2])
    episode, outcome = _run(world, llm, store, plan)

    assert outcome.result.success
    kinds = [(event["kind"], event.get("action_obj", {}).get("kind")) for event in episode.log.events]
    assert kinds.index(("step", "goto")) < kinds.index(("build_scan", None))
    goto = _steps_of(episode, "goto")[0]
    assert goto["ok"] and goto["action_obj"]["pos"][2] == plan.origin[2] + 5 + 1  # 2 blocks south of the footprint


def test_unreachable_with_the_scaffold_used_up_reruns_the_materials(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=90, oak_log=20, torch=1, dirt=16)  # spare: the dirt may come from the site
    act = world.act
    refusals = []  # the dirt is gone from check 6 on; the next place answers unreachable, and so does its retry
    checks = []

    def lose_the_dirt():
        checks.append(1)
        if len(checks) == 6:
            del world.inventory["dirt"]
            refusals.append("armed")

    def refuse_the_next_place_twice(action):
        if action.kind == "place" and refusals and len(refusals) < 3:
            refusals.append("refused")
            return Outcome(ok=False, error="unreachable", inventory=dict(world.inventory))
        return act(action)

    world.act = refuse_the_next_place_twice
    episode = _episode(world, llm, plan)
    outcome = run_build(episode, store, plan, before_step=NO_HOOK, check_interrupts=lose_the_dirt,
                        excluded=frozenset())
    assert outcome.result.success
    mined = [step for step in _steps_of(episode, "mine") if step["action_obj"]["target"] in ("dirt", "grass_block")]
    assert mined  # the scaffold was gathered again, and the cell was not counted as a failure
    assert _events(episode, "build_done")[0]["matched"]


def test_a_cell_that_fails_twice_stops_the_build_stuck(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=12, torch=1, dirt=16)
    bad = plan.steps[5].pos
    act = world.act

    def refuse_one_cell(action):
        if action.kind == "place" and action.pos == bad:
            return Outcome(ok=False, error="no_support", inventory=dict(world.inventory))
        return act(action)

    world.act = refuse_one_cell
    episode, outcome = _run(world, llm, store, plan)
    assert not outcome.result.success
    assert outcome.reason == f"stuck at {bad[0]} {bad[1]} {bad[2]}: no_support"
    assert _events(episode, "build_done")[0]["matched"] is False
    assert world._keep_out is None


def test_the_final_scan_failing_to_match_ends_after_the_passes(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=70, oak_log=20, torch=1, dirt=16)  # spare: each pass places again
    scan = world.scan

    def erase_one_cell(origin, size):
        found = scan(origin, size)
        found[plan.origin[0] + 1, plan.origin[1], plan.origin[2]] = "stone"  # never what the blueprint says
        return found

    world.scan = erase_one_cell
    episode, outcome = _run(world, llm, store, plan)
    assert outcome.reason == "1 cells don't match" and not outcome.result.success
    assert [e["pass"] for e in _events(episode, "build_scan")] == [1, 1, 2, 2, 3, 3]
    assert _events(episode, "build_done")[0]["mismatched"] == [[plan.origin[0] + 1, plan.origin[1], plan.origin[2]]]


def test_a_site_that_stays_unseen_fails_cant_see_the_site(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=12, torch=1, dirt=16)
    scan = world.scan
    world.scan = lambda origin, size: {**scan(origin, size), plan.origin: None}
    episode, outcome = _run(world, llm, store, plan)
    assert outcome.reason == "can't see the site"
    assert len(_steps_of(episode, "goto")) >= 1 and not _steps_of(episode, "place")


def test_running_out_of_budget_ends_out_of_steps(world, llm, store):
    plan = _prepare(world, store)
    world.inventory.update(oak_planks=57, oak_log=12, torch=1, dirt=16)
    episode = _episode(world, llm, budget=12)
    outcome = run_build(episode, store, plan, before_step=NO_HOOK, check_interrupts=lambda: None,
                        excluded=frozenset())
    assert outcome.reason == "out of steps" and not outcome.result.success
    assert 0 < outcome.placed < outcome.total


def test_materials_that_cannot_be_gathered_fail_the_goal_and_free_the_keep_out_box(world, llm, store):
    plan = _prepare(world, store)
    episode = _episode(world, llm, plan)
    outcome = run_build(episode, SchemaStore(), plan, before_step=NO_HOOK, check_interrupts=lambda: None,
                        excluded=frozenset())  # no rules: the planner has nothing to plan with
    assert outcome.reason == "couldn't get 12 logs, 57 planks, 1 torch, 16 dirt" and not outcome.result.success
    assert world._keep_out is None


def _build_run(world, llm, store, plan):
    return build_runner._BuildRun(
        episode=_episode(world, llm, plan), store=store, plan=plan, before_step=NO_HOOK,
        check_interrupts=lambda: None, excluded=frozenset(),
    )


def _spy_on_pursue(monkeypatch):
    calls = []

    def spy(episode, store, needs, **kwargs):
        calls.append(dict(needs))
        return pursue(episode, store, needs, **kwargs)

    monkeypatch.setattr(build_runner, "pursue", spy)
    return calls


def test_gathering_with_a_door_need_pursues_everything_else_first_then_all(world, llm, store, monkeypatch):
    calls = _spy_on_pursue(monkeypatch)
    run = _build_run(world, llm, store, _prepare(world, store))
    needs = {"#door": 1, "#log": 2, "dirt": 3}

    run.gather_materials(needs)

    assert calls == [{"#log": 2, "dirt": 3}, needs]


def test_gathering_without_a_door_need_pursues_once(world, llm, store, monkeypatch):
    calls = _spy_on_pursue(monkeypatch)
    run = _build_run(world, llm, store, _prepare(world, store))
    needs = {"#log": 2, "dirt": 3}

    assert run.gather_materials(needs) == ""

    assert calls == [needs]


def test_the_reason_names_every_need_not_held_in_order(world, llm, store):
    run = _build_run(world, llm, SchemaStore(), _prepare(world, store))  # no rules: nothing can be gathered

    assert run.gather_materials({"#door": 1, "#log": 12, "dirt": 0}) == "couldn't get 1 door, 12 logs"


# --- placing a group ---------------------------------------------------------------------


def test_a_group_places_the_member_held_most_and_a_door_any_door_held():
    held = {"birch_planks": 5, "oak_planks": 9, "spruce_planks": 9, "oak_log": 3, "birch_door": 1, "oak_door": 0}
    assert _held_member("#planks", held) == "oak_planks"  # ties go by name
    assert _held_member("#log", held) == "oak_log"
    assert _held_member("oak_door", held) == "birch_door"
    assert _held_member("glass", held) == "glass"
    assert _held_member("#cobblestone", held) is None
    assert _held_member("oak_door", {"oak_planks": 6}) is None


# --- Episode.act's policy for the build kinds ---------------------------------------------


class ScriptedBackend:
    """Answers each act with the next scripted error (None is success); records every call."""

    name = "scripted"

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def act(self, action):
        self.calls.append(action)
        error = self.errors.pop(0) if self.errors else None
        return Outcome(ok=error is None, error=error, inventory={})

    def look(self, blocks, radius=64):
        self.calls.append(("look", blocks))
        return {}

    def observe(self):
        raise AssertionError("a build action must not explore, so it never observes")

    def say(self, message):
        pass


def _act(tmp_path, errors, action):
    backend = ScriptedBackend(errors)
    episode = Episode(
        episode_id="e", system="D", task=Task("g", "train", BUILD_GOAL, 1, {}, (), 40, "test"), layout_seed=0,
        world_id=WORLD_ID, backend=backend, llm=LLM(mode="fake", cache_path=tmp_path / "c.jsonl"),
        log=EpisodeLog(None, "test"), natural=True,
    )
    outcome = episode.act(action)
    return outcome, backend.calls


PLACE = Action("place", target="oak_planks", pos=(1, 2, 3))


def test_a_place_that_is_unreachable_is_tried_twice_in_place(tmp_path):
    outcome, calls = _act(tmp_path, ["unreachable", "unreachable", "unreachable"], PLACE)
    assert outcome.error == "unreachable" and calls == [PLACE, PLACE]


def test_a_place_that_times_out_once_is_retried_and_succeeds(tmp_path):
    outcome, calls = _act(tmp_path, ["timeout"], PLACE)
    assert outcome.ok and calls == [PLACE, PLACE]


def test_a_place_with_no_support_is_tried_once(tmp_path):
    outcome, calls = _act(tmp_path, ["no_support"], PLACE)
    assert outcome.error == "no_support" and calls == [PLACE]


def test_a_clear_answering_hazard_never_explores(tmp_path):
    clear = Action("clear", pos=(1, 2, 3))
    outcome, calls = _act(tmp_path, ["hazard", "hazard"], clear)
    assert outcome.error == "hazard" and calls == [clear]


def test_a_goto_not_found_or_unreachable_never_explores(tmp_path):
    goto = Action("goto", pos=(1, 2, 3))
    assert _act(tmp_path, ["not_found"], goto)[1] == [goto]
    assert _act(tmp_path, ["unreachable", "unreachable"], goto)[1] == [goto, goto]


def test_an_interrupted_build_action_is_retried_in_place_as_before(tmp_path):
    outcome, calls = _act(tmp_path, ["interrupted", "interrupted"], PLACE)
    assert outcome.ok and calls == [PLACE, PLACE, PLACE]
