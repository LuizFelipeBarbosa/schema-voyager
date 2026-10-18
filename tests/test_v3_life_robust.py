"""The life survives a lost bot (v3.0h): it waits, requeues the goal and goes on, or stops with "bot_down"; and it
writes live.json while it runs.

`FlakyWorld` raises `BackendError` where a test says so. `chat_at` maps a `status()` call number to a chat command
emitted just before that call answers (call 1 is the life's start-up, each pass and each step polls once more).
"""

import json
import os

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, run_life
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import BackendError
from schema_voyager.models import Task
from schema_voyager.trace import EpisodeLog

BUILD_AND_APPROVE = {2: ("goal", "build a small house"), 6: ("yes", "")}


class FlakyWorld(NaturalWorld):
    def __init__(self, chat_at=None, seed=1):
        super().__init__(seed)
        self.said: list[str] = []
        self.status_calls = 0
        self.act_calls = 0
        self.chat_at = chat_at if chat_at is not None else {2: ("goal", "4 sticks")}
        self.fail_act_on: set[int] = set()  # act call numbers that raise
        self.fail_status_on: set[int] = set()
        self.fail_begin_on: set[int] = set()  # begin_episode call numbers that raise
        self.begin_calls = 0
        self.connected_answers: list[bool] = []  # one per connected() call; True once they run out
        self.on_connected = None  # () -> None, runs on each connected() call
        self.broken = False  # every call raises, connected() is False
        self.first_place_fails = False
        self.first_scan_fails = False

    def _check_up(self):
        if self.broken:
            raise BackendError("connection refused")

    def _lose_the_bot(self):
        self.broken = self.broken_after_failure
        raise BackendError("connection reset")

    def say(self, message):
        self._check_up()
        self.said.append(message)

    def status(self, since=0):
        self._check_up()
        self.status_calls += 1
        if self.status_calls in self.fail_status_on:
            raise BackendError("status timed out")
        if self.status_calls in self.chat_at:
            command, args = self.chat_at[self.status_calls]
            self._emit({"type": "chat_command", "phase": "step", "player": "Otopo", "command": command, "args": args})
        return super().status(since)

    def observe(self):
        self._check_up()
        return super().observe()

    def begin_episode(self):
        self._check_up()
        self.begin_calls += 1
        if self.begin_calls in self.fail_begin_on:
            raise BackendError("reset")
        return super().begin_episode()

    def act(self, action):
        self._check_up()
        self.act_calls += 1
        if self.act_calls in self.fail_act_on:
            self._lose_the_bot()
        if action.kind == "place" and self.first_place_fails:
            self.first_place_fails = False
            self._lose_the_bot()
        return super().act(action)

    broken_after_failure = False

    def scan(self, *args, **kwargs):
        self._check_up()
        if self.first_scan_fails:
            self.first_scan_fails = False
            self._lose_the_bot()
        return super().scan(*args, **kwargs)

    def connected(self):
        if self.on_connected is not None:
            self.on_connected()
        if self.broken:
            return False
        return self.connected_answers.pop(0) if self.connected_answers else True


def run(tmp_path, world, hooks=None, **config):
    config.setdefault("idle_poll_s", 0)
    config.setdefault("mode", "play")
    config.setdefault("bot_poll_s", 0)
    config.setdefault("wall_clock_s", 30.0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-robust-test", **config)
    summary = run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), life_config, hooks)
    return summary, records(tmp_path)


def records(run_dir):
    return [json.loads(line) for line in (run_dir / "life.jsonl").read_text().splitlines()]


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def statuses(run_dir, goal_id):
    lines = (run_dir / "goals.jsonl").read_text().splitlines()
    return [line["status"] for line in map(json.loads, lines) if line["id"] == goal_id]


