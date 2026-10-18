"""Chests and home stations (v3.5): NaturalWorld's deposit, withdraw and set_stations, and MineflayerBackend's
/deposit, /withdraw and the station field of /craft and /smelt against a fake HTTP bot (contracts/bot_api.md v3.5)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager.environment import INVENTORY_SLOTS, THREATENED_ACTIONS, NaturalWorld
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend
from schema_voyager.models import Action

# --- the simulator ---------------------------------------------------------------


def _world(**inventory):
    world = NaturalWorld(7)
    world.inventory.update(inventory)
    return world


def _site(world):
    """The (x, y, z) of a cell on a made-up 5x5 grass floor with air above it."""
    ground = world._column(0, 0)[0]
    for x in range(-2, 5):
        for z in range(-2, 5):
            world._changes[(x, ground, z)] = "grass_block"
            for y in range(ground + 1, ground + 5):
                world._changes[(x, y, z)] = "air"
    return (2, ground + 1, 2)


def _chest(world):
    """Place a chest the world is given for the purpose and return its cell."""
    pos = _site(world)
    world.inventory["chest"] += 1
    assert world.act(Action("place", target="chest", pos=pos)).ok
    return pos


def _withdraw(world, pos, item, times):
    return world.act(Action("withdraw", target=item, times=times, pos=pos))


def test_withdraw_is_threatened():
    assert "withdraw" in THREATENED_ACTIONS


def test_deposit_and_withdraw_round_trip():
    world = _world(coal=10, dirt=5)
    pos = _chest(world)

    result = world.deposit(pos, {"coal": 8, "dirt": 9, "stone": 3})
    assert result.error is None
    assert result.moved == {"coal": 8, "dirt": 5}
    assert result.contents == {"coal": 8, "dirt": 5}
    assert result.detail["chest_full"] is False
    assert result.detail["chest_free_slots"] == 25
    assert result.detail["free_slots"] == 35
    assert "distance" in result.detail["travel"]
    assert world.inventory["coal"] == 2 and world.inventory["dirt"] == 0

    outcome = _withdraw(world, pos, "coal", 3)
    assert outcome.ok and outcome.error is None
    assert outcome.inventory_delta == {"coal": 3}
    assert outcome.detail["withdrawn"] == {"coal": 3}
    assert outcome.detail["chest"] == {"coal": 5, "dirt": 5}
    assert outcome.detail["inventory_full"] is False
    assert world.inventory["coal"] == 5


def test_withdraw_short_is_not_found_with_the_chest_contents():
    world = _world(coal=2)
    pos = _chest(world)
    world.deposit(pos, {"coal": 2})

    outcome = _withdraw(world, pos, "coal", 5)

    assert not outcome.ok and outcome.error == "not_found"
    assert outcome.detail["withdrawn"] == {"coal": 2}
    assert outcome.detail["chest"] == {}


def test_chest_holds_27_slots_and_deposit_stops_when_full():
    world = _world(cobblestone=64 * 30, coal=3)
    pos = _chest(world)

    result = world.deposit(pos, {"cobblestone": 64 * 30, "coal": 3})

    assert result.moved == {"cobblestone": 64 * 27}
    assert result.detail["chest_full"] is True
    assert result.detail["chest_free_slots"] == 0
    assert world.inventory["cobblestone"] == 64 * 3 and world.inventory["coal"] == 3


def test_deposit_fills_a_partial_stack_in_a_full_chest():
    world = _world(cobblestone=64 * 26 + 10 + 100)
    pos = _chest(world)
    world.deposit(pos, {"cobblestone": 64 * 26 + 10})  # 27 slots, the last one holding 10

    result = world.deposit(pos, {"cobblestone": 100})

    assert result.moved == {"cobblestone": 54}
    assert result.detail["chest_full"] is True


def test_withdraw_stops_when_the_inventory_is_full():
    world = _world(coal=10)
    pos = _chest(world)
    world.deposit(pos, {"coal": 10})
    world.inventory["dirt"] += 64 * INVENTORY_SLOTS  # every slot taken

    outcome = _withdraw(world, pos, "coal", 4)

    assert not outcome.ok and outcome.error == "not_found"
    assert outcome.detail["inventory_full"] is True
    assert outcome.detail["withdrawn"] == {}
    assert outcome.detail["chest"] == {"coal": 10}

    world.inventory["dirt"] -= 64  # one free slot: 4 fit
    assert _withdraw(world, pos, "coal", 4).ok


def test_a_cell_that_is_not_a_chest_answers_not_found():
    world = _world(coal=1)
    pos = _site(world)

    result = world.deposit(pos, {"coal": 1})
    outcome = _withdraw(world, pos, "coal", 1)

    assert result.error == "not_found" and result.moved == {} and result.contents == {}
    assert outcome.error == "not_found" and "chest" not in outcome.detail
    assert world.inventory["coal"] == 1


def test_a_chest_in_unreachable_cells_answers_unreachable():
    world = _world(coal=1)
    pos = _chest(world)
    world.unreachable_cells.add(pos)

    assert world.deposit(pos, {"coal": 1}).error == "unreachable"
    assert _withdraw(world, pos, "coal", 1).error == "unreachable"
    assert world.inventory["coal"] == 1


def _craft_pickaxe(world):
    return world.act(Action("craft", target="wooden_pickaxe", times=1))


def test_craft_uses_the_home_table_when_none_is_held():
    world = _world(oak_planks=3, stick=2)
    pos = _site(world)
    world._changes[pos] = "crafting_table"
    world.set_stations({"crafting_table": pos})

    outcome = _craft_pickaxe(world)

    assert outcome.ok
    assert outcome.detail["used_home_station"] is True
    assert outcome.detail["used_table"] is True
    assert outcome.detail["table_retrieved"] is None
    assert world.inventory["wooden_pickaxe"] == 1
    assert world.block_at(pos) == "crafting_table"


def test_a_held_table_is_preferred_over_the_home_one():
    world = _world(oak_planks=3, stick=2, crafting_table=1)
    pos = _site(world)
    world._changes[pos] = "crafting_table"
    world.set_stations({"crafting_table": pos})

    outcome = _craft_pickaxe(world)

    assert outcome.ok
    assert "used_home_station" not in outcome.detail
    assert outcome.detail["table_retrieved"] is True


def test_craft_without_stations_is_missing_the_table_without_station_gone():
    outcome = _craft_pickaxe(_world(oak_planks=3, stick=2))
    assert outcome.error == "missing_crafting_table" and "station_gone" not in outcome.detail


def test_craft_reports_station_gone_after_the_table_cell_is_cleared():
    world = _world(oak_planks=3, stick=2)
    pos = _site(world)
    world._changes[pos] = "crafting_table"
    world.set_stations({"crafting_table": pos})
    assert world.act(Action("clear", pos=pos)).ok

    outcome = _craft_pickaxe(world)

    assert outcome.error == "missing_crafting_table"
    assert outcome.detail["station_gone"] is True


def test_craft_reports_station_unreachable_for_an_unreachable_table():
    world = _world(oak_planks=3, stick=2)
    pos = _site(world)
    world._changes[pos] = "crafting_table"
    world.set_stations({"crafting_table": pos})
    world.unreachable_cells.add(pos)

    outcome = _craft_pickaxe(world)

    assert outcome.error == "unreachable"
    assert outcome.detail["station_unreachable"] is True


def _smelt(world):
    return world.act(Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1))


def test_smelt_uses_the_home_furnace_then_reports_it_gone_or_unreachable():
    world = _world(raw_iron=3, coal=3)
    pos = _site(world)
    world._changes[pos] = "furnace"
    world.set_stations({"furnace": pos})

    outcome = _smelt(world)
    assert outcome.ok
    assert outcome.detail["used_home_station"] is True and outcome.detail["furnace_retrieved"] is None

    world.unreachable_cells.add(pos)
    outcome = _smelt(world)
    assert outcome.error == "unreachable" and outcome.detail["station_unreachable"] is True

    world.unreachable_cells.clear()
    world._changes[pos] = "air"
    outcome = _smelt(world)
    assert outcome.error == "missing_furnace" and outcome.detail["station_gone"] is True


# --- the Mineflayer backend --------------------------------------------------------


class FakeBot:
    """Serves canned JSON per path (or an HTTP error status) and records every request."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.statuses: dict[str, int] = {}
        self.requests: list[tuple[str, dict]] = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                bot.requests.append((self.path, body))
                payload = json.dumps(bot.responses.get(self.path, {"ok": True})).encode()
                self.send_response(bot.statuses.get(self.path, 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def bodies(self, path):
        return [body for requested, body in self.requests if requested == path]


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def backend(bot):
    return MineflayerBackend(api_url=bot.url, natural=True)


CHEST = (5, 76, 36)
TRAVEL = {"distance": 4.0, "dug": {}, "placed": {}, "picked_up": {}}


def test_deposit_ok(bot, backend):
    bot.responses["/deposit"] = {
        "ok": True, "deposited": {"coal": 64}, "chest": {"coal": 64}, "chest_free_slots": 26, "chest_full": False,
        "inventory": {"dirt": 3}, "free_slots": 35, "travel": TRAVEL, "events": [],
    }

    result = backend.deposit(CHEST, {"coal": 64, "dirt": 120})

    assert bot.requests == [("/deposit", {"pos": [5, 76, 36], "items": {"coal": 64, "dirt": 120}})]
    assert result.error is None
    assert result.moved == {"coal": 64} and result.contents == {"coal": 64}
    assert result.detail == {"chest_full": False, "chest_free_slots": 26, "free_slots": 35, "travel": TRAVEL}


def test_deposit_short_because_the_chest_is_full(bot, backend):
    bot.responses["/deposit"] = {
        "ok": True, "deposited": {"coal": 10}, "chest": {"coal": 10}, "chest_full": True, "free_slots": 30, "travel": TRAVEL,
    }

    result = backend.deposit(CHEST, {"coal": 64})

    assert result.moved == {"coal": 10} and result.detail["chest_full"] is True


@pytest.mark.parametrize("code", ["not_found", "unreachable", "interrupted", "timeout", "other_error"])
def test_deposit_action_errors_come_back_in_the_result(bot, backend, code):
    bot.responses["/deposit"] = {"ok": False, "error": code, "detail": {"found": "dirt"}, "travel": TRAVEL}

    result = backend.deposit(CHEST, {"coal": 1})

    assert result.error == code
    assert result.moved == {} and result.contents == {}
    assert result.detail == {"detail": {"found": "dirt"}, "travel": TRAVEL}


def test_deposit_raises_on_a_500_and_a_lost_bot(bot, backend):
    bot.statuses["/deposit"] = 500
    with pytest.raises(BackendError):
        backend.deposit(CHEST, {"coal": 1})

    bot.statuses.clear()
    bot.responses["/deposit"] = {"ok": False, "error": "not_connected"}
    with pytest.raises(BackendError):
        backend.deposit(CHEST, {"coal": 1})


def test_deposit_and_withdraw_carry_protect(bot, backend):
    backend.set_protected([((0, 70, 0), (4, 4, 4))])
    backend.deposit(CHEST, {"coal": 1})
    backend.act(Action("withdraw", target="coal", times=1, pos=CHEST))

    protect = [{"origin": [0, 70, 0], "size": [4, 4, 4]}]
    assert bot.bodies("/deposit") == [{"pos": [5, 76, 36], "items": {"coal": 1}, "protect": protect}]
    assert bot.bodies("/withdraw") == [{"pos": [5, 76, 36], "items": {"coal": 1}, "protect": protect}]


def _withdraw_action(times=2):
    return Action("withdraw", target="coal", times=times, pos=CHEST)


def test_withdraw_ok(bot, backend):
    bot.responses["/withdraw"] = {
        "ok": True, "withdrawn": {"coal": 2}, "chest": {"coal": 3}, "inventory_full": False,
        "inventory": {"coal": 2}, "travel": TRAVEL, "events": [{"type": "x"}],
    }

    outcome = backend.act(_withdraw_action())

    assert bot.requests == [("/withdraw", {"pos": [5, 76, 36], "items": {"coal": 2}})]
    assert outcome.ok and outcome.error is None
    assert outcome.inventory_delta == {"coal": 2} and outcome.inventory == {"coal": 2}
    assert outcome.blocks_walked == 4.0 and outcome.travel == TRAVEL and outcome.events == [{"type": "x"}]
    assert outcome.detail["withdrawn"] == {"coal": 2}
    assert outcome.detail["chest"] == {"coal": 3}
    assert outcome.detail["inventory_full"] is False


def test_withdraw_short_is_not_found_and_keeps_the_chest(bot, backend):
    bot.responses["/withdraw"] = {
        "ok": True, "withdrawn": {"coal": 1}, "chest": {}, "inventory_full": True, "inventory": {"coal": 1}, "travel": TRAVEL,
    }

    outcome = backend.act(_withdraw_action())

    assert not outcome.ok and outcome.error == "not_found"
    assert outcome.detail["withdrawn"] == {"coal": 1}
    assert outcome.detail["chest"] == {}
    assert outcome.detail["inventory_full"] is True


def test_withdraw_from_a_missing_chest_has_no_chest_in_the_detail(bot, backend):
    bot.responses["/withdraw"] = {"ok": False, "error": "not_found", "detail": {"found": "dirt"}, "inventory": {}, "travel": TRAVEL}

    outcome = backend.act(_withdraw_action())

    assert not outcome.ok and outcome.error == "not_found"
    assert "chest" not in outcome.detail
    assert outcome.detail["detail"] == {"found": "dirt"}


def test_withdraw_unreachable_maps_to_the_outcome_error(bot, backend):
    bot.responses["/withdraw"] = {"ok": False, "error": "unreachable", "inventory": {}, "travel": TRAVEL}

    outcome = backend.act(_withdraw_action())

    assert outcome.error == "unreachable" and "chest" not in outcome.detail


def test_craft_and_smelt_carry_station_and_protect_when_set(bot, backend):
    backend._latest_inventory = {"oak_planks": 3, "stick": 2, "raw_iron": 1, "coal": 1}
    backend.set_protected([((0, 70, 0), (4, 4, 4))])
    backend.set_stations({"crafting_table": (1, 2, 3), "furnace": (4, 5, 6)})

    backend.act(Action("craft", target="wooden_pickaxe", times=1))
    backend.act(Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1))

    protect = [{"origin": [0, 70, 0], "size": [4, 4, 4]}]
    assert bot.bodies("/craft") == [{"item": "wooden_pickaxe", "times": 1, "protect": protect, "station": [1, 2, 3]}]
    assert bot.bodies("/smelt") == [{
        "ingredient": "raw_iron", "item": "iron_ingot", "times": 1, "fuel": "coal", "protect": protect, "station": [4, 5, 6],
    }]


def test_craft_and_smelt_bodies_are_unchanged_without_stations_or_boxes(bot, backend):
    backend._latest_inventory = {"oak_planks": 3, "stick": 2, "raw_iron": 1, "coal": 1}

    backend.act(Action("craft", target="wooden_pickaxe", times=1))
    backend.act(Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1))

    assert bot.bodies("/craft") == [{"item": "wooden_pickaxe", "times": 1}]
    assert bot.bodies("/smelt") == [{"ingredient": "raw_iron", "item": "iron_ingot", "times": 1, "fuel": "coal"}]


