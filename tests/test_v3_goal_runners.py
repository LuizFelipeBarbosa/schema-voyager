"""Kill and guard goals run in the life (v3.3i), in the simulator with the fake LLM.

`RunnerWorld` takes chat commands on the Nth `status()` call (call 1 is the life's start-up), can script the
outcome of each kill action, and records `follow` and `stop_follow`. The runners' clock and sleep are replaced by a
fake clock that only `sleep` and scripted kills move.
"""

import json

import pytest

from schema_voyager import goal_runners
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import BackendError
from schema_voyager.models import Outcome


class RunnerWorld(NaturalWorld):
    def __init__(self, events=None, seed=2, kill_script=None, **kwargs):
        super().__init__(seed, **kwargs)
        self.said: list[str] = []
        self.status_calls = 0
        self.events = events or {}
        self.kill_script = kill_script  # one entry per kill action: {"error": ..., "events": [...]}; None: the simulator's
        self.follow_calls: list[tuple] = []
        self.stop_calls = 0
        self.follow_answer = "unset"  # what follow returns; "unset" answers the name asked for
        self.stop_error: Exception | None = None

    def say(self, message):
        self.said.append(message)

    def status(self, since=0):
        self.status_calls += 1
        new = self.events.get(self.status_calls, [])
        for event in [new] if isinstance(new, dict) else new:
            self._emit(event)
        return super().status(since)

    def act(self, action):
        if action.kind != "kill" or self.kill_script is None:
            return super().act(action)
        step = self.kill_script.pop(0) if self.kill_script else {"error": "not_found"}
        emitted = [self._emit(dict(event)) for event in step.get("events", [])]
        return Outcome(ok=step.get("error") is None, error=step.get("error"), inventory=self._inventory(), events=emitted)

    def follow(self, player, distance=3.0):
        self.follow_calls.append((player, distance))
        return super().follow(player, distance) if self.follow_answer == "unset" else self.follow_answer

    def stop_follow(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        super().stop_follow()


def chat(command, args="", player="Otopo"):
    return {"type": "chat_command", "phase": "step", "player": player, "command": command, "args": args}


def killed(mob_id, mob="cow"):
    return {"type": "fight_end", "target": mob, "id": mob_id, "outcome": "killed"}


def goal_command(text, call=2, player="Otopo"):
    return {call: chat("goal", text, player)}


@pytest.fixture
def fake_clock(monkeypatch):
    """The runners' clock, moved by `sleep` (2 s) and by `clock[0] +=` in a test. `on_sleep(n)` runs at the nth sleep."""
    state = {"now": 0.0, "sleeps": 0, "on_sleep": lambda n: None}

    def sleep(seconds):
        state["sleeps"] += 1
        state["now"] += seconds
        state["on_sleep"](state["sleeps"])

    monkeypatch.setattr(goal_runners, "_monotonic", lambda: state["now"])
    monkeypatch.setattr(goal_runners, "_sleep", sleep)
    return state


@pytest.fixture
def one_attempt_per_act(monkeypatch):
    """An act is one backend call, so a kill script has one entry per act, not per retry."""
    monkeypatch.setattr(Episode, "act", lambda self, action, annotate=None: self._act_once(action, annotate))


def run(tmp_path, world, resume=False, **config):
    config.setdefault("idle_poll_s", 0)
    config.setdefault("mode", "play")
    config.setdefault("max_goals", 1)
    config.setdefault("wall_clock_s", 600.0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-runners-test", **config)
    return run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), life_config, resume=resume)


def entry(run_dir, goal_id="u001"):
    return GoalQueue(run_dir / "goals.jsonl").get(goal_id)


def record(run_dir, index=0):
    return json.loads((run_dir / "life.jsonl").read_text().splitlines()[index])


def transitions(run_dir):
    return json.loads((run_dir / "store.json").read_text())["transitions"]