def episode_events(run_dir, index):
    path = next((run_dir / "episodes").glob(f"D_life_g{index:03d}_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_bot_lost_mid_goal_is_waited_for_and_the_goal_runs_again(tmp_path):
    world = FlakyWorld()
    world.fail_act_on = {1}
    world.connected_answers = [False, False, True]

    summary, recs = run(tmp_path, world, max_goals=2)

    assert summary["stop_reason"] == "max_goals"
    assert [(r["goal_id"], r["outcome"], r.get("interrupt")) for r in recs] == [
        ("u001", "interrupted", "bot"), ("u001", "success", None),
    ]
    assert statuses(tmp_path, "u001")[-5:] == ["queued", "running", "queued", "running", "done"]
    [down] = life_events(tmp_path, "bot_down")
    [back] = life_events(tmp_path, "bot_back")
    assert down["goal_id"] == back["goal_id"] == "u001" and "connection reset" in down["error"] and back["down_s"] >= 0
    interrupted = [e for e in episode_events(tmp_path, 1) if e["kind"] == "goal_interrupted"]
    assert [(e["goal_id"], e["reason"]) for e in interrupted] == [("u001", "bot")]
    assert not any("Couldn't" in line for line in world.said)


def test_a_bot_that_does_not_come_back_ends_the_life_with_the_goal_queued(tmp_path):
    world = FlakyWorld()
    world.fail_act_on = {1}
    world.broken_after_failure = True

    summary, recs = run(tmp_path, world, bot_wait_s=0.05)

    assert summary["stop_reason"] == "bot_down"
    assert [(r["outcome"], r["interrupt"]) for r in recs] == [("interrupted", "bot")]
    assert statuses(tmp_path, "u001")[-1] == "queued"
    assert life_events(tmp_path, "bot_down") and not life_events(tmp_path, "bot_back")
    assert not (tmp_path / "live.json").exists()


def test_a_bot_that_fails_before_a_goal_runs_leaves_no_goal_running(tmp_path):
    world = FlakyWorld()
    world.fail_begin_on = {1}

    summary, recs = run(tmp_path, world, max_goals=1)

    assert [(r["goal_id"], r["outcome"]) for r in recs] == [("u001", "success")]
    [down] = life_events(tmp_path, "bot_down")
    assert down["goal_id"] is None and life_events(tmp_path, "bot_back")


def test_a_build_goal_that_loses_the_bot_while_it_is_prepared_is_requeued(tmp_path):
    world = FlakyWorld(BUILD_AND_APPROVE, seed=2)
    world.first_scan_fails = True
    world.broken_after_failure = True

    summary, recs = run(tmp_path, world, bot_wait_s=0.05)

    assert summary["stop_reason"] == "bot_down" and recs == []
    [down] = life_events(tmp_path, "bot_down")
    assert down["goal_id"] == "u001"
    assert statuses(tmp_path, "u001")[-2:] == ["running", "queued"]


def test_a_build_goal_that_loses_the_bot_while_it_is_prepared_runs_again(tmp_path):
    world = FlakyWorld(BUILD_AND_APPROVE, seed=2)
    world.first_scan_fails = True

    summary, recs = run(tmp_path, world, max_goals=1)

    assert [(r["goal_kind"], r["outcome"]) for r in recs] == [("build", "success")]
    assert statuses(tmp_path, "u001")[-5:] == ["queued", "running", "queued", "running", "done"]


def test_a_poll_error_on_the_idle_path_is_waited_for(tmp_path):
    world = FlakyWorld({6: ("goal", "4 sticks")})
    world.fail_status_on = {3}

    summary, recs = run(tmp_path, world, max_goals=1)

    assert [(r["goal_id"], r["outcome"]) for r in recs] == [("u001", "success")]
    [down] = life_events(tmp_path, "bot_down")
    assert down["goal_id"] is None
    [back] = life_events(tmp_path, "bot_back")
    assert back["goal_id"] is None


def test_a_goal_lost_three_times_fails(tmp_path):
    world = FlakyWorld()
    world.fail_act_on = {1, 2, 3}

    summary, recs = run(tmp_path, world, max_goals=3)

    assert [r.get("interrupt") for r in recs] == ["bot", "bot", "bot"]
    assert statuses(tmp_path, "u001")[-1] == "failed"
    entry = GoalQueue(tmp_path / "goals.jsonl").get("u001")
    assert entry.reason == "lost the bot 3 times"
    assert sum(line.startswith("Couldn't get") for line in world.said) == 1


def test_a_goal_cancelled_while_the_bot_is_down_ends_cancelled(tmp_path):
    world = FlakyWorld()
    world.fail_act_on = {1}
    world.on_connected = lambda: GoalQueue(tmp_path / "goals.jsonl").cancel("u001")

    summary, recs = run(tmp_path, world, max_goals=1)

    assert recs[0]["interrupt"] == "bot"
    assert statuses(tmp_path, "u001")[-1] == "cancelled"


def test_a_build_goal_lost_mid_build_is_queued_and_then_built(tmp_path):
    world = FlakyWorld(BUILD_AND_APPROVE, seed=2)
    world.first_place_fails = True

    summary, recs = run(tmp_path, world, max_goals=2)

    assert [(r["goal_kind"], r["outcome"], r.get("interrupt")) for r in recs] == [
        ("build", "interrupted", "bot"), ("build", "success", None),
    ]
    assert statuses(tmp_path, "u001")[-5:] == ["queued", "running", "queued", "running", "done"]


def test_live_json_exists_during_the_life_and_is_removed_after(tmp_path):
    world = FlakyWorld()
    seen = []
    live = tmp_path / "live.json"
    hooks = LifeHooks(after_goal=lambda record: seen.append(json.loads(live.read_text())))

    run(tmp_path, world, hooks, max_goals=1, mode="play")

    [during] = seen
    assert during["pid"] == os.getpid() and during["mode"] == "play"
    assert "started" in during
    assert not live.exists()


def test_live_json_is_removed_when_the_life_raises(tmp_path):
    world = FlakyWorld()

    real_status = world.status
    calls = []

    def status(since=0):
        calls.append(1)
        if len(calls) > 1:  # call 1 is the life's start-up
            raise RuntimeError("boom")
        return real_status(since)

    world.status = status

    with pytest.raises(RuntimeError):
        run(tmp_path, world)

    assert not (tmp_path / "live.json").exists()


def test_finish_works_when_observe_raises(tmp_path):
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")

    class DownBackend:
        def observe(self):
            raise BackendError("gone")

        def say(self, message):
            raise BackendError("gone")

    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=DownBackend(),
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
        deadline=None, inventory={"oak_log": 2},
    )

    result = episode.finish(success=False)

    assert result.final_inventory == {"oak_log": 2} and not result.success
