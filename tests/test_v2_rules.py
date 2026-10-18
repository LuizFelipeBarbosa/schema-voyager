import json

import pytest

from schema_voyager import normalize
from schema_voyager.models import Action, Outcome, Rule
from schema_voyager.normalize import counts, family, members, resolve, species, species_mismatch
from schema_voyager.schema_store import SchemaStore, Transition, applies, epoch_key
from schema_voyager.verifier import QUARANTINE_EVENTS, check, evaluate, kind_of, predict

ALL_PICKAXES = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe"]
STONE_TIER = ["stone_pickaxe", "iron_pickaxe"]


# ---- helpers -----------------------------------------------------------------


def mine_rule(subject, drops, tools=(), **fields) -> Rule:
    return Rule(
        id=fields.pop("id", f"mine_{subject}"),
        action={"type": "mine", "block": subject},
        preconditions={"tool_any": list(tools)},
        effects={"block_removed": True, "drops": drops},
        **fields,
    )


def craft_rule(subject, consumes, gains, station=None, **fields) -> Rule:
    return Rule(
        id=fields.pop("id", f"craft_{subject}"),
        action={"type": "craft", "item": subject},
        preconditions={"consumes": consumes, "station": station},
        effects={"gains": gains},
        **fields,
    )


def smelt_rule() -> Rule:
    return Rule(
        id="smelt_iron_ingot",
        action={"type": "smelt", "item": "iron_ingot"},
        preconditions={"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "#planks"]},
        effects={"gains": {"iron_ingot": 1}},
    )


def event(type_, phase=None, **extra):
    return {"seq": 1, "type": type_, **({"phase": phase} if phase else {}), **extra}


LOG_RULE = mine_rule("#log", {"#log": 1})
PLANKS_RULE = craft_rule("#planks", {"#log": 1}, {"#planks": 4})
MINE_LOG = Action("mine", "#log")
CRAFT_PLANKS = Action("craft", "#planks")


def mined_log(**fields) -> Outcome:
    return Outcome(ok=True, block_removed=True, drops={"birch_log": 1}, **fields)


# ---- normalize ---------------------------------------------------------------


def test_family_maps_raw_ids_to_groups_and_variants():
    assert family("birch_log") == "#log"
    assert family("stripped_dark_oak_wood") == "#log"
    assert family("cherry_planks") == "#planks"
    assert family("azalea_leaves") == "#leaves"
    assert family("spruce_sapling") == "#sapling"
    assert family("mangrove_propagule") == "#sapling"
    assert family("cobbled_deepslate") == "#cobblestone"
    assert family("deepslate_coal_ore") == "coal_ore"
    assert family("deepslate_lapis_ore") == "lapis_ore"
    assert family("stick") == "stick"
    assert family("stone") == "stone" and family("deepslate") == "deepslate"


@pytest.mark.parametrize("name", ["#log", "coal_ore", "stick", "oak_log", "never_heard_of_it"])
def test_family_is_idempotent(name):
    assert family(family(name)) == family(name)


def test_members_expand_groups_and_families_sorted():
    assert members("coal_ore") == ["coal_ore", "deepslate_coal_ore"]
    assert members("stone") == ["stone"]
    assert members("stick") == ["stick"]
    logs = members("#log")
    assert logs == sorted(logs)
    assert {"birch_log", "oak_wood", "stripped_cherry_log", "stripped_dark_oak_wood"} <= set(logs)
    assert len(logs) == 4 * len(normalize.SPECIES)
    assert members("#planks") == sorted(f"{s}_planks" for s in normalize.SPECIES)
    assert {"cobblestone", "cobbled_deepslate", "blackstone"} == set(members("#cobblestone"))
    assert {"azalea_leaves", "flowering_azalea_leaves", "oak_leaves"} <= set(members("#leaves"))


def test_every_member_maps_back_to_its_group():
    for name in ("#log", "#planks", "#leaves", "#sapling", "#cobblestone", "coal_ore", "iron_ore", "emerald_ore"):
        assert all(family(raw) == name for raw in members(name)), name


def test_species_list_is_the_overworld_set():
    assert set(normalize.SPECIES) == {
        "oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry",
    }


def test_counts_sum_by_family_drop_zeros_and_sort():
    assert counts({"birch_planks": 4, "oak_planks": 2, "stick": 1}) == {"#planks": 6, "stick": 1}
    assert counts({"birch_log": -1, "birch_planks": 4}) == {"#log": -1, "#planks": 4}
    assert counts({"oak_log": -1, "birch_log": 1, "stick": 0}) == {}
    assert list(counts({"stick": 1, "birch_planks": 1})) == ["#planks", "stick"]


def test_counts_is_idempotent():
    once = counts({"birch_planks": 4, "oak_planks": 2, "stick": 1, "deepslate_coal_ore": 2})
    assert counts(once) == once


def test_species():
    assert species("birch_planks") == "birch"
    assert species("stripped_dark_oak_log") == "dark_oak"
    assert species("dark_oak_leaves") == "dark_oak"
    assert species("stick") is None
    assert species("#planks") is None
    assert species("azalea_leaves") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"birch_log": -1, "birch_planks": 4}, None),
        ({"oak_log": -1, "birch_log": -1, "oak_planks": 4, "birch_planks": 4}, None),
        ({"oak_planks": -3, "stick": -2, "wooden_pickaxe": 1}, None),
        ({"birch_log": -1, "oak_planks": 4}, "birch_log -> oak_planks"),
    ],
)
def test_species_mismatch(raw, expected):
    assert species_mismatch(raw) == expected


