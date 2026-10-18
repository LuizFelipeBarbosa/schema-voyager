"""v3.3d: the prior-rule call proposes kill rules for mobs, the fake priors know them, vanilla_mobs()."""

import json

from schema_voyager import llm as llm_module
from schema_voyager.llm import LLM, _to_rule_v2, vanilla_mobs

WORLD = "w"
EXPECTED_USER_WITHOUT_MOBS = (
    "Block families the agent has seen near it so far: oak_log.\n"
    "Write the vanilla rules for these names: stick, stone.\n"
    "For a block, write the rule that mines it. For an item, write the rule that crafts or smelts it. "
    "Write exactly one rule per name, and also one rule for each raw ingredient an item needs "
    "that is not a name above, so the agent can plan from blocks it can mine."
)


def fake_llm(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")


def captured_request(tmp_path, **kwargs):
    llm = LLM(mode="live", cache_path=tmp_path / "llm_cache.jsonl")
    sent = []

    def stub(purpose, system, user, schema):
        sent.append((system, user, schema))
        return {"rules": []}

    llm._json_call = stub
    llm.propose_prior_rules_v2(["stick", "stone"], ["oak_log"], WORLD, **kwargs)
    return sent[0]


def kill_json(**overrides):
    raw = {
        "action_type": "kill", "subject": "minecraft:cow", "tool_any": ["iron_sword"], "consumes": [],
        "station": "", "fuel_any": [], "yields": [{"item": "beef", "count": 2}], "check": "exact",
        "allowed": [{"item": "beef", "min": 1, "max": 3}, {"item": "leather", "min": 0, "max": 2}],
        "rationale": "A cow drops beef and leather.",
    }
    return {**raw, **overrides}


def test_request_without_mobs_is_unchanged(tmp_path):
    for kwargs in ({}, {"mobs": []}):
        system, user, schema = captured_request(tmp_path, **kwargs)
        assert system == llm_module.WORLD_MODEL_SYSTEM_V2
        assert user == EXPECTED_USER_WITHOUT_MOBS
        assert schema is llm_module.RULES_SCHEMA_V2
        assert "kill" not in json.dumps(schema)


def test_request_with_mobs_asks_for_kill_rules(tmp_path):
    system, user, schema = captured_request(tmp_path, mobs=["cow", "zombie"])
    assert system.startswith(llm_module.WORLD_MODEL_SYSTEM_V2)
    assert 'action_type "kill"' in system
    assert schema["properties"]["rules"]["items"]["properties"]["action_type"]["enum"] == [
        "mine", "craft", "smelt", "kill",
    ]
    assert llm_module.RULES_SCHEMA_V2["properties"]["rules"]["items"]["properties"]["action_type"]["enum"] == [
        "mine", "craft", "smelt",
    ]
    assert user.startswith(EXPECTED_USER_WITHOUT_MOBS)
    assert "cow, zombie" in user and "one kill rule per mob" in user


def test_to_rule_builds_a_kill_rule():
    rule = _to_rule_v2(kill_json(), source="prior", scope=WORLD)
    assert rule.id == "kill_cow"
    assert rule.action == {"type": "kill", "mob": "cow"}
    assert rule.preconditions == {"tool_any": []}
    assert rule.effects == {
        "killed": True, "drops": {"beef": 1}, "allowed": {"beef": [1, 3], "leather": [0, 2]},
    }
    assert rule.check == "stochastic"
    assert (rule.subject, rule.yields()) == ("cow", {"beef": 1})


def test_to_rule_without_allowed_plans_nothing():
    rule = _to_rule_v2(kill_json(allowed=[]), source="prior", scope=WORLD)
    assert rule.effects["drops"] == {}
    assert rule.effects["allowed"] == {"beef": [0, 2]}


def test_fake_priors_give_kill_rules_only_for_mobs_sent(tmp_path):
    llm = fake_llm(tmp_path)
    with_mobs = llm.propose_prior_rules_v2([], [], WORLD, mobs=["cow"])
    cow = next(rule for rule in with_mobs if rule.id == "kill_cow")
    assert cow.effects["allowed"] == {"beef": [1, 3], "leather": [0, 2]}
    assert [rule.id for rule in with_mobs] == ["kill_cow"]
    assert llm.propose_prior_rules_v2(["beef", "leather"], [], WORLD) == []


def test_fake_meat_smelt_rules(tmp_path):
    rules = fake_llm(tmp_path).propose_prior_rules_v2(["cooked_beef"], [], WORLD)
    assert [rule.id for rule in rules] == ["smelt_cooked_beef"]
    assert rules[0].preconditions["consumes"] == {"beef": 1}
    assert rules[0].preconditions["station"] == "furnace"


def test_revising_and_repairing_a_kill_rule_return_nothing(tmp_path):
    llm = fake_llm(tmp_path)
    cow = llm.propose_prior_rules_v2([], [], WORLD, mobs=["cow"])[0]
    assert llm.revise_rule_v2(cow, "", {"observed": {}, "action": {}}, [], WORLD) == []
    assert llm.repair_rule_v2(cow, ["beef"], [], WORLD) == []


def test_vanilla_mobs():
    mobs = vanilla_mobs()
    assert mobs["cow"] == "animal"
    assert mobs["zombie"] == "hostile"
    assert mobs["player"] == "player"
