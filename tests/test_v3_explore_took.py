"""v3.0d: /explore reports the attempt that moved, and the episode kills the requested direction when a neighbour did the moving."""

import pytest

from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Outcome, Task
from schema_voyager.trace import EpisodeLog

from test_v2_backends import FakeBot


@pytest.fixture
def bot():
    fake = FakeBot()
    fake.responses["/observe"] = {"ok": True, "inventory": {}, "nearby_blocks": {}}
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def make_episode(tmp_path):
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=None,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
        deadline=None,
    )


def test_explore_puts_the_attempt_that_moved_into_the_detail(bot):
    bot.responses["/explore"] = {"ok": True, "took": {"direction": "NE", "distance": 10}, "travel": {"distance": 9.0}}
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("explore", direction="N"))
    assert outcome.detail["took"] == {"direction": "NE", "distance": 10}


def test_explore_omits_took_when_the_bot_does_not_send_it(bot):
    bot.responses["/explore"] = {"ok": False, "error": "unreachable", "took": None}
    backend = MineflayerBackend(bot.url, natural=True)
    assert "took" not in backend.act(Action("explore", direction="N")).detail
    bot.responses["/explore"] = {"ok": True, "travel": {"distance": 9.0}}
    assert "took" not in backend.act(Action("explore", direction="N")).detail


def test_a_neighbour_that_moved_kills_the_requested_direction_and_a_big_move_clears_the_rest(tmp_path):
    episode = make_episode(tmp_path)
    episode._dead_directions = {"S", "W"}
    outcome = Outcome(ok=True, travel={"distance": 40.0}, detail={"took": {"direction": "NE", "distance": 10}})
    episode._remember_explore(Action("explore", direction="N"), outcome)
    assert episode._dead_directions == {"N"}


def test_a_short_neighbour_move_keeps_the_other_dead_directions(tmp_path):
    episode = make_episode(tmp_path)
    episode._dead_directions = {"S"}
    outcome = Outcome(ok=True, travel={"distance": 5.0}, detail={"took": {"direction": "NE", "distance": 10}})
    episode._remember_explore(Action("explore", direction="N"), outcome)
    assert episode._dead_directions == {"S", "N"}


def test_the_short_hop_the_same_way_keeps_the_direction_alive(tmp_path):
    episode = make_episode(tmp_path)
    outcome = Outcome(ok=True, travel={"distance": 5.0}, detail={"took": {"direction": "N", "distance": 10}})
    episode._remember_explore(Action("explore", direction="N"), outcome)
    assert episode._dead_directions == set()


def test_without_took_a_short_explore_still_kills_its_direction(tmp_path):
    episode = make_episode(tmp_path)
    episode._remember_explore(Action("explore", direction="N"), Outcome(ok=True, travel={"distance": 1.0}))
    assert episode._dead_directions == {"N"}
    episode._remember_explore(Action("explore", direction="E"), Outcome(ok=True, travel={"distance": 20.0}))
    assert episode._dead_directions == set()
