from dataclasses import replace

import pytest

from schema_voyager.agent import run_schemavoyager
from schema_voyager.baseline import SkillLibrary, run_skill_only
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM, fixture_prior_rules
from schema_voyager.models import Action, Observation, Outcome
from schema_voyager.schema_store import SchemaStore
from schema_voyager.tasks import get_task
from schema_voyager.trace import EpisodeLog

MINE_LOG = Action("mine", "oak_log")


class ScriptedBackend:
    """Fails each non-explore action with the next scripted error, then succeeds
    with the real effect of the action; explores always succeed."""

    name = "scripted"

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []
        self.inventory = {}

    def act(self, action):
        self.calls.append(action.describe())
        if action.kind == "explore":
            return Outcome(ok=True, inventory=dict(self.inventory))
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            return Outcome(ok=False, error=error, inventory=dict(self.inventory))
        if action.kind == "mine":
            self.inventory["oak_log"] = self.inventory.get("oak_log", 0) + 1
            drops = {"oak_log": 1}
            return Outcome(ok=True, block_removed=True, drops=drops, picked_up=drops,
                           inventory_delta=drops, inventory=dict(self.inventory))
        self.inventory["oak_log"] -= 1
        self.inventory["oak_planks"] = self.inventory.get("oak_planks", 0) + 4
        return Outcome(ok=True, inventory_delta={"oak_log": -1, "oak_planks": 4}, inventory=dict(self.inventory))

    def observe(self):
        return Observation((0.0, -60.0, 0.0), dict(self.inventory), {"oak_log": 3})

    def say(self, message):
        pass


# A one-craft goal: mine a log, craft planks.
PLANKS_TASK = replace(get_task("h3"), goal_item="oak_planks", goal_count=4)


def _episode(backend, task=PLANKS_TASK, system="D"):
    return Episode(
        episode_id=f"{system}/{task.task_id}/L6", system=system, task=task, layout_seed=6, world_id="w1",
        backend=backend, llm=LLM(mode="fake"), log=EpisodeLog(None, "test"), inventory=dict(backend.inventory),
    )


def _steps(episode):
    return [e for e in episode.log.events if e["kind"] == "step"]


@pytest.mark.parametrize("error", ["unreachable", "craft_failed", "dig_failed", "timeout"])
def test_a_flaky_failure_is_retried_after_an_explore(error):
    backend = ScriptedBackend([error])
    episode = _episode(backend)
    outcome = episode.act(MINE_LOG)
    assert outcome.ok
    assert backend.calls == ["mine oak_log with hand", "explore", "mine oak_log with hand"]
    assert episode.actions == 3


def test_retries_stop_after_two_more_attempts():
    backend = ScriptedBackend(["unreachable"] * 5)
    episode = _episode(backend)
    outcome = episode.act(MINE_LOG)
    assert not outcome.ok and outcome.error == "unreachable"
    assert backend.calls == ["mine oak_log with hand", "explore"] * 2 + ["mine oak_log with hand"]
    assert episode.actions == 5
    assert [step["ok"] for step in _steps(episode)] == [False, True, False, True, False]


def test_out_of_view_targets_still_get_three_explores():
    backend = ScriptedBackend(["not_found"] * 3)
    episode = _episode(backend)
    assert episode.act(MINE_LOG).ok
    assert backend.calls.count("explore") == 3 and episode.actions == 7


def test_errors_about_the_agents_state_are_not_retried():
    for error in ("missing_tool", "missing_ingredients", "missing_crafting_table"):
        backend = ScriptedBackend([error])
        episode = _episode(backend)
        assert episode.act(MINE_LOG).error == error
        assert backend.calls == ["mine oak_log with hand"]


def test_retries_stay_within_the_budget():
    backend = ScriptedBackend(["unreachable"] * 3)
    episode = _episode(backend)
    episode.actions = episode.task.budget - 4
    episode.act(MINE_LOG)
    # A second explore would leave no action for its retry.
    assert backend.calls == ["mine oak_log with hand", "explore", "mine oak_log with hand"]
    assert episode.budget_left == 1


def test_every_attempt_is_annotated_and_the_last_verdict_wins():
    backend = ScriptedBackend(["dig_failed"])
    episode = _episode(backend)
    verdicts = []
    episode.act(MINE_LOG, annotate=lambda outcome: verdicts.append(outcome.ok) or {"check": str(outcome.ok)})
    assert verdicts == [False, True]
    assert [step.get("check") for step in _steps(episode)] == ["False", None, "True"]


def test_d_retries_a_flaky_step_instead_of_replanning():
    backend = ScriptedBackend(["unreachable"])
    store = SchemaStore()
    for rule in fixture_prior_rules():
        store.add_rule(rule)
    episode = _episode(backend)
    result = run_schemavoyager(episode, store)
    assert result.success
    assert backend.calls == ["mine oak_log with hand", "explore", "mine oak_log with hand", "craft oak_planks"]
    assert sum(1 for e in episode.log.events if e["kind"] == "plan") == 1


def test_b_gets_the_same_retries():
    backend = ScriptedBackend(["craft_failed"])
    backend.inventory = {"oak_log": 1}
    episode = _episode(backend, system="B")
    result = run_skill_only(episode, SkillLibrary(), fake_planner=lambda inventory: [Action("craft", "oak_planks")])
    assert result.success
    assert backend.calls == ["craft oak_planks", "explore", "craft oak_planks"]
