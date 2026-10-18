"""The life handles the bot's deaths (v3.2d) and hunger (v3.2e), in the simulator with the fake LLM.

`DyingWorld` kills the bot right after chosen actions (`die_after`, counted over the whole run) and emits chat
commands on chosen `status()` calls, as in test_v3_life_queue.py. Call 1 is the life's start-up.
"""

import dataclasses
import json
import time
import types

import pytest

from schema_voyager import life
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, _Life, run_life
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec
from schema_voyager.planner import PlanningError


class DyingWorld(NaturalWorld):
    def __init__(self, die_after=(), commands=None):
        super().__init__(1)
        self.die_after = set(die_after)
        self.commands = commands or {}
        self.acts = 0
        self.status_calls = 0

    def act(self, action):
        outcome = super().act(action)
        self.acts += 1
        if self.acts in self.die_after:
            self.die()  # the bot's next poll sees it
        return outcome

    def status(self, since=0):
        self.status_calls += 1
        if self.status_calls in self.commands:
            self._emit({"type": "chat_command", "phase": "step", "player": "Otopo", **self.commands[self.status_calls]})
        return super().status(since)


def goal_command(args="4 sticks", call=2):
    return {call: {"command": "goal", "args": args}}


def run(tmp_path, world, resume=False, **config):
    config.setdefault("idle_poll_s", 0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-death-test", **config)
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    summary = run_life(world, llm, life_config, resume=resume)
    lines = (tmp_path / "life.jsonl").read_text().splitlines()
    return summary, [json.loads(line) for line in lines]


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def entry(run_dir, goal_id):
    return GoalQueue(run_dir / "goals.jsonl").get(goal_id)


def actions_of_first_user_goal(tmp_path_factory):
    """How many actions the first user goal takes when nobody dies."""
    path = tmp_path_factory.mktemp("count")
    world = DyingWorld(commands=goal_command())
    run(path, world, max_goals=1)
    return world.acts


def test_a_death_mid_goal_interrupts_it_and_the_life_goes_on(tmp_path):
    world = DyingWorld(die_after={1}, commands=goal_command())

    summary, recs = run(tmp_path, world, max_goals=2)

    first = recs[0]
    assert (first["goal_id"], first["outcome"], first["interrupt"]) == ("u001", "interrupted", "death")
    assert len(recs) == 2 and recs[1]["goal_id"] == "u001" and recs[1]["outcome"] == "success"  # queued again, then done
    (death,) = life_events(tmp_path, "death")
    assert (death["goal_id"], death["inventory_lost"]) == ("u001", {})
    assert death["position"] is not None
    assert entry(tmp_path, "u001").deaths == 1
    assert json.loads((tmp_path / "life_state.json").read_text())["deaths"] == [pytest.approx(time.time(), abs=60)]
    assert summary["stop_reason"] == "max_goals"


def test_a_death_in_the_last_step_of_a_goal_is_not_a_failure(tmp_path, tmp_path_factory):
    last_step = actions_of_first_user_goal(tmp_path_factory)
    world = DyingWorld(die_after={last_step}, commands=goal_command())

    _, recs = run(tmp_path, world, max_goals=1)

    assert (recs[0]["outcome"], recs[0]["interrupt"]) == ("interrupted", "death")
    assert entry(tmp_path, "u001").status == "queued"


def test_a_user_goal_goes_back_to_queued_at_its_first_death_and_fails_at_its_second(tmp_path):
    first_death = tmp_path / "first"
    run(first_death, DyingWorld(die_after={1}, commands=goal_command()), max_goals=1)
    queued = entry(first_death, "u001")
    assert (queued.status, queued.deaths) == ("queued", 1) and queued.target_count is not None

    _, recs = run(tmp_path, DyingWorld(die_after={1, 2}, commands=goal_command()), max_goals=2)

    failed = entry(tmp_path, "u001")
    assert (failed.status, failed.deaths, failed.reason) == ("failed", 2, "died twice trying")
    assert failed.target_count == queued.target_count
    assert [r["interrupt"] for r in recs] == ["death", "death"]


def test_a_system_goal_is_cancelled_by_a_death(tmp_path):
    run(tmp_path, DyingWorld(), max_goals=1)  # the life empties goals.jsonl on a fresh start; --resume keeps it
    queue = GoalQueue(tmp_path / "goals.jsonl")
    text = "hunger: 4 stick"
    goal_id = queue.submit(text, "system")
    queue.accept(GoalSpec(id=goal_id, kind="obtain", source="system", text=text, item="stick", count=4))

    _, recs = run(tmp_path, DyingWorld(die_after={1}), resume=True, max_goals=2)

    assert (recs[1]["goal_id"], recs[1]["source"], recs[1]["interrupt"]) == (goal_id, "system", "death")
    cancelled = entry(tmp_path, goal_id)
    assert (cancelled.status, cancelled.reason) == ("cancelled", "the bot died")


def test_three_deaths_in_ten_minutes_stop_the_life_and_count_after_a_resume(tmp_path):
    world = DyingWorld(die_after={1, 2, 3})

    summary, _ = run(tmp_path, world, max_goals=10)

    assert summary["stop_reason"] == "deaths"
    assert len(json.loads((tmp_path / "life_state.json").read_text())["deaths"]) == 3
    assert len(life_events(tmp_path, "death")) == 3

    resumed, _ = run(tmp_path, DyingWorld(), resume=True, max_goals=10)
    assert resumed["stop_reason"] == "deaths"  # the three deaths before the resume still count


def test_two_deaths_before_a_resume_plus_one_after_stop_the_life(tmp_path):
    run(tmp_path, DyingWorld(die_after={1, 2}), max_goals=2)

    summary, _ = run(tmp_path, DyingWorld(die_after={1}), resume=True, max_goals=10)

    assert summary["stop_reason"] == "deaths"
    assert len(json.loads((tmp_path / "life_state.json").read_text())["deaths"]) == 3


def test_old_deaths_do_not_count(tmp_path):
    run(tmp_path, DyingWorld(), max_goals=1)
    state = json.loads((tmp_path / "life_state.json").read_text())
    state["deaths"] = [time.time() - 700] * 5
    (tmp_path / "life_state.json").write_text(json.dumps(state))

    summary, _ = run(tmp_path, DyingWorld(), resume=True, max_goals=2)

    assert summary["stop_reason"] == "max_goals"


def test_a_backend_without_status_gets_no_death_or_hunger_handling(tmp_path):
    class NoStatus:
        def __init__(self, world):
            self.world = world

        def __getattr__(self, name):
            if name == "status":
                raise AttributeError(name)
            return getattr(self.world, name)

    world = DyingWorld(die_after={1})
    world.food = 3

    summary, recs = run(tmp_path, NoStatus(world), max_goals=2)

    assert summary["stop_reason"] == "max_goals" and all("interrupt" not in r for r in recs)
    assert life_events(tmp_path, "death") == [] and life_events(tmp_path, "hungry") == []


# -- hunger ------------------------------------------------------------------


def new_life(tmp_path, world):
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-hunger-test")
    return _Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=False)


