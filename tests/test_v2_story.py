"""The stage story: step_start and richer plan events in the trace, exploration's
episode_end, and the short chat lines for the moments that matter. Built on the
scripted natural world of test_v2_life."""

import json

from test_v2_life import (
    FakeLifeBackend, events_of, make_config, make_episode, make_llm, prior_store,
)

from schema_voyager import agent, life
from schema_voyager.agent import MAX_CHAT_CHARS, MAX_CHAT_MESSAGES, run_schemavoyager
from schema_voyager.life import LifeHooks
from schema_voyager.models import Action, Rule
from schema_voyager.schema_store import MIN_CONFIRMATIONS

STORY_FIELDS = ("status", "confirmed", "needed", "predicts")


def run_goal(tmp_path, goal, count=1, backend=None, families=None):
    backend = backend or FakeLifeBackend()
    llm = make_llm(tmp_path)
    store = prior_store(llm, *(families or (goal,)))
    episode = make_episode(backend, llm, goal, count)
    run_schemavoyager(episode, store)
    return episode, backend


def test_step_start_comes_before_its_step_with_the_story_fields(tmp_path):
    episode, _ = run_goal(tmp_path, "#planks", 4, families=("#planks", "#log"))
    events = episode.log.events
    starts, steps = events_of(episode, "step_start"), events_of(episode, "step")
    assert [e["index"] for e in starts] == [e["index"] for e in steps] == [1, 2]
    for start, step in zip(starts, steps):
        assert events.index(start) < events.index(step)
        assert start["action"] == step["action"] and start["rule"] == step["rule"]
        assert start["experimental"] is True and start["needed"] == MIN_CONFIRMATIONS
        assert start["status"] == "hypothesis" and start["confirmed"] == 0 and start["predicts"]


def test_plan_steps_carry_the_story_fields(tmp_path):
    episode, _ = run_goal(tmp_path, "#planks", 4, families=("#planks", "#log"))
    plan = events_of(episode, "plan")[0]
    assert all(all(field in step for field in STORY_FIELDS) for step in plan["steps"])
    assert plan["steps"][0]["rule"] and plan["steps"][0]["experimental"] is True
    assert all(len(step["predicts"]) <= 120 for step in plan["steps"])


def test_v1_mode_logs_neither_step_start_nor_story_fields(tmp_path):
    backend, llm = FakeLifeBackend(), make_llm(tmp_path)
    episode = make_episode(backend, llm, "#planks", 4)
    episode.natural = False
    run_schemavoyager(episode, prior_store(llm, "#planks", "#log"))
    assert not events_of(episode, "step_start")
    assert all(not any(field in step for field in STORY_FIELDS) for plan in events_of(episode, "plan") for step in plan["steps"])


def rule(kind, subject, preconditions, yields, **effects):
    key = "drops" if kind == "mine" else "gains"
    return Rule(
        id=f"{kind}_{subject}", action={"type": kind, "block" if kind == "mine" else "item": subject},
        preconditions=preconditions, effects={key: yields, **effects},
    )


def test_predicts_for_a_mine_a_craft_with_a_group_and_a_smelt():
    mine = rule("mine", "coal_ore", {"tool_any": ["wooden_pickaxe", "stone_pickaxe"]}, {"coal": 1})
    craft = rule("craft", "stick", {"consumes": {"#planks": 2}, "station": ""}, {"stick": 4})
    smelt = rule("smelt", "iron_ingot", {"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal"]}, {"iron_ingot": 1})
    assert agent._predicts(mine, Action("mine", "coal_ore", "wooden_pickaxe")) == "coal_ore with wooden_pickaxe -> 1 coal"
    assert agent._predicts(craft) == "2 planks -> 4 stick"
    assert agent._predicts(smelt) == "raw_iron + fuel in a furnace -> 1 iron_ingot"


def test_exploration_round_ends_in_success(tmp_path):
    backend = FakeLifeBackend()
    config = make_config(tmp_path, keep_going=True)
    life._Life(backend, make_llm(tmp_path), config, LifeHooks(), resume=False)._run_exploration_round()
    trace = next((config.run_dir / "episodes").glob("*explore.jsonl"))
    end = [json.loads(line) for line in trace.read_text().splitlines()][-1]
    assert end["kind"] == "episode_end" and end["success"] is True
    assert "Exploration done." in backend.said and not any(m.startswith("Goal reached") for m in backend.said)


def test_a_normal_goal_says_at_most_two_lines(tmp_path):
    _, backend = run_goal(tmp_path, "#planks", 4, families=("#planks", "#log"))
    assert backend.said[0].startswith("Goal: 4 planks, to test ")
    assert len(backend.said) <= 2


def test_a_contradiction_goal_says_mismatch_then_hypothesis_then_recovery(tmp_path):
    backend = FakeLifeBackend(coal_needs_stone=True)
    backend.inventory = {"wooden_pickaxe": 1, "stone_pickaxe": 1}
    episode, backend = run_goal(tmp_path, "coal", backend=backend, families=("coal", "coal_ore"))
    said = backend.said
    mismatch = next(i for i, m in enumerate(said) if m.startswith("MISMATCH: expected 1 coal, got nothing"))
    assert said[0].startswith("Goal: 1 coal, to test coal_ore with wooden_pickaxe")
    assert said[mismatch + 1].startswith("Hypothesis: coal_ore needs ")
    assert any(m.startswith("Recovered: got 1 coal with ") for m in said)


def test_chat_bursts_stay_within_the_limits(tmp_path):
    backend = FakeLifeBackend(coal_needs_stone=True)
    backend.inventory = {"wooden_pickaxe": 1, "stone_pickaxe": 1}
    episode, backend = run_goal(tmp_path, "coal", backend=backend, families=("coal", "coal_ore"))
    assert all(len(m) <= MAX_CHAT_CHARS for m in backend.said)
    backend.said.clear()
    agent._say(episode, ["x" * 500] * 5)
    assert len(backend.said) == MAX_CHAT_MESSAGES