def test_resolve_picks_the_species_held_most():
    assert resolve("#planks", {"oak_log": 1, "birch_log": 3, "spruce_planks": 9}) == "birch_planks"
    assert resolve("#planks", {"oak_log": 2, "birch_log": 2}) == "birch_planks"  # ties by name
    assert resolve("#log", {"oak_log": 1, "birch_log": 3}) == "birch_log"
    assert resolve("#planks", {"oak_planks": 1, "birch_planks": 4, "stick": 2}) == "birch_planks"
    assert resolve("coal", {"oak_log": 1}) == "coal"
    assert resolve("birch_planks", {}) == "birch_planks"


def test_resolve_raises_when_no_species_is_held():
    with pytest.raises(ValueError):
        resolve("#planks", {"stick": 4})
    with pytest.raises(ValueError):
        resolve("#log", {})


# ---- check: kinds, statuses ----------------------------------------------------


def test_exact_mine_matches_and_normalizes_both_sides():
    result = check(LOG_RULE, MINE_LOG, mined_log())
    assert result.status == "match" and result.kind == "exact"
    assert result.predicted == result.observed == {"block_removed": True, "drops": {"#log": 1}}


def test_v1_raw_rule_still_matches_raw_observation():
    rule = mine_rule("oak_log", {"oak_log": 1})
    outcome = Outcome(ok=True, block_removed=True, drops={"oak_log": 1})
    result = check(rule, Action("mine", "oak_log"), outcome)
    assert result.status == "match"
    assert result.observed == predict(rule, Action("mine", "oak_log")) == {
        "block_removed": True, "drops": {"#log": 1},
    }


def test_exact_mismatch_even_for_a_certified_rule():
    rule = mine_rule("coal_ore", {"coal": 1}, ALL_PICKAXES, status="certified")
    action = Action("mine", "coal_ore", tool="wooden_pickaxe")
    result = check(rule, action, Outcome(ok=True, block_removed=True, drops={}))
    assert result.status == "mismatch"
    assert "drops" in result.detail


def test_pickup_is_tolerant():
    rule = mine_rule("coal_ore", {"coal": 1}, ALL_PICKAXES)
    outcome = Outcome(ok=True, block_removed=True, drops={"coal": 1}, picked_up={})
    assert check(rule, Action("mine", "deepslate_coal_ore", tool="stone_pickaxe"), outcome).status == "match"


def test_variant_family_applies_and_matches():
    rule = mine_rule("coal_ore", {"coal": 1}, ALL_PICKAXES)
    action = Action("mine", "deepslate_coal_ore", tool="stone_pickaxe")
    assert applies(rule, action)
    assert not applies(rule, Action("mine", "iron_ore", tool="stone_pickaxe"))
    assert not applies(rule, Action("mine", "coal_ore", tool="golden_pickaxe"))


