"""survey and goto: MineflayerBackend against a fake HTTP bot (contracts/bot_api.md v3.4b) and NaturalWorld's own ranking."""

import math

import pytest

from schema_voyager import environment
from schema_voyager.environment import THREATENED_ACTIONS, NaturalWorld
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Site
from tests.test_v3_toss_backend import FakeBot

SEED = 7
TRAVEL = {"distance": 6.5, "dug": {}, "placed": {}, "picked_up": {}}


# --- MineflayerBackend ---------------------------------------------------------


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture(params=[False, True], ids=["plain", "natural"])
def backend(request, bot):
    return MineflayerBackend(api_url=bot.url, natural=request.param, arena=object())


def test_survey_posts_size_and_radius_and_keeps_the_bots_order(bot, backend):
    bot.responses["/survey"] = {
        "ok": True,
        "sites": [
            {"origin": [8, 64, -5], "cut": 3, "fill": 1, "distance": 6.2},
            {"origin": [-2, 63, 9], "cut": 0, "fill": 4, "distance": 7},
        ],
        "considered": 1760,
        "ruled_out": {"liquid": 120, "tree": 300, "unknown": 0, "steep": 40, "protected": 0},
    }
    sites = backend.survey((7, 5), radius=20)
    assert bot.requests == [("/survey", {"size": [7, 5], "radius": 20})]
    assert sites == [Site((8, 64, -5), 3, 1, 6.2), Site((-2, 63, 9), 0, 4, 7.0)]
    assert sites[0].cost == 4 and isinstance(sites[1].distance, float)


def test_survey_with_no_site_is_an_empty_list(bot, backend):
    bot.responses["/survey"] = {"ok": True, "sites": []}
    assert backend.survey((16, 16)) == []
    assert bot.requests == [("/survey", {"size": [16, 16], "radius": 24})]


def test_goto_payload_and_outcome(bot, backend):
    bot.responses["/goto"] = {
        "ok": True,
        "arrived": True,
        "inventory": {"oak_planks": 12},
        "inventory_before_step": {"oak_planks": 12},
        "travel": TRAVEL,
        "events": [{"seq": 4, "type": "note"}],
    }
    outcome = backend.act(Action("goto", pos=(10, 64, -3)))
    assert bot.requests == [("/goto", {"x": 10, "y": 64, "z": -3, "range": 3})]
    assert outcome.ok and outcome.error is None
    assert outcome.inventory == {"oak_planks": 12} and outcome.inventory_delta == {}
    assert outcome.travel == TRAVEL and outcome.blocks_walked == 6.5
    assert outcome.events == [{"seq": 4, "type": "note"}]
    assert outcome.detail == {
        "arrived": True,
        "inventory_before_step": {"oak_planks": 12},
        "target": {"x": 10, "y": 64, "z": -3},
    }


def test_goto_unreachable_and_interrupted_errors(bot, backend):
    bot.responses["/goto"] = {"ok": False, "error": "unreachable", "inventory": {}, "travel": TRAVEL}
    outcome = backend.act(Action("goto", pos=(1, 2, 3)))
    assert not outcome.ok and outcome.error == "unreachable" and outcome.blocks_walked == 6.5

    bot.responses["/goto"] = {
        "ok": False,
        "error": "interrupted",
        "detail": {"threat": "zombie", "outcome": "fled"},
        "inventory": {},
    }
    outcome = backend.act(Action("goto", pos=(1, 2, 3)))
    assert outcome.error == "interrupted"
    assert outcome.detail == {"threat": "zombie", "outcome": "fled", "target": {"x": 1, "y": 2, "z": 3}}


def test_goto_needs_a_pos(backend):
    with pytest.raises(ValueError):
        backend.act(Action("goto"))


# --- NaturalWorld.survey -------------------------------------------------------


def _top_of(world, x, z):
    """(y, block) of the first thing that is not air or ground cover, going down from high above."""
    cover = ("air", "short_grass", "tall_grass", "poppy", "dandelion")
    return next((y, block) for y in range(60, -50, -1) if (block := world.block_at((x, y, z))) not in cover)


def _columns(site, size):
    x0, _, z0 = site.origin
    return [(x, z) for x in range(x0, x0 + size[0]) for z in range(z0, z0 + size[1])]


def test_survey_is_sorted_and_avoids_trees_and_water():
    world = NaturalWorld(SEED)
    sites = world.survey((5, 5))
    assert 0 < len(sites) <= 10
    assert [(s.cost, s.distance, s.origin[0], s.origin[2]) for s in sites] == sorted(
        (s.cost, s.distance, s.origin[0], s.origin[2]) for s in sites
    )
    for site in sites:
        for x, z in _columns(site, (5, 5)):
            _, top = _top_of(world, x, z)
            assert top in ("grass_block", "stone"), (site, x, z, top)


