"""A tool that wears out while the bot digs its station back up (v3.0e)."""

import pytest

from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Outcome, Rule
from schema_voyager.verifier import check

SMELT_RULE = Rule(
    id="smelt_iron_ingot", action={"type": "smelt", "item": "iron_ingot"},
    preconditions={"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "#planks", "stick"]},
    effects={"gains": {"iron_ingot": 1}},
)
SMELT_3 = Action("smelt", "iron_ingot", times=3, fuel="coal", ingredient="raw_iron")
CRAFT_RULE = Rule(
    id="craft_stone_pickaxe", action={"type": "craft", "item": "stone_pickaxe"},
    preconditions={"consumes": {"cobblestone": 3, "stick": 2}, "station": "crafting_table"},
    effects={"gains": {"stone_pickaxe": 1}},
)
CRAFT = Action("craft", "stone_pickaxe")


def _smelt_outcome(delta_extra=None, **detail_extra):
    delta = {"iron_ingot": 3, "raw_iron": -3, "wooden_pickaxe": -1, "coal": -1} | (delta_extra or {})
    detail = {"fuel": "coal", "fuel_used": 1, "furnace_retrieved": True, "used_furnace": True} | detail_extra
    return Outcome(ok=True, inventory_delta=delta, detail=detail)


def test_a_smelt_whose_pickaxe_broke_on_the_furnace_dig_matches():
    outcome = _smelt_outcome(tools_broken=["wooden_pickaxe"])
    result = check(SMELT_RULE, SMELT_3, outcome)
    assert result.status == "match"
    assert "wooden_pickaxe wore out (tolerant)" in result.detail


def test_the_same_smelt_without_tools_broken_is_a_mismatch():
    assert check(SMELT_RULE, SMELT_3, _smelt_outcome()).status == "mismatch"


def test_a_craft_whose_tool_broke_on_the_table_dig_matches():
    outcome = Outcome(
        ok=True,
        inventory_delta={"cobblestone": -3, "stick": -2, "stone_pickaxe": 1, "wooden_pickaxe": -1},
        detail={"used_table": True, "table_retrieved": True, "tools_broken": ["wooden_pickaxe"]},
    )
    result = check(CRAFT_RULE, CRAFT, outcome)
    assert result.status == "match" and "wooden_pickaxe wore out (tolerant)" in result.detail


def test_wear_and_an_unretrieved_station_are_both_reported():
    outcome = Outcome(
        ok=True,
        inventory_delta={"cobblestone": -3, "stick": -2, "stone_pickaxe": 1, "wooden_pickaxe": -1, "crafting_table": -1},
        detail={"table_retrieved": False, "tools_broken": ["wooden_pickaxe"]},
    )
    result = check(CRAFT_RULE, CRAFT, outcome)
    assert result.status == "match"
    assert "crafting_table not picked back up (tolerant)" in result.detail
    assert "wooden_pickaxe wore out (tolerant)" in result.detail


def test_a_mismatch_from_something_else_stays_a_mismatch():
    outcome = _smelt_outcome(delta_extra={"iron_ingot": 2}, tools_broken=["wooden_pickaxe"])
    assert check(SMELT_RULE, SMELT_3, outcome).status == "mismatch"


# ---- backend ---------------------------------------------------------------------


def _backend(monkeypatch, bodies):
    backend = MineflayerBackend("http://unused", natural=True)
    responses = iter(bodies)
    monkeypatch.setattr(backend, "_post", lambda path, payload=None, **kwargs: next(responses))
    monkeypatch.setattr(backend, "_known_inventory", lambda: {"raw_iron": 3, "coal": 1, "oak_log": 1, "birch_log": 1})
    return backend


def _body(**fields):
    return {"ok": True, "inventory_delta": {}, "inventory": {}, "travel": {}, "events": []} | fields


@pytest.mark.parametrize("reported,expected", [
    (["wooden_pickaxe"], ["wooden_pickaxe"]), ([], None), (None, None),
])
def test_smelt_copies_tools_broken_only_when_not_empty(monkeypatch, reported, expected):
    fields = {} if reported is None else {"tools_broken": reported}
    backend = _backend(monkeypatch, [_body(**fields)])
    outcome = backend.act(SMELT_3)
    assert outcome.detail.get("tools_broken") == expected
    assert ("tools_broken" in outcome.detail) == (expected is not None)


def test_craft_concatenates_tools_broken_across_its_calls(monkeypatch):
    bodies = [_body(tools_broken=["wooden_pickaxe"]), _body(tools_broken=["stone_pickaxe"])]
    backend = _backend(monkeypatch, bodies)
    outcome = backend.act(Action("craft", "#planks", times=2))
    assert outcome.detail["tools_broken"] == ["wooden_pickaxe", "stone_pickaxe"]


def test_craft_omits_tools_broken_when_no_call_reports_any(monkeypatch):
    backend = _backend(monkeypatch, [_body(tools_broken=[])])
    assert "tools_broken" not in backend.act(Action("craft", "stick")).detail