def system_goals(run_dir):
    return [g for g in GoalQueue(run_dir / "goals.jsonl").goals() if g.source == "system"]


@pytest.fixture
def only_bread_is_reachable(monkeypatch):
    def make_plan(food, count, inventory, rules, skill_for=None, visible=None):
        if food != "bread":
            raise PlanningError("no")
        return []

    monkeypatch.setattr(life, "make_plan", make_plan)


def test_hunger_without_reachable_food_logs_hungry_once_per_five_minutes(tmp_path):
    world = DyingWorld()
    world.food = 6
    hungry = new_life(tmp_path, world)

    for _ in range(3):
        hungry._poll_bot()
        hungry._queue_hunger_goal()

    assert [e["food"] for e in life_events(tmp_path, "hungry")] == [6]
    assert system_goals(tmp_path) == []
    hungry._last_hungry_log -= life.HUNGRY_LOG_EVERY_S
    hungry._queue_hunger_goal()
    assert len(life_events(tmp_path, "hungry")) == 2


def test_hunger_queues_one_system_goal_for_a_reachable_food(tmp_path, only_bread_is_reachable):
    world = DyingWorld()
    world.food = 6
    hungry = new_life(tmp_path, world)

    for _ in range(3):
        hungry._poll_bot()
        hungry._queue_hunger_goal()

    (goal,) = system_goals(tmp_path)
    assert (goal.text, goal.status, goal.spec.item, goal.spec.count) == ("hunger: 4 bread", "queued", "bread", 4)
    hungry.queue.mark(goal.id, "running")
    hungry._queue_hunger_goal()
    assert len(system_goals(tmp_path)) == 1
    assert life_events(tmp_path, "hungry") == []