def test_survey_rules_out_a_tree_and_a_pool():
    world = NaturalWorld(SEED)
    best = world.survey((5, 5))[0]
    x, z = _columns(best, (5, 5))[12]
    ground = best.origin[1] - 1

    world._changes[(x, ground + 1, z)] = "oak_log"
    assert best.origin not in [s.origin for s in world.survey((5, 5))]

    world._changes[(x, ground + 1, z)] = "water"
    assert best.origin not in [s.origin for s in world.survey((5, 5))]


def test_survey_cut_and_fill_match_a_hand_count():
    world = NaturalWorld(SEED)
    site = world.survey((5, 5))[0]
    floor = site.origin[1]
    columns = _columns(site, (5, 5))
    grounds = [_top_of(world, x, z)[0] for x, z in columns]
    assert max(grounds) - min(grounds) <= 4  # inspected: what the hand count below is made of
    median = sorted(grounds)[12]
    assert floor == median + 1
    assert site.cut == sum(max(0, g - median) for g in grounds)
    assert site.fill == sum(max(0, median - g) for g in grounds)
    assert site.distance == round(math.hypot(site.origin[0] + 2.5 - 0.5, site.origin[2] + 2.5 - 0.5), 1)


def test_a_bump_adds_cut_and_a_hole_adds_fill(monkeypatch):
    monkeypatch.setattr(environment, "SURVEY_MAX_SITES", 10_000)  # the changed site no longer ranks in the first ten
    world = NaturalWorld(SEED)
    site = world.survey((5, 5))[0]
    floor = site.origin[1]
    columns = _columns(site, (5, 5))
    # a bump: one column rises to the floor, so one block is cut
    bump_x, bump_z = columns[3]
    world._changes[(bump_x, floor, bump_z)] = "dirt"
    # a hole: one column drops a block under the layer below the floor, so one block is filled
    hole_x, hole_z = columns[21]
    world._changes[(hole_x, floor - 1, hole_z)] = "air"
    after = next(s for s in world.survey((5, 5)) if s.origin == site.origin)
    assert (after.cut, after.fill) == (site.cut + 1, site.fill + 1)


def test_protected_box_rules_out_a_site_whose_ring_overlaps_it():
    world = NaturalWorld(SEED)
    site = world.survey((5, 5))[0]
    x0, y, z0 = site.origin

    def origins(box):
        return [s.origin for s in world._rank_sites((5, 5), 24, protected=[box])]

    assert site.origin in origins(((x0 + 8, y, z0), (3, 3, 3)))
    assert site.origin in origins(((x0 + 6, y, z0), (3, 3, 3)))  # beyond the ring: x0 + 5 is the ring's last column
    assert site.origin not in origins(((x0 + 5, y, z0), (3, 3, 3)))  # in the ring
    assert site.origin not in origins(((x0 + 2, y + 40, z0 + 2), (1, 1, 1)))  # x and z only


def test_an_underground_bot_has_no_ground():
    world = NaturalWorld(SEED)
    world._cell = (0, -20, 0)
    assert world.survey((3, 3)) == []


# --- NaturalWorld goto ---------------------------------------------------------


def test_goto_is_a_threatened_action():
    assert "goto" in THREATENED_ACTIONS


def test_goto_moves_within_range_and_reports_travel():
    world = NaturalWorld(SEED)
    ground = world._column(10, 4)[0]
    outcome = world.act(Action("goto", pos=(10, ground + 1, 4)))
    x, y, z = world._cell
    assert outcome.ok and outcome.detail["arrived"] is True
    assert outcome.detail["target"] == {"x": 10, "y": ground + 1, "z": 4}
    assert math.hypot(x - 10, z - 4) <= 3
    assert outcome.travel["distance"] == round(math.hypot(x, z), 1) > 0
    assert outcome.blocks_walked == round(world.blocks_walked, 1)
    assert outcome.inventory_delta == {}


def test_goto_where_the_bot_already_is_walks_nowhere():
    world = NaturalWorld(SEED)
    start = world._cell
    outcome = world.act(Action("goto", pos=start))
    assert outcome.ok and outcome.travel["distance"] == 0 and world._cell == start


def test_goto_inside_solid_rock_is_unreachable():
    world = NaturalWorld(SEED)
    start = world._cell
    outcome = world.act(Action("goto", pos=(10, -20, 4)))
    assert not outcome.ok and outcome.error == "unreachable"
    assert outcome.detail["arrived"] is False and world._cell == start


def test_goto_needs_a_pos_in_the_simulator():
    with pytest.raises(ValueError):
        NaturalWorld(SEED).act(Action("goto"))
