"""Repair: a usable rule that needs an item nothing known produces gets one repair call."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM
from schema_voyager.schema_store import SchemaStore

WORLD = "life-sim2"


class WrongIronLLM(LLM):
    """Fake LLM whose prior for smelting iron ingots consumes the iron_ore item (a 1.21 mistake)."""

    def __init__(self, cache_path: Path, wrong: bool = True, **kwargs):
        super().__init__(mode="fake", cache_path=cache_path, **kwargs)
        self.wrong, self.repairs = wrong, []

    def propose_prior_rules_v2(self, families, known_blocks, world_id):
        rules = super().propose_prior_rules_v2(families, known_blocks, world_id)
        for rule in rules:
            if self.wrong and rule.id == "smelt_iron_ingot":
                rule.preconditions["consumes"] = {"iron_ore": 1}
        return rules

    def repair_rule_v2(self, rule, dead_items, obtainable, world_id):
        self.repairs.append((rule.id, dead_items, obtainable))
        return super().repair_rule_v2(rule, dead_items, obtainable, world_id)


def life(run_dir: Path, llm: LLM, max_goals: int, resume: bool = False) -> dict:
    config = LifeConfig(run_dir=run_dir, world_id=WORLD, max_goals=max_goals)
    return run_life(NaturalWorld(seed=2), llm, config, resume=resume)


def records(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "life.jsonl").read_text().splitlines()]


def test_a_dead_ingredient_gets_one_repair_call_and_the_iron_goal_becomes_plannable(tmp_path):
    llm = WrongIronLLM(tmp_path / "c.jsonl")
    life(tmp_path, llm, max_goals=40)

    assert [call[0] for call in llm.repairs] == ["smelt_iron_ingot"]
    assert llm.repairs[0][1] == ["iron_ore"]
    store = SchemaStore.load(tmp_path / "store.json")
    fix = store.rules[f"smelt_iron_ingot.fix1@{WORLD}"]
    assert fix.preconditions["consumes"] == {"raw_iron": 1}
    assert (fix.source, fix.revision_of, fix.rank) == ("revision", "smelt_iron_ingot", 0)
    assert store.rules["smelt_iron_ingot"].preconditions["consumes"] == {"iron_ore": 1}  # the old rule stays

    lines = records(tmp_path)
    repaired = [line for line in lines if "repairs" in line]
    assert len(repaired) == 1
    assert repaired[0]["repairs"] == [
        {"rule": "smelt_iron_ingot", "dead_items": ["iron_ore"], "proposed": [fix.id]}
    ]
    assert repaired[0]["llm_calls"] >= 1
    assert any(line["goal"] == "iron_ingot" and line["outcome"] == "success" for line in lines)
    assert json.loads((tmp_path / "life_state.json").read_text())["repaired"] == ["smelt_iron_ingot"]


def test_the_repair_is_traced_said_and_printed(tmp_path, capsys):
    life(tmp_path, WrongIronLLM(tmp_path / "c.jsonl"), max_goals=40)

    first = next(line for line in records(tmp_path) if "repairs" in line)
    trace = next((tmp_path / "episodes").glob(f"D_life_g{first['index']:03d}_*.jsonl"))
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [e["rule"] for e in events if e["kind"] == "repair"] == ["smelt_iron_ingot"]
    assert "[repair] smelt_iron_ingot" in capsys.readouterr().out


def test_nothing_is_repaired_when_nothing_is_dead(tmp_path):
    llm = WrongIronLLM(tmp_path / "c.jsonl", wrong=False)
    life(tmp_path, llm, max_goals=20)

    assert llm.repairs == []
    assert not any("repairs" in line for line in records(tmp_path))
    assert not any(call.purpose == "repair_rule" for call in llm.calls)


def test_a_rule_is_never_repaired_twice_across_a_resume(tmp_path):
    first = WrongIronLLM(tmp_path / "c1.jsonl")
    life(tmp_path, first, max_goals=40)
    second = WrongIronLLM(tmp_path / "c2.jsonl")
    life(tmp_path, second, max_goals=44, resume=True)

    assert len(first.repairs) == 1 and second.repairs == []
    assert sum(len(line.get("repairs", [])) for line in records(tmp_path)) == 1


def test_the_spend_cap_stops_a_repair_that_would_pass_it(tmp_path, monkeypatch):
    llm = LLM(mode="live", cache_path=tmp_path / "c.jsonl", spend_cap_usd=0.165)
    created = []
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    reply = SimpleNamespace(
        stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps({"rules": []}))]
    )
    monkeypatch.setattr(llm, "_create", lambda *args: created.append(1) or reply)
    rule = LLM(mode="fake", cache_path=tmp_path / "f.jsonl").propose_prior_rules_v2(["iron_ingot"], [], WORLD)[0]

    assert llm.repair_rule_v2(rule, ["iron_ore"], ["coal"], WORLD) == []  # first call fits under the cap
    with pytest.raises(Exception, match="cap"):
        llm.repair_rule_v2(rule, ["iron_ore"], ["coal"], WORLD)
    assert len(created) == 1 and llm.spend_cap_hit