def test_stochastic_in_range_is_stochastic_not_match():
    rule = mine_rule("gravel", {"gravel": 1})
    rule.effects["allowed"] = {"gravel": [0, 1], "flint": [0, 1]}
    action = Action("mine", "gravel")
    assert kind_of(rule, action) == "stochastic"  # the whitelist wins over check="exact"
    assert predict(rule, action) == {"block_removed": True, "allowed": {"flint": [0, 1], "gravel": [0, 1]}}
    result = check(rule, action, Outcome(ok=True, block_removed=True, drops={"flint": 1}))
    assert result.status == "stochastic" and result.kind == "stochastic"
    assert "in range" in result.detail
    assert evaluate(rule, action, result.observed) == "confirm"


def test_stochastic_out_of_range_is_never_mismatch():
    rule = mine_rule("gravel", {"gravel": 1})
    action = Action("mine", "gravel")
    result = check(rule, action, Outcome(ok=True, block_removed=True, drops={"gravel": 3, "dirt": 1}))
    assert result.status == "stochastic"
    assert "out of range" in result.detail
    assert evaluate(rule, action, result.observed) == "neutral"


def test_stochastic_allowed_is_derived_from_drops_and_merges_families():
    rule = mine_rule("#leaves", {"stick": 2, "#sapling": 1})
    assert predict(rule, Action("mine", "oak_leaves")) == {
        "block_removed": True, "allowed": {"#sapling": [0, 1], "stick": [0, 2]},
    }
    outcome = Outcome(ok=True, block_removed=True, drops={"oak_sapling": 1})
    assert check(rule, Action("mine", "oak_leaves"), outcome).status == "stochastic"


def test_declared_stochastic_applies_only_outside_the_whitelist():
    action = Action("mine", "mystery_block")
    assert kind_of(mine_rule("mystery_block", {"x": 1}), action) == "exact"
    assert kind_of(mine_rule("mystery_block", {"x": 1}, check="stochastic"), action) == "stochastic"
    assert kind_of(mine_rule("stone", {"#cobblestone": 1}, check="stochastic"), Action("mine", "stone")) == "exact"
    assert kind_of(mine_rule("copper_ore", {"raw_copper": 2}), Action("mine", "copper_ore")) == "stochastic"
    assert kind_of(PLANKS_RULE, CRAFT_PLANKS) == "exact"


@pytest.mark.parametrize("event_type", sorted(QUARANTINE_EVENTS))
def test_each_step_phase_event_quarantines(event_type):
    result = check(LOG_RULE, MINE_LOG, mined_log(events=[event(event_type, "step")]))
    assert result.status == "quarantined" and result.kind == "quarantined"


def test_quarantine_events_vocabulary():
    assert QUARANTINE_EVENTS == {"damage", "death", "respawn", "target_despawned", "interference"}


def test_event_without_phase_counts_as_step():
    assert check(LOG_RULE, MINE_LOG, mined_log(events=[event("damage")])).status == "quarantined"


def test_travel_phase_damage_is_not_quarantined():
    result = check(LOG_RULE, MINE_LOG, mined_log(events=[event("damage", "travel", amount=2.0)]))
    assert result.status == "match"


@pytest.mark.parametrize("event_type", ["death", "respawn"])
@pytest.mark.parametrize("phase", ["travel", "step"])
def test_death_and_respawn_quarantine_in_either_phase(event_type, phase):
    assert check(LOG_RULE, MINE_LOG, mined_log(events=[event(event_type, phase)])).status == "quarantined"


def test_hazard_events_do_not_quarantine():
    assert check(LOG_RULE, MINE_LOG, mined_log(events=[event("hazard", "step", kind="lava")])).status == "match"


