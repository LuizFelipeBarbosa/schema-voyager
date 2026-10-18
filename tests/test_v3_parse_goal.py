"""v3.1e: LLM.parse_goal, vanilla_items / vanilla_foods and the committed items file."""

import json

import pytest

from schema_voyager import llm as llm_module
from schema_voyager.llm import LLM, MAX_GOAL_COUNT, PARSE_GOAL_SCHEMA, vanilla_foods, vanilla_items
from schema_voyager.models import GoalSpec, Unsupported

ITEMS = vanilla_items()
DEFAULT_REPLY = 'I can only fetch items for now: try "get me 10 torches".'


def parse_fake(text):
    return LLM(mode="fake").parse_goal(text, {}, ITEMS, goal_id="u001")


def parse_live(monkeypatch, payload, text="get me stuff"):
    """Run parse_goal in live mode with _json_call returning `payload`; returns (result, calls)."""
    calls = []

    def fake_json_call(self, purpose, system, user, schema):
        calls.append((purpose, system, user, schema))
        return {"kind": "obtain", "item": "", "count": 1, "target": "", "reply": "", **payload}

    monkeypatch.setattr(LLM, "_json_call", fake_json_call)
    return LLM(mode="live").parse_goal(text, {"stick": 2}, ITEMS, goal_id="u007"), calls


# -- fake mode ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, item, count",
    [
        ("get me 10 torches", "torch", 10),
        ("4 sticks", "stick", 4),
        ("planks", "#planks", 1),
        ("3 logs", "#log", 3),
        ("oak_planks", "#planks", 1),
        ("Get me 2 Diamonds", "diamond", 2),
        ("get me 0 torches", "torch", 1),
        ("get me 256 torches", "torch", 256),
    ],
)
def test_fake_obtain(text, item, count):
    spec = parse_fake(text)
    assert spec == GoalSpec(id="u001", kind="obtain", source="user", text=text, item=item, count=count)


def test_fake_count_over_the_limit():
    assert MAX_GOAL_COUNT == 256
    assert parse_fake("get me 300 torches") == Unsupported("That's more than I can carry. Ask for 256 or fewer.")


def test_fake_unknown_item():
    assert parse_fake("get me 5 unobtainium") == Unsupported("I don't know an item called unobtainium.")


def test_fake_text_without_a_word():
    assert isinstance(parse_fake("10"), Unsupported)


@pytest.mark.parametrize("text, reply_part", [("build a castle", "can't build"), ("kill 3 zombies", "can't hunt"),
                                              ("guard me", "can't guard")])
def test_fake_other_kinds_are_unsupported(text, reply_part):
    result = parse_fake(text)
    assert isinstance(result, Unsupported)
    assert reply_part in result.reply


def test_fake_is_metered_and_makes_no_call():
    llm = LLM(mode="fake")
    llm.parse_goal("get me 10 torches", {}, ITEMS, goal_id="u001")
    assert [call.purpose for call in llm.calls] == ["parse_goal"]


def test_known_items_limits_the_fake_match():
    assert parse_fake("get me 3 torches").item == "torch"
    result = LLM(mode="fake").parse_goal("get me 3 torches", {}, frozenset({"stick"}), goal_id="u001")
    assert result == Unsupported("I don't know an item called torches.")


# -- live path ------------------------------------------------------------------


def test_live_obtain(monkeypatch):
    spec, calls = parse_live(monkeypatch, {"item": "diamond", "count": 3}, text="3 diamonds please")
    assert spec == GoalSpec(id="u007", kind="obtain", source="user", text="3 diamonds please", item="diamond", count=3)
    purpose, _system, user, schema = calls[0]
    assert purpose == "parse_goal"
    assert schema is PARSE_GOAL_SCHEMA
    assert "3 diamonds please" in user and "stick" in user


def test_live_strips_namespace_and_spaces(monkeypatch):
    spec, _ = parse_live(monkeypatch, {"item": " minecraft:torch ", "count": 5})
    assert (spec.item, spec.count) == ("torch", 5)


def test_live_group_and_family(monkeypatch):
    spec, _ = parse_live(monkeypatch, {"item": "birch_planks", "count": 4})
    assert spec.item == "#planks"
    spec, _ = parse_live(monkeypatch, {"item": "#log", "count": 4})
    assert spec.item == "#log"


def test_live_unknown_item(monkeypatch):
    result, _ = parse_live(monkeypatch, {"item": "#unobtainium", "count": 1})
    assert result == Unsupported("I don't know an item called unobtainium.")


def test_live_count_bounds(monkeypatch):
    assert parse_live(monkeypatch, {"item": "torch", "count": -4})[0].count == 1
    assert isinstance(parse_live(monkeypatch, {"item": "torch", "count": 257})[0], Unsupported)


def test_live_unsupported_default_reply(monkeypatch):
    result, _ = parse_live(monkeypatch, {"kind": "unsupported", "reply": ""})
    assert result == Unsupported(DEFAULT_REPLY)


def test_live_reply_has_no_hash_and_is_cut(monkeypatch):
    result, _ = parse_live(monkeypatch, {"kind": "unsupported", "reply": "Try #planks " + "x" * 300})
    assert "#" not in result.reply
    assert len(result.reply) == 240


@pytest.mark.parametrize("kind, reply_part", [("build", "can't build"), ("kill", "can't hunt"),
                                              ("guard", "can't guard")])
def test_live_other_kinds_get_the_fixed_reply(monkeypatch, kind, reply_part):
    result, _ = parse_live(monkeypatch, {"kind": kind, "target": "zombie", "reply": "ignored"})
    assert isinstance(result, Unsupported)
    assert reply_part in result.reply and "ignored" not in result.reply


def test_llm_errors_propagate(monkeypatch):
    def failing(self, *args):
        raise llm_module.LLMError("boom")

    monkeypatch.setattr(LLM, "_json_call", failing)
    with pytest.raises(llm_module.LLMError):
        LLM(mode="live").parse_goal("get me 10 torches", {}, ITEMS, goal_id="u001")


# -- items file -------------------------------------------------------------------


def test_vanilla_items_and_foods():
    assert {"torch", "stick", "oak_planks"} <= ITEMS
    assert "planks" not in ITEMS
    foods = vanilla_foods()
    assert foods["bread"] == 5
    assert set(foods) <= ITEMS


def test_items_file_header():
    data = json.loads((llm_module.DATA_DIR / "items_1_21_1.json").read_text(encoding="utf-8"))
    assert data["version"] == "1.21.1"
    assert data["minecraft_data"] == "3.117.0"
    assert data["items"] == sorted(data["items"])
    assert list(data["foods"]) == sorted(data["foods"])
