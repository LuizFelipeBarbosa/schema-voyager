"""v3.3i: LLM.parse_goal for kill and guard goals."""

import pytest

from schema_voyager.llm import LLM, PARSE_GOAL_SCHEMA, PARSE_GOAL_SYSTEM, vanilla_items
from schema_voyager.models import GoalSpec, Unsupported

ITEMS = vanilla_items()


def parse_fake(text):
    return LLM(mode="fake").parse_goal(text, {}, ITEMS, goal_id="u001")


def parse_live(monkeypatch, payload):
    """Run parse_goal in live mode with _json_call returning `payload`; returns (result, calls)."""
    calls = []

    def fake_json_call(self, purpose, system, user, schema):
        calls.append((purpose, system, user, schema))
        return {"kind": "obtain", "item": "", "count": 1, "target": "", "reply": "", **payload}

    monkeypatch.setattr(LLM, "_json_call", fake_json_call)
    return LLM(mode="live").parse_goal("whatever", {}, ITEMS, goal_id="u007"), calls


@pytest.mark.parametrize(
    "text, target, count",
    [
        ("kill 3 zombies", "zombie", 3),
        ("kill a zombie", "zombie", 1),
        ("kill zombie", "zombie", 1),
        ("kill 2 cows", "cow", 2),
        ("Kill 2 cave spiders", "cave_spider", 2),
        ("kill 2 witches", "witch", 2),
    ],
)
def test_fake_kill(text, target, count):
    expected = GoalSpec(id="u001", kind="kill", source="user", text=text, target=target, count=count)
    assert parse_fake(text) == expected


@pytest.mark.parametrize("text, target", [("guard me", "me"), ("guard Tester", "Tester"), ("guard", "me")])
def test_fake_guard(text, target):
    assert parse_fake(text) == GoalSpec(id="u001", kind="guard", source="user", text=text, target=target)


def test_fake_kill_refusals():
    assert parse_fake("kill 2 unicorns") == Unsupported("I don't know a creature called unicorns.")
    assert parse_fake("kill 2 players") == Unsupported("I won't fight players.")
    assert parse_fake("kill 17 zombies") == Unsupported("I can kill at most 16 at a time.")
    assert parse_fake("kill 16 zombies").count == 16


def test_live_kill_reads_the_target(monkeypatch):
    result, _ = parse_live(monkeypatch, {"kind": "kill", "target": "zombies", "count": 3})
    assert result == GoalSpec(id="u007", kind="kill", source="user", text="whatever", target="zombie", count=3)


def test_live_kill_checks(monkeypatch):
    result, _ = parse_live(monkeypatch, {"kind": "kill", "target": "Cave Spiders", "count": 0})
    assert (result.target, result.count) == ("cave_spider", 1)
    result, _ = parse_live(monkeypatch, {"kind": "kill", "target": "player", "count": 1})
    assert result == Unsupported("I won't fight players.")
    result, _ = parse_live(monkeypatch, {"kind": "kill", "target": "zombie", "count": 17})
    assert result == Unsupported("I can kill at most 16 at a time.")


def test_live_guard_keeps_the_name_and_defaults_to_me(monkeypatch):
    result, _ = parse_live(monkeypatch, {"kind": "guard", "target": "Tester"})
    assert result == GoalSpec(id="u007", kind="guard", source="user", text="whatever", target="Tester")
    result, _ = parse_live(monkeypatch, {"kind": "guard", "target": ""})
    assert result.target == "me"


def test_live_obtain_is_unchanged_and_prompt_is_untouched(monkeypatch):
    result, calls = parse_live(monkeypatch, {"kind": "obtain", "item": "torch", "count": 4})
    assert result == GoalSpec(id="u007", kind="obtain", source="user", text="whatever", item="torch", count=4)
    _, system, _, schema = calls[0]
    assert system is PARSE_GOAL_SYSTEM and schema is PARSE_GOAL_SCHEMA
    assert "kill" in system and "guard" in system