def test_stray_pickup_quarantines_craft_and_smelt_but_not_mine():
    stray = [event("stray_pickup", "step", items={"dirt": 1})]
    assert check(LOG_RULE, MINE_LOG, mined_log(events=stray)).status == "match"
    crafted = Outcome(ok=True, inventory_delta={"birch_log": -1, "birch_planks": 4}, events=stray)
    assert check(PLANKS_RULE, CRAFT_PLANKS, crafted).status == "quarantined"
    smelted = Outcome(ok=True, inventory_delta={"raw_iron": -1, "iron_ingot": 1}, events=stray)
    assert check(smelt_rule(), Action("smelt", "iron_ingot", fuel="coal"), smelted).status == "quarantined"


def test_error_quarantined_status():
    result = check(LOG_RULE, MINE_LOG, Outcome(ok=False, error="quarantined"))
    assert result.status == "quarantined" and result.kind == "quarantined"


def test_failed_and_wrong_tool_held():
    assert check(LOG_RULE, MINE_LOG, Outcome(ok=False, error="not_found")).status == "failed"

    rule = mine_rule("stone", {"#cobblestone": 1}, ALL_PICKAXES)
    action = Action("mine", "stone", tool="stone_pickaxe")
    outcome = Outcome(ok=True, block_removed=True, drops={"cobblestone": 1}, detail={"tool_used": "wooden_axe"})
    result = check(rule, action, outcome)
    assert result.status == "failed" and result.detail == "wrong tool held"
    outcome.detail["tool_used"] = "stone_pickaxe"
    assert check(rule, action, outcome).status == "match"
    outcome.detail["tool_used"] = None  # bare hand
    assert check(rule, action, outcome).status == "failed"
    assert check(LOG_RULE, MINE_LOG, mined_log(detail={"tool_used": None})).status == "match"


def test_craft_across_species_is_checked_by_species_not_raw_id():
    wrong = Outcome(ok=True, inventory_delta={"birch_log": -1, "oak_planks": 4})
    result = check(PLANKS_RULE, CRAFT_PLANKS, wrong)
    assert result.status == "mismatch"
    assert result.observed["species_mismatch"] == "birch_log -> oak_planks"
    assert "species_mismatch" not in predict(PLANKS_RULE, CRAFT_PLANKS)

    right = Outcome(ok=True, inventory_delta={"birch_log": -1, "birch_planks": 4})
    assert check(PLANKS_RULE, CRAFT_PLANKS, right).status == "match"


def test_mixed_species_craft_split_across_two_species_matches():
    mixed = Outcome(
        ok=True, inventory_delta={"oak_log": -1, "birch_log": -1, "oak_planks": 4, "birch_planks": 4}
    )
    result = check(PLANKS_RULE, Action("craft", "#planks", times=2), mixed)
    assert result.status == "match"
    assert result.observed == {"inventory_delta": {"#log": -2, "#planks": 8}}


def test_mine_species_mismatch_uses_the_block_actually_mined():
    outcome = Outcome(ok=True, block_removed=True, drops={"oak_log": 1}, detail={"block": "birch_log"})
    assert check(LOG_RULE, MINE_LOG, outcome).status == "mismatch"
    outcome = Outcome(ok=True, block_removed=True, drops={"birch_log": 1}, detail={"block": "birch_log"})
    assert check(LOG_RULE, MINE_LOG, outcome).status == "match"


def test_smelt_with_planks_as_fuel_matches():
    action = Action("smelt", "iron_ingot", fuel="#planks", ingredient="raw_iron")
    outcome = Outcome(
        ok=True,
        inventory_delta={"raw_iron": -1, "iron_ingot": 1, "birch_planks": -1},
        detail={"fuel": "birch_planks", "fuel_used": 1, "furnace_retrieved": True},
    )
    result = check(smelt_rule(), action, outcome)
    assert result.status == "match"
    assert result.observed == {"inventory_delta": {"iron_ingot": 1, "raw_iron": -1}}


def test_smelt_furnace_not_retrieved_is_tolerated():
    action = Action("smelt", "iron_ingot", fuel="coal", ingredient="raw_iron")
    outcome = Outcome(
        ok=True,
        inventory_delta={"raw_iron": -1, "iron_ingot": 1, "coal": -1, "furnace": -1},
        detail={"fuel": "coal", "fuel_used": 1, "furnace_retrieved": False},
    )
    assert check(smelt_rule(), action, outcome).status == "match"


