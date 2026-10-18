"""v3.4b: LLM.design_blueprint (fake hut, live retries with the errors, limits, the schema)."""

import json

import pytest

from schema_voyager import blueprint
from schema_voyager.llm import (
    BLUEPRINT_SCHEMA,
    DESIGN_RETRIES,
    FAKE_HUT,
    LLM,
    BlueprintDesignError,
    BlueprintLimits,
    LLMError,
)

HUT_MATERIALS = {"#planks": 55, "#log": 12, "torch": 1, "oak_door": 1}


def claude_form(bp):
    return {**bp, "palette": [{"key": key, "block": block} for key, block in bp["palette"].items()]}


GOOD = claude_form({"name": "slab", "size": [2, 1, 2], "palette": {"P": "#planks"}, "layers": [["PP", "PP"]]})
FLOATING = claude_form({"name": "bad", "size": [1, 2, 1], "palette": {"P": "#planks"}, "layers": [["."], ["P"]]})


def design_live(monkeypatch, answers, text="a hut", limits=BlueprintLimits()):
    """Run design_blueprint with _json_call returning `answers` in order; returns (outcome, calls)."""
    calls = []
    remaining = list(answers)

    def fake_json_call(self, purpose, system, user, schema):
        calls.append((purpose, system, user, schema))
        return remaining.pop(0)

    monkeypatch.setattr(LLM, "_json_call", fake_json_call)
    try:
        return LLM(mode="live").design_blueprint(text, limits), calls
    except BlueprintDesignError as error:
        return error, calls


def test_fake_returns_the_hut_metered_and_copied():
    llm = LLM(mode="fake")
    first = llm.design_blueprint("anything")
    assert first == FAKE_HUT
    assert blueprint.validate(FAKE_HUT) == []
    assert blueprint.materials(FAKE_HUT) == HUT_MATERIALS
    first["layers"][0][0] = "changed"
    assert llm.design_blueprint("anything") == FAKE_HUT
    assert [call.purpose for call in llm.calls] == ["design_blueprint", "design_blueprint"]


def test_live_valid_answer_has_a_palette_map(monkeypatch):
    bp, calls = design_live(monkeypatch, [GOOD])
    assert bp == {"name": "slab", "size": [2, 1, 2], "palette": {"P": "#planks"}, "layers": [["PP", "PP"]]}
    assert len(calls) == 1 and calls[0][0] == "design_blueprint"
    assert "a hut" in calls[0][2] and "7 wide" in calls[0][2]


def test_invalid_then_valid_retries_with_the_error(monkeypatch):
    bp, calls = design_live(monkeypatch, [FLOATING, GOOD])
    assert bp["name"] == "slab"
    assert len(calls) == 2
    first_error = blueprint.validate({**FLOATING, "palette": {"P": "#planks"}})[0]
    assert first_error in calls[1][2]
    assert "a hut" in calls[1][2] and json.dumps(FLOATING) in calls[1][2]


def test_three_invalid_answers_raise(monkeypatch):
    error, calls = design_live(monkeypatch, [FLOATING] * (DESIGN_RETRIES + 1))
    assert isinstance(error, BlueprintDesignError) and not isinstance(error, LLMError)
    assert len(calls) == 3
    assert error.errors == blueprint.validate({**FLOATING, "palette": {"P": "#planks"}})
    assert str(error) == error.errors[0]


def test_repeated_palette_key(monkeypatch):
    twice = {**GOOD, "palette": GOOD["palette"] * 2}
    error, _ = design_live(monkeypatch, [twice] * 3)
    assert error.errors == ["palette: key 'P' is listed twice"]


def test_over_size_limit(monkeypatch):
    big = claude_form({"name": "big", "size": [9, 1, 9], "palette": {"P": "#planks"}, "layers": [["P" * 9] * 9]})
    error, _ = design_live(monkeypatch, [big] * 3)
    assert error.errors == ["size: [9, 1, 9] is over this build's limit 7 x 4 x 7"]


def test_over_placed_limit(monkeypatch):
    error, _ = design_live(monkeypatch, [GOOD] * 3, limits=BlueprintLimits(max_placed=3))
    assert error.errors == ["layers: 4 placed cells, this build's limit is 3"]


def test_llm_error_propagates(monkeypatch):
    def failing(self, purpose, system, user, schema):
        raise LLMError("boom")

    monkeypatch.setattr(LLM, "_json_call", failing)
    with pytest.raises(LLMError):
        LLM(mode="live").design_blueprint("a hut")


def test_schema_enum_and_closed_objects():
    enum = BLUEPRINT_SCHEMA["properties"]["palette"]["items"]["properties"]["block"]["enum"]
    assert set(enum) == set(blueprint.PLACEABLE) | set(blueprint.GROUPS) | {"air", "any"}

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from objects(value)

    found = list(objects(BLUEPRINT_SCHEMA))
    assert len(found) == 2
    for node in found:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
