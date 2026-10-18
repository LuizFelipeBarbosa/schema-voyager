"""Never retry an identical failed step (v3.0a): the second identical failure of a rule in a goal
blocks it, planning leaves blocked and excluded rules out, and search failures never block."""

from schema_voyager.environment import NaturalWorld
from schema_voyager.agent import run_schemavoyager
from schema_voyager.models import Rule

from test_v2_life import (  # noqa: F401  (the two fixtures are autouse in test_v2_life)
    FakeLifeBackend, fake_normalize, make_episode, make_llm, prior_store, use_fake_check,
)


def _bucket_rule(rule_id, ingredient, station=None):
    return Rule(
        id=rule_id, action={"type": "craft", "item": "bucket"},
        preconditions={"consumes": {ingredient: 3}, "station": station}, effects={"gains": {"bucket": 1}},
    )


def _bucket_episode(tmp_path, rules, inventory, backend=None, budget=40):
    """A bucket goal where the world's real recipe takes iron ingots on a crafting table."""
    if backend is None:
        backend = FakeLifeBackend()
        backend.inventory = dict(inventory)
    llm = make_llm(tmp_path)
    store = prior_store(llm, "bucket")
    store.rules.clear()
    for rule in rules:
        store.add_rule(rule)
    return make_episode(backend, llm, "bucket", budget=budget), store


def _events(episode, kind):
    return [e for e in episode.log.events if e["kind"] == kind]


def test_a_rule_that_fails_the_same_way_twice_is_blocked_and_the_goal_ends(tmp_path):
    wrong = _bucket_rule("craft_bucket", "raw_iron")
    episode, store = _bucket_episode(tmp_path, [wrong], {"raw_iron": 3})
    result = run_schemavoyager(episode, store)
    assert not result.success and episode.actions <= 3
    (blocked,) = _events(episode, "rule_blocked")
    assert (blocked["rule"], blocked["error"]) == ("craft_bucket", "missing_ingredients")
    assert blocked["missing"] is None  # FakeLifeBackend reports no `missing` detail
    assert episode.blocked_rules == {"craft_bucket": "missing_ingredients"}
    kinds = [e["kind"] for e in episode.log.events]
    assert kinds.index("rule_blocked") < kinds.index("plan_failed")


def test_two_failures_with_different_inventories_do_not_block(tmp_path):
    class PickyBackend(FakeLifeBackend):
        def act(self, action):
            outcome = super().act(action)
            if not outcome.ok:  # something is picked up on every failure, so the inventory changes
                self.inventory["dirt"] = self.inventory.get("dirt", 0) + 1
                outcome.inventory = dict(self.inventory)
            return outcome

    wrong = _bucket_rule("craft_bucket", "raw_iron")
    picky = PickyBackend()
    picky.inventory = {"raw_iron": 3}
    episode, store = _bucket_episode(tmp_path, [wrong], None, backend=picky, budget=6)
    run_schemavoyager(episode, store)
    assert episode.actions == 6 and not episode.blocked_rules
    assert not _events(episode, "rule_blocked")


def test_a_repeated_not_found_never_blocks(tmp_path):
    backend = FakeLifeBackend()
    backend.fail_with = ["not_found"] * 500
    llm = make_llm(tmp_path)
    episode = make_episode(backend, llm, "#log", budget=20)
    run_schemavoyager(episode, prior_store(llm, "#log"))
    assert episode.actions > 3 and not episode.blocked_rules
    assert not _events(episode, "rule_blocked")


def test_an_excluded_rule_is_never_planned(tmp_path):
    wrong = _bucket_rule("craft_bucket", "raw_iron")
    right = _bucket_rule("craft_bucket_from_ingots", "iron_ingot", station="crafting_table")
    held = {"raw_iron": 3, "iron_ingot": 3, "crafting_table": 1}

    episode, store = _bucket_episode(tmp_path, [wrong, right], held)
    result = run_schemavoyager(episode, store, excluded=frozenset({"craft_bucket"}))
    assert result.success and not episode.blocked_rules
    assert {e["rule"] for e in _events(episode, "step_start")} == {"craft_bucket_from_ingots"}

    episode, store = _bucket_episode(tmp_path, [wrong], held)
    result = run_schemavoyager(episode, store, excluded=frozenset({"craft_bucket"}))
    assert not result.success and episode.actions == 0
    assert len(_events(episode, "plan_failed")) == 1


def test_in_the_simulator_a_wrong_bucket_rule_is_blocked_within_three_actions(tmp_path):
    world = NaturalWorld(seed=7)
    world.inventory.update({"raw_iron": 3, "crafting_table": 1})
    wrong = _bucket_rule("craft_bucket", "raw_iron", station="crafting_table")
    episode, store = _bucket_episode(tmp_path, [wrong], None, backend=world)
    result = run_schemavoyager(episode, store)
    assert not result.success and episode.actions <= 3
    (blocked,) = _events(episode, "rule_blocked")
    assert (blocked["rule"], blocked["error"]) == ("craft_bucket", "missing_ingredients")
    assert blocked["missing"] == {"iron_ingot": 3}
    assert episode.blocked_rules == {"craft_bucket": "missing_ingredients"}
