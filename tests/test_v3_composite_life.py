"""Composite goals in the life (v3.5f): a parsed list is a parent with ordered children, each run and checked on
its own, a failed child gets one replan, and the parent ends done, failed or cancelled. Whole simulator lives with
the fake LLM; "4 eggs" has no plan in the simulator, which is how a child fails."""

import json

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


def run(tmp_path, world, resume=False, hooks=None, **config):
    config.setdefault("idle_poll_s", 0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-composite-test", **config)
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    summary = run_life(world, llm, life_config, hooks, resume=resume)
    lines = (tmp_path / "life.jsonl").read_text().splitlines()
    return summary, [json.loads(line) for line in lines]


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def statuses(run_dir, *goal_ids):
    queue = GoalQueue(run_dir / "goals.jsonl")
    return [queue.get(goal_id).status for goal_id in goal_ids]


def test_two_children_run_in_order_and_the_parent_ends_done(tmp_path):
    world = ChatWorld({2: chat("goal", "4 sticks and 2 torches")})

    _, recs = run(tmp_path, world, max_goals=2)

    assert [(r["goal_id"], r["outcome"], r["parent"]) for r in recs] == [("u002", "success", "u001"), ("u003", "success", "u001")]
    queue = GoalQueue(tmp_path / "goals.jsonl")
    assert queue.get("u001").children == ("u002", "u003") and statuses(tmp_path, "u001", "u002", "u003") == ["done"] * 3
    (children,) = life_events(tmp_path, "goal_children")
    assert children["goal_id"] == "u001" and [c["id"] for c in children["children"]] == ["u002", "u003"]
    assert children["children"][0]["label"] == "4 sticks"
    assert [(e["goal_id"], e["status"]) for e in life_events(tmp_path, "goal_parent_end")] == [("u001", "done")]
    queued_line = next(line for line in world.said if line.startswith("Queued u001"))
    assert queued_line == "Queued u001: 4 sticks and 2 torches: u002 4 sticks, then u003 2 torches."
    assert world.said.index("Done: 4 sticks") < world.said.index("Done u001: 4 sticks and 2 torches.")


def test_a_failed_child_is_replanned_once_and_the_parent_still_ends_done(tmp_path):
    world = ChatWorld({2: chat("goal", "4 eggs and 4 sticks")})

    _, recs = run(tmp_path, world, max_goals=2)

    assert [(r["goal_id"], r["outcome"]) for r in recs] == [("u002", "failed"), ("u004", "success")]
    queue = GoalQueue(tmp_path / "goals.jsonl")
    assert statuses(tmp_path, "u002", "u003", "u004", "u001") == ["failed", "cancelled", "done", "done"]
    assert queue.get("u003").reason == "replaced"
    parent = queue.get("u001")
    assert parent.replanned and parent.children == ("u002", "u003", "u004")
    (replanned,) = life_events(tmp_path, "goal_replanned")
    assert (replanned["goal_id"], replanned["failed"], replanned["reason"]) == ("u001", "u002", "no plan")
    assert replanned["children"] == [{"id": "u004", "label": "4 sticks"}]
    assert [e["status"] for e in life_events(tmp_path, "goal_parent_end")] == ["done"]
    assert "Done u001: 4 eggs and 4 sticks." in world.said


def test_a_second_failure_fails_the_parent_and_cancels_the_rest(tmp_path):
    world = ChatWorld({2: chat("goal", "4 eggs and 3 eggs and 4 sticks")})

    _, recs = run(tmp_path, world, max_goals=2)

    assert [(r["goal_id"], r["outcome"]) for r in recs] == [("u002", "failed"), ("u005", "failed")]
    queue = GoalQueue(tmp_path / "goals.jsonl")
    parent = queue.get("u001")
    assert (parent.status, parent.reason) == ("failed", "3 eggs: no plan")
    assert (queue.get("u006").status, queue.get("u006").reason) == ("cancelled", "parent failed")
    assert len(life_events(tmp_path, "goal_replanned")) == 1
    assert [e["status"] for e in life_events(tmp_path, "goal_parent_end")] == ["failed"]
    assert "Couldn't finish u001: 3 eggs: no plan" in world.said


def test_a_replan_that_gives_up_or_raises_fails_the_parent(tmp_path, monkeypatch):
    def gives_up(self, *args, **kwargs):
        return []

    def raises(self, *args, **kwargs):
        raise LLMError("no network")

    for name, replacement in (("empty", gives_up), ("error", raises)):
        monkeypatch.setattr(LLM, "replan_children", replacement)
        run_dir = tmp_path / name
        run_dir.mkdir()
        world = ChatWorld({2: chat("goal", "4 eggs and 4 sticks")})

        run(run_dir, world, max_goals=1)

        queue = GoalQueue(run_dir / "goals.jsonl")
        assert (queue.get("u001").status, queue.get("u001").reason) == ("failed", "4 eggs: no plan")
        assert (queue.get("u003").status, queue.get("u003").reason) == ("cancelled", "parent failed")
        assert not life_events(run_dir, "goal_replanned")
        assert "Couldn't finish u001: 4 eggs: no plan" in world.said


def test_a_child_cancelled_while_queued_lets_the_next_one_run(tmp_path):
    world = ChatWorld({2: chat("goal", "4 sticks and 2 torches and 2 sticks"), 3: chat("cancel", "u003")})

    _, recs = run(tmp_path, world, max_goals=2)

    assert [r["goal_id"] for r in recs] == ["u002", "u004"]
    assert statuses(tmp_path, "u001", "u002", "u003", "u004") == ["done", "done", "cancelled", "done"]
    assert [e["status"] for e in life_events(tmp_path, "goal_parent_end")] == ["done"]
    assert "Done u001: 4 sticks and 2 torches and 2 sticks." in world.said


def test_children_cancelled_while_queued_cancel_the_parent_in_the_next_pass(tmp_path):
    run(tmp_path, ChatWorld(), max_goals=1)
    queue = GoalQueue(tmp_path / "goals.jsonl")
    queue.submit("4 sticks and 2 torches")
    queue.accept_children("u001", [
        GoalSpec("", "obtain", "user", "4 sticks", item="stick", count=4),
        GoalSpec("", "obtain", "user", "2 torches", item="torch", count=2),
    ])
    queue.cancel("u002")
    queue.cancel("u003")
    world = ChatWorld()

    run(tmp_path, world, resume=True, max_goals=2)

    assert queue.get("u001").status == "cancelled"
    assert [(e["goal_id"], e["status"]) for e in life_events(tmp_path, "goal_parent_end")] == [("u001", "cancelled")]
    assert "Cancelled u001: 4 sticks and 2 torches." in world.said


def test_cancelling_a_running_parent_stops_the_child_and_cancels_the_rest(tmp_path):
    world = ChatWorld({2: chat("goal", "4 sticks and 2 torches")})
    queue = GoalQueue(tmp_path / "goals.jsonl")

    def cancel_parent_on_second_step(episode, step, plan):
        if episode.actions == 1:
            queue.cancel("u001")

    _, recs = run(tmp_path, world, hooks=LifeHooks(before_step=cancel_parent_on_second_step), max_goals=1)

    assert (recs[0]["goal_id"], recs[0]["outcome"], recs[0]["interrupt"]) == ("u002", "interrupted", "cancelled")
    assert statuses(tmp_path, "u001", "u002", "u003") == ["cancelled"] * 3


def test_resume_requeues_a_running_child_and_the_parent_stays_running_until_done(tmp_path):
    run(tmp_path, ChatWorld(), max_goals=1)
    queue = GoalQueue(tmp_path / "goals.jsonl")
    queue.submit("4 sticks and 2 torches")
    queue.accept_children("u001", [
        GoalSpec("", "obtain", "user", "4 sticks", item="stick", count=4),
        GoalSpec("", "obtain", "user", "2 torches", item="torch", count=2),
    ])
    queue.mark("u002", "running", target_count=4)
    assert queue.get("u001").status == "running"

    _, recs = run(tmp_path, ChatWorld(), resume=True, max_goals=3)

    assert [(r["goal_id"], r["parent"]) for r in recs[1:]] == [("u002", "u001"), ("u003", "u001")]
    assert statuses(tmp_path, "u001", "u002", "u003") == ["done"] * 3


def test_a_build_child_holds_back_the_later_children_until_approved(tmp_path):
    world = ChatWorld({2: chat("goal", "get ready for night")})

    _, recs = run(tmp_path, world, max_goals=1)

    assert recs[0]["source"] == "curriculum"  # nothing of the parent could run
    assert statuses(tmp_path, "u001", "u002", "u003", "u004") == ["queued", "awaiting_approval", "queued", "queued"]
    assert any(line.startswith("Queued u001: get ready for night: u002 ") for line in world.said)
    assert any("yes" in line.lower() and "u002" in line for line in world.said)  # the plan line a single build gets

    approved = tmp_path / "approved"
    approved.mkdir()
    world = ChatWorld({2: chat("goal", "get ready for night"), 3: chat("yes", "u002")})

    _, recs = run(approved, world, max_goals=5)

    user_recs = [r for r in recs if r["source"] == "user"]  # a curriculum goal may run before the approval
    assert [(r["goal_id"], r["goal_kind"]) for r in user_recs] == [("u002", "build"), ("u003", "obtain"), ("u004", "obtain")]
    assert statuses(approved, "u001", "u002", "u003", "u004") == ["done"] * 4
