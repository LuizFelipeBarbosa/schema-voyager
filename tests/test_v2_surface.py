"""Back to open sky (v2.1): the "up" explore of NaturalWorld and MineflayerBackend,
and the natural explore policy that tries it first. Real normalize, no fakes of it."""

import pytest

from schema_voyager import environment
from schema_voyager.adapter import DIRECTIONS
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import SCAFFOLD_BLOCKS, MineflayerBackend
from schema_voyager.models import Action, Outcome, Task
from schema_voyager.trace import EpisodeLog

from test_v2_backends import FakeBot, bot  # noqa: F401  (the fixture)

NOTHING_SEEN = {direction: {} for direction in DIRECTIONS}


class ScriptedBackend:
    """Every mine fails with the given errors; explores succeed; look answers `looks` in turn."""

    def __init__(self, errors, looks):
        self.errors = list(errors)
        self.looks = list(looks)
        self.calls = []

    def look(self, blocks, radius=64):
        return self.looks.pop(0) if len(self.looks) > 1 else self.looks[0]

    def act(self, action):
        self.calls.append(action)
        if action.kind == "mine" and self.errors:
            return Outcome(ok=False, error=self.errors.pop(0))
        return Outcome(ok=True)


def make_episode(backend, tmp_path, budget=40):
    task = Task("g001", "train", "#log", 1, {}, (), budget, "test")
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=llm, log=EpisodeLog(None, "test"), natural=True,
    )


def explored(backend):
    return [call.direction for call in backend.calls if call.kind == "explore"]


# -- the policy ----------------------------------------------------------------------


def test_the_first_fallback_is_up_then_the_compass(tmp_path):
    backend = ScriptedBackend(["not_found"] * 9, [NOTHING_SEEN])
    make_episode(backend, tmp_path).act(Action("mine", "#log"))
    assert explored(backend) == ["up", *DIRECTIONS[:3]]


def test_up_is_tried_once_per_target_not_once_per_life(tmp_path):
    backend = ScriptedBackend(["not_found"] * 9, [NOTHING_SEEN])
    episode = make_episode(backend, tmp_path)
    episode.act(Action("mine", "#log"))
    episode.act(Action("mine", "#log"))
    assert explored(backend).count("up") == 2


def test_a_hazard_is_searched_with_up_first_too(tmp_path):
    backend = ScriptedBackend(["hazard", "not_found"], [NOTHING_SEEN])
    make_episode(backend, tmp_path).act(Action("mine", "#log"))
    assert explored(backend)[0] == "up"


def test_a_look_hit_still_goes_toward_the_hit_and_up_waits_for_a_blind_look(tmp_path):
    seen = {"N": {"oak_log": {"distance": 40.0}}, "SE": {"birch_log": {"distance": 20.0}}}
    backend = ScriptedBackend(["not_found"] * 9, [seen, seen, NOTHING_SEEN, NOTHING_SEEN])
    make_episode(backend, tmp_path).act(Action("mine", "#log"))
    assert explored(backend) == ["SE", "SE", "up", "N"]


def test_v1_and_non_mine_explores_never_go_up(tmp_path):
    backend = ScriptedBackend(["not_found"] * 9, [NOTHING_SEEN])
    episode = make_episode(backend, tmp_path)
    episode.natural = False
    episode.act(Action("mine", "oak_log"))
    assert explored(backend) == [None, None, None]


# -- MineflayerBackend ----------------------------------------------------------------


def test_the_natural_up_request_has_no_distance(bot):  # noqa: F811
    bot.responses["/explore"] = {
        "ok": True, "error": None, "position": {"x": 1.5, "y": 66, "z": 2.5}, "sky": True, "blocks_walked": 3.0,
        "travel": {"distance": 3.0, "dug": {"stone": 3}, "placed": {"dirt": 1}, "picked_up": {"cobblestone": 3}}, "events": [],
    }
    bot.responses["/observe"] = {"ok": True, "inventory": {"dirt": 4}, "nearby_blocks": {}}
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("explore", direction="up"))
    explore = [body for path, body in bot.requests if path == "/explore"]
    assert explore == [{"direction": "up", "dig_through": True, "scaffold": list(SCAFFOLD_BLOCKS)}]
    assert outcome.ok and outcome.detail == {"position": {"x": 1.5, "y": 66, "z": 2.5}, "sky": True}
    assert outcome.travel["dug"] == {"stone": 3}