def armed_world(**kwargs):
    world = RunnerWorld(**kwargs)
    world.inventory["wooden_sword"] = 1
    return world


# -- kill ---------------------------------------------------------------------


def test_kill_two_cows_in_the_simulator_is_done_without_rule_evidence(tmp_path):
    world = RunnerWorld(goal_command("kill 2 cow"), seed=7)

    run(tmp_path, world)

    assert entry(tmp_path).status == "done"
    assert (record(tmp_path)["kills"], record(tmp_path)["goal_kind"], record(tmp_path)["outcome"]) == (2, "kill", "success")
    assert "Done: kill 2 cow" in world.said
    assert "kill" not in {t["action"]["kind"] for t in transitions(tmp_path)}


def test_a_kill_goal_for_a_mob_that_needs_a_weapon_arms_first(tmp_path):
    world = RunnerWorld(goal_command("1 wooden_sword"), seed=1)
    run(tmp_path, world)
    world.inventory["wooden_sword"] = 0  # the store knows how to make one; the bot holds none now
    world.difficulty = "easy"  # skeletons about
    GoalQueue(tmp_path / "goals.jsonl").submit("kill 1 skeleton")

    run(tmp_path, world, resume=True, max_goals=2)

    steps = [e["action_obj"] for e in episode_events(tmp_path, 2) if e["kind"] == "step"]
    assert any(a["kind"] == "craft" and a["target"] == "wooden_sword" for a in steps)
    assert [a for a in steps if a["kind"] == "kill"][0]["tool"] == "wooden_sword"
    assert entry(tmp_path, "u002").status == "done" and record(tmp_path, 1)["kills"] == 1


def test_a_kill_goal_with_no_reachable_sword_fails(tmp_path):
    world = RunnerWorld(goal_command("kill 1 skeleton"), seed=1, difficulty="easy")

    run(tmp_path, world)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "I need a sword first.")
    assert "Couldn't do kill 1 skeleton: I need a sword first." in world.said
    assert record(tmp_path)["kills"] == 0
    assert not [e for e in episode_events(tmp_path, 1) if e["kind"] == "step"]


def test_three_failed_acts_in_a_row_fail_the_goal_with_the_last_reason(tmp_path, one_attempt_per_act):
    world = RunnerWorld(goal_command("kill 2 cow"), kill_script=[{"error": "not_found"}] * 5)

    run(tmp_path, world)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "not found: cow")
    assert len([e for e in episode_events(tmp_path, 1) if e["kind"] == "step"]) == 3
    assert record(tmp_path)["kills"] == 0


def test_a_kill_inside_an_interrupted_act_counts_and_resets_the_streak(tmp_path, one_attempt_per_act):
    script = [
        {"error": "not_found"}, {"error": "not_found"},
        {"error": "interrupted", "events": [killed(11)]},  # the guard's
        {"error": "not_found"}, {"error": "not_found"},
        {"events": [killed(12)]},
    ]
    world = RunnerWorld(goal_command("kill 2 cow"), kill_script=script)

    run(tmp_path, world)

    assert entry(tmp_path).status == "done"
    assert record(tmp_path)["kills"] == 2 and not world.kill_script


def test_a_new_kill_resets_the_streak_but_only_a_kill_does(tmp_path, one_attempt_per_act):
    script = [{"events": [killed(1)]}] + [{"error": "escaped"}] * 3
    world = RunnerWorld(goal_command("kill 2 cow"), kill_script=script)

    run(tmp_path, world)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "escaped: cow")
    assert record(tmp_path)["kills"] == 1


def test_the_same_fight_end_counts_once_in_the_outcome_and_the_poll(tmp_path, one_attempt_per_act):
    # The outcome carries the event with its seq; call 4 of status() emits it again under a new seq, as a restarted bot would.
    script = [{"events": [killed(5)]}, {"events": [killed(5)]}, {"events": [killed(6)]}]
    world = RunnerWorld({**goal_command("kill 2 cow"), 4: killed(5)}, kill_script=script)

    run(tmp_path, world)

    assert entry(tmp_path).status == "done"
    assert record(tmp_path)["kills"] == 2
    assert len([e for e in episode_events(tmp_path, 1) if e["kind"] == "step"]) == 3


