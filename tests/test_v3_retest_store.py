"""v3.0f: a demoted rule is retested as a new rule with its own evidence window."""

import copy
import json

import pytest

from schema_voyager.models import Action, Rule
from schema_voyager.schema_store import SchemaStore, Transition, epoch_key

WORLD = "w"
KEY = epoch_key(WORLD, "smelt", "iron_ingot")
SMELT = Action("smelt", "iron_ingot", fuel="coal", ingredient="raw_iron")
MATCH = {"inventory_delta": {"iron_ingot": 1, "raw_iron": -1}}
WORN_PICKAXE = {"inventory_delta": {"iron_ingot": 1, "raw_iron": -1, "wooden_pickaxe": -1}}


def smelt_rule(rule_id="smelt_iron_ingot", **fields) -> Rule:
    return Rule(
        id=rule_id,
        action={"type": "smelt", "item": "iron_ingot"},
        preconditions={"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "#planks"]},
        effects={"gains": {"iron_ingot": 1}},
        scope=WORLD,
        **fields,
    )


def record(store, tid, episode, observed):
    store.record_transition(Transition(tid, episode, WORLD, SMELT, observed))


def demoted_store(confirmations=0, rule_id="smelt_iron_ingot"):
    """A store whose smelt rule had `confirmations` matches, then a contradiction that demoted it."""
    store = SchemaStore()
    rule = smelt_rule(rule_id)
    store.add_rule(rule)
    for n in range(confirmations):
        record(store, f"w/{n}", f"e{n}", MATCH)
    record(store, "w/bad", "e9", WORN_PICKAXE)
    store.demote(rule.id, "w/bad", WORLD)
    return store, rule


def test_retest_builds_the_copy_and_leaves_the_original_alone():
    store, original = demoted_store(confirmations=3)
    original_before = copy.deepcopy(original)
    demoted_before = copy.deepcopy(store.demoted_in)
    epochs_before = copy.deepcopy(store.epochs)

    retest = store.retest(original.id, WORLD)

    assert store.rules[retest.id] is retest
    assert retest.id == "smelt_iron_ingot.retest@w"
    assert retest.source == "retest" and retest.revision_of == original.id
    assert retest.status == "hypothesis" and retest.scope == WORLD and retest.rank == 0
    assert retest.rationale == "retest of smelt_iron_ingot, demoted by w/bad"
    assert retest.evidence == {"confirmed": [], "contradicted": [], "counterexamples": []}
    assert retest.epoch == 1 and retest.since_seq == len(store.transitions)
    assert (retest.action, retest.preconditions, retest.effects, retest.check) == (
        original.action,
        original.preconditions,
        original.effects,
        original.check,
    )
    assert retest in store.usable_rules(WORLD) and original not in store.usable_rules(WORLD)
    assert original == original_before
    assert store.demoted_in == demoted_before
    assert store.epochs == epochs_before

    retest.preconditions["fuel_any"].append("stick")
    assert original.preconditions["fuel_any"] == ["coal", "#planks"]


def test_retest_ids_append_to_the_full_rule_id():
    store, _ = demoted_store()
    assert store.retest("smelt_iron_ingot", WORLD).id == "smelt_iron_ingot.retest@w"

    store, _ = demoted_store(rule_id="x.rev1@w")
    assert store.retest("x.rev1@w", WORLD).id == "x.rev1@w.retest@w"


def test_retests_of_nested_revisions_get_different_ids_and_both_stay():
    store, _ = demoted_store(rule_id="x.rev1@w")
    nested = smelt_rule("x.rev1@w.rev1@w")
    store.add_rule(nested)
    store.demote(nested.id, "w/bad", WORLD)

    first = store.retest("x.rev1@w", WORLD)
    second = store.retest("x.rev1@w.rev1@w", WORLD)

    assert first.id != second.id
    assert store.rules[first.id] is first and store.rules[second.id] is second


def test_retest_refuses_an_id_that_already_exists():
    store, _ = demoted_store(rule_id="y")
    existing = smelt_rule("y.retest@w")
    store.add_rule(existing)
    existing_before = copy.deepcopy(existing)

    with pytest.raises(ValueError, match="y.retest@w"):
        store.retest("y", WORLD)

    assert store.rules["y.retest@w"] == existing_before


def test_retest_refuses_a_rule_that_is_not_demoted():
    store = SchemaStore()
    store.add_rule(smelt_rule())
    with pytest.raises(ValueError, match="not demoted"):
        store.retest("smelt_iron_ingot", WORLD)


def test_retest_refuses_a_rule_demoted_only_in_another_world():
    store, rule = demoted_store()
    with pytest.raises(ValueError, match="not demoted"):
        store.retest(rule.id, "other")


def test_retest_refuses_a_retest_and_a_second_retest():
    store, rule = demoted_store()
    retest = store.retest(rule.id, WORLD)

    with pytest.raises(ValueError, match="already has a retest"):
        store.retest(rule.id, WORLD)

    record(store, "w/r1", "e10", WORN_PICKAXE)
    store.demote(retest.id, "w/r1", WORLD)
    with pytest.raises(ValueError, match="already a retest"):
        store.retest(retest.id, WORLD)


def test_replay_of_a_fresh_retest_ignores_the_contradiction_and_earlier_confirmations():
    store, original = demoted_store(confirmations=3)
    assert len(store.replay(original)[0]) == 3 and len(store.replay(original)[1]) == 1

    retest = store.retest(original.id, WORLD)

    assert store.replay(retest) == ([], [])


def test_retest_confirmations_come_only_from_transitions_after_it_and_certify_the_copy():
    store, original = demoted_store(confirmations=3)
    retest = store.retest(original.id, WORLD)

    record(store, "w/r1", "e10", MATCH)
    confirmed, contradicted = store.replay(retest)
    assert [t.id for t in confirmed] == ["w/r1"] and contradicted == []
    assert store.certify() == []

    record(store, "w/r2", "e11", MATCH)
    assert store.certify() == [retest.id]
    assert retest.status == "certified" and retest.evidence["confirmed"] == ["w/r1", "w/r2"]
    assert original.status == "demoted"


def test_retest_after_a_demotion_that_opened_an_epoch_keeps_one_epoch():
    """v3-baseline-1: three confirmations, then a contradiction at a worn-out pickaxe."""
    store, original = demoted_store(confirmations=3)
    assert [e.number for e in store.epochs[KEY]] == [1]

    retest = store.retest(original.id, WORLD)

    assert [e.number for e in store.epochs[KEY]] == [1]
    assert retest.epoch == 1


def test_retest_after_a_demotion_that_opened_no_epoch_starts_in_epoch_zero():
    """m4-2024: no confirmation, then a contradiction with an empty observed delta."""
    store = SchemaStore()
    rule = smelt_rule()
    store.add_rule(rule)
    record(store, "w/bad", "e1", {})
    store.demote(rule.id, "w/bad", WORLD)
    assert store.epochs == {}

    retest = store.retest(rule.id, WORLD)

    assert retest.epoch == 0 and store.epochs == {}
    assert store.replay(retest) == ([], [])


def test_a_retest_with_no_window_confirmation_opens_no_epoch_when_demoted():
    store, original = demoted_store(confirmations=3)
    retest = store.retest(original.id, WORLD)

    record(store, "w/r1", "e10", WORN_PICKAXE)
    store.demote(retest.id, "w/r1", WORLD)

    assert [e.number for e in store.epochs[KEY]] == [1]


def test_a_retest_with_a_window_confirmation_opens_an_epoch_when_demoted():
    store, original = demoted_store(confirmations=3)
    retest = store.retest(original.id, WORLD)

    record(store, "w/r1", "e10", MATCH)
    record(store, "w/r2", "e10", WORN_PICKAXE)
    store.demote(retest.id, "w/r2", WORLD)

    assert [e.number for e in store.epochs[KEY]] == [1, 2]
    assert store.epochs[KEY][1].rule_id == retest.id


def test_a_revision_added_after_a_retest_is_stamped_with_the_current_epoch_and_has_no_window():
    store, original = demoted_store(confirmations=3)
    store.retest(original.id, WORLD)

    revision = smelt_rule("smelt_iron_ingot.rev1@w", source="revision", revision_of=original.id)
    store.add_rule(revision)

    assert revision.epoch == 1 and revision.since_seq == 0


def test_save_and_load_round_trip_since_seq_and_the_retest_source(tmp_path):
    store, original = demoted_store(confirmations=3)
    retest = store.retest(original.id, WORLD)
    path = tmp_path / "store.json"

    store.save(path)
    loaded = SchemaStore.load(path)

    assert loaded.rules[retest.id].since_seq == retest.since_seq == 4
    assert loaded.rules[retest.id].source == "retest"
    assert loaded.rules[retest.id] == retest


def test_a_saved_rule_without_since_seq_loads_with_zero(tmp_path):
    store, original = demoted_store()
    path = tmp_path / "store.json"
    store.save(path)
    payload = json.loads(path.read_text())
    (rule_payload,) = payload["rules"]
    assert rule_payload["id"] == original.id
    del rule_payload["since_seq"]
    path.write_text(json.dumps(payload))

    assert SchemaStore.load(path).rules[original.id].since_seq == 0
