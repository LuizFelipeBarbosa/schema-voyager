"""Blocked rules across goals (v3.0a): a rule blocked with `missing_ingredients` in 2 goals is left out of
every later goal's planning and out of the curriculum's goal choice, and this survives --resume."""

import json

import pytest

from schema_voyager import life
from schema_voyager.curriculum import Curriculum
from schema_voyager.life import run_life

from test_v2_life import (  # noqa: F401  (the two fixtures are autouse in test_v2_life)
    FakeLifeBackend, fake_normalize, make_config, make_llm, priors, prior_store, use_fake_check,
)


@pytest.fixture
def excluded_per_goal(monkeypatch):
    """Every goal blocks `craft_bucket` with the error in `blocked_with`; the list gets each goal's `excluded`."""
    seen = {"excluded": [], "blocked_with": "missing_ingredients"}
    real = life.run_schemavoyager

    def run_and_block(episode, store, before_step=None, excluded=frozenset()):
        seen["excluded"].append(excluded)
        result = real(episode, store, before_step, excluded=excluded)
        episode.blocked_rules["craft_bucket"] = seen["blocked_with"]
        return result

    monkeypatch.setattr(life, "run_schemavoyager", run_and_block)
    return seen


def _state(tmp_path):
    return json.loads((tmp_path / "run" / "life_state.json").read_text())


def test_a_rule_blocked_in_two_goals_is_excluded_from_the_third(tmp_path, excluded_per_goal, capsys):
    run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=3))

    assert excluded_per_goal["excluded"] == [frozenset(), frozenset(), frozenset({"craft_bucket"})]
    assert _state(tmp_path)["blocked"] == {"craft_bucket": 3}
    assert capsys.readouterr().out.count("[blocked] craft_bucket: blocked in 2 goals") == 1


def test_a_rule_blocked_with_another_error_is_not_counted(tmp_path, excluded_per_goal):
    excluded_per_goal["blocked_with"] = "missing_crafting_table"
    run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=3))

    assert excluded_per_goal["excluded"] == [frozenset()] * 3
    assert _state(tmp_path)["blocked"] == {}


def test_the_exclusion_survives_resume(tmp_path, excluded_per_goal):
    run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=2))
    assert _state(tmp_path)["blocked"] == {"craft_bucket": 2}
    excluded_per_goal["excluded"].clear()

    run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=3), resume=True)

    assert excluded_per_goal["excluded"] == [frozenset({"craft_bucket"})]


def test_a_save_from_before_blocked_memory_still_loads(tmp_path, excluded_per_goal):
    excluded_per_goal["blocked_with"] = "missing_crafting_table"
    run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=1))
    state_path = tmp_path / "run" / "life_state.json"
    state = json.loads(state_path.read_text())
    del state["blocked"]
    state_path.write_text(json.dumps(state))

    life_after = life._Life(
        FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path), life.LifeHooks(), resume=True
    )

    assert life_after.blocked == {}


def test_next_goal_leaves_out_an_excluded_rule(priors):
    curriculum = Curriculum(["#planks"])  # its only producer is craft_#planks

    assert curriculum.next_goal({}, priors, "w1").item == "#planks"
    assert curriculum.next_goal({}, priors, "w1", excluded=frozenset({"craft_#planks"})) is None