def test_a_kill_of_another_mob_or_a_fight_that_did_not_kill_is_not_a_kill(tmp_path, one_attempt_per_act):
    other = {"type": "fight_end", "target": "pig", "id": 3, "outcome": "killed"}
    fled = {"type": "fight_end", "target": "cow", "id": 4, "outcome": "fled"}
    world = RunnerWorld(goal_command("kill 1 cow"), kill_script=[{"events": [other, fled]}, {"events": [killed(9)]}])

    run(tmp_path, world)

    assert record(tmp_path)["kills"] == 1 and entry(tmp_path).status == "done"
    assert len([e for e in episode_events(tmp_path, 1) if e["kind"] == "step"]) == 2


def test_the_kill_clock_is_at_least_a_minute_per_kill(tmp_path, fake_clock, one_attempt_per_act):
    class SlowWorld(RunnerWorld):
        def act(self, action):
            fake_clock["now"] += 100.0
            return super().act(action)

    world = SlowWorld(goal_command("kill 5 cow"), kill_script=[{}] * 20)  # ok acts, no kill: the streak never fills

    run(tmp_path, world, goal_wall_clock_s=250.0)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "out of time")
    assert len(world.kill_script) == 20 - 3  # the clock was max(250, 300) s, 100 s per act: 3 acts


def test_cancelling_a_kill_goal_ends_it_between_acts(tmp_path, one_attempt_per_act):
    world = RunnerWorld({**goal_command("kill 3 cow"), 4: chat("stop")}, kill_script=[{"events": [killed(i)]} for i in range(1, 5)])

    run(tmp_path, world)

    assert entry(tmp_path).status == "cancelled"
    assert record(tmp_path)["interrupt"] == "cancelled" and record(tmp_path)["kills"] < 3


# -- guard --------------------------------------------------------------------


