"""A kill goal searches for its mob only within KILL_SEARCH_RADIUS of where it started (v3.0i)."""

from schema_voyager import goal_runners
from schema_voyager.episode import Episode
from schema_voyager.goal_runners import KILL_SEARCH_RADIUS, RunnerOutcome, run_kill
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec, Observation, Outcome, Task
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog

SWORD = {"iron_sword": 1}


class WanderingBackend:
    """Every explore moves the bot `step` blocks east. A kill finds nothing until `kill_after_explores` explores."""

    def __init__(self, step=40.0, kill_after_explores=None):
        self.x = 0.0
        self.step = step
        self.kill_after_explores = kill_after_explores
        self.explores = 0
        self.kills = 0

    def observe(self):
        return Observation(position=(self.x, 64.0, 0.0), inventory=dict(SWORD), nearby={})

    def look(self, blocks, radius, mobs=None):
        return {}

    def act(self, action):
        if action.kind == "explore":
            self.explores += 1
            self.x += self.step
            return Outcome(ok=True, inventory=dict(SWORD), travel={"distance": self.step})
        self.kills += 1
        found = self.kill_after_explores is not None and self.explores >= self.kill_after_explores
        events = [{"type": "fight_end", "target": "zombie", "id": 100 + self.kills, "outcome": "killed"}] if found else []
        return Outcome(ok=found, error=None if found else "not_found", inventory=dict(SWORD), events=events)


def run(tmp_path, backend, count=1):
    task = Task("t", "train", "explore", 1, dict(SWORD), (), 60, "kill")
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    episode = Episode("e", "D", task, 1, "w", backend, llm, EpisodeLog(None, "t"), natural=True, inventory=dict(SWORD))
    spec = GoalSpec(id="u001", kind="kill", source="user", count=count, target="zombie")
    outcome = RunnerOutcome()
    result = run_kill(
        episode, SchemaStore(tmp_path / "store.json"), spec, bot_events=[], check_interrupts=lambda: None,
        before_step=lambda *args, **kwargs: None, excluded=frozenset(), clock_s=3600.0, outcome=outcome,
    )
    return episode, result, outcome


def test_a_search_that_drifts_out_of_the_radius_stops_exploring_and_fails_not_found_nearby(tmp_path):
    backend = WanderingBackend(step=40.0)

    episode, result, outcome = run(tmp_path, backend)

    assert not result.success
    assert outcome.reason == "not found nearby"
    assert backend.x > KILL_SEARCH_RADIUS
    assert backend.explores == 2  # 40 is inside, 80 is outside: no third explore
    assert any(e["kind"] == "search_bound" for e in episode.log.events)


def test_inside_the_radius_a_kill_search_is_unchanged(tmp_path):
    backend = WanderingBackend(step=10.0, kill_after_explores=2)

    _, result, outcome = run(tmp_path, backend)

    assert result.success
    assert outcome.reason == ""
    assert backend.x == 20.0


def test_the_bound_is_measured_from_where_the_goal_started(tmp_path):
    backend = WanderingBackend(step=40.0, kill_after_explores=2)
    backend.x = 1000.0  # far from the origin, but the goal starts here

    episode, result, _ = run(tmp_path, backend)

    assert result.success
    assert episode.search_bound == ((1000.0, 0.0), KILL_SEARCH_RADIUS)


def test_an_episode_without_a_bound_explores_as_before(tmp_path):
    task = Task("t", "train", "explore", 1, {}, (), 60, "x")
    episode = Episode("e", "D", task, 1, "w", WanderingBackend(step=500.0), LLM(mode="fake", cache_path=tmp_path / "c.jsonl"), EpisodeLog(None, "t"), natural=True)

    assert episode.search_bound is None
    assert not episode.outside_search_bound()
    assert episode._explore_toward("zombie", True, None, mob=True) is not None


def test_goal_runners_exports_the_radius():
    assert goal_runners.KILL_SEARCH_RADIUS == 64.0
