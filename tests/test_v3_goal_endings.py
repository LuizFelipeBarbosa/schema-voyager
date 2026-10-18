"""How a user goal ends (v3.1d): the wall-clock requeue, the per-goal clock and the order of failure reasons,
in the simulator with the fake LLM. Time is a fake monotonic clock that only moves when a test moves it."""

import time as real_time
from types import SimpleNamespace

import pytest

from schema_voyager import episode, life
from schema_voyager.life import GOAL_SECONDS_PER_PLAN_STEP, LifeHooks, _Life
from test_v3_life_queue import ChatWorld, chat, entry, run


class FakeClock:
    """Stands in for the `time` module in life and episode: monotonic() is frozen until `now` is moved."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(life, "time", fake)
    monkeypatch.setattr(episode, "time", fake)
    return fake


def _fake_episode(steps, *, out_of_time=False, budget_left=10, plan_failed=False):
    """What `_failure_reason` reads: `steps` are (error, target) pairs."""
    events = [{"kind": "step", "error": error, "action_obj": {"target": target}} for error, target in steps]
    events += [{"kind": "plan_failed"}] if plan_failed else []
    events.append({"kind": "episode_end"})
    return SimpleNamespace(log=SimpleNamespace(events=events), out_of_time=out_of_time, budget_left=budget_left)


def test_a_user_goal_cut_short_by_the_life_clock_goes_back_to_queued(tmp_path, clock):
    world = ChatWorld({2: chat("goal", "4 torches")})

    def forty_seconds_pass(episode, step, plan):
        clock.now += 40

    summary, recs = run(
        tmp_path, world, hooks=LifeHooks(before_step=forty_seconds_pass), wall_clock_s=100, goal_wall_clock_s=300
    )

    assert (recs[0]["goal_id"], recs[0]["outcome"], recs[0]["interrupt"]) == ("u001", "interrupted", "wall_clock")
    requeued = entry(tmp_path, "u001")
    assert requeued.status == "queued" and requeued.target_count is not None
    assert not any(line.startswith("Couldn't get") for line in world.said)
    assert summary["stop_reason"] == "wall_clock" and len(recs) == 1


def test_a_user_goal_gets_thirty_seconds_per_planned_step_and_a_curriculum_goal_does_not(tmp_path, clock):
    world = ChatWorld({2: chat("goal", "4 torches")})
    time_left = {}

    def note_time_left(episode, step, plan):
        time_left.setdefault(episode.task.task_id, episode.deadline - clock.now)

    _, recs = run(
        tmp_path, world, hooks=LifeHooks(before_step=note_time_left), max_goals=2, wall_clock_s=10_000, goal_wall_clock_s=60
    )

    user, curriculum = recs
    assert user["source"] == "user" and curriculum["source"] == "curriculum"
    assert user["plan_length"] > 60 / GOAL_SECONDS_PER_PLAN_STEP
    assert time_left["g001"] == GOAL_SECONDS_PER_PLAN_STEP * user["plan_length"]
    assert time_left["g002"] == 60


def test_the_life_clock_caps_a_user_goals_clock(tmp_path, clock):
    world = ChatWorld({2: chat("goal", "4 torches")})
    time_left = []

    def note_time_left(episode, step, plan):
        time_left.append(episode.deadline - clock.now)

    run(tmp_path, world, hooks=LifeHooks(before_step=note_time_left), max_goals=1, wall_clock_s=100, goal_wall_clock_s=60)

    assert time_left[0] == 100


def test_a_goal_that_runs_out_of_time_fails_with_out_of_time(tmp_path, clock):
    world = ChatWorld({2: chat("goal", "4 torches")})

    def a_long_time_passes(episode, step, plan):
        clock.now += 1000

    _, recs = run(
        tmp_path, world, hooks=LifeHooks(before_step=a_long_time_passes), max_goals=1, wall_clock_s=100_000, goal_wall_clock_s=60
    )

    assert recs[0]["outcome"] == "failed" and "interrupt" not in recs[0]
    failed = entry(tmp_path, "u001")
    assert (failed.status, failed.reason) == ("failed", "out of time")
    assert "Couldn't get 4 torches: out of time" in world.said


@pytest.mark.parametrize(
    "episode_args, steps, reason",
    [
        pytest.param({"plan_failed": True, "out_of_time": True, "budget_left": 0}, [("not_found", "#logs")], "no plan", id="no plan beats all"),
        pytest.param({"out_of_time": True, "budget_left": 0}, [("not_found", "iron_ore")], "not found: iron ore", id="missing resource beats out of time"),
        pytest.param({}, [("unreachable", "#oak_logs")], "unreachable: oak logs", id="unreachable"),
        pytest.param({}, [("hazard", "diamond_ore")], "hazard: diamond ore", id="hazard"),
        pytest.param({"out_of_time": True, "budget_left": 0}, [("missing_tool", "stone")], "out of time", id="out of time beats out of steps"),
        pytest.param({"budget_left": 0}, [("missing_tool", "stone")], "out of steps", id="out of steps"),
        pytest.param({}, [("missing_tool", "stone"), (None, "log")], "missing tool: stone", id="the last failed step"),
        pytest.param({}, [(None, "log")], "failed", id="no failed step"),
        pytest.param({}, [], "failed", id="no step at all"),
        pytest.param({}, [("missing_tool", "x" * 200)], "missing tool: " + "x" * 66, id="cut to 80 characters"),
    ],
)
def test_the_failure_reasons_come_in_order(episode_args, steps, reason):
    result = _Life._failure_reason(_fake_episode(steps, **episode_args))

    assert result == reason
    assert len(result) <= 80