def test_a_guard_goal_follows_the_speaker_and_is_done_at_the_clock(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard me", player="Tester"))
    fight = {"type": "fight_start", "target": "zombie", "id": 31}

    def on_sleep(n):
        if n == 1:
            world._emit(fight)
            world._emit({"type": "fight_end", "target": "zombie", "id": 31, "outcome": "killed"})
    fake_clock["on_sleep"] = on_sleep

    run(tmp_path, world, goal_wall_clock_s=10.0)

    assert world.follow_calls == [("Tester", 3.0)] and world.stop_calls >= 1 and world.status().follow is None
    assert entry(tmp_path).spec.target == "Tester" and entry(tmp_path).status == "done"
    assert record(tmp_path)["guard"] == {"player": "Tester", "alive": True, "duration_s": 10.0, "fights": 1, "kills": 1}
    assert "Done: guard me" in world.said


def test_a_guard_goal_for_a_panel_goal_follows_the_nearest_owner(tmp_path, fake_clock):
    run(tmp_path, armed_world(), max_goals=1, goal_wall_clock_s=10.0, mode="learn")  # a first life, to resume
    GoalQueue(tmp_path / "goals.jsonl").submit("guard me")
    world = armed_world()

    run(tmp_path, world, resume=True, max_goals=2, goal_wall_clock_s=10.0)

    assert entry(tmp_path).spec.target is None
    assert world.follow_calls == [(None, 3.0)]
    assert record(tmp_path, 1)["guard"]["player"] == "Otopo"


@pytest.mark.parametrize(
    ("reason", "said", "alive"), [("died", "Tester died.", False), ("gone", "I lost sight of Tester.", True)], ids=["died", "gone"]
)
def test_a_follow_end_that_died_or_gone_fails_the_guard_goal(tmp_path, fake_clock, reason, said, alive):
    world = armed_world(events=goal_command("guard Tester"))
    fake_clock["on_sleep"] = lambda n: world._emit({"type": "follow_end", "player": "Tester", "reason": reason}) if n == 2 else None

    run(tmp_path, world, goal_wall_clock_s=60.0)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", said)
    assert record(tmp_path)["guard"]["alive"] is alive and record(tmp_path)["guard"]["duration_s"] == 4.0
    assert world.stop_calls >= 1


def test_a_stopped_follow_end_does_not_end_the_goal(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard Tester"))
    fake_clock["on_sleep"] = lambda n: world._emit({"type": "follow_end", "player": "Tester", "reason": "stopped"}) if n == 1 else None

    run(tmp_path, world, goal_wall_clock_s=6.0)

    assert entry(tmp_path).status == "done"


def test_a_guard_goal_that_cannot_see_the_player_fails(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard Tester"))
    world.follow_answer = None

    run(tmp_path, world)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "I can't see Tester.")
    assert fake_clock["sleeps"] == 0


def test_a_guard_goal_for_nobody_in_view_says_a_player(tmp_path, fake_clock):
    run(tmp_path, armed_world(), mode="learn", goal_wall_clock_s=10.0)
    GoalQueue(tmp_path / "goals.jsonl").submit("guard me")
    world = armed_world()
    world.follow_answer = None

    run(tmp_path, world, resume=True, max_goals=2)

    assert entry(tmp_path).reason == "I can't see a player."


def test_stop_cancels_a_guard_goal_and_keeps_its_stats(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard me"))
    fake_clock["on_sleep"] = lambda n: world._emit(chat("stop")) if n == 2 else None

    run(tmp_path, world, goal_wall_clock_s=60.0)

    assert entry(tmp_path).status == "cancelled" and record(tmp_path)["interrupt"] == "cancelled"
    assert record(tmp_path)["guard"] == {"player": "Otopo", "alive": True, "duration_s": 4.0, "fights": 0, "kills": 0}
    assert world.stop_calls >= 1


def test_following_stops_even_when_the_loop_raises(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard me"))

    def on_sleep(n):
        raise RuntimeError("boom")
    fake_clock["on_sleep"] = on_sleep

    with pytest.raises(RuntimeError, match="boom"):
        run(tmp_path, world, goal_wall_clock_s=60.0)

    assert world.stop_calls >= 1


def test_a_failing_stop_follow_does_not_mask_the_error(tmp_path, fake_clock):
    world = armed_world(events=goal_command("guard me"))

    def on_sleep(n):
        world.stop_error = BackendError("bot down")  # from now on stop_follow raises
        raise RuntimeError("boom")
    fake_clock["on_sleep"] = on_sleep

    with pytest.raises(RuntimeError, match="boom"):
        run(tmp_path, world, goal_wall_clock_s=60.0)

    assert world.stop_calls == 2  # the life's start, and the guard's finally


def test_a_guard_goal_with_no_reachable_sword_fails(tmp_path, fake_clock):
    world = RunnerWorld(goal_command("guard Tester"))

    run(tmp_path, world)

    failed = entry(tmp_path)
    assert (failed.status, failed.reason) == ("failed", "I need a sword first.")
    assert world.follow_calls == []


def test_the_life_stops_following_when_it_starts(tmp_path):
    world = RunnerWorld()
    world.follow("Otopo")

    run(tmp_path, world, max_goals=0)

    assert world.stop_calls == 1 and world.status().follow is None


def test_a_bot_that_is_down_at_the_start_does_not_crash_the_life(tmp_path):
    world = RunnerWorld()
    world.stop_error = BackendError("down")

    summary = run(tmp_path, world, max_goals=0)

    assert summary["stop_reason"] == "max_goals"


def episode_events(run_dir, index):
    path = next((run_dir / "episodes").glob(f"D_life_g{index:03d}_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]
