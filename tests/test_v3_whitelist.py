"""Armor and a shield are learnable, kept once held, and craftable offline."""

import pytest

from schema_voyager.curriculum import WHITELIST, Curriculum, is_kept_item
from schema_voyager.environment import WHITELIST_RECIPES
from schema_voyager.inventory import junk_to_toss
from schema_voyager.llm import LLM
from schema_voyager.schema_store import SchemaStore

LEATHER_ARMOR = ["leather_helmet", "leather_chestplate", "leather_leggings", "leather_boots"]
ARMOR_AND_SHIELD = [
    "iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots", "shield",
]


def fake_priors(tmp_path, families):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl").propose_prior_rules_v2(families, [], "w")


@pytest.mark.parametrize("item", ARMOR_AND_SHIELD)
def test_armor_and_shield_are_whitelisted_and_kept(item):
    assert item in WHITELIST
    assert is_kept_item(item)


@pytest.mark.parametrize("item", LEATHER_ARMOR)
def test_leather_armor_is_kept_but_not_whitelisted_until_leather_can_be_obtained(item):
    assert item not in WHITELIST
    assert is_kept_item(item)


@pytest.mark.parametrize("item", ["stick", "iron_ingot", "leather"])
def test_materials_are_still_not_kept(item):
    assert not is_kept_item(item)


def test_next_goal_skips_a_held_iron_chestplate(tmp_path):
    curriculum = Curriculum(frontier=["iron_chestplate"])
    store = SchemaStore()
    for rule in fake_priors(tmp_path, ["iron_chestplate"]):
        store.add_rule(rule)
    craftable = {"iron_ingot": 8, "crafting_table": 1}
    assert curriculum.next_goal(craftable, store, "w") is not None
    assert curriculum.next_goal({**craftable, "iron_chestplate": 1}, store, "w") is None


def test_junk_toss_keeps_a_shield_and_armor():
    inventory = {"shield": 1, "iron_helmet": 1, "poppy": 3}
    assert junk_to_toss(inventory, [], [], 0) == {"poppy": 3}


def test_fake_mode_serves_an_iron_helmet_rule(tmp_path):
    rule = fake_priors(tmp_path, ["iron_helmet"])[0]
    assert rule.preconditions == {"consumes": {"iron_ingot": 5}, "station": "crafting_table"}


def test_simulator_recipes():
    assert WHITELIST_RECIPES["iron_helmet"] == ({"iron_ingot": 5}, 1, True)
    assert WHITELIST_RECIPES["shield"] == ({"#planks": 6, "iron_ingot": 1}, 1, True)