def test_crafting_table_not_retrieved_is_tolerated():
    rule = craft_rule(
        "wooden_pickaxe", {"#planks": 3, "stick": 2}, {"wooden_pickaxe": 1}, station="crafting_table"
    )
    outcome = Outcome(
        ok=True,
        inventory_delta={"birch_planks": -3, "stick": -2, "wooden_pickaxe": 1, "crafting_table": -1},
        detail={"table_retrieved": False},
    )
    result = check(rule, Action("craft", "wooden_pickaxe"), outcome)
    assert result.status == "match" and "not picked back up" in result.detail


# ---- evaluate ------------------------------------------------------------------


def test_evaluate_renormalizes_raw_stored_observations():
    raw = {"block_removed": True, "drops": {"oak_log": 1}}
    assert evaluate(LOG_RULE, MINE_LOG, raw) == "confirm"
    assert evaluate(LOG_RULE, MINE_LOG, {"block_removed": True, "drops": {}}) == "contradict"
    craft_raw = {"inventory_delta": {"oak_log": -1, "oak_planks": 4}}
    assert evaluate(PLANKS_RULE, CRAFT_PLANKS, craft_raw) == "confirm"
    assert evaluate(PLANKS_RULE, CRAFT_PLANKS, {"inventory_delta": {"#log": -1, "#planks": 3}}) == "contradict"


def test_evaluate_stochastic_requires_removal_and_range():
    rule = mine_rule("gravel", {"gravel": 1})
    action = Action("mine", "gravel")
    assert evaluate(rule, action, {"block_removed": True, "drops": {}}) == "confirm"
    assert evaluate(rule, action, {"block_removed": False, "drops": {}}) == "neutral"
    assert evaluate(rule, action, {"block_removed": True, "drops": {"gravel": 2}}) == "neutral"


# ---- epochs --------------------------------------------------------------------


def record(store, tid, episode, world, action, outcome, rule):
    """Check an outcome against a rule and store the transition the way the agent does."""
    result = check(rule, action, outcome)
    assert result.status in ("match", "mismatch")
    store.record_transition(Transition(tid, episode, world, action, result.observed))
    return result


def stick_outcome(sticks):
    return Outcome(ok=True, inventory_delta={"birch_planks": -2, "stick": sticks})


def test_epoch_helpers():
    assert epoch_key("w", "mine", "deepslate_coal_ore") == "w/mine:coal_ore"
    store = SchemaStore()
    assert store.current_epoch("w/mine:coal_ore") == 0
    store.record_transition(Transition("a", "e1", "w", Action("mine", "coal_ore"), {}))
    assert store.transitions[0].seq == 0
    store.record_transition(Transition("b", "e1", "w", Action("mine", "coal_ore"), {}))
    assert store.transitions[1].seq == 1


def test_a_contradicted_certified_rule_opens_an_epoch_and_its_revision_certifies_on_new_evidence():
    store = SchemaStore()
    old = craft_rule("stick", {"#planks": 2}, {"stick": 4}, scope="w", status="certified")
    store.add_rule(old)
    action = Action("craft", "stick")
    record(store, "w/1", "e0a", "w", action, stick_outcome(4), old)
    record(store, "w/2", "e0b", "w", action, stick_outcome(4), old)
    contradiction = record(store, "w/3", "e1", "w", action, stick_outcome(2), old)
    assert contradiction.status == "mismatch"

    store.demote(old.id, "w/3", "w")
    key = epoch_key("w", "craft", "stick")
    opened = store.epochs[key][0]
    assert (opened.number, opened.start_seq, opened.opened_by, opened.rule_id) == (1, 2, "w/3", old.id)
    assert store.current_epoch(key) == 1
    assert [store.epoch_of(t) for t in store.transitions] == [0, 0, 1]

    revision = craft_rule(
        "stick", {"#planks": 2}, {"stick": 2}, id="craft_stick.rev1", scope="w", source="revision",
        revision_of=old.id,
    )
    store.add_rule(revision)
    assert revision.epoch == 1
    assert old.epoch == 0

    # The contradicting transition belongs to the new epoch and confirms it; epoch 0 evidence is ignored.
    confirmed, contradicted = store.replay(revision)
    assert [t.id for t in confirmed] == ["w/3"] and contradicted == []
    assert store.certify() == []
    assert revision.status == "hypothesis"

    record(store, "w/4", "e2", "w", action, stick_outcome(2), revision)
    assert store.certify() == [revision.id]


