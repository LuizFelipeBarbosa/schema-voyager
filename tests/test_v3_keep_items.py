"""Learn mode keeps what it already holds: no second crafting table, furnace or tool just to test a rule."""

import json
from pathlib import Path

import pytest

from schema_voyager.agent import seed_prior_rules
from schema_voyager.curriculum import Curriculum, is_kept_item
from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM
from schema_voyager.schema_store import SchemaStore

KEPT = ["crafting_table", "furnace", "chest", "bucket", "shears",
        "wooden_pickaxe", "stone_axe", "iron_shovel", "iron_sword"]
NOT_KEPT = ["stick", "#planks", "iron_ingot", "torch", "ladder", "coal"]


@pytest.mark.parametrize("item", KEPT)
def test_kept_items(item):
    assert is_kept_item(item)


@pytest.mark.parametrize("item", NOT_KEPT)
def test_consumables_and_materials_are_not_kept(item):
    assert not is_kept_item(item)


@pytest.mark.parametrize("item", ["crafting_table", "wooden_pickaxe"])  # the fake priors have no furnace rule
def test_next_goal_skips_a_kept_item_while_one_is_held(item, tmp_path):
    curriculum = Curriculum(frontier=[item])
    store = SchemaStore()
    seed_prior_rules(store, LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl"))
    plentiful = {"oak_planks": 20, "cobblestone": 20, "oak_log": 5, "stick": 5, "coal": 5}
    assert curriculum.next_goal(plentiful, store, "w") is not None  # craftable, so only the skip can hide it
    assert curriculum.next_goal({**plentiful, item: 1}, store, "w") is None
    assert curriculum.skipped == {} and curriculum.failures == {}


def test_a_fifteen_goal_life_crafts_at_most_one_table_and_one_furnace(tmp_path: Path):
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim1", max_goals=15)
    run_life(NaturalWorld(seed=1), LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl"), config)
    crafted = []
    for trace in sorted((tmp_path / "episodes").glob("*.jsonl")):
        for line in trace.read_text().splitlines():
            event = json.loads(line)
            if event["kind"] == "step" and event["ok"] and event["action_obj"]["kind"] == "craft":
                crafted.append(event["action_obj"]["target"])
    assert crafted.count("crafting_table") <= 1
    assert crafted.count("furnace") <= 1
