"""Kill rules (v3.3a-c): always stochastic in the checker, certified from in-range kills only, planned like a
mine step with the low end of each drop range, and narrated as "kill cow"."""

import time

import pytest

from schema_voyager import agent
from schema_voyager.agent import pursue
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.models import Action, Observation, Outcome, Rule, Task
from schema_voyager.planner import PlanningError, make_plan
from schema_voyager.schema_store import TRAIN_WORLD, SchemaStore, Transition, applies
from schema_voyager.trace import EpisodeLog
from schema_voyager.verifier import check, evaluate, kind_of, predict

KILL_COW = Action("kill", "cow")
ALLOWED = {"beef": [1, 3], "leather": [0, 2]}


def kill_cow(check_kind="stochastic", tool_any=(), **fields) -> Rule:
    return Rule(
        id="kill_cow", action={"type": "kill", "mob": "cow"}, preconditions={"tool_any": list(tool_any)},
        effects={"killed": True, "drops": {"beef": 1}, "allowed": ALLOWED}, check=check_kind, **fields,
    )


def smelt_rule(item, output, **fields) -> Rule:
    return Rule(
        id=f"smelt_{output}", action={"type": "smelt", "item": output},
        preconditions={"consumes": {item: 1}, "station": "furnace", "fuel_any": ["coal"]},
        effects={"gains": {output: 1}}, **fields,
    )


def kill_outcome(drops, killed=True, events=(), **fields) -> Outcome:
    return Outcome(
        ok=True, drops=drops, picked_up=drops, block_removed=False,
        detail={"killed": killed, "mob": "cow"}, events=list(events), **fields,
    )


# --- the check ---------------------------------------------------------------------------


def test_a_kill_rule_is_stochastic_even_when_it_says_exact():
    assert kind_of(kill_cow(check_kind="exact"), KILL_COW) == "stochastic"
    assert predict(kill_cow(), KILL_COW) == {"killed": True, "allowed": {"beef": [1, 3], "leather": [0, 2]}}


def test_drops_in_range_are_stochastic_and_say_so():
    result = check(kill_cow(), KILL_COW, kill_outcome({"beef": 2, "leather": 1}))
    assert (result.status, result.kind, result.detail) == ("stochastic", "stochastic", "drops in range")
    assert result.observed == {"killed": True, "drops": {"beef": 2, "leather": 1}}


@pytest.mark.parametrize("drops", [
    {"beef": 4}, {"beef": 0}, {}, {"beef": 1, "feather": 1}, {"cooked_beef": 1, "beef": 1}, {"beef": 1, "leather": 3},
])
def test_drops_out_of_range_are_neutral_never_a_mismatch(drops):
    result = check(kill_cow(check_kind="exact"), KILL_COW, kill_outcome(drops))
    assert (result.status, result.detail) == ("stochastic", "drops out of range (neutral)")


def test_an_ok_outcome_that_killed_nothing_is_a_failed_check():
    result = check(kill_cow(), KILL_COW, kill_outcome({}, killed=False))
    assert (result.status, result.detail) == ("failed", "not killed")


def test_a_failed_kill_is_failed():
    result = check(kill_cow(), KILL_COW, Outcome(ok=False, error="escaped"))
    assert (result.status, result.detail) == ("failed", "escaped")


def test_a_weapon_that_differs_from_the_action_is_not_a_wrong_tool():
    outcome = kill_outcome({"beef": 1})
    outcome.detail["tool_used"] = "iron_sword"
    assert check(kill_cow(), Action("kill", "cow", tool="stone_sword"), outcome).status == "stochastic"


def test_damage_in_the_step_phase_quarantines_a_kill():
    outcome = kill_outcome({"beef": 1}, events=[{"type": "damage", "phase": "step"}])
    assert check(kill_cow(), KILL_COW, outcome).status == "quarantined"


def test_a_stray_pickup_does_not_quarantine_a_kill():
    outcome = kill_outcome({"beef": 1}, events=[{"type": "stray_pickup", "phase": "step"}])
    assert check(kill_cow(), KILL_COW, outcome).status == "stochastic"