def test_hunger_is_not_queued_when_food_is_fine_or_edible_food_is_held(tmp_path, only_bread_is_reachable):
    world = DyingWorld()
    hungry = new_life(tmp_path, world)
    hungry._poll_bot()
    hungry._queue_hunger_goal()  # food 20

    world.food = 6
    hungry._inventory_seen = {"apple": 1}
    hungry._poll_bot()
    hungry._queue_hunger_goal()

    assert system_goals(tmp_path) == [] and life_events(tmp_path, "hungry") == []


def test_a_banned_food_is_not_edible_food(tmp_path, only_bread_is_reachable):
    world = DyingWorld()
    world.food = 6
    hungry = new_life(tmp_path, world)
    hungry._inventory_seen = {"rotten_flesh": 3}

    hungry._poll_bot()
    hungry._queue_hunger_goal()

    assert [g.text for g in system_goals(tmp_path)] == ["hunger: 4 bread"]


def test_the_hunger_goal_runs_in_the_loop(tmp_path, only_bread_is_reachable):
    world = DyingWorld()
    world.food = 6

    _, recs = run(tmp_path, world, max_goals=1)

    assert (recs[0]["source"], recs[0]["text"]) == ("system", "hunger: 4 bread")


def test_the_junk_toss_keeps_edible_foods(tmp_path, monkeypatch):
    wanted_seen = []
    monkeypatch.setattr(life, "junk_to_toss", lambda inventory, rules, wanted, free, min_free: wanted_seen.append(wanted) or {})
    world = DyingWorld()
    crowded = life._Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), LifeConfig(tmp_path, "w"), LifeHooks(), False)
    observation = dataclasses.replace(world.observe(), free_slots=0)

    crowded._make_room("stick", observation)

    (wanted,) = wanted_seen
    assert {"stick", "bread", "cooked_beef", "apple"} <= wanted
    assert not wanted & life.AUTO_EAT_BANNED


def test_a_death_on_an_idle_pass_survives_a_resume(tmp_path):
    run(tmp_path, DyingWorld(), max_goals=1)  # leaves a store for --resume to load
    world = DyingWorld()
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-death-test")
    idle = _Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=True)
    world.die()

    idle._poll_bot()  # no goal is running

    resumed = _Life(DyingWorld(), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=True)
    assert len(resumed.deaths) == 1


def test_play_mode_idle_passes_log_hungry_once_and_never_observe(tmp_path):
    world = DyingWorld(commands=goal_command("4 sticks", call=12))
    world.food = 6
    observed = []
    real_observe = world.observe
    world.observe = lambda: observed.append(1) or real_observe()
    seen_before_goal = []
    real_status = world.status

    def status(since=0):
        if world.status_calls == 10:
            seen_before_goal.append((len(observed), len(life_events(tmp_path, "hungry"))))
        return real_status(since)

    world.status = status

    run(tmp_path, world, mode="play", max_goals=1)

    assert seen_before_goal == [(0, 1)]  # ten idle passes: no observe, one hungry event


# -- hunger retry ------------------------------------------------------------


