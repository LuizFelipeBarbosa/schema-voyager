"""The life takes goals from the queue (v3.1d, v3.1g, v3.1h), in the simulator with the fake LLM.

Chat commands are injected through the world's own `_emit`, from a NaturalWorld whose `status()` emits on the
Nth call, so seqs stay consecutive. Call 1 is the life's start-up (`bot_seq`); each pass and each step after
that polls once.
"""

import json

import pytest

from schema_voyager import demo, life
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, run_life
from schema_voyager.llm import LLM, LLMError
from schema_voyager.models import GoalSpec


class ChatWorld(NaturalWorld):
    """`events` maps a `status()` call number to the event (or list of events) emitted just before that call answers."""

    def __init__(self, events=None, seed=1):
        super().__init__(seed)
        self.said: list[str] = []
        self.status_calls = 0
        self.events = events or {}

    def say(self, message):
        self.said.append(message)

    def status(self, since=0):
        self.status_calls += 1
        new = self.events.get(self.status_calls, [])
        for event in [new] if isinstance(new, dict) else new:
            self._emit(event)
        return super().status(since)


def chat(command, args="", player="Otopo"):
    return {"type": "chat_command", "phase": "step", "player": player, "command": command, "args": args}


def run(tmp_path, world, resume=False, hooks=None, llm=None, **config):
    config.setdefault("idle_poll_s", 0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-queue-test", **config)
    llm = llm or LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    summary = run_life(world, llm, life_config, hooks, resume=resume)
    return summary, records(tmp_path)


def records(run_dir):
    return [json.loads(line) for line in (run_dir / "life.jsonl").read_text().splitlines()]


def events_of(run_dir, record):
    path = next((run_dir / "episodes").glob(f"D_life_g{record['index']:03d}_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def entry(run_dir, goal_id):
    return GoalQueue(run_dir / "goals.jsonl").get(goal_id)


def test_a_queued_goal_runs_before_the_curriculum(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches")})
    held = world.inventory["torch"]

    summary, recs = run(tmp_path, world, max_goals=2)

    first = recs[0]
    assert (first["goal_id"], first["source"], first["goal_kind"], first["text"]) == ("u001", "user", "obtain", "4 torches")
    assert first["outcome"] == "success" and first["goal"] == "torch" and first["count"] == held + 4
    assert recs[1]["source"] == "curriculum" and recs[1]["goal_id"] == "g002"
    done = entry(tmp_path, "u001")
    assert (done.status, done.target_count) == ("done", held + 4)
    goal_event = events_of(tmp_path, first)[0]
    assert goal_event["kind"] == "goal" and goal_event["asked"] == 4 and goal_event["count"] == held + 4
    assert "Queued u001: 4 torches" in world.said and "Done: 4 torches" in world.said
    assert [e["goal_id"] for e in life_events(tmp_path, "goal_parsed")] == ["u001"]
    assert life_events(tmp_path, "chat_command")[0]["args"] == "4 torches"
    assert summary["goals"] == 2


def test_a_cancel_mid_goal_stops_it_at_the_next_step(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches")})
    steps_at_cancel = []

    def cancel_on_second_step(episode, step, plan):
        if episode.actions == 1 and not steps_at_cancel:
            steps_at_cancel.append(episode.actions)
            GoalQueue(tmp_path / "goals.jsonl").cancel("u001")

    _, recs = run(tmp_path, world, hooks=LifeHooks(before_step=cancel_on_second_step), max_goals=1)

    events = events_of(tmp_path, recs[0])
    assert [e["kind"] for e in events].count("step") == 2  # the step the cancel came in, and no later one
    interrupted = next(e for e in events if e["kind"] == "goal_interrupted")
    assert (interrupted["goal_id"], interrupted["reason"]) == ("u001", "cancelled")
    assert [e["kind"] for e in events][-2:] == ["episode_end", "certified"]
    assert recs[0]["outcome"] == "interrupted" and recs[0]["interrupt"] == "cancelled"
    assert entry(tmp_path, "u001").status == "cancelled"
    assert "Cancelled: 4 torches" in world.said


def test_play_mode_idles_then_runs_a_goal_typed_later(tmp_path):
    world = ChatWorld({6: chat("goal", "4 sticks")})

    summary, recs = run(tmp_path, world, mode="play", max_goals=1)

    assert [(r["goal_id"], r["source"], r["outcome"]) for r in recs] == [("u001", "user", "success")]
    assert world.status_calls >= 6  # the idle passes polled the bot
    assert summary["stop_reason"] == "max_goals"
    assert len(list((tmp_path / "episodes").glob("*.jsonl"))) == 1  # no curriculum goal, no exploration


def test_play_mode_without_goals_stops_on_the_wall_clock_without_observing(tmp_path):
    world = ChatWorld()
    observed = []
    real_observe = world.observe
    world.observe = lambda: observed.append(1) or real_observe()

    summary, recs = run(tmp_path, world, mode="play", wall_clock_s=0.05, idle_poll_s=0.01)

    assert summary["stop_reason"] == "wall_clock" and recs == [] and observed == []


def test_resume_keeps_the_queue(tmp_path):
    run(tmp_path, ChatWorld(), max_goals=1)
    queue = GoalQueue(tmp_path / "goals.jsonl")
    queue.submit("4 sticks")
    queue.accept(GoalSpec("u001", "obtain", "user", item="stick", count=4))
    queue.mark("u001", "running", target_count=4)
    queue.submit("2 torches")  # u002: still waiting for its parse

    summary, recs = run(tmp_path, ChatWorld(), resume=True, max_goals=3)

    assert [r["goal_id"] for r in recs[1:]] == ["u001", "u002"]
    assert [entry(tmp_path, goal_id).status for goal_id in ("u001", "u002")] == ["done", "done"]
    assert entry(tmp_path, "u001").target_count == 4  # fixed by the first run, kept
    assert summary["goals"] == 3


def test_a_fresh_life_empties_the_queue_and_the_event_log(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    stale = GoalQueue(tmp_path / "goals.jsonl")
    stale.submit("4 sticks")
    (tmp_path / "life_events.jsonl").write_text('{"kind": "old"}\n')

    run(tmp_path, ChatWorld(), max_goals=1)

    assert stale.get("u001") is None
    assert life_events(tmp_path, "old") == []


def test_torches_made_for_the_user_are_not_tossed_as_junk_later(tmp_path, monkeypatch):
    world = ChatWorld({2: chat("goal", "10 torches")})
    wanted_seen = []
    real_junk_to_toss = life.junk_to_toss

    def spy(inventory, rules, wanted, *args, **kwargs):
        wanted_seen.append(set(wanted))
        return real_junk_to_toss(inventory, rules, wanted, *args, **kwargs)

    monkeypatch.setattr(life, "junk_to_toss", spy)

    def fill_the_bag_after_the_first_goal(record):
        world.inventory.update({"dirt": 33 * 64, "poppy": 3})

    _, recs = run(tmp_path, world, hooks=LifeHooks(after_goal=fill_the_bag_after_the_first_goal), max_goals=2)

    assert recs[0]["goal_id"] == "u001" and recs[0]["outcome"] == "success"
    torches = world.inventory["torch"]
    assert torches >= 10
    assert recs[1]["tossed"] and "torch" not in recs[1]["tossed"] and "dirt" in recs[1]["tossed"]
    assert all("torch" in wanted for wanted in wanted_seen[-1:])
    assert world.inventory["torch"] >= 10


def test_chat_commands_and_their_replies(tmp_path):
    world = ChatWorld(
        {
            2: chat("goal", "build a castle"),
            3: [  # one burst, while the first curriculum goal runs; u002 to u004 are cancelled before any parse
                chat("status"),
                chat("goal", "4 sticks"),
                chat("cancel", "u009"),
                chat("cancel", "u001"),
                chat("cancel", "u002"),
                chat("goal", "3 torches"),
                chat("goal", "2 sticks"),
                chat("stop"),
                chat("stop"),
                chat("dance"),
                {"type": "damage", "phase": "step", "amount": 1, "health": 19},
                chat("goal"),
                chat("cancel"),
            ],
        }
    )

    run(tmp_path, world, max_goals=1)

    said = world.said
    assert next(s for s in said if "can't build" in s)
    assert entry(tmp_path, "u001").status == "unsupported"
    assert "Learning: 1 plank." in said
    assert "No goal u009." in said
    assert "u001 is already unsupported." in said
    assert "Cancelled u002: 4 sticks" in said
    assert "Stopped: cancelled 2 goals." in said
    assert said.count("Nothing to cancel.") == 2  # the second !stop and the bare !cancel
    assert "Commands: !goal <what>, !cancel [id], !stop, !status" in said
    assert "Usage: !goal <what>, like !goal 10 torches" in said
    assert not any(line.startswith("Queued") for line in said)
    assert [entry(tmp_path, g).status for g in ("u002", "u003", "u004")] == ["cancelled"] * 3
    commands = life_events(tmp_path, "chat_command")
    assert [c["command"] for c in commands] == ["goal"] + ["status", "goal", "cancel", "cancel", "cancel", "goal", "goal", "stop", "stop", "dance", "goal", "cancel"]
    assert life_events(tmp_path, "goal_unsupported")[0]["goal_id"] == "u001"
    assert all(len(s) <= 240 and "#" not in s for s in said)


def test_status_during_a_goal_names_it_and_what_waits(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches"), 4: chat("goal", "2 sticks"), 5: chat("status")})

    run(tmp_path, world, max_goals=2)

    assert "Doing u001: 4 torches. Queued: 1." in world.said


def test_a_cancel_from_chat_says_cancelled_once(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches"), 4: chat("cancel")})

    _, recs = run(tmp_path, world, max_goals=1)

    assert recs[0]["interrupt"] == "cancelled" and entry(tmp_path, "u001").status == "cancelled"
    assert "Cancelled u001: 4 torches" in world.said
    assert "Cancelled: 4 torches" not in world.said


def test_events_from_before_the_life_are_not_handled(tmp_path):
    world = ChatWorld()
    world._emit(chat("goal", "4 sticks"))

    run(tmp_path, world, max_goals=1)

    assert GoalQueue(tmp_path / "goals.jsonl").goals() == []
    assert life_events(tmp_path, "chat_command") == []


def test_a_restarted_bot_is_polled_from_the_start(tmp_path):
    world = ChatWorld()
    world._emit({"type": "damage", "phase": "step", "amount": 1, "health": 19})
    world._emit({"type": "damage", "phase": "step", "amount": 1, "health": 19})
    real_status = world.status
    calls = []

    def status(since=0):
        calls.append(since)
        if len(calls) == 2:  # the bot restarted: its seq starts over and its first event is a command
            world._seq = 0
            world._events = []
            world._emit(chat("status"))
            world._seq = 0
        return real_status(since)

    world.status = status

    run(tmp_path, world, max_goals=1)

    assert 0 in calls[2:3] and "Idle." in world.said


def test_a_goal_typed_during_a_curriculum_goal_preempts_it(tmp_path):
    world = ChatWorld({4: chat("goal", "4 sticks")})

    summary, recs = run(tmp_path, world, max_goals=2)

    assert recs[0]["source"] == "curriculum" and recs[0]["outcome"] == "interrupted" and recs[0]["interrupt"] == "preempted"
    assert recs[1]["goal_id"] == "u001" and recs[1]["outcome"] == "success"
    interrupted = next(e for e in events_of(tmp_path, recs[0]) if e["kind"] == "goal_interrupted")
    assert interrupted["reason"] == "preempted"
    store = json.loads((tmp_path / "life_state.json").read_text())
    assert recs[0]["goal"] not in store["curriculum"]["failures"]
    assert not any(line.startswith("Pausing") for line in world.said)  # nothing is said for a curriculum goal
    assert summary["goals"] == 2


def test_a_preempted_user_goal_goes_back_to_queued_and_says_so(tmp_path, monkeypatch):
    world = ChatWorld({2: chat("goal", "4 torches")})
    queue = None

    def system_goal_arrives(episode, step, plan):
        nonlocal queue
        if queue is None:
            queue = GoalQueue(tmp_path / "goals.jsonl")
            goal_id = queue.submit("hunger: 4 bread", "system")
            queue.accept(GoalSpec(goal_id, "obtain", "system", item="stick", count=1))

    _, recs = run(tmp_path, world, hooks=LifeHooks(before_step=system_goal_arrives), max_goals=3)

    assert [(r["goal_id"], r["outcome"]) for r in recs[:3]] == [
        ("u001", "interrupted"), ("s001", "success"), ("u001", "success"),
    ]
    assert recs[0]["interrupt"] == "preempted"
    assert "Pausing 4 torches for hunger: 4 bread" in world.said
    assert entry(tmp_path, "u001").status == "done" and entry(tmp_path, "u001").target_count is not None


def test_a_goal_that_cannot_be_planned_fails_with_no_plan_and_the_life_goes_on(tmp_path):
    world = ChatWorld({2: chat("goal", "4 eggs")})

    summary, recs = run(tmp_path, world, max_goals=2)

    assert recs[0]["goal_id"] == "u001" and recs[0]["outcome"] == "failed" and recs[0]["plan_length"] == 0
    failed = entry(tmp_path, "u001")
    assert (failed.status, failed.reason) == ("failed", "no plan")
    assert "Couldn't get 4 eggs: no plan" in world.said
    assert recs[1]["source"] == "curriculum"
    state = json.loads((tmp_path / "life_state.json").read_text())
    assert "egg" not in state["curriculum"]["failures"] and "egg" not in state["curriculum"]["skipped"]
    assert summary["goals"] == 2


def test_a_parse_that_raises_leaves_the_goal_submitted(tmp_path, monkeypatch):
    def broken(self, *args, **kwargs):
        raise LLMError("no network")

    monkeypatch.setattr(LLM, "parse_goal", broken)
    world = ChatWorld({2: chat("goal", "4 sticks")})

    _, recs = run(tmp_path, world, max_goals=1)

    assert entry(tmp_path, "u001").status == "submitted"
    assert recs[0]["source"] == "curriculum"


def test_the_goal_event_and_record_of_a_curriculum_goal(tmp_path):
    _, recs = run(tmp_path, ChatWorld(), max_goals=1)

    assert (recs[0]["goal_id"], recs[0]["source"], recs[0]["goal_kind"], recs[0]["text"]) == ("g001", "curriculum", "obtain", "")
    goal_event = events_of(tmp_path, recs[0])[0]
    assert goal_event["goal_id"] == "g001" and goal_event["source"] == "curriculum" and "asked" not in goal_event
    assert "interrupt" not in recs[0]


def test_mode_is_a_life_option_that_defaults_to_learn():
    parser_args = []
    real_run = demo.run_life_command
    demo.run_life_command = parser_args.append
    try:
        demo.main(["life", "--backend", "sim", "--llm", "fake"])
        demo.main(["life", "--backend", "sim", "--llm", "fake", "--mode", "play"])
    finally:
        demo.run_life_command = real_run
    assert [args.mode for args in parser_args] == ["learn", "play"]
    with pytest.raises(SystemExit):
        demo.main(["life", "--mode", "dream"])
