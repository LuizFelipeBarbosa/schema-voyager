"""The life queues an arming goal when a threat finds the bot unarmed (v3.2e), in the simulator with the fake LLM."""

import json
import types

import pytest

from schema_voyager import life
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, _Life, run_life
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec
from schema_voyager.planner import PlanningError


class ThreatenedWorld(NaturalWorld):
    """Emits a `threat` event on the chosen `status()` calls. Call 1 is the life's start-up."""

    def __init__(self, threat_on=()):
        super().__init__(1)
        self.threat_on = set(threat_on)
        self.status_calls = 0

    def status(self, since=0):
        self.status_calls += 1
        if self.status_calls in self.threat_on:
            self._emit({"type": "threat", "phase": "step", "mob": "zombie"})
        return super().status(since)

    def threaten(self):
        self._emit({"type": "threat", "phase": "step", "mob": "zombie"})


def new_life(tmp_path, world=None):
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-arm-test")
    return _Life(world or ThreatenedWorld(), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=False)


def system_goals(run_dir):
    return [g for g in GoalQueue(run_dir / "goals.jsonl").goals() if g.source == "system"]


def reachable(monkeypatch, *items):
    def make_plan(item, count, inventory, rules, skill_for=None, visible=None):
        if item not in items:
            raise PlanningError("no")
        return []

    monkeypatch.setattr(life, "make_plan", make_plan)


def threatened(life_, world):
    world.threaten()
    life_._poll_bot()


def test_a_threat_while_unarmed_queues_the_best_reachable_sword(tmp_path, monkeypatch):
    reachable(monkeypatch, "stone_sword", "wooden_sword")
    world = ThreatenedWorld()
    unarmed = new_life(tmp_path, world)

    threatened(unarmed, world)

    (goal,) = system_goals(tmp_path)
    assert (goal.text, goal.status, goal.spec.item, goal.spec.count) == ("arm: 1 stone_sword", "queued", "stone_sword", 1)


@pytest.mark.parametrize("held", ["stone_sword", "iron_axe"])
def test_a_threat_while_armed_queues_nothing(tmp_path, monkeypatch, held):
    reachable(monkeypatch, "iron_sword", "stone_sword", "wooden_sword")
    world = ThreatenedWorld()
    armed = new_life(tmp_path, world)
    armed._inventory_seen = {held: 1}

    threatened(armed, world)

    assert system_goals(tmp_path) == []


def test_no_reachable_sword_queues_nothing(tmp_path, monkeypatch):
    reachable(monkeypatch)
    world = ThreatenedWorld()
    unarmed = new_life(tmp_path, world)

    threatened(unarmed, world)

    assert system_goals(tmp_path) == []


def test_no_event_without_a_threat_queues_nothing(tmp_path, monkeypatch):
    reachable(monkeypatch, "wooden_sword")
    unarmed = new_life(tmp_path)

    unarmed._poll_bot()

    assert system_goals(tmp_path) == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_second_threat_while_the_arming_goal_is_open_queues_nothing(tmp_path, monkeypatch, status):
    reachable(monkeypatch, "wooden_sword")
    world = ThreatenedWorld()
    unarmed = new_life(tmp_path, world)
    threatened(unarmed, world)
    (goal,) = system_goals(tmp_path)
    unarmed.queue.mark(goal.id, status)

    threatened(unarmed, world)

    assert len(system_goals(tmp_path)) == 1


def end_arming_goal(armed, sword, status):
    """Queue and run `arm: 1 <sword>` to its ending, as _end_goal does it."""
    text = f"arm: 1 {sword}"
    goal_id = armed.queue.submit(text, "system")
    spec = GoalSpec(id=goal_id, kind="obtain", source="system", text=text, item=sword, count=1)
    armed.queue.accept(spec)
    armed.queue.mark(goal_id, "running")
    armed._failure_reason = lambda episode: "no wood"
    armed._goal_record = lambda *args, **kwargs: {}
    armed._save = lambda record: None
    result = types.SimpleNamespace(success=status == "done")
    episode = types.SimpleNamespace(blocked_rules={})
    armed._end_goal(spec, None, 1, 1, 0.0, episode, result, {}, None)
    assert armed.queue.get(goal_id).status == status


def finish_latest_goal(unarmed, run_dir):
    goal_id = system_goals(run_dir)[-1].id
    unarmed.queue.mark(goal_id, "running")
    unarmed.queue.mark(goal_id, "done")


def test_a_sword_whose_arming_goal_failed_is_skipped_for_the_retry_time(tmp_path, monkeypatch):
    reachable(monkeypatch, "stone_sword", "wooden_sword")
    clock = [1000.0]
    monkeypatch.setattr(life.time, "monotonic", lambda: clock[0])
    world = ThreatenedWorld()
    unarmed = new_life(tmp_path, world)
    end_arming_goal(unarmed, "stone_sword", "failed")

    threatened(unarmed, world)
    assert system_goals(tmp_path)[-1].text == "arm: 1 wooden_sword"  # stone_sword is skipped
    finish_latest_goal(unarmed, tmp_path)

    clock[0] += life.ARM_RETRY_S - 1
    threatened(unarmed, world)
    assert system_goals(tmp_path)[-1].text == "arm: 1 wooden_sword"
    finish_latest_goal(unarmed, tmp_path)

    clock[0] += 1
    threatened(unarmed, world)
    assert system_goals(tmp_path)[-1].text == "arm: 1 stone_sword"


def test_a_threat_mid_goal_preempts_the_running_curriculum_goal(tmp_path, monkeypatch):
    reachable(monkeypatch, "wooden_sword")
    world = ThreatenedWorld(threat_on={3})
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-arm-test", max_goals=2, idle_poll_s=0)
    run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config)

    recs = [json.loads(line) for line in (tmp_path / "life.jsonl").read_text().splitlines()]
    assert (recs[0]["source"], recs[0]["outcome"], recs[0]["interrupt"]) == ("curriculum", "interrupted", "preempted")
    assert (recs[1]["source"], recs[1]["text"]) == ("system", "arm: 1 wooden_sword")
    assert len(system_goals(tmp_path)) == 1
