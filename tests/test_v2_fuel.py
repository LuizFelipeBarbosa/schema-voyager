"""Fuel values (items smelted per fuel item) in the planner and the simulator,
and how a smelt that came up short is checked."""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager import verifier
from schema_voyager.environment import NaturalWorld
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Rule
from schema_voyager.planner import PlanningError, _fuel_units, _Planner

SMELT_RULE = Rule(
    id="smelt_iron_ingot", action={"type": "smelt", "item": "iron_ingot"},
    preconditions={"consumes": {"raw_iron": 1}, "station": "furnace", "fuel_any": ["coal", "#planks", "stick"]},
    effects={"gains": {"iron_ingot": 1}},
)


@pytest.mark.parametrize("fuel,items,units", [
    ("stick", 1, 2), ("stick", 3, 6), ("birch_planks", 1, 1), ("birch_planks", 3, 2),
    ("spruce_log", 3, 2), ("crimson_stem", 2, 2), ("coal", 9, 2), ("charcoal", 8, 1),
])
def test_fuel_units(fuel, items, units):
    assert _fuel_units(fuel, items) == units


def test_cheapest_fuel_prefers_a_held_fuel_in_sufficient_quantity():
    planner = _Planner([SMELT_RULE], lambda item: None, None)
    assert planner._cheapest_fuel(["coal", "stick"], 1, Counter(stick=2), []) == ("stick", [])
    assert planner._cheapest_fuel(["coal", "stick"], 1, Counter(stick=1, coal=1), []) == ("coal", [])


PRIOR_FUELS = ["coal", "charcoal", "#planks", "#log", "stick", "coal_block", "lava_bucket", "blaze_rod"]
STICK_RULE = Rule(
    id="craft_stick", action={"type": "craft", "item": "stick"},
    preconditions={"consumes": {"bamboo": 2}, "station": None}, effects={"gains": {"stick": 2}},
)


def test_cheapest_fuel_skips_fuels_it_does_not_know():
    planner = _Planner([SMELT_RULE], lambda item: None, None)
    assert planner._cheapest_fuel(PRIOR_FUELS, 1, Counter(stick=2), []) == ("stick", [])


def test_cheapest_fuel_plans_the_shortest_known_fuel_when_nothing_is_held():
    planner = _Planner([SMELT_RULE, STICK_RULE], lambda item: None, None)
    inv = Counter(bamboo=2)
    fuel, steps = planner._cheapest_fuel(PRIOR_FUELS, 1, inv, [])
    assert fuel == "stick" and [step.action.target for step in steps] == ["stick"]


def test_cheapest_fuel_with_only_unknown_fuels_raises():
    planner = _Planner([SMELT_RULE], lambda item: None, None)
    with pytest.raises(PlanningError, match="no known fuel"):
        planner._cheapest_fuel(["coal_block", "lava_bucket", "blaze_rod"], 1, Counter(coal_block=5), [])


@pytest.mark.parametrize("fuel,held,times", [("stick", 1, 1), ("birch_planks", 1, 2), ("coal", 1, 9)])
def test_simulator_refuses_a_short_fuel(fuel, held, times):
    world = NaturalWorld(7)
    world.inventory.update({"raw_iron": times, fuel: held, "furnace": 1})
    outcome = world.act(Action("smelt", "iron_ingot", times=times, fuel=fuel, ingredient="raw_iron"))
    assert not outcome.ok and outcome.error == "missing_ingredients"


def test_simulator_spends_the_right_fuel():
    world = NaturalWorld(7)
    world.inventory.update({"raw_iron": 1, "stick": 2, "furnace": 1})
    outcome = world.act(Action("smelt", "iron_ingot", times=1, fuel="stick", ingredient="raw_iron"))
    assert outcome.ok and outcome.inventory_delta == {"raw_iron": -1, "stick": -2, "iron_ingot": 1}


def test_backend_maps_a_short_smelt_to_a_failed_check():
    responses = {
        "/observe": {"ok": True, "inventory": {"raw_iron": 1, "stick": 1, "furnace": 1}, "nearby_blocks": {}},
        "/smelt": {
            "ok": False, "error": "craft_failed", "detail": "smelted 0 of 1", "smelted": 0, "output_taken": 0,
            "ingredient": "raw_iron", "item": "iron_ingot", "times": 1, "fuel": "stick", "fuel_used": 1,
            "used_furnace": True, "furnace_retrieved": True, "inventory_delta": {"stick": -1},
            "inventory": {"raw_iron": 1, "furnace": 1}, "travel": {}, "events": [],
            "inventory_before_step": {"raw_iron": 1, "stick": 1, "furnace": 1},
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps(responses[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        action = Action("smelt", "iron_ingot", times=1, fuel="stick", ingredient="raw_iron")
        outcome = MineflayerBackend(f"http://127.0.0.1:{server.server_address[1]}", natural=True).act(action)
    finally:
        server.shutdown()
        server.server_close()

    assert not outcome.ok and outcome.error == "craft_failed"
    assert outcome.detail["smelted"] == 0 and outcome.detail["detail"] == "smelted 0 of 1"
    check = verifier.check(SMELT_RULE, action, outcome)
    assert check.status == "failed"