def test_a_horizontal_natural_explore_still_sends_its_distance(bot):  # noqa: F811
    MineflayerBackend(bot.url, natural=True).act(Action("explore", direction="NE"))
    body = next(body for path, body in bot.requests if path == "/explore")
    assert body["direction"] == "NE" and body["distance"] == 32


# -- NaturalWorld ---------------------------------------------------------------------


def underground(depth=30, **inventory):
    world = NaturalWorld(7)
    world.inventory.update(inventory)
    x, y, z = world._cell
    world._cell = (x, y - depth, z)
    world._changes[world._cell] = "air"
    return world


def test_up_from_underground_reaches_the_surface_and_accounts_for_the_dig(monkeypatch):
    monkeypatch.setattr(environment, "TRAVEL_DAMAGE_CHANCE", 0.0)
    world = underground(stone_pickaxe=1)
    x, y, z = world._cell
    surface = world._column(x, z)[0]
    column = [world.block_at((x, level, z)) for level in range(y + 1, surface + 1)]

    outcome = world.act(Action("explore", direction="up"))

    assert outcome.ok and outcome.detail["sky"] is True
    assert world._cell == (x, surface + 1, z)
    assert world.status().position[1] == surface + 1
    assert outcome.travel["distance"] == surface + 1 - y == outcome.blocks_walked
    assert sum(outcome.travel["dug"].values()) == len(column)
    assert outcome.travel["picked_up"]["cobbled_deepslate"] == column.count("deepslate")
    assert outcome.inventory == {"stone_pickaxe": 1, **outcome.travel["picked_up"]}
    assert all(world.block_at((x, level, z)) == "air" for level in range(y, surface + 2))


def test_up_under_open_sky_does_not_move():
    world = NaturalWorld(7)
    before = world._cell
    outcome = world.act(Action("explore", direction="up"))
    assert outcome.ok and outcome.detail["sky"] is True and world._cell == before
    assert outcome.travel == {"distance": 0.0, "dug": {}, "placed": {}, "picked_up": {}}


def test_up_does_not_count_leaves_as_a_roof():
    world = NaturalWorld(7)
    x, y, z = world._cell
    world._changes[(x, y + 3, z)] = "oak_leaves"
    outcome = world.act(Action("explore", direction="up"))
    assert outcome.ok and world._cell == (x, y, z) and outcome.travel["dug"] == {}


def test_up_stops_before_a_liquid_overhead():
    world = underground(depth=5)
    x, y, z = world._cell
    world._changes[(x, y + 3, z)] = "water"
    outcome = world.act(Action("explore", direction="up"))
    assert outcome.ok and outcome.detail["sky"] is False
    assert world._cell[1] == y + 2 and "water" not in outcome.travel["dug"]


def test_up_is_unreachable_when_water_is_directly_overhead():
    world = underground(depth=5)
    x, y, z = world._cell
    world._changes[(x, y + 1, z)] = "water"
    outcome = world.act(Action("explore", direction="up"))
    assert not outcome.ok and outcome.error == "unreachable" and world._cell == (x, y, z)


# -- a life that starts underground ----------------------------------------------------


def test_a_bot_underground_comes_up_and_gets_its_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "TRAVEL_DAMAGE_CHANCE", 0.0)
    monkeypatch.setattr(environment, "STEP_DAMAGE_CHANCE", 0.0)
    world = underground(depth=45, stone_pickaxe=1)
    rock_look = world.look
    # Deep in rock the look sees nothing, as in the live run.
    world.look = lambda blocks, radius=64: NOTHING_SEEN if world._cell[1] < 0 else rock_look(blocks, radius)
    assert world.act(Action("mine", "#log")).error == "not_found"

    episode = make_episode(world, tmp_path)
    outcome = episode.act(Action("mine", "#log"))

    assert outcome.ok and outcome.block_removed and outcome.detail["block"].endswith("_log")
    explores = [e["action_obj"]["direction"] for e in episode.log.events if e["kind"] == "step" and e["action_obj"]["kind"] == "explore"]
    assert explores == ["up"]
    assert any(e["kind"] == "travel" and e["dug"] for e in episode.log.events)
    assert episode.goal_met and episode.counts["#cobblestone"] > 0  # the dig on the way was picked up
