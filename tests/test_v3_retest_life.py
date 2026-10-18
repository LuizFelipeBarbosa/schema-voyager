"""v3.0f: the life retests a demoted rule whose product nothing else makes, reports the outcome, and
change_report counts what a retest repeats."""

import json
import time
from types import SimpleNamespace

import pytest

from schema_voyager import change_report
from schema_voyager.agent import run_schemavoyager
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.life import ChangeTrigger, LifeConfig, LifeHooks, _Life
from schema_voyager.llm import LLM
from schema_voyager.models import Action, Observation, Rule, Task
from schema_voyager.schema_store import SchemaStore, Transition, epoch_key
from schema_voyager.trace import EpisodeLog

WORLD = "life-retest"
OBSERVATION = Observation(position=(0, 0, 0), inventory={}, nearby={})
SMELT = Action("smelt", "iron_ingot", fuel="coal", ingredient="raw_iron")
SMELT_MATCH = {"inventory_delta": {"iron_ingot": 1, "raw_iron": -1}}
SMELT_WORN_PICKAXE = {"inventory_delta": {"iron_ingot": 1, "raw_iron": -1, "wooden_pickaxe": -1}}
PICKAXES = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe"]
COAL_KEY = epoch_key(WORLD, "mine", "coal_ore")


class CountingLLM(LLM):
    """The fake LLM, except that repairs are counted and propose nothing."""

    def __init__(self, tmp_path):
        super().__init__(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")
        self.repair_calls: list[str] = []

    def repair_rule_v2(self, rule, dead_items, obtainable, world_id):
        self.repair_calls.append(rule.id)
        return []


class NoRevisionLLM(CountingLLM):
    def revise_rule_v2(self, rule, evidence_summary, counterexample, known_items, world_id):
        return []


class FakeLog:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


# -- fixtures ----------------------------------------------------------------------------


def smelt_rule(rule_id="smelt_iron_ingot") -> Rule:
    return Rule(
        id=rule_id, action={"type": "smelt", "item": "iron_ingot"},
        preconditions={"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "#planks"]},
        effects={"gains": {"iron_ingot": 1}}, scope=WORLD,
    )


def mine_rule(rule_id, block, drop, tools=PICKAXES, **fields) -> Rule:
    return Rule(
        id=rule_id, action={"type": "mine", "block": block}, preconditions={"tool_any": list(tools)},
        effects={"block_removed": True, "drops": {drop: 1}}, scope=WORLD, **fields,
    )


def craft_rule(rule_id, item, consumes) -> Rule:
    return Rule(
        id=rule_id, action={"type": "craft", "item": item},
        preconditions={"consumes": consumes, "station": None}, effects={"gains": {item: 1}}, scope=WORLD,
    )


def record(store, transition_id, episode, action, observed):
    store.record_transition(Transition(transition_id, episode, WORLD, action, observed))


def mine(tool, drops):
    return Action("mine", "coal_ore", tool=tool), {"block_removed": True, "drops": drops}


def demote_after_confirmations(store, rule, action, confirmed, contradicted, episodes=2):
    """`rule` is confirmed in `episodes` episodes, then contradicted once, then demoted."""
    store.add_rule(rule)
    for n in range(episodes):
        record(store, f"{rule.id}/c{n}", f"e{n}", action, confirmed)
    record(store, f"{rule.id}/bad", "e9", action, contradicted)
    store.demote(rule.id, f"{rule.id}/bad", WORLD)


def make_life(tmp_path, llm=None, resume=False):
    chat = []
    life = _Life(
        SimpleNamespace(say=chat.append), llm or CountingLLM(tmp_path),
        LifeConfig(run_dir=tmp_path / "run", world_id=WORLD), LifeHooks(), resume=resume,
    )
    return life, chat


def smelt_life(tmp_path, llm=None):
    """v3-baseline-1: a smelt rule confirmed in two episodes, contradicted by a worn-out pickaxe, demoted
    (epoch 1). A usable pickaxe rule consumes its ingots."""
    life, chat = make_life(tmp_path, llm)
    demote_after_confirmations(life.store, smelt_rule(), SMELT, SMELT_MATCH, SMELT_WORN_PICKAXE)
    life.store.add_rule(craft_rule("craft_iron_pickaxe", "iron_pickaxe", {"iron_ingot": 3}))
    life.asked.add("iron_ingot")
    return life, chat


def run_goal(life, world, item, inventory):
    world.inventory.update(inventory)
    observation = world.begin_episode()
    task = Task("g001", "train", item, 1, dict(observation.inventory), (), 12, f"Obtain 1 {item}")
    episode = Episode(
        episode_id=f"D/life/g001-{item}", system="D", task=task, layout_seed=0, world_id=WORLD, backend=world,
        llm=life.llm, log=EpisodeLog(None, "retest", say=world.say), inventory=dict(observation.inventory), natural=True,
    )
    run_schemavoyager(episode, life.store)
    return [event for event in episode.log.events if event["kind"] == "step"]


def retest_of(store, rule_id):
    (retest,) = [r for r in store.rules.values() if r.source == "retest" and r.revision_of == rule_id]
    return retest


# -- the life ------------------------------------------------------------------------------


def test_a_false_demotion_is_retested_with_no_repair_and_the_copy_matches(tmp_path, capsys):
    life, chat = smelt_life(tmp_path)
    epochs_before = {key: list(opened) for key, opened in life.store.epochs.items()}

    assert life._repair_dead_ingredients(OBSERVATION) is True

    retest = retest_of(life.store, "smelt_iron_ingot")
    demoted_by = life.store.demoted_in[WORLD]["smelt_iron_ingot"]
    assert life.llm.repair_calls == []
    assert retest in life.store.usable_rules(WORLD)
    assert life._retests_unreported == [{
        "rule": "smelt_iron_ingot", "retest": retest.id, "outcome": "started", "transition": demoted_by, "item": "iron_ingot",
    }]
    assert chat == ["Trying smelt iron_ingot again: nothing else I know gives iron_ingot."]
    assert f"[retest] {retest.id}" in capsys.readouterr().out

    world = NaturalWorld(seed=3)
    (step,) = run_goal(life, world, "iron_ingot", {"raw_iron": 1, "coal": 1, "furnace": 1})
    assert step["rule"] == retest.id and step["check"] == "match"

    life._retests_unreported.clear()
    assert life._repair_dead_ingredients(OBSERVATION) is False

    (match,) = life._retests_unreported
    assert match == {
        "rule": "smelt_iron_ingot", "retest": retest.id, "outcome": "match",
        "transition": life.store.replay(retest)[0][0].id, "item": None,
    }
    assert chat[-1] == "smelt iron_ingot works again."
    assert life.retests == {retest.id: "match"}
    assert "smelt_iron_ingot" in life.store.demoted_in[WORLD] and life.store.rules["smelt_iron_ingot"].status == "demoted"
    assert life.store.epochs == epochs_before and len(life.store.epochs[epoch_key(WORLD, "smelt", "iron_ingot")]) == 1
    assert life.llm.repair_calls == []


def test_a_genuine_change_mismatches_once_and_is_never_retested_again(tmp_path):
    life, chat = make_life(tmp_path, NoRevisionLLM(tmp_path))
    action, confirmed = mine("wooden_pickaxe", {"coal": 1})
    demote_after_confirmations(
        life.store, mine_rule("mine_coal_ore", "coal_ore", "coal"), action, confirmed, mine("wooden_pickaxe", {})[1], episodes=1
    )
    life.store.add_rule(craft_rule("craft_torch", "torch", {"coal": 1}))
    life.asked.add("coal")
    assert [e.number for e in life.store.epochs[COAL_KEY]] == [1]

    assert life._repair_dead_ingredients(OBSERVATION) is True
    retest = retest_of(life.store, "mine_coal_ore")
    assert life.llm.repair_calls == [] and life._retests_unreported[0]["item"] == "coal"

    # r2: a wooden pickaxe no longer drops coal. Coal ore is placed next to the bot, which has only a wooden pickaxe.
    world = NaturalWorld(seed=3)
    world.set_world_rules(("r2",))
    x, y, z = world._cell
    world._changes[(x + 1, y, z)] = "coal_ore"
    steps = run_goal(life, world, "coal", {"wooden_pickaxe": 1})
    assert [(s["rule"], s["check"]) for s in steps] == [(retest.id, "mismatch")]

    life._retests_unreported.clear()
    # the torch rule has no coal source, so it is sent to repair; the fake proposes nothing, so nothing is added
    assert life._repair_dead_ingredients(OBSERVATION) is False

    (mismatch,) = life._retests_unreported
    assert mismatch == {
        "rule": "mine_coal_ore", "retest": retest.id, "outcome": "mismatch",
        "transition": life.store.demoted_in[WORLD][retest.id], "item": None,
    }
    assert chat[-2:] == ["mine coal_ore really changed; I won't use it again.", "Rethinking craft torch: nothing I know gives coal."]
    assert life.retests == {retest.id: "mismatch"}
    assert [e.number for e in life.store.epochs[COAL_KEY]] == [1]
    assert life.llm.repair_calls == ["craft_torch"]
    retests_before = [r.id for r in life.store.rules.values() if r.source == "retest"]

    assert life._repair_dead_ingredients(OBSERVATION) is False
    assert [r.id for r in life.store.rules.values() if r.source == "retest"] == retests_before
    assert life.llm.repair_calls == ["craft_torch"]


def test_a_chain_of_demoted_rules_is_retested_in_passes_before_any_repair(tmp_path):
    life, chat = smelt_life(tmp_path)
    action = Action("mine", "iron_ore", tool="stone_pickaxe")
    demote_after_confirmations(
        life.store, mine_rule("mine_iron_ore", "iron_ore", "raw_iron", PICKAXES[1:]), action,
        {"block_removed": True, "drops": {"raw_iron": 1}}, {"block_removed": True, "drops": {}},
    )
    life.asked.add("raw_iron")

    assert life._repair_dead_ingredients(OBSERVATION) is True

    assert [(e["rule"], e["outcome"], e["item"]) for e in life._retests_unreported] == [
        ("smelt_iron_ingot", "started", "iron_ingot"), ("mine_iron_ore", "started", "raw_iron"),
    ]
    assert chat == [
        "Trying smelt iron_ingot again: nothing else I know gives iron_ingot.",
        "Trying mine iron_ore again: nothing else I know gives raw_iron.",
    ]
    assert life.llm.repair_calls == []


def test_the_producer_with_most_confirmations_is_retested_and_a_rule_out_of_scope_or_not_asked_is_not(tmp_path):
    life, _ = smelt_life(tmp_path)
    rival = smelt_rule("smelt_iron_ingot_b")
    demote_after_confirmations(life.store, rival, SMELT, SMELT_MATCH, SMELT_WORN_PICKAXE, episodes=0)
    life.asked.discard("iron_ingot")
    assert life._repair_dead_ingredients(OBSERVATION) is False and life._retests_unreported == []

    life.asked.add("iron_ingot")
    life._repair_dead_ingredients(OBSERVATION)

    assert [e["rule"] for e in life._retests_unreported] == ["smelt_iron_ingot"]


# -- the change trigger -------------------------------------------------------------------


def coal_store_with_retest():
    """mine_coal_ore: confirmed twice with a wooden pickaxe, contradicted, demoted (epoch 1), then retested."""
    store = SchemaStore()
    action, confirmed = mine("wooden_pickaxe", {"coal": 1})
    original = mine_rule("mine_coal_ore", "coal_ore", "coal")
    demote_after_confirmations(store, original, action, confirmed, mine("wooden_pickaxe", {})[1])
    original.evidence["confirmed"] = [f"{original.id}/c0", f"{original.id}/c1"]
    return store, original, store.retest(original.id, WORLD)


def r2_trigger(tmp_path, store):
    trigger = ChangeTrigger("r2", tmp_path, lambda change: None)
    trigger.bind_store(store)
    trigger.logged.append({"t": time.time(), "change": "r2", "action": "mine", "subject": "coal_ore", "goal": "g008"})
    return trigger


def test_a_certified_retest_keeps_the_change_open_but_a_certified_revision_closes_it(tmp_path):
    store, _, retest = coal_store_with_retest()
    action, confirmed = mine("wooden_pickaxe", {"coal": 1})
    record(store, "w/r1", "e10", action, confirmed)
    record(store, "w/r2", "e11", action, confirmed)
    assert store.certify() == [retest.id] and retest.status == "certified" and retest.epoch == 1
    trigger = r2_trigger(tmp_path, store)

    assert trigger._open_change() == "r2"

    revision = mine_rule("mine_coal_ore.rev1@w", "coal_ore", "coal", PICKAXES[1:], source="revision", revision_of="mine_coal_ore")
    store.add_rule(revision)
    revision.status = "certified"
    assert revision.epoch == 1
    assert trigger._open_change() is None


def test_the_trigger_does_not_fire_r2_on_a_retest_that_has_not_run(tmp_path):
    store, original, retest = coal_store_with_retest()
    action = Action("mine", "coal_ore", tool="wooden_pickaxe")
    episode = SimpleNamespace(task=SimpleNamespace(task_id="g008"), log=SimpleNamespace(event=lambda kind, **data: None))
    switched = []

    for rule, expected in ((retest, []), (original, ["r2"])):
        trigger = ChangeTrigger("r2", tmp_path / rule.id, switched.append)
        trigger(episode, SimpleNamespace(action=action, rule=rule), [])
        assert switched == expected
        switched.clear()
    assert retest.evidence["confirmed"] == [] and original.evidence["confirmed"]


# -- resume --------------------------------------------------------------------------------


def test_a_resumed_life_neither_retests_nor_reports_an_outcome_again(tmp_path):
    life, _ = smelt_life(tmp_path)
    life._repair_dead_ingredients(OBSERVATION)
    retest = retest_of(life.store, "smelt_iron_ingot")
    record(life.store, "w/r1", "e10", SMELT, SMELT_MATCH)
    life._repair_dead_ingredients(OBSERVATION)
    assert life.retests == {retest.id: "match"}
    life._save({"index": 1})
    assert json.loads((tmp_path / "run" / "life_state.json").read_text())["retests"] == {retest.id: "match"}

    resumed, chat = make_life(tmp_path, resume=True)

    assert resumed.retests == {retest.id: "match"} and resumed._retests_unreported == []
    assert resumed._repair_dead_ingredients(OBSERVATION) is False
    assert resumed._retests_unreported == [] and chat == []
    assert [r.id for r in resumed.store.rules.values() if r.source == "retest"] == [retest.id]


def test_a_life_saved_before_retests_resumes_with_none(tmp_path):
    life, _ = make_life(tmp_path)
    life._save({"index": 1})
    state_path = tmp_path / "run" / "life_state.json"
    state = json.loads(state_path.read_text())
    del state["retests"]
    state_path.write_text(json.dumps(state))

    assert make_life(tmp_path, resume=True)[0].retests == {}


# -- events and records --------------------------------------------------------------------


def test_the_flush_helpers_write_repairs_before_retests_and_empty_both_queues(tmp_path):
    life, _ = make_life(tmp_path)
    repair = {"rule": "r", "dead_items": ["x"], "proposed": [], "skipped": []}
    retest = {"rule": "a", "retest": "a.retest@w", "outcome": "started", "transition": "t", "item": "x"}
    life._repairs_unreported.append(repair)
    life._retests_unreported.append(retest)
    log = FakeLog()

    life._log_unreported(log)
    life._log_unreported(log)  # logging does not clear

    assert log.events == [("repair", repair), ("retest", retest)] * 2
    record_ = {}
    life._attach_unreported(record_)
    assert record_ == {"repairs": [repair], "retests": [retest]}
    assert life._repairs_unreported == [] and life._retests_unreported == []


def test_a_record_has_a_key_only_for_a_queue_that_is_not_empty(tmp_path):
    life, _ = make_life(tmp_path)
    repair = {"rule": "r", "dead_items": ["x"], "proposed": [], "skipped": []}
    retest = {"rule": "a", "retest": "a.retest@w", "outcome": "match", "transition": "t", "item": None}
    empty, only_repairs, only_retests = {}, {}, {}

    life._attach_unreported(empty)
    life._repairs_unreported.append(repair)
    life._attach_unreported(only_repairs)
    life._retests_unreported.append(retest)
    life._attach_unreported(only_retests)

    assert (empty, only_repairs, only_retests) == ({}, {"repairs": [repair]}, {"retests": [retest]})


# -- change_report -------------------------------------------------------------------------


def step_event(rule, tool="wooden_pickaxe", ok=True):
    return {"kind": "step", "ok": ok, "rule": rule, "action_obj": {"kind": "mine", "target": "coal_ore", "tool": tool}}


def write_run(run_dir, store, goals, life_calls=None):
    """A run with r2 switched on before g008. `goals` maps a goal number to its events (g008 starts with world_change)."""
    (run_dir / "episodes").mkdir(parents=True)
    (run_dir / "changes.jsonl").write_text(
        json.dumps({"t": 1.0, "change": "r2", "action": "mine", "subject": "coal_ore", "goal": "g008"}) + "\n"
    )
    for number, events in goals.items():
        lines = [json.dumps(event) for event in events]
        (run_dir / "episodes" / f"D_life_g{number:03d}_coal.jsonl").write_text("\n".join(lines) + "\n")
    store.save(run_dir / "store.json")
    (run_dir / "life.jsonl").write_text(
        "".join(json.dumps({"index": number, "llm_calls": 0}) + "\n" for number in goals)
    )


def reported(run_dir, capsys):
    """(exit code, the lines of the single change's report)"""
    code = change_report.main([str(run_dir)])
    return code, capsys.readouterr().out.splitlines()


def story_store():
    """mine_coal_ore: two confirmations, then the r2 contradiction (epoch 1, demoted), a revision certified
    in epoch 1, and a retest of mine_coal_ore."""
    store, original, retest = coal_store_with_retest()
    revision = mine_rule("mine_coal_ore.rev1@w", "coal_ore", "coal", PICKAXES[1:], source="revision", revision_of=original.id)
    store.add_rule(revision)
    for n in (1, 2):
        record(store, f"s{n}", f"e{n}", *mine("stone_pickaxe", {"coal": 1}))
    store.certify()
    assert revision.status == "certified" and revision.epoch == 1
    return store, original, retest, revision


def the_story(contradicted, revision, later):
    """g008: the change, the mistake, its contradiction and one revise call; g009: `later` events."""
    return {
        8: [
            {"kind": "world_change", "change": "r2"},
            step_event(contradicted),
            {"kind": "contradiction", "rule": contradicted, "transition": "mine_coal_ore/bad"},
            {"kind": "revision", "rule": contradicted, "tokens": 90},
            {"kind": "episode_end", "llm_calls": 1},
        ],
        9: [*later, step_event(revision, "stone_pickaxe"), {"kind": "episode_end", "llm_calls": 0}],
    }


def test_a_repeat_by_a_retest_is_counted_and_a_retest_with_another_tool_is_not(tmp_path, capsys):
    store, original, retest, revision = story_store()

    write_run(tmp_path / "repeat", store, the_story(original.id, revision.id, [step_event(retest.id)]))
    code, lines = reported(tmp_path / "repeat", capsys)
    assert code == 1
    assert lines[5].startswith("FAIL (e)") and lines[5].endswith("1 times (1 by a retest)")

    write_run(tmp_path / "stone", store, the_story(original.id, revision.id, [step_event(retest.id, "stone_pickaxe")]))
    code, lines = reported(tmp_path / "stone", capsys)
    assert lines[5].startswith("PASS (e)") and lines[5].endswith("0 times")
    assert code == 0


def test_a_certified_retest_is_not_the_certified_revision(tmp_path, capsys):
    store, original, retest, revision = story_store()
    del store.rules[revision.id]
    for n in (1, 2):
        record(store, f"r{n}", f"e{n}", *mine("stone_pickaxe", {"coal": 1}))
    retest.status = "certified"

    write_run(tmp_path, store, the_story(original.id, retest.id, []))
    code, lines = reported(tmp_path, capsys)

    assert lines[3].startswith("FAIL (c) no revision certified")
    assert code == 1


def test_a_second_contradiction_and_revision_by_the_retest_leave_a_and_b_passing(tmp_path, capsys):
    store, original, retest, revision = story_store()
    later = [
        step_event(retest.id, "stone_pickaxe"),
        {"kind": "contradiction", "rule": retest.id, "transition": "mine_coal_ore/bad"},
        {"kind": "revision", "rule": retest.id, "tokens": 50},
    ]
    write_run(tmp_path, store, the_story(original.id, revision.id, later))

    _, lines = reported(tmp_path, capsys)

    assert lines[1].startswith("PASS (a)")
    assert lines[2] == "PASS (b) LLM calls for the revision: 1 (90 tokens); 1 more after its retest"


def test_a_retest_started_before_the_change_can_be_the_first_rule_it_contradicts(tmp_path, capsys):
    """An earlier false demotion (no epoch), its retest certified, then r2 contradicts the retest: epoch 1, a
    revision of the retest certified in it, and all five criteria pass."""
    store = SchemaStore()
    old = mine_rule("mine_coal_ore", "coal_ore", "coal")
    old_action, _ = mine("wooden_pickaxe", {})
    store.add_rule(old)
    record(store, "old/bad", "e0", old_action, {"block_removed": True, "drops": {}})
    store.demote(old.id, "old/bad", WORLD)
    assert COAL_KEY not in store.epochs
    retest = store.retest(old.id, WORLD)
    for n in (1, 2):
        record(store, f"r{n}", f"e{n}", *mine("wooden_pickaxe", {"coal": 1}))
    assert store.certify() == [retest.id]
    record(store, "retest/bad", "e5", *mine("wooden_pickaxe", {}))
    store.demote(retest.id, "retest/bad", WORLD)
    assert [e.number for e in store.epochs[COAL_KEY]] == [1]
    revision = mine_rule("mine_coal_ore.retest.rev1@w", "coal_ore", "coal", PICKAXES[1:], source="revision", revision_of=retest.id)
    store.add_rule(revision)
    for n in (6, 7):
        record(store, f"s{n}", f"e{n}", *mine("stone_pickaxe", {"coal": 1}))
    store.certify()

    stories = the_story(retest.id, revision.id, [])
    stories[8][2]["transition"] = "retest/bad"
    write_run(tmp_path, store, stories)
    code, lines = reported(tmp_path, capsys)

    assert [line.split()[0] for line in lines[1:6]] == ["PASS"] * 5, lines
    assert code == 0
