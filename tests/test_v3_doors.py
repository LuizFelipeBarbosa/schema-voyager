"""Doors are any wooden door (docs/v3-interfaces.md v3.4b): a #door group like #planks."""

import pytest

from schema_voyager import normalize
from schema_voyager.blueprint import DOORS, matches
from schema_voyager.environment import WHITELIST_RECIPES, NaturalWorld
from schema_voyager.llm import LLM, _to_rule_v2
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action
from schema_voyager.planner import make_plan
from tests.test_v3_toss_backend import FakeBot


def _fake_rules(tmp_path, families):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl").propose_prior_rules_v2(families, [], "w")


# --- normalize ---------------------------------------------------------------


def test_the_door_group_has_one_member_per_species():
    assert normalize.members("#door") == sorted(f"{s}_door" for s in normalize.SPECIES)
    assert set(normalize.members("#door")) == DOORS
    assert len(normalize.members("#door")) == 8


def test_family_and_species_of_a_door():
    assert normalize.family("birch_door") == "#door"
    assert normalize.family("#door") == "#door"
    assert normalize.species("birch_door") == "birch"
    assert normalize.counts({"oak_door": 1, "birch_door": 2}) == {"#door": 3}


def test_iron_door_is_not_a_wooden_door():
    assert normalize.family("iron_door") == "iron_door"


@pytest.mark.parametrize(
    "inventory, expected",
    [
        ({"birch_planks": 6, "oak_planks": 3}, "birch_door"),
        ({"birch_planks": 3, "oak_planks": 4}, "oak_door"),
        ({"birch_planks": 6, "oak_planks": 9, "spruce_planks": 7}, "oak_door"),
        ({"birch_planks": 6, "oak_planks": 6}, "birch_door"),
        ({"birch_planks": 9, "oak_planks": 7, "cherry_planks": 5}, "birch_door"),
        ({"spruce_door": 1}, "spruce_door"),
        ({"spruce_door": 1, "oak_door": 2}, "oak_door"),
        ({"spruce_door": 5, "oak_planks": 1}, "oak_door"),
    ],
)
def test_resolve_door(inventory, expected):
    assert normalize.resolve("#door", inventory) == expected


def test_resolve_door_with_nothing_held_fails():
    with pytest.raises(ValueError):
        normalize.resolve("#door", {"dirt": 4})


def test_resolve_planks_is_unchanged():
    assert normalize.resolve("#planks", {"oak_log": 1, "birch_log": 3, "spruce_planks": 9}) == "birch_planks"
    assert normalize.resolve("#planks", {"spruce_planks": 9}) == "spruce_planks"


# --- blueprint.matches -------------------------------------------------------


def test_a_door_matches_any_wooden_door():
    assert matches("oak_door", "birch_door")
    assert matches("oak_door", "oak_door")
    assert not matches("oak_door", "air")
    assert not matches("oak_door", "iron_door")
    assert not matches("oak_door", None)
    assert not matches("oak_planks", "birch_planks")


# --- a rule about a raw door ---------------------------------------------------


def test_a_raw_door_rule_normalizes_to_the_group():
    raw = {
        "action_type": "craft", "subject": "oak_door", "tool_any": [], "fuel_any": [], "station": "crafting_table",
        "consumes": [{"item": "oak_planks", "count": 6}], "yields": [{"item": "oak_door", "count": 3}],
        "check": "exact", "allowed": [], "rationale": "Six oak planks make three oak doors.",
    }
    rule = _to_rule_v2(raw, "claude", "w")
    assert rule.action == {"type": "craft", "item": "#door"}
    assert rule.preconditions == {"consumes": {"#planks": 6}, "station": "crafting_table"}
    assert rule.effects["gains"] == {"#door": 3}


def test_a_fake_mode_life_plans_a_door_from_a_log(tmp_path):
    rules = _fake_rules(tmp_path, ["#door"])
    assert [rule.action["item"] for rule in rules] == ["#door"]
    all_rules = _fake_rules(tmp_path, ["#door", "#planks", "crafting_table", "#log"])
    plan = make_plan("#door", 1, {}, all_rules, visible={"#log": 8})
    steps = [(step.action.kind, step.action.target) for step in plan]
    assert list(dict.fromkeys(steps)) == [
        ("mine", "#log"), ("craft", "#planks"), ("craft", "crafting_table"), ("craft", "#door"),
    ]


# --- the simulator -----------------------------------------------------------


def _world(**inventory):
    world = NaturalWorld(7)
    world.inventory.update(inventory)
    return world


def test_the_simulator_recipe():
    assert WHITELIST_RECIPES["#door"] == ({"#planks": 6}, 3, True)


@pytest.mark.parametrize("target", ["#door", "birch_door"])
def test_the_simulator_crafts_a_door_of_the_planks_species(target):
    world = _world(birch_planks=6, oak_planks=3, crafting_table=1)
    outcome = world.act(Action("craft", target=target))
    assert outcome.ok
    assert world.inventory["birch_door"] == 3
    assert world.inventory["birch_planks"] == 0
    assert world.inventory["oak_planks"] == 3
    assert world.inventory["oak_door"] == 0


def test_the_simulator_refuses_a_door_from_mixed_planks():
    world = _world(birch_planks=3, oak_planks=3, crafting_table=1)
    outcome = world.act(Action("craft", target="#door"))
    assert not outcome.ok and outcome.error == "missing_ingredients"
    assert world.inventory["birch_planks"] == 3 and world.inventory["oak_planks"] == 3


def test_the_simulator_needs_a_crafting_table_for_a_door():
    world = _world(birch_planks=6)
    outcome = world.act(Action("craft", target="#door"))
    assert not outcome.ok and outcome.error == "missing_crafting_table"


# --- the backend ---------------------------------------------------------------


def test_the_backend_crafts_a_door_group_as_the_door_of_the_planks_held():
    bot = FakeBot()
    try:
        bot.responses["/observe"] = {"ok": True, "inventory": {"birch_planks": 6, "crafting_table": 1}, "nearby_blocks": {}}
        bot.responses["/craft"] = {"ok": True, "inventory": {"birch_door": 3, "crafting_table": 1}}
        backend = MineflayerBackend(api_url=bot.url, natural=True)
        assert backend.act(Action("craft", target="#door")).ok
        assert [(path, body) for path, body in bot.requests if path == "/craft"] == [
            ("/craft", {"item": "birch_door", "times": 1})
        ]
    finally:
        bot.server.shutdown()
        bot.server.server_close()