def test_without_epochs_old_evidence_would_contradict_the_revision():
    store = SchemaStore()
    action = Action("craft", "stick")
    revision = craft_rule("stick", {"#planks": 2}, {"stick": 2}, scope="w", source="revision")
    store.record_transition(Transition("w/1", "e0", "w", action, {"inventory_delta": {"#planks": -2, "stick": 4}}))
    assert len(store.replay(revision)[1]) == 1  # epoch 0 includes the old evidence


def test_coal_case_revision_certifies_on_stone_pickaxe_evidence_of_the_new_epoch():
    store = SchemaStore()
    old = mine_rule("coal_ore", {"coal": 1}, ALL_PICKAXES, scope="w", status="certified")
    store.add_rule(old)
    wooden = Action("mine", "coal_ore", tool="wooden_pickaxe")
    stone = Action("mine", "deepslate_coal_ore", tool="stone_pickaxe")
    got_coal = Outcome(ok=True, block_removed=True, drops={"coal": 1})
    no_coal = Outcome(ok=True, block_removed=True, drops={})

    record(store, "w/1", "e0a", "w", wooden, got_coal, old)
    record(store, "w/2", "e0b", "w", stone, got_coal, old)  # epoch 0 stone evidence must not count later
    assert record(store, "w/3", "e1", "w", wooden, no_coal, old).status == "mismatch"
    store.demote(old.id, "w/3", "w")

    revision = mine_rule(
        "coal_ore", {"coal": 1}, STONE_TIER, id="mine_coal_ore.rev1", scope="w", source="revision"
    )
    store.add_rule(revision)
    assert revision.epoch == 1
    assert store.replay(revision) == ([], [])  # the wooden-pickaxe transition is not its business
    assert store.certify() == []

    record(store, "w/4", "e2", "w", stone, got_coal, revision)
    assert store.certify() == [] and revision.status == "hypothesis"  # one episode in the new epoch
    record(store, "w/5", "e3", "w", stone, got_coal, revision)
    assert store.certify() == [revision.id]
    assert [t.id for t in store.replay(revision)[0]] == ["w/4", "w/5"]


def test_demote_opens_an_epoch_only_after_a_confirmation_or_certification():
    key = epoch_key("w", "mine", "coal_ore")
    action = Action("mine", "coal_ore", tool="stone_pickaxe")
    confirmation = Outcome(ok=True, block_removed=True, drops={"coal": 1})
    contradiction = Outcome(ok=True, block_removed=True, drops={})

    confirmed_once = SchemaStore()
    rule = mine_rule("coal_ore", {"coal": 1}, scope="w")
    confirmed_once.add_rule(rule)
    record(confirmed_once, "w/1", "e1", "w", action, confirmation, rule)
    record(confirmed_once, "w/2", "e1", "w", action, contradiction, rule)
    confirmed_once.demote(rule.id, "w/2", "w")
    assert confirmed_once.current_epoch(key) == 1

    never_confirmed = SchemaStore()
    rule = mine_rule("coal_ore", {"coal": 1}, scope="w")
    never_confirmed.add_rule(rule)
    record(never_confirmed, "w/1", "e1", "w", action, contradiction, rule)
    never_confirmed.demote(rule.id, "w/1", "w")
    assert never_confirmed.current_epoch(key) == 0
    assert never_confirmed.epochs == {}


def test_demote_keeps_its_v1_behaviour():
    store = SchemaStore()
    rule = mine_rule("coal_ore", {"coal": 1}, scope="train", status="certified")
    store.add_rule(rule)
    assert store.demote(rule.id, "x/1", "w9") == []
    assert rule.status == "certified" and rule.evidence["counterexamples"] == ["x/1"]
    assert rule.id not in {r.id for r in store.usable_rules("w9")}
    assert rule.id in {r.id for r in store.usable_rules("w8")}


