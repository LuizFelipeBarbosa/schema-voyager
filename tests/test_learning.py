import json

import pytest

from schema_voyager.agent import seed_prior_rules
from schema_voyager.baseline import SkillLibrary
from schema_voyager.demo import Runner
from schema_voyager.environment import SimulatedWorld
from schema_voyager.llm import LLM
from schema_voyager.models import Action
from schema_voyager.schema_store import SchemaStore
from schema_voyager.tasks import get_task, training_tasks


class RecordingWorld(SimulatedWorld):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.actions = []

    def say(self, message):
        self.messages.append(message)

    def act(self, action: Action):
        self.actions.append(action)
        return super().act(action)


def _runner(tmp_path):
    world = RecordingWorld()
    runner = Runner(world, LLM(mode="fake"), tmp_path)
    store = SchemaStore()
    seed_prior_rules(store, runner.llm)
    return runner, store, world


def test_training_learning_messages_are_certified_and_bounded(tmp_path):
    runner, store, world = _runner(tmp_path)
    runner.run("D", get_task("t1"), 1, store=store)
    assert not any(message.startswith("Certified:") for message in world.messages)

    runner.run("D", get_task("t1"), 2, store=store)
    assert any("Certified:" in message and "craft_stick" in message and "2 episodes" in message
               for message in world.messages)
    assert any(message.startswith("Learned skill_wooden_pickaxe") for message in world.messages)
    assert all(len(message) <= 240 for message in world.messages)
    assert len([message for message in world.messages if message.startswith(("Certified:", "Learned ", "Kept "))]) <= 3


def test_holdout_learning_mentions_revision_and_skill_dependency(tmp_path):
    runner, store, world = _runner(tmp_path)
    for task in training_tasks():
        for seed in (1, 2):
            runner.run("D", task, seed, store=store)
    assert any("Learned skill_torch" in message and "mine_coal_ore" in message for message in world.messages)

    world.messages.clear()
    runner.run("D", get_task("h3"), 6, store=store)
    kept = [message for message in world.messages if message.startswith("Kept for this world:")]
    assert kept and "mine_coal_ore.rev1" in kept[0] and "@" not in kept[0]
    h3_chat = " ".join(world.messages)
    assert "rev3" not in h3_chat
    for rule in store.rules.values():
        _, contradicted = store.replay(rule)
        if rule.source == "revision" and contradicted:
            assert rule.id.split("@", 1)[0] not in h3_chat


def test_holdout_revisit_reuses_revision_without_llm(tmp_path):
    runner, store, world = _runner(tmp_path)
    for task in training_tasks():
        for seed in (1, 2):
            runner.run("D", task, seed, store=store)
    first = runner.run("D", get_task("h3"), 6, store=store)
    world.messages.clear()
    second = runner.run("D", get_task("h3"), 6, store=store, world_id=first.world_id, label="visit2")

    assert second.success and second.contradictions == 0 and second.llm_calls == 0
    coal = [action for action in world.actions if action.kind == "mine" and action.target == "coal_ore"]
    assert coal[-1].tool == "stone_pickaxe"
    assert any("Certified: mine_coal_ore.rev1 (2 episodes)" in message for message in world.messages)
    assert (tmp_path / "episodes" / "D_h3_L6_visit2.jsonl").exists()
    results = json.loads((tmp_path / "results.json").read_text())
    assert {result["episode_id"] for result in results if result["task_id"] == "h3"} == {
        "D/h3/L6", "D/h3/L6/visit2"
    }


def test_world_id_is_rejected_for_training(tmp_path):
    runner, store, _ = _runner(tmp_path)
    with pytest.raises(ValueError):
        runner.run("D", get_task("t1"), 1, store=store, world_id="w123")


def test_every_action_and_decision_goes_to_chat(tmp_path):
    runner, store, world = _runner(tmp_path)
    for task in training_tasks():
        for seed in (1, 2):
            runner.run("D", task, seed, store=store)
    world.messages.clear()
    result = runner.run("D", get_task("h3"), 6, store=store)

    chat = world.messages
    assert all(0 < len(message) <= 240 for message in chat)
    assert len([message for message in chat if message.startswith("#")]) == result.actions
    assert any(message.startswith("#10 mine coal_ore with wooden_pickaxe") and "MISMATCH" in message for message in chat)
    assert any(message.startswith("contradiction: ") for message in chat)
    assert any(message.startswith("LLM revision") for message in chat)
    assert any(message.startswith("1. mine coal_ore with") for message in chat)
    assert len([message for message in chat if message.startswith("plan (")]) == 2
    assert chat[-2].startswith("SUCCESS") and chat[-1].startswith("Kept for this world:")


def test_the_baselines_plans_and_feedback_go_to_chat(tmp_path):
    world = RecordingWorld()
    runner = Runner(world, LLM(mode="fake"), tmp_path)
    runner.run("B+", get_task("h3"), 6, library=SkillLibrary())
    assert any(message.startswith("LLM plan attempt 1") for message in world.messages)
    assert any(message.startswith("feedback: ") for message in world.messages)
    assert all(len(message) <= 240 for message in world.messages)