def test_a_station_is_sent_only_for_its_own_route(bot, backend):
    backend._latest_inventory = {"oak_planks": 3, "stick": 2, "raw_iron": 1, "coal": 1}
    backend.set_stations({"furnace": (4, 5, 6)})

    backend.act(Action("craft", target="wooden_pickaxe", times=1))

    assert "station" not in bot.bodies("/craft")[0]


def test_craft_parses_the_station_flags(bot, backend):
    backend._latest_inventory = {"oak_planks": 3, "stick": 2}
    bot.responses["/craft"] = {
        "ok": False, "error": "unreachable", "station_unreachable": True, "used_home_station": False,
        "inventory": {"oak_planks": 3, "stick": 2},
    }

    outcome = backend.act(Action("craft", target="wooden_pickaxe", times=1))

    assert outcome.detail["station_unreachable"] is True
    assert outcome.detail["used_home_station"] is False
    assert "station_gone" not in outcome.detail


def test_craft_ors_the_flags_across_split_planks_calls(bot, backend):
    backend._latest_inventory = {"oak_log": 1, "birch_log": 1}
    answers = iter([
        {"ok": True, "used_home_station": False, "inventory": {"birch_log": 1}},
        {"ok": False, "error": "missing_crafting_table", "station_gone": True, "inventory": {}},
    ])
    backend._post = lambda path, payload=None, **kw: next(answers)

    outcome = backend.act(Action("craft", target="#planks", times=2))

    assert outcome.detail["station_gone"] is True
    assert outcome.detail["used_home_station"] is False


def test_smelt_parses_the_station_flags(bot, backend):
    backend._latest_inventory = {"raw_iron": 1, "coal": 1}
    bot.responses["/smelt"] = {
        "ok": True, "used_home_station": True, "used_furnace": True, "furnace_retrieved": None, "inventory": {"iron_ingot": 1},
    }

    outcome = backend.act(Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1))

    assert outcome.ok and outcome.detail["used_home_station"] is True
    assert "station_gone" not in outcome.detail
