"""Protected boxes (v3.4b): set_protected on MineflayerBackend (the payloads) and NaturalWorld (the rules)."""

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action
from tests.test_v3_toss_backend import FakeBot

SEED = 7
BOX = ((8, 64, -5), (7, 4, 5))
PROTECT = [{"origin": [8, 64, -5], "size": [7, 4, 5]}]

ACTIONS = {
    "/mine": Action("mine", target="oak_log"),
    "/explore": Action("explore", direction="north"),
    "/look": None,
    "/place": Action("place", target="oak_planks", pos=(1, 2, 3)),
    "/clear": Action("clear", pos=(1, 2, 3)),
    "/goto": Action("goto", pos=(1, 2, 3)),
    "/survey": None,
}


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture(params=[False, True], ids=["plain", "natural"])
def backend(request, bot):
    return MineflayerBackend(api_url=bot.url, natural=request.param, arena=object())


def _call(backend, bot, route):
    bot.responses["/survey"] = {"ok": True, "sites": []}
    bot.responses["/look"] = {"ok": True, "directions": {}}
    bot.responses["/observe"] = {"ok": True, "inventory": {}}
    bot.requests.clear()
    action = ACTIONS[route]
    if route == "/look":
        backend.look(["oak_log"]) if backend.natural else None
    elif route == "/survey":
        backend.survey((5, 5))
    else:
        backend.act(action)
    return [body for path, body in bot.requests if path == route]


@pytest.mark.parametrize("route", list(ACTIONS))
def test_protect_rides_every_route_once_set_and_not_before_or_after(bot, backend, route):
    if route == "/look" and not backend.natural:
        pytest.skip("look is a natural-mode route")
    (before,) = _call(backend, bot, route)
    assert "protect" not in before

    backend.set_protected([BOX])
    (during,) = _call(backend, bot, route)
    assert during["protect"] == PROTECT

    backend.set_protected([])
    (after,) = _call(backend, bot, route)
    assert after == before


def test_natural_explore_up_carries_protect(bot):
    backend = MineflayerBackend(api_url=bot.url, natural=True)
    backend.set_protected([BOX])
    backend.act(Action("explore", direction="up"))
    assert bot.requests[0][1]["protect"] == PROTECT
    assert bot.requests[0][1]["direction"] == "up"


def test_set_protected_stores_a_copy(bot):
    backend = MineflayerBackend(api_url=bot.url, natural=True)
    boxes = [BOX]
    bot.responses["/survey"] = {"ok": True, "sites": []}
    backend.set_protected(boxes)
    boxes.clear()
    backend.survey((5, 5))
    assert bot.requests[0][1]["protect"] == PROTECT


# --- the simulator -------------------------------------------------------------


def _world():
    world = NaturalWorld(SEED)
    world.inventory["iron_pickaxe"] = 1
    return world


def _logs_around_bot(world, count=3):
    """Log cells on open made-up ground beside the bot, returned in nearest-first order."""
    x, y, z = world._cell
    cells = []
    for i in range(count):
        for level in range(y, y + 3):
            world._changes[(x + 2 + i, level, z)] = "air"
        world._changes[(x + 2 + i, y - 1, z)] = "grass_block"
        cells.append((x + 2 + i, y, z))
        world._changes[cells[-1]] = "oak_log"
    return cells


def _box_around(cells):
    xs, ys, zs = zip(*cells)
    origin = (min(xs), min(ys), min(zs))
    return (origin, (max(xs) - origin[0] + 1, max(ys) - origin[1] + 1, max(zs) - origin[2] + 1))


def test_mine_never_takes_a_protected_log():
    world = _world()
    logs = _logs_around_bot(world)
    box = _box_around(logs[:2])
    world.set_protected([box])
    before = world.scan(*box)
    outcome = world.act(Action("mine", target="#log"))
    assert world.scan(*box) == before
    if outcome.ok:
        assert outcome.detail["target"] not in [{"x": x, "y": y, "z": z} for x, y, z in logs[:2]]
    else:
        assert outcome.error in ("not_found", "hazard")
    for _ in range(3):
        world.act(Action("mine", target="#log"))
    assert world.scan(*box) == before


def test_choose_target_skips_a_protected_log():
    world = _world()
    log = _logs_around_bot(world, count=1)[0]
    assert world._choose_target({"oak_log"}, []).pos == log
    world.set_protected([(log, (1, 1, 1))])
    assert getattr(world._choose_target({"oak_log"}, []), "pos", None) != log


def test_look_leaves_out_protected_blocks():
    world = _world()
    logs = _logs_around_bot(world, count=2)
    seen = lambda: {(b["x"], b["y"], b["z"]) for sector in world.look(["oak_log"]).values() for b in sector.values()}
    assert logs[0] in seen()
    world.set_protected([_box_around(logs[:1])])
    assert logs[0] not in seen()


def test_a_buried_target_behind_a_protected_cell_is_skipped():
    world = _world()
    x, y, z = world._cell
    for dx in range(1, 6):
        for dy in range(-1, 3):
            for dz in range(-1, 2):
                world._changes[(x + dx, y + dy, z + dz)] = "stone"
    world._changes[(x + 4, y, z)] = "iron_ore"
    plain = world._choose_target({"iron_ore"}, [])
    assert getattr(plain, "pos", None) == (x + 4, y, z)
    assert plain.path

    world.set_protected([((x + 2, y, z), (1, 1, 1))])
    skipped = world._choose_target({"iron_ore"}, [])
    assert getattr(skipped, "pos", None) != (x + 4, y, z)


def test_explore_up_stops_below_a_protected_roof():
    world = _world()
    x, y, z = world._cell
    for level in range(y + 1, y + 6):
        world._changes[(x, level, z)] = "air"
    for level in (y + 1, y + 2, y + 3):
        world._changes[(x, level, z)] = "oak_planks"
    world.set_protected([((x, y + 2, z), (1, 2, 1))])
    outcome = world.act(Action("explore", direction="up"))
    assert [world._changes[(x, level, z)] for level in (y + 1, y + 2, y + 3)] == ["air", "oak_planks", "oak_planks"]
    assert outcome.detail["sky"] is False


def test_explore_up_under_a_protected_roof_with_nothing_to_climb_is_unreachable():
    world = _world()
    x, y, z = world._cell
    world._changes[(x, y + 1, z)] = "oak_planks"
    world.set_protected([((x, y + 1, z), (1, 1, 1))])
    assert world.act(Action("explore", direction="up")).error == "unreachable"
    assert world._changes[(x, y + 1, z)] == "oak_planks"


def test_survey_rules_out_sites_overlapping_a_protected_box():
    world = _world()
    free = world.survey((5, 5))
    assert free
    x, _, z = world._cell
    world.set_protected([((x - 30, 0, z - 30), (60, 200, 60))])
    assert world.survey((5, 5)) == []
    world.set_protected([])
    assert world.survey((5, 5)) == free
