"""Navigation (v2.2): exploring toward what the look sees, including upward, without
oscillating, and a goal cut short by the life's own clock is not a failed attempt."""

import json
import time

from schema_voyager import life
from schema_voyager.adapter import DIRECTIONS
from schema_voyager.episode import Episode
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM
from schema_voyager.models import Action, Observation, Outcome, Task
from schema_voyager.trace import EpisodeLog

NOTHING_SEEN = {direction: {} for direction in DIRECTIONS}
FEET_Y = 10


def hit(direction, distance, y=FEET_Y):
    return {direction: {"oak_log": {"x": 0, "y": y, "z": 0, "distance": distance}}}


class ScriptedBackend:
    """Every mine fails; explores succeed; look answers `looks` in turn (the last one repeats)."""

    def __init__(self, looks, mine_errors=9):
        self.looks = list(looks)
        self.errors = ["not_found"] * mine_errors
        self.calls = []

    def look(self, blocks, radius=64):
        result = self.looks.pop(0) if len(self.looks) > 1 else self.looks[0]
        return {**NOTHING_SEEN, **result}

    def observe(self):
        return Observation((0.5, float(FEET_Y), 0.5), {}, {})

    def act(self, action):
        self.calls.append(action)
        if action.kind == "mine" and self.errors:
            return Outcome(ok=False, error=self.errors.pop(0))
        return Outcome(ok=True)


def explores_for(backend, tmp_path):
    task = Task("g001", "train", "oak_log", 1, {}, (), 40, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
    )
    episode.act(Action("mine", "oak_log"))
    return [call.direction for call in backend.calls if call.kind == "explore"]


# -- explore toward what is seen ------------------------------------------------------


def test_a_log_far_above_makes_the_first_explore_go_up(tmp_path):
    backend = ScriptedBackend([hit("N", 64.0, y=FEET_Y + 40)])
    explores = explores_for(backend, tmp_path)
    assert explores == ["up", "N", "N", "N"]  # up once per target, then toward the hit


def test_a_hit_barely_above_is_explored_toward(tmp_path):
    backend = ScriptedBackend([hit("E", 50.0, y=FEET_Y + 2)])
    assert explores_for(backend, tmp_path)[0] == "E"


def test_hits_on_both_sides_do_not_reverse_the_direction_each_time(tmp_path):
    # The nearest hit alternates between east and west, as the bot's own moves change the view.
    east_nearer = {**hit("E", 30.0), **hit("W", 35.0)}
    west_nearer = {**hit("E", 35.0), **hit("W", 30.0)}
    backend = ScriptedBackend([east_nearer, west_nearer, east_nearer, west_nearer])
    explores = explores_for(backend, tmp_path)
    assert explores == ["E", "E", "E", "E"]


def test_the_opposite_direction_is_taken_when_it_is_the_only_hit(tmp_path):
    backend = ScriptedBackend([hit("E", 30.0), hit("W", 30.0)])
    assert explores_for(backend, tmp_path)[:2] == ["E", "W"]


def test_nothing_in_sight_is_still_up_then_the_compass(tmp_path):
    backend = ScriptedBackend([NOTHING_SEEN])
    assert explores_for(backend, tmp_path) == ["up", *DIRECTIONS[:3]]


# -- a goal cut short by the life's own clock -----------------------------------------


class LifeBackend:
    def __init__(self):
        self.said = []

    def begin_episode(self):
        return self.observe()

    def observe(self):
        return Observation((0.0, 70.0, 0.0), {}, {"oak_log": 5})

    def say(self, message):
        self.said.append(message)


def run_one_goal(tmp_path, monkeypatch, end_goal, **config):
    """One goal whose run is `end_goal(episode)`, which leaves the episode unfinished."""
    def stub(episode, store, before_step=None, excluded=frozenset()):
        end_goal(episode)
        return episode.finish()

    monkeypatch.setattr(life, "run_schemavoyager", stub)
    run_dir = tmp_path / "run"
    run_life(LifeBackend(), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"),
             LifeConfig(run_dir=run_dir, world_id="life-7-x", max_goals=1, **config))
    record = json.loads((run_dir / "life.jsonl").read_text().splitlines()[0])
    failures = json.loads((run_dir / "life_state.json").read_text())["curriculum"]["failures"]
    return record["outcome"], failures


def test_a_goal_cut_short_by_the_life_clock_is_interrupted_and_not_counted(tmp_path, monkeypatch):
    def life_clock_runs_out(episode):
        episode.deadline = time.monotonic() - 1  # the deadline the life's clock set

    outcome, failures = run_one_goal(tmp_path, monkeypatch, life_clock_runs_out, wall_clock_s=60.0, goal_wall_clock_s=300.0)
    assert outcome == "interrupted" and failures == {}


def test_a_goal_that_hit_its_own_deadline_still_counts(tmp_path, monkeypatch):
    def goal_clock_runs_out(episode):
        episode.deadline = time.monotonic() - 1

    outcome, failures = run_one_goal(tmp_path, monkeypatch, goal_clock_runs_out, wall_clock_s=3600.0, goal_wall_clock_s=60.0)
    assert outcome == "failed" and sum(failures.values()) == 1


def test_a_goal_that_ran_out_of_budget_still_counts_even_near_the_life_clock(tmp_path, monkeypatch):
    def budget_spent(episode):
        episode.actions = episode.task.budget

    outcome, failures = run_one_goal(tmp_path, monkeypatch, budget_spent, wall_clock_s=60.0, goal_wall_clock_s=300.0)
    assert outcome == "failed" and sum(failures.values()) == 1