@pytest.fixture
def apple_and_melon_slice_are_reachable(monkeypatch):
    def make_plan(food, count, inventory, rules, skill_for=None, visible=None):
        if food not in ("apple", "melon_slice"):
            raise PlanningError("no")
        return []

    monkeypatch.setattr(life, "make_plan", make_plan)


def end_hunger_goal(hungry, food, status):
    """Queue and run `hunger: 4 <food>` to its ending, as _end_goal does it: failed, done, or cancelled by a death."""
    text = f"hunger: {life.HUNGER_FOOD_COUNT} {food}"
    goal_id = hungry.queue.submit(text, "system")
    spec = GoalSpec(id=goal_id, kind="obtain", source="system", text=text, item=food, count=life.HUNGER_FOOD_COUNT)
    hungry.queue.accept(spec)
    hungry.queue.mark(goal_id, "running")
    hungry._failure_reason = lambda episode: "no apples"
    hungry._goal_record = lambda *args, **kwargs: {}
    hungry._save = lambda record: None
    result = types.SimpleNamespace(success=status == "done")
    episode = types.SimpleNamespace(blocked_rules={})
    interrupt = "death" if status == "cancelled" else None
    hungry._end_goal(spec, None, 1, 1, 0.0, episode, result, {}, interrupt)
    assert hungry.queue.get(goal_id).status == status


def hungry_life(tmp_path):
    world = DyingWorld()
    world.food = 6
    hungry = new_life(tmp_path, world)
    hungry._poll_bot()
    return hungry


def test_a_food_whose_hunger_goal_failed_is_skipped_for_the_retry_time(tmp_path, apple_and_melon_slice_are_reachable, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(life.time, "monotonic", lambda: clock[0])
    hungry = hungry_life(tmp_path)
    end_hunger_goal(hungry, "apple", "failed")

    hungry._queue_hunger_goal()

    assert [g.text for g in system_goals(tmp_path)] == ["hunger: 4 apple", "hunger: 4 melon_slice"]
    clock[0] += life.HUNGER_RETRY_S
    melon_goal_id = system_goals(tmp_path)[-1].id
    hungry.queue.mark(melon_goal_id, "running")
    hungry.queue.mark(melon_goal_id, "done")
    hungry._queue_hunger_goal()
    assert system_goals(tmp_path)[-1].text == "hunger: 4 apple"  # apple has more food points


def test_without_another_reachable_food_a_failed_food_logs_hungry_until_the_retry_time_passes(tmp_path, monkeypatch):
    def make_plan(food, count, inventory, rules, skill_for=None, visible=None):
        if food != "apple":
            raise PlanningError("no")
        return []

    monkeypatch.setattr(life, "make_plan", make_plan)
    clock = [1000.0]
    monkeypatch.setattr(life.time, "monotonic", lambda: clock[0])
    hungry = hungry_life(tmp_path)
    end_hunger_goal(hungry, "apple", "failed")

    clock[0] += life.HUNGER_RETRY_S - 1
    hungry._queue_hunger_goal()
    hungry._queue_hunger_goal()

    assert [g.text for g in system_goals(tmp_path)] == ["hunger: 4 apple"]  # only the failed one
    assert [e["food"] for e in life_events(tmp_path, "hungry")] == [6]

    clock[0] += 1
    hungry._queue_hunger_goal()

    assert [g.text for g in system_goals(tmp_path)] == ["hunger: 4 apple", "hunger: 4 apple"]


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_a_hunger_goal_that_did_not_fail_does_not_block_its_food(tmp_path, monkeypatch, status):
    def make_plan(food, count, inventory, rules, skill_for=None, visible=None):
        if food != "apple":
            raise PlanningError("no")
        return []

    monkeypatch.setattr(life, "make_plan", make_plan)
    hungry = hungry_life(tmp_path)
    end_hunger_goal(hungry, "apple", status)

    hungry._queue_hunger_goal()

    assert [g.text for g in system_goals(tmp_path)] == ["hunger: 4 apple", "hunger: 4 apple"]
