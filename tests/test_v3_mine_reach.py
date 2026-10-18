"""v3.0g: a natural `mine` that answers unreachable retries at once while the bot is still approaching,
and otherwise explores toward the target (up from underground) before the retry."""

import pytest

from schema_voyager.adapter import DIRECTIONS
from schema_voyager.episode import MAX_APPROACH_RETRIES, MAX_RETRIES, Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Observation, Outcome, Task
from schema_voyager.trace import EpisodeLog

from test_v2_backends import FakeBot

MINE_LOG = Action("mine", "#log")
BOT_Y = 50
NOTHING_SEEN = {direction: {} for direction in DIRECTIONS}
NORTH_LOG = {"N": {"oak_log": {"distance": 12.0, "y": BOT_Y}}}
LOGS_ABOVE = {"N": {"oak_log": {"distance": 20.0, "y": BOT_Y + 20}}}


def unreachable(reason=None):
    detail = {"reach": {"reason": reason}} if reason else {}
    return Outcome(ok=False, error="unreachable", detail=detail)


class ScriptedBackend:
    """Each mine answers the next scripted outcome, then succeeds; explores succeed (moving 10 blocks)."""

    def __init__(self, mines, looks=(NORTH_LOG,)):
        self.mines = list(mines)
        self.looks = list(looks)
        self.calls = []

    def look(self, blocks, radius=64):
        return self.looks.pop(0) if len(self.looks) > 1 else self.looks[0]

    def observe(self):
        return Observation((0.0, float(BOT_Y), 0.0), {}, {})

    def act(self, action):
        self.calls.append(action)
        if action.kind == "mine" and self.mines:
            return self.mines.pop(0)
        return Outcome(ok=True, travel={"distance": 10.0})


def make_episode(backend, tmp_path, budget=40):
    task = Task("g001", "train", "#log", 1, {}, (), budget, "test")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
    )


def kinds(backend):
    return [(call.kind, call.direction) if call.kind == "explore" else call.kind for call in backend.calls]


def test_approaching_retries_at_once_without_an_explore(tmp_path):
    backend = ScriptedBackend([unreachable("approaching")])
    outcome = make_episode(backend, tmp_path).act(MINE_LOG)
    assert outcome.ok
    assert kinds(backend) == ["mine", "mine"]


def test_a_second_approaching_is_a_real_failure_explored_toward(tmp_path):
    backend = ScriptedBackend([unreachable("approaching")] * 2)
    episode = make_episode(backend, tmp_path)
    assert MAX_APPROACH_RETRIES == 1
    outcome = episode.act(MINE_LOG)
    assert outcome.ok
    assert kinds(backend) == ["mine", "mine", ("explore", "N"), "mine"]


def test_the_approach_retry_is_not_counted_in_retries(tmp_path):
    backend = ScriptedBackend([unreachable("approaching")] + [unreachable("no_path")] * 3)
    make_episode(backend, tmp_path).act(MINE_LOG)
    explores = [call for call in backend.calls if call.kind == "explore"]
    assert len(explores) == MAX_RETRIES
    assert kinds(backend).count("mine") == 1 + 1 + MAX_RETRIES


@pytest.mark.parametrize("failure", [unreachable("no_path"), unreachable()])
def test_other_reasons_and_no_reach_explore_toward_the_nearest_hit(tmp_path, failure):
    backend = ScriptedBackend([failure])
    outcome = make_episode(backend, tmp_path).act(MINE_LOG)
    assert outcome.ok
    assert kinds(backend) == ["mine", ("explore", "N"), "mine"]


def test_underground_with_the_logs_above_asks_for_up_once_per_act(tmp_path):
    backend = ScriptedBackend([unreachable("no_path")] * 3, looks=[LOGS_ABOVE])
    make_episode(backend, tmp_path).act(MINE_LOG)
    directions = [call.direction for call in backend.calls if call.kind == "explore"]
    assert directions[0] == "up"
    assert directions.count("up") == 1


def test_when_every_direction_is_dead_and_up_is_spent_the_step_fails_and_the_search_is_exhausted(tmp_path):
    backend = ScriptedBackend([unreachable("no_path")] * 3, looks=[NOTHING_SEEN])
    episode = make_episode(backend, tmp_path)
    episode._dead_directions = set(DIRECTIONS)
    outcome = episode.act(MINE_LOG)
    assert not outcome.ok
    assert kinds(backend) == ["mine", ("explore", "up"), "mine"]
    assert episode._search_exhausted
    assert not episode.can_act


def test_retries_stay_within_the_budget(tmp_path):
    backend = ScriptedBackend([unreachable("no_path")] * 5)
    episode = make_episode(backend, tmp_path)
    episode.actions = episode.task.budget - 4
    episode.act(MINE_LOG)
    # A second explore would leave no action for its retry.
    assert kinds(backend) == ["mine", ("explore", "N"), "mine"]
    assert episode.budget_left == 1


def test_a_non_mine_unreachable_keeps_the_blind_explore(tmp_path):
    class CraftBackend(ScriptedBackend):
        def act(self, action):
            self.calls.append(action)
            if action.kind == "craft" and self.mines:
                return self.mines.pop(0)
            return Outcome(ok=True)

    backend = CraftBackend([unreachable("no_path")])
    make_episode(backend, tmp_path).act(Action("craft", "stick"))
    assert [call.kind for call in backend.calls] == ["craft", "explore", "craft"]
    assert backend.calls[1].direction is None


def test_a_non_natural_mine_keeps_the_blind_explore(tmp_path):
    backend = ScriptedBackend([unreachable("no_path")])
    episode = make_episode(backend, tmp_path)
    episode.natural = False
    episode.act(MINE_LOG)
    assert [call.kind for call in backend.calls] == ["mine", "explore", "mine"]
    assert backend.calls[1].direction is None


def test_a_timeout_on_a_natural_mine_keeps_the_blind_explore(tmp_path):
    backend = ScriptedBackend([Outcome(ok=False, error="timeout")])
    make_episode(backend, tmp_path).act(MINE_LOG)
    assert [call.kind for call in backend.calls] == ["mine", "explore", "mine"]
    assert backend.calls[1].direction is None


@pytest.fixture
def bot():
    fake = FakeBot()
    fake.responses["/observe"] = {"ok": True, "inventory": {}, "nearby_blocks": {}}
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def test_a_natural_mine_copies_the_reach_into_the_detail(bot):
    reach = {"reason": "approaching", "distance": 7.5}
    bot.responses["/mine"] = {"ok": False, "error": "unreachable", "reach": reach}
    outcome = MineflayerBackend(bot.url, natural=True).act(MINE_LOG)
    assert outcome.error == "unreachable"
    assert outcome.detail["reach"] == reach


def test_a_natural_mine_without_reach_has_none_in_the_detail(bot):
    bot.responses["/mine"] = {"ok": False, "error": "unreachable"}
    assert "reach" not in MineflayerBackend(bot.url, natural=True).act(MINE_LOG).detail