def test_replay_confirms_in_range_and_is_neutral_otherwise_never_a_contradiction():
    rule = kill_cow()
    assert evaluate(rule, KILL_COW, {"killed": True, "drops": {"beef": 2}}) == "confirm"
    assert evaluate(rule, KILL_COW, {"killed": True, "drops": {"beef": 9}}) == "neutral"
    assert evaluate(rule, KILL_COW, {"killed": True, "drops": {"cooked_beef": 1, "beef": 1}}) == "neutral"
    assert evaluate(rule, KILL_COW, {"killed": False, "drops": {"beef": 2}}) == "neutral"


# --- the store ---------------------------------------------------------------------------


def _transition(number, episode_id, drops):
    observed = {"killed": True, "drops": drops}
    return Transition(f"{episode_id}/{number}", episode_id, TRAIN_WORLD, KILL_COW, observed, kind="stochastic")


def test_a_kill_rule_applies_to_a_kill_of_its_mob_with_an_allowed_weapon():
    assert applies(kill_cow(), KILL_COW)
    assert applies(kill_cow(), Action("kill", "cow", tool="stone_sword"))
    assert not applies(kill_cow(), Action("kill", "pig"))
    assert not applies(kill_cow(), Action("mine", "cow"))
    assert not applies(kill_cow(tool_any=["iron_sword"]), Action("kill", "cow", tool="stone_sword"))
    assert applies(kill_cow(tool_any=["iron_sword"]), Action("kill", "cow", tool="iron_sword"))


def test_two_in_range_kills_in_two_episodes_certify_a_hypothesis_kill_rule():
    store = SchemaStore()
    store.add_rule(kill_cow())
    store.record_transition(_transition(1, "e1", {"beef": 2}))
    store.record_transition(_transition(2, "e2", {"beef": 1, "leather": 1}))
    assert store.certify() == ["kill_cow"]


def test_out_of_range_kills_never_demote_a_rule_or_open_an_epoch():
    store = SchemaStore()
    store.add_rule(kill_cow())
    for number, drops in enumerate([{"beef": 9}, {}, {"cooked_beef": 1}], start=1):
        store.record_transition(_transition(number, f"e{number}", drops))

    assert store.certify() == []
    confirmed, contradicted = store.replay(store.rules["kill_cow"])
    assert confirmed == [] and contradicted == []
    assert store.epochs == {} and store.rules["kill_cow"].status == "hypothesis"


# --- the planner -------------------------------------------------------------------------


def test_beef_is_planned_as_kills_with_no_weapon():
    plan = make_plan("beef", 3, {}, [kill_cow()])
    assert [step.action for step in plan] == [Action("kill", "cow", tool=None)] * 3
    assert plan[-1].expected_inventory == {"beef": 3}


def test_a_drop_whose_low_end_is_zero_is_never_planned_through_a_kill():
    with pytest.raises(PlanningError):
        make_plan("leather", 1, {}, [kill_cow()])


def test_cooked_beef_is_kills_then_a_smelt():
    furnace = Rule(
        id="craft_furnace", action={"type": "craft", "item": "furnace"},
        preconditions={"consumes": {"cobblestone": 8}}, effects={"gains": {"furnace": 1}},
    )
    cobble = Rule(
        id="mine_cobblestone", action={"type": "mine", "block": "stone"}, preconditions={"tool_any": []},
        effects={"drops": {"cobblestone": 1}}, check="exact",
    )
    coal = Rule(
        id="mine_coal", action={"type": "mine", "block": "coal_ore"}, preconditions={"tool_any": []},
        effects={"drops": {"coal": 1}}, check="exact",
    )
    rules = [kill_cow(), smelt_rule("beef", "cooked_beef"), furnace, cobble, coal]

    plan = make_plan("cooked_beef", 2, {}, rules)

    kinds = [step.action.kind for step in plan]
    assert kinds.count("kill") == 2 and kinds[-1] == "smelt"
    assert max(i for i, kind in enumerate(kinds) if kind == "kill") < len(kinds) - 1
    assert plan[-1].expected_inventory["cooked_beef"] == 2


