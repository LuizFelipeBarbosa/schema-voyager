"""Repair dedupe: a proposal equal to a known rule or still consuming a dead item is skipped, and a repair is never repaired."""

import copy
from types import SimpleNamespace

from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.models import Observation, Rule

WORLD = "life-sim3"


def sword_rule(rule_id: str, consumes: dict[str, int]) -> Rule:
    return Rule(
        id=rule_id,
        action={"type": "craft", "item": "iron_sword"},
        preconditions={"consumes": consumes, "station": "crafting_table"},
        effects={"gains": {"iron_sword": 1}},
        scope=WORLD,
    )


class FakeRepairLLM:
    def __init__(self, propose):
        self.propose, self.calls = propose, []

    def repair_rule_v2(self, rule, dead_items, obtainable, world_id):
        self.calls.append(rule.id)
        return self.propose(rule, len(self.calls))


def make_life(tmp_path, llm) -> _Life:
    life = _Life(
        SimpleNamespace(say=lambda message: None),
        llm,
        LifeConfig(run_dir=tmp_path, world_id=WORLD),
        LifeHooks(),
        resume=False,
    )
    life.store.add_rule(sword_rule("craft_iron_sword", {"iron_ingot": 2, "stick": 1}))
    life.asked.add("iron_ingot")
    return life


def repair_passes(life: _Life, passes: int = 1) -> list[dict]:
    observation = Observation(position=(0, 0, 0), inventory={"stick": 1}, nearby={})
    for _ in range(passes):
        life._repair_dead_ingredients(observation)
    return life._repairs_unreported


def test_a_proposal_equal_to_the_repaired_rule_is_skipped_and_the_rule_is_asked_about_once(tmp_path):
    def same_recipe(rule, call):
        twin = copy.deepcopy(rule)
        twin.id = f"{rule.id}.fix{call}"
        return [twin]

    llm = FakeRepairLLM(same_recipe)
    life = make_life(tmp_path, llm)

    records = repair_passes(life, passes=4)

    assert llm.calls == ["craft_iron_sword"]
    assert list(life.store.rules) == ["craft_iron_sword"]
    assert len(records) == 1
    assert records[0]["proposed"] == []
    assert records[0]["skipped"] == [{"id": "craft_iron_sword.fix1", "why": "same as craft_iron_sword"}]


def test_a_proposal_that_still_consumes_the_dead_item_is_skipped(tmp_path):
    llm = FakeRepairLLM(lambda rule, call: [sword_rule("sword.fix1", {"iron_ingot": 3})])
    life = make_life(tmp_path, llm)

    record = repair_passes(life)[0]

    assert record["proposed"] == []
    assert record["skipped"] == [{"id": "sword.fix1", "why": "still consumes iron_ingot"}]
    assert "sword.fix1" not in life.store.rules


def test_an_added_repair_is_marked_repaired_and_never_repaired_itself(tmp_path):
    # the proposal consumes raw_iron, which nothing produces, so it would be repaired if it were not marked
    llm = FakeRepairLLM(lambda rule, call: [sword_rule(f"sword.fix{call}", {"raw_iron": 1})])
    life = make_life(tmp_path, llm)
    life.asked.add("raw_iron")

    repair_passes(life, passes=3)

    assert llm.calls == ["craft_iron_sword"]
    assert "sword.fix1" in life.store.rules and "sword.fix1" in life.repaired


def test_a_valid_repair_is_added_and_listed_with_nothing_skipped(tmp_path):
    llm = FakeRepairLLM(lambda rule, call: [sword_rule("sword.fix1", {"iron_block": 1, "stick": 1})])
    life = make_life(tmp_path, llm)

    record = repair_passes(life)[0]

    assert record == {
        "rule": "craft_iron_sword",
        "dead_items": ["iron_ingot"],
        "proposed": ["sword.fix1"],
        "skipped": [],
    }
    assert "sword.fix1" in life.store.rules
    assert "sword.fix1" in life.repaired
