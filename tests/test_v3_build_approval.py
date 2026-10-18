"""Build goals up to approval (v3.4b): parse_goal designs a blueprint, accept waits for approval, chat says !yes or !no."""

import json

import pytest

from schema_voyager import llm as llm_module
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue, goal_label
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import FAKE_HUT, LLM, BlueprintDesignError, vanilla_items
from schema_voyager.models import GoalSpec, Unsupported


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


def chat(command, args=""):
    return {"type": "chat_command", "phase": "step", "player": "Otopo", "command": command, "args": args}


def run(tmp_path, world, **config):
    config.setdefault("idle_poll_s", 0)
    config.setdefault("mode", "play")
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-approval-test", **config)
    return run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), life_config)


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def entry(run_dir, goal_id):
    return GoalQueue(run_dir / "goals.jsonl").get(goal_id)


def fake_llm(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")


def hut_spec(goal_id="u001"):
    return GoalSpec(id=goal_id, kind="build", source="user", text="build a small house", blueprint=dict(FAKE_HUT))


def test_fake_parse_of_a_build_designs_the_hut(tmp_path):
    spec = fake_llm(tmp_path).parse_goal("build a small house", {}, vanilla_items(), goal_id="u001")

    assert spec == GoalSpec(id="u001", kind="build", source="user", text="build a small house", blueprint=FAKE_HUT)
    assert (spec.item, spec.count, spec.target) == (None, 1, None)


def test_a_design_that_never_validates_is_unsupported(tmp_path, monkeypatch):
    def failing(self, text):
        raise BlueprintDesignError(["no door"])

    monkeypatch.setattr(LLM, "design_blueprint", failing)

    result = fake_llm(tmp_path).parse_goal("build a small house", {}, vanilla_items(), goal_id="u001")

    assert result == Unsupported("I couldn't design that. Try \"build a small wooden house with a door\".")


def test_an_llm_error_from_the_design_propagates(tmp_path, monkeypatch):
    def failing(self, text):
        raise llm_module.LLMError("boom")

    monkeypatch.setattr(LLM, "design_blueprint", failing)

    with pytest.raises(llm_module.LLMError):
        fake_llm(tmp_path).parse_goal("build a small house", {}, vanilla_items(), goal_id="u001")


def test_accept_waits_for_approval_for_a_build_and_queues_an_obtain(tmp_path):
    queue = GoalQueue(tmp_path / "goals.jsonl")
    queue.submit("build a small house")
    queue.submit("4 sticks")

    assert queue.accept(hut_spec("u001"))
    assert queue.accept(GoalSpec("u002", "obtain", "user", item="stick", count=4))

    assert queue.get("u001").status == "awaiting_approval"
    assert queue.get("u001").spec.blueprint == FAKE_HUT
    assert queue.get("u002").status == "queued"
    assert queue.next().id == "u002"
    assert queue.mark("u001", "queued") and queue.next().id == "u001"


def test_goal_label_of_a_build_is_the_blueprint_name():
    assert goal_label(hut_spec()) == "small hut"
    assert goal_label(GoalSpec("u001", "build", "user", "build a hut")) == "build a hut"


def test_a_build_goal_is_announced_and_waits_for_approval(tmp_path):
    world = ChatWorld({2: chat("goal", "build a small house")})

    run(tmp_path, world, wall_clock_s=0.3)

    assert entry(tmp_path, "u001").status == "awaiting_approval"
    parsed = life_events(tmp_path, "goal_parsed")[0]
    assert (parsed["goal_id"], parsed["goal_kind"], parsed["name"]) == ("u001", "build", "small hut")
    plan = next(s for s in world.said if s.startswith("Plan for u001"))
    assert plan.startswith("Plan for u001: small hut, 5 x 4 x 5: ")
    assert plan.endswith("Approve it in the panel, or say !yes or !no.")
    assert len(plan) <= 240 and "#" not in plan


def test_yes_approves_and_the_unwired_runner_fails_the_goal_cleanly(tmp_path):
    world = ChatWorld({2: chat("goal", "build a small house"), 6: chat("yes")})

    summary = run(tmp_path, world, wall_clock_s=0.5)

    assert "Approved u001: small hut." in world.said
    failed = entry(tmp_path, "u001")
    assert (failed.status, failed.reason) == ("failed", "building isn't wired yet")
    assert "Couldn't build small hut: building isn't wired yet" in world.said
    assert summary["stop_reason"] == "wall_clock"


def test_no_discards(tmp_path):
    world = ChatWorld({2: chat("goal", "build a small house"), 6: chat("no")})

    run(tmp_path, world, wall_clock_s=0.3)

    discarded = entry(tmp_path, "u001")
    assert (discarded.status, discarded.reason) == ("cancelled", "discarded")
    assert "Discarded u001: small hut." in world.said


def test_answers_that_miss_and_the_status_line(tmp_path):
    world = ChatWorld({
        2: chat("goal", "build a small house"),
        6: [chat("yes", "u009"), chat("no", "u009"), chat("status"), chat("help")],
        7: chat("goal", "4 sticks"),  # u002, an obtain goal that is not waiting for approval
        12: [chat("yes", "u002"), chat("no", "u001"), chat("no"), chat("yes")],
    })

    run(tmp_path, world, wall_clock_s=0.8)

    said = world.said
    assert said.count("No goal u009.") == 2
    assert "Idle. Waiting for approval: u001." in said
    assert "Commands: !goal <what>, !yes [id], !no [id], !cancel [id], !stop, !status" in said
    assert "u002 isn't waiting for approval." in said
    assert "Discarded u001: small hut." in said
    assert "Nothing to discard." in said and "Nothing to approve." in said