def test_a_kill_with_a_weapon_rule_is_planned_with_that_weapon():
    plan = make_plan("beef", 1, {"stone_sword": 1}, [kill_cow(tool_any=["stone_sword"])])
    assert plan[0].action == Action("kill", "cow", tool="stone_sword")


def test_a_rule_of_an_unknown_kind_is_not_planned_as_a_harvest():
    rule = Rule(id="odd", action={"type": "place", "item": "dirt"}, preconditions={}, effects={"gains": {"dirt": 1}})
    with pytest.raises(PlanningError):
        make_plan("dirt", 1, {}, [rule])


def test_a_seen_mob_is_preferred_to_an_unseen_one():
    kill_pig = Rule(
        id="kill_pig", action={"type": "kill", "mob": "pig"}, preconditions={"tool_any": []},
        effects={"killed": True, "drops": {"meat": 1}, "allowed": {"meat": [1, 3]}},
    )
    kill_cow_meat = kill_cow()
    kill_cow_meat.effects = {"killed": True, "drops": {"meat": 1}, "allowed": {"meat": [1, 3]}}
    rules = [kill_cow_meat, kill_pig]

    assert make_plan("meat", 1, {}, rules, visible={"pig": 2})[0].action.target == "pig"
    assert make_plan("meat", 1, {}, rules, visible={"cow": 1})[0].action.target == "cow"


# --- the agent ---------------------------------------------------------------------------


class ScriptedBackend:
    """A natural backend whose every kill answers with `outcome`."""

    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        self.said: list[str] = []
        self.acted: list[Action] = []

    def observe(self):
        return Observation((0, 64, 0), {}, {}, entities={"cow": {"count": 2, "nearest": 5.0, "kind": "animal"}})

    def act(self, action):
        self.acted.append(action)
        return self.outcome

    def say(self, message):
        self.said.append(message)


def _run(tmp_path, outcome, goal_count=1, budget=3):
    llm = LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")
    backend = ScriptedBackend(outcome)
    task = Task("g001", "train", "beef", goal_count, {}, (), budget, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id=TRAIN_WORLD, backend=backend,
        llm=llm, log=EpisodeLog(None, "test", say=backend.say), natural=True, deadline=time.monotonic() + 60,
    )
    store = SchemaStore()
    store.add_rule(kill_cow())
    pursue(episode, store, {"beef": goal_count})
    return episode, store, backend, llm


def test_an_out_of_range_kill_neither_demotes_nor_asks_the_llm(tmp_path):
    outcome = kill_outcome({"beef": 7}, inventory={"beef": 7})

    episode, store, backend, llm = _run(tmp_path, outcome)

    assert llm.calls == [] and episode.revisions == 0
    assert store.rules["kill_cow"].status == "hypothesis" and store.demoted_in == {}
    assert [(t.kind, t.observed) for t in store.transitions] == [("stochastic", {"killed": True, "drops": {"beef": 7}})]
    assert backend.acted == [KILL_COW]


def test_two_escaped_kills_do_not_block_the_rule(tmp_path):
    episode, store, backend, llm = _run(tmp_path, Outcome(ok=False, error="escaped"), goal_count=1, budget=4)

    assert len(backend.acted) >= 3 and episode.blocked_rules == {}


def test_visible_merges_mob_counts_from_the_observation(tmp_path):
    episode, *_ = _run(tmp_path, kill_outcome({"beef": 1}, inventory={"beef": 1}))
    assert agent._visible(episode) == {"cow": 2}


# --- narration ---------------------------------------------------------------------------


def test_narration_of_a_kill_rule_and_step():
    rule = kill_cow()
    assert agent._revision_description(rule) == "kill cow"
    assert agent._hypothesis_text(rule) == "kill cow"
    assert agent._predicts(rule, KILL_COW) == "cow -> drops within beef 1-3, leather 0-2"
    assert agent._summarize(rule).startswith("kill cow -> drops within beef 1-3, leather 0-2")
    task = Task("g001", "train", "beef", 3, {}, (), 10, "test")
    step = make_plan("beef", 3, {}, [rule])
    assert agent._goal_line(task, step) == "Goal: 3 beef, to test kill cow"
