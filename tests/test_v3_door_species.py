"""A build that needs a door ensures 6 planks of one species first (docs/v3-interfaces.md v3.0h)."""

import time

import pytest

from schema_voyager.build_runner import BUILD_GOAL, prepare_build, run_build
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec, Task
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog

SEED = 7
WORLD_ID = "w"
FAMILIES = ["#door", "#planks", "#log", "stick", "torch", "dirt", "coal", "coal_ore", "crafting_table", "wooden_pickaxe",
            "grass_block"]
DOOR_ONLY = {"name": "door", "size": [1, 2, 1], "palette": {"D": "oak_door"}, "layers": [["D"], ["D"]]}
SPEC = GoalSpec(id="u001", kind="build", source="user", text="a door", blueprint=DOOR_ONLY)


@pytest.fixture
def llm(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")


def _build_with(llm, tmp_path, **inventory):
    """Run the door-only build with these items held (plus a crafting table and scaffold); the episode and outcome."""
    world = NaturalWorld(SEED)
    store = SchemaStore()
    for rule in llm.propose_prior_rules_v2(FAMILIES, [], WORLD_ID):
        store.add_rule(rule)
    plan = prepare_build(world, store, SPEC, origin=None, protected=[], world_id=WORLD_ID, excluded=frozenset())
    assert not isinstance(plan, str), plan
    world.inventory.update(crafting_table=1, dirt=16, **inventory)
    observation = world.observe()
    task = Task("g001", "train", BUILD_GOAL, 1, dict(observation.inventory), (), plan.budget, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id=WORLD_ID, backend=world,
        llm=llm, log=EpisodeLog(None, "test", say=world.say), inventory=dict(observation.inventory), natural=True,
        deadline=time.monotonic() + 3000,
    )
    outcome = run_build(episode, store, plan, before_step=lambda *args: None, check_interrupts=lambda: None,
                        excluded=frozenset())
    return episode, outcome


def _crafts(episode):
    return [event["action_obj"]["target"] for event in episode.log.events
            if event["kind"] == "step" and event["action_obj"]["kind"] == "craft"]


def _gathered_logs(episode):
    return [event for event in episode.log.events
            if event["kind"] == "step" and event["action_obj"]["kind"] == "mine"]


def test_enough_logs_of_one_species_craft_its_planks_first(llm, tmp_path):
    episode, outcome = _build_with(llm, tmp_path, oak_planks=3, spruce_planks=3, oak_log=2)

    assert outcome.result.success, outcome.reason
    assert _crafts(episode)[0] == "oak_planks" and len(_crafts(episode)) == 2
    assert not episode.blocked_rules


def test_no_species_reaching_six_gathers_logs_then_crafts_planks(llm, tmp_path):
    episode, outcome = _build_with(llm, tmp_path, oak_planks=3, spruce_planks=3, birch_log=1)

    assert outcome.result.success, outcome.reason
    assert _gathered_logs(episode)
    crafts = _crafts(episode)
    assert len(crafts) == 2 and crafts[0].endswith("_planks") and "door" in crafts[1]
    assert not episode.blocked_rules


def test_six_planks_of_one_species_need_no_planks_craft(llm, tmp_path):
    episode, outcome = _build_with(llm, tmp_path, spruce_planks=6, oak_planks=3)

    assert outcome.result.success, outcome.reason
    assert [c for c in _crafts(episode) if c.endswith("_planks")] == []


def test_a_door_already_held_crafts_nothing(llm, tmp_path):
    episode, outcome = _build_with(llm, tmp_path, oak_planks=3, spruce_planks=3, oak_log=2, spruce_door=1)

    assert outcome.result.success, outcome.reason
    assert _crafts(episode) == []