def test_priors_keep_epoch_zero_after_an_epoch_opens():
    store = SchemaStore()
    old = craft_rule("stick", {"#planks": 2}, {"stick": 4}, scope="w", status="certified")
    store.add_rule(old)
    store.record_transition(Transition("w/1", "e1", "w", Action("craft", "stick"), {}))
    store.demote(old.id, "w/1", "w")
    prior = craft_rule("stick", {"#planks": 2}, {"stick": 2}, id="prior2", scope="w")
    store.add_rule(prior)
    assert prior.epoch == 0


def test_save_and_load_round_trip_epochs_kinds_and_seqs(tmp_path):
    store = SchemaStore()
    old = craft_rule("stick", {"#planks": 2}, {"stick": 4}, scope="w", status="certified")
    store.add_rule(old)
    action = Action("craft", "stick")
    record(store, "w/1", "e0", "w", action, stick_outcome(4), old)
    store.record_transition(
        Transition("w/2", "e0", "w", Action("mine", "gravel"), {"block_removed": True, "drops": {}}, kind="stochastic")
    )
    record(store, "w/3", "e1", "w", action, stick_outcome(2), old)
    store.demote(old.id, "w/3", "w")
    store.add_rule(craft_rule("stick", {"#planks": 2}, {"stick": 2}, id="rev", scope="w", source="revision"))

    path = tmp_path / "store.json"
    store.save(path)
    loaded = SchemaStore.load(path)
    assert loaded.epochs == store.epochs and loaded.epochs
    assert [(t.id, t.kind, t.seq) for t in loaded.transitions] == [("w/1", "exact", 0), ("w/2", "stochastic", 1), ("w/3", "exact", 2)]
    assert loaded.rules["rev"].epoch == 1
    assert loaded.current_epoch(epoch_key("w", "craft", "stick")) == 1
    assert loaded.to_dict() == store.to_dict()


V1_STORE = {
    "rules": [
        {
            "id": "mine_oak_log", "action": {"type": "mine", "block": "oak_log"},
            "preconditions": {"tool_any": []}, "effects": {"block_removed": True, "drops": {"oak_log": 1}},
            "status": "hypothesis", "source": "prior", "scope": "train", "rank": 0, "rationale": "",
            "revision_of": None, "evidence": {"confirmed": [], "contradicted": [], "counterexamples": []},
        },
        {
            "id": "craft_oak_planks", "action": {"type": "craft", "item": "oak_planks"},
            "preconditions": {"consumes": {"oak_log": 1}, "station": None}, "effects": {"gains": {"oak_planks": 4}},
            "status": "hypothesis", "source": "prior", "scope": "train", "rank": 0, "rationale": "",
            "revision_of": None, "evidence": {"confirmed": [], "contradicted": [], "counterexamples": []},
        },
    ],
    "skills": [],
    "demoted_in": {},
    "transitions": [
        {"id": "e1/1", "episode_id": "e1", "world_id": "train", "action": {"kind": "mine", "target": "oak_log"},
         "observed": {"block_removed": True, "drops": {"oak_log": 1}}},
        {"id": "e1/2", "episode_id": "e1", "world_id": "train", "action": {"kind": "craft", "target": "oak_planks"},
         "observed": {"inventory_delta": {"oak_log": -1, "oak_planks": 4}}},
        {"id": "e2/1", "episode_id": "e2", "world_id": "train", "action": {"kind": "mine", "target": "oak_log"},
         "observed": {"block_removed": True, "drops": {"oak_log": 1}}},
        {"id": "e2/2", "episode_id": "e2", "world_id": "train", "action": {"kind": "craft", "target": "oak_planks"},
         "observed": {"inventory_delta": {"oak_log": -1, "oak_planks": 4}}},
    ],
}


def test_a_v1_store_with_raw_ids_loads_and_certifies_the_same_rules(tmp_path):
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(V1_STORE), encoding="utf-8")
    store = SchemaStore.load(path)
    assert store.epochs == {}
    assert [t.seq for t in store.transitions] == [0, 1, 2, 3]
    assert all(t.kind == "exact" for t in store.transitions)
    assert sorted(store.certify()) == ["craft_oak_planks", "mine_oak_log"]
