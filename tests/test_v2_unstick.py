"""Never loop on one failing goal (P1-8): an explore that got nowhere kills its direction
for the rest of the goal, and a goal that failed for want of a resource is followed by an
exploration round."""

from schema_voyager.adapter import DIRECTIONS
from schema_voyager.episode import Episode
from schema_voyager.life import run_life
from schema_voyager.llm import LLM
from schema_voyager.models import Action, Outcome, Task
from schema_voyager.trace import EpisodeLog

from test_v2_life import FakeLifeBackend, make_config, make_llm, read_lines

LOGS_NORTH_WEST = {
    direction: ({"oak_log": {"distance": 30.0}} if direction == "NW" else {}) for direction in DIRECTIONS
}


class StuckBackend:
    """Look always reports logs to the NW. Explores toward `open_directions` move the bot 20
    blocks and, once one has, mines find a log; every other explore is unreachable at distance 0."""

    def __init__(self, open_directions):
        self.open_directions = open_directions
        self.moved = False
        self.explored = []

    def look(self, blocks, radius=64):
        return LOGS_NORTH_WEST

    def act(self, action):
        if action.kind == "explore":
            self.explored.append(action.direction)
            if action.direction in self.open_directions:
                self.moved = True
                return Outcome(ok=True, travel={"distance": 20.0})
            return Outcome(ok=False, error="unreachable", travel={"distance": 0})
        if self.moved:
            return Outcome(ok=True, inventory={"oak_log": 1})
        return Outcome(ok=False, error="not_found")


def search_until_done(backend, tmp_path):
    """Replan the way the agent does, until the goal is met or the episode cannot act."""
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
        deadline=None,
    )
    outcome = Outcome(ok=False)
    while not episode.goal_met and episode.can_act:
        outcome = episode.act(Action("mine", "#log"))
    return episode, outcome


def test_a_dead_direction_is_explored_once_per_goal_and_another_is_tried(tmp_path):
    backend = StuckBackend(open_directions={"E"})
    episode, outcome = search_until_done(backend, tmp_path)
    assert outcome.ok and episode.goal_met
    assert backend.explored.count("NW") == 1 and "E" in backend.explored
    looks = [e for e in episode.log.events if e["kind"] == "look"]
    assert looks[0]["dead"] == [] and "NW" in looks[1]["dead"]


def test_a_far_explore_gives_dead_directions_another_chance(tmp_path):
    backend = StuckBackend(open_directions={"E"})
    episode, _ = search_until_done(backend, tmp_path)
    assert episode._dead_directions == set()  # the 20 blocks east cleared them


def test_when_every_direction_is_dead_the_goal_fails_before_its_budget_or_clock(tmp_path):
    backend = StuckBackend(open_directions=set())
    episode, outcome = search_until_done(backend, tmp_path)
    assert not outcome.ok and outcome.error == "not_found"
    assert not episode.out_of_time and episode.budget_left > 0
    assert all(backend.explored.count(direction) == 1 for direction in DIRECTIONS)


def test_a_life_explores_after_a_goal_failed_on_not_found(tmp_path):
    backend = FakeLifeBackend()
    backend.fail_with = ["not_found"] * 500
    config = make_config(tmp_path, max_goals=2)
    run_life(backend, make_llm(tmp_path), config)
    first, second = read_lines(config.run_dir / "life.jsonl")
    assert first["outcome"] == "failed" and first["goal"] != "explore"
    assert (second["goal"], second["outcome"]) == ("explore", "success")
