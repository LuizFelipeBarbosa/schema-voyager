"""NaturalWorld and the natural mode of MineflayerBackend (v2).

normalize.py belongs to another lane, so these tests patch in a small fake that
follows the documented examples of docs/v2-interfaces.md."""

import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager import environment, mineflayer_backend, normalize
from schema_voyager.adapter import DIRECTIONS
from schema_voyager.environment import NaturalWorld
from schema_voyager.mineflayer_backend import SCAFFOLD_BLOCKS, BackendError, MineflayerBackend
from schema_voyager.models import Action

FAKE_SPECIES = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry")
ORES = ("coal", "iron", "copper", "gold", "diamond", "redstone", "lapis", "emerald")


def _species_of(name):
    for species in FAKE_SPECIES:
        if name in (f"{species}_log", f"{species}_wood", f"stripped_{species}_log", f"stripped_{species}_wood"):
            return "#log", species
        if name == f"{species}_planks":
            return "#planks", species
        if name == f"{species}_leaves":
            return "#leaves", species
    return None, None


def _fake_family(name):
    group, _ = _species_of(name)
    if group:
        return group
    if name in ("cobblestone", "cobbled_deepslate", "blackstone"):
        return "#cobblestone"
    if name.startswith("deepslate_") and name.endswith("_ore"):
        return name.removeprefix("deepslate_")
    return name


def _fake_members(name):
    universe = [f"{prefix}{s}{suffix}" for s in FAKE_SPECIES for prefix, suffix in
                (("", "_log"), ("", "_wood"), ("stripped_", "_log"), ("stripped_", "_wood"), ("", "_planks"), ("", "_leaves"))]
    universe += ["cobblestone", "cobbled_deepslate", "blackstone"]
    universe += [ore for kind in ORES for ore in (f"{kind}_ore", f"deepslate_{kind}_ore")]
    return sorted(raw for raw in universe if _fake_family(raw) == name) or [name]


def _fake_resolve(name, inventory):
    if name == "#planks":
        logs = {item: n for item, n in inventory.items() if _fake_family(item) == "#log" and n > 0}
        planks = {item: n for item, n in inventory.items() if _fake_family(item) == "#planks" and n > 0}
        if logs:
            by_species = Counter()
            for item, n in logs.items():
                by_species[_species_of(item)[1]] += n
            return f"{min(by_species, key=lambda s: (-by_species[s], s))}_planks"
        if planks:
            return max(planks, key=lambda item: (planks[item], item))
        raise ValueError("no species held")
    return name


@pytest.fixture(autouse=True)
def fake_normalize(monkeypatch):
    monkeypatch.setattr(normalize, "SPECIES", FAKE_SPECIES)
    monkeypatch.setattr(normalize, "family", _fake_family)
    monkeypatch.setattr(normalize, "members", _fake_members)
    monkeypatch.setattr(normalize, "species", lambda name: _species_of(name)[1])
    monkeypatch.setattr(normalize, "resolve", _fake_resolve)


def _world(seed=7, **inventory):
    world = NaturalWorld(seed)
    world.inventory.update(inventory)
    return world


def _quiet(monkeypatch):
    monkeypatch.setattr(environment, "TRAVEL_DAMAGE_CHANCE", 0.0)
    monkeypatch.setattr(environment, "STEP_DAMAGE_CHANCE", 0.0)


# --- terrain -----------------------------------------------------------------


def test_the_same_seed_gives_the_same_world():
    first, second, other = NaturalWorld(3), NaturalWorld(3), NaturalWorld(4)
    cells = [(x, y, z) for x in range(-20, 20, 3) for z in range(-20, 20, 3) for y in range(-30, 20, 5)]
    assert [first.block_at(c) for c in cells] == [second.block_at(c) for c in cells]
    assert [first.block_at(c) for c in cells] != [other.block_at(c) for c in cells]


def test_the_world_has_every_kind_of_block():
    world = NaturalWorld(5)
    looked = world.look(
        ["coal_ore", "deepslate_coal_ore", "iron_ore", "deepslate_iron_ore", "gravel", "water", "lava", "short_grass", "poppy", "dandelion", "stone"] +
        [f"{s}_{kind}" for s in FAKE_SPECIES for kind in ("log", "leaves")],
        radius=96,
    )
    found = {block for sector in looked.values() for block in sector}
    assert {"coal_ore", "deepslate_coal_ore", "iron_ore", "gravel", "water", "lava", "short_grass", "stone"} <= found
    assert any(block.endswith("_log") for block in found) and any(block.endswith("_leaves") for block in found)


def test_trees_use_one_to_three_species_of_the_given_list():
    for seed in range(6):
        world = NaturalWorld(seed, species=("birch", "spruce", "oak"))
        found = {b.removesuffix("_log") for d in world.look([f"{s}_log" for s in ("birch", "spruce", "oak", "acacia")], 96).values() for b in d}
        assert found and found <= {"birch", "spruce", "oak"} and len(found) <= 3


def test_deepslate_ore_only_below_zero():
    world = NaturalWorld(2)
    ores = world.look(["deepslate_coal_ore", "deepslate_iron_ore", "coal_ore", "iron_ore"], 96)
    for sector in ores.values():
        for block, info in sector.items():
            assert (info["y"] < 0) == block.startswith("deepslate_"), (block, info)


# --- LifeBackend methods -----------------------------------------------------


def test_observe_status_say_and_begin_episode():
    world = _world()
    observation = world.observe()
    assert observation.inventory == {} and observation.nearby
    assert "short_grass" not in observation.nearby and "air" not in observation.nearby
    world.say("hello")  # does nothing
    status = world.status()
    assert (status.health, status.food, status.alive, status.last_seq) == (20.0, 20, True, 0)
    assert status.position == observation.position


def test_mining_an_exposed_log_with_a_hand(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    outcome = world.act(Action("mine", "#log"))
    log = outcome.detail["block"]
    assert outcome.ok and outcome.block_removed and log.endswith("_log")
    assert outcome.drops == {log: 1} and outcome.inventory == {log: 1}
    assert outcome.detail["tool_used"] is None and outcome.detail["inventory_before_step"] == {}
    assert outcome.travel["dug"] == {} and outcome.travel["distance"] >= 0
    assert outcome.blocks_walked == pytest.approx(outcome.travel["distance"], abs=0.1)
    assert world.block_at(tuple(outcome.detail["target"][k] for k in "xyz")) == "air"


def test_mining_a_buried_block_digs_through_and_reports_travel(monkeypatch):
    _quiet(monkeypatch)
    world = _world(wooden_pickaxe=1)
    x, y, z = world._cell
    world._changes[(x, y - 9, z)] = "diamond_ore"  # nothing else is a diamond ore
    column = [world.block_at((x, level, z)) for level in range(y - 1, y - 9, -1)]
    outcome = world.act(Action("mine", "diamond_ore", tool="wooden_pickaxe"))
    assert outcome.ok and outcome.detail["block"] == "diamond_ore" and outcome.detail["tool_used"] == "wooden_pickaxe"
    assert outcome.travel["dug"] == dict(Counter(column))
    picked = outcome.travel["picked_up"]
    assert picked["dirt"] == column.count("dirt") + column.count("grass_block") and picked["cobblestone"] == column.count("stone")
    assert outcome.inventory_delta == {} and outcome.drops == {}  # a diamond ore drops nothing in the whitelist
    assert outcome.inventory == {"wooden_pickaxe": 1, **picked}
    assert outcome.detail["inventory_before_step"] == outcome.inventory
    assert world.status().position[1] == y - 8


def test_group_family_and_deepslate_targets(monkeypatch):
    _quiet(monkeypatch)
    world = _world(stone_pickaxe=1)
    coal = world.act(Action("mine", "coal_ore", tool="stone_pickaxe"))
    assert coal.ok and coal.detail["block"] in ("coal_ore", "deepslate_coal_ore") and coal.drops == {"coal": 1}
    planted = _world(stone_pickaxe=1)
    x, y, z = planted._cell
    for dx in range(-3, 4):
        for dz in range(-3, 4):
            planted._changes[(x + dx, y - 30, z + dz)] = "deepslate_iron_ore"
    iron = planted.act(Action("mine", "iron_ore", tool="stone_pickaxe"))
    assert iron.detail["block"] in ("iron_ore", "deepslate_iron_ore") and iron.drops == {"raw_iron": 1}


def test_stone_and_deepslate_drop_cobblestone_variants_for_a_pickaxe(monkeypatch):
    _quiet(monkeypatch)
    world = _world(wooden_pickaxe=1)
    assert world.act(Action("mine", "stone", tool="wooden_pickaxe")).drops == {"cobblestone": 1}
    assert world.act(Action("mine", "stone")).drops == {}
    deep = world.act(Action("mine", "deepslate", tool="wooden_pickaxe"))
    assert deep.drops == {"cobbled_deepslate": 1} and deep.travel["dug"]


def test_iron_needs_a_stone_pickaxe(monkeypatch):
    _quiet(monkeypatch)
    world = _world(wooden_pickaxe=1, stone_pickaxe=1)
    assert world.act(Action("mine", "iron_ore", tool="wooden_pickaxe")).drops == {}
    assert world.act(Action("mine", "iron_ore", tool="stone_pickaxe")).drops == {"raw_iron": 1}


def test_a_missing_tool_and_an_unknown_target_fail(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    assert world.act(Action("mine", "#log", tool="wooden_pickaxe")).error == "missing_tool"
    assert world.act(Action("mine", "emerald_ore")).error == "not_found"


def test_exposed_blocks_are_preferred_over_nearer_buried_ones(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    x, y, z = world._cell
    world._changes[(x + 20, world._column(x + 20, z)[0] + 1, z)] = "diamond_ore"  # exposed, far
    world._changes[(x, y - 7, z)] = "diamond_ore"  # buried, near
    outcome = world.act(Action("mine", "diamond_ore"))
    assert outcome.detail["target"]["x"] == x + 20 and outcome.travel["dug"] == {}


def test_r2_and_stick2_switch_on_mid_run(monkeypatch):
    _quiet(monkeypatch)
    world = _world(wooden_pickaxe=1, stone_pickaxe=1, oak_planks=20)
    assert world.act(Action("mine", "coal_ore", tool="wooden_pickaxe")).drops == {"coal": 1}
    assert world.act(Action("craft", "stick")).inventory_delta == {"oak_planks": -2, "stick": 4}
    world.set_world_rules(("r2", "stick2"))
    assert world.act(Action("mine", "coal_ore", tool="wooden_pickaxe")).drops == {}
    assert world.act(Action("mine", "coal_ore", tool="stone_pickaxe")).drops == {"coal": 1}
    assert world.act(Action("craft", "stick")).inventory_delta == {"oak_planks": -2, "stick": 2}
    world.set_world_rules(())
    assert world.act(Action("craft", "stick")).inventory_delta == {"oak_planks": -2, "stick": 4}
    assert NaturalWorld(1, world_rules=("r2",)).r2


# --- random drops ------------------------------------------------------------


def _drop_sequence(seed, block, tool, n):
    world = NaturalWorld(seed)
    return [world._drops(block, tool) for _ in range(n)]


def test_random_drops_are_deterministic_by_seed_and_follow_the_rates():
    assert _drop_sequence(1, "gravel", None, 50) == _drop_sequence(1, "gravel", None, 50)
    assert _drop_sequence(1, "gravel", None, 50) != _drop_sequence(2, "gravel", None, 50)
    gravel = _drop_sequence(1, "gravel", None, 2000)
    assert all(d in ({"gravel": 1}, {"flint": 1}) for d in gravel)
    assert 0.07 < sum("flint" in d for d in gravel) / 2000 < 0.13
    grass = _drop_sequence(1, "short_grass", None, 4000)
    assert 0.10 < sum(bool(d) for d in grass) / 4000 < 0.15 and all(d in ({}, {"wheat_seeds": 1}) for d in grass)
    leaves = _drop_sequence(1, "oak_leaves", None, 6000)
    items = Counter(item for d in leaves for item in d)
    assert 0.03 < items["oak_sapling"] / 6000 < 0.07 and 0.01 < items["stick"] / 6000 < 0.035 and items["apple"] > 0
    assert all(1 <= d.get("stick", 1) <= 2 for d in leaves)
    assert "apple" not in {item for d in _drop_sequence(1, "birch_leaves", None, 6000) for item in d}


# --- events, hazards ---------------------------------------------------------


def test_event_phases_and_status_since(monkeypatch):
    monkeypatch.setattr(environment, "TRAVEL_DAMAGE_CHANCE", 1.0)
    monkeypatch.setattr(environment, "STEP_DAMAGE_CHANCE", 1.0)
    world = _world()
    outcome = world.act(Action("mine", "#log"))
    assert [e["phase"] for e in outcome.events] == ["travel", "step"]
    assert all(e["type"] == "damage" and e["amount"] > 0 for e in outcome.events)
    assert [e["seq"] for e in outcome.events] == [1, 2]
    craft = world.act(Action("explore", direction="N"))
    assert [e["phase"] for e in craft.events] == ["travel"]
    status = world.status(since=1)
    assert [e["seq"] for e in status.events] == [2, 3] and status.last_seq == 3
    assert world.status(since=3).events == []


def test_damage_is_mostly_travel_phase_over_many_actions():
    world = _world()
    phases = Counter()
    for i in range(300):
        for event in world.act(Action("explore", direction=DIRECTIONS[i % 8])).events:
            phases[event["phase"]] += 1
        world.act(Action("craft", "stick"))
    assert phases["travel"] > 0 and phases["travel"] > 3 * phases["step"]


def test_a_target_next_to_lava_is_skipped_with_a_hazard_event(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    x, y, z = world._cell
    world._changes[(x + 5, y, z)] = "diamond_ore"
    world._changes[(x + 5, y + 1, z)] = "lava"
    outcome = world.act(Action("mine", "diamond_ore"))
    assert not outcome.ok and outcome.error == "hazard"
    assert [(e["type"], e["phase"], e["kind"]) for e in outcome.events] == [("hazard", "travel", "lava")]
    assert world.block_at((x + 5, y, z)) == "diamond_ore"
    # The skip list remembers it until the next episode.
    assert world.act(Action("mine", "diamond_ore")).events == []
    world.begin_episode()
    assert len(world.act(Action("mine", "diamond_ore")).events) == 1


def test_a_safe_candidate_is_taken_after_a_hazardous_one(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    x, y, z = world._cell
    world._changes[(x + 4, y, z)] = "diamond_ore"
    world._changes[(x + 4, y + 1, z)] = "lava"
    world._changes[(x - 9, y, z)] = "diamond_ore"
    outcome = world.act(Action("mine", "diamond_ore"))
    assert outcome.ok and outcome.detail["target"]["x"] == x - 9
    assert [e["type"] for e in outcome.events] == ["hazard"]


def test_begin_episode_never_changes_the_world(monkeypatch):
    _quiet(monkeypatch)
    world = _world()
    world.act(Action("mine", "#log"))
    world.act(Action("explore", direction="E"))
    before = (dict(world._changes), world._cell, world._inventory())
    observation = world.begin_episode()
    assert (world._changes, world._cell, world._inventory()) == before
    assert observation.blocks_walked == 0.0 and world.blocks_walked == 0.0


# --- crafting ----------------------------------------------------------------


def test_group_planks_take_the_species_of_the_logs(monkeypatch):
    _quiet(monkeypatch)
    world = _world(birch_log=3, oak_log=1)
    outcome = world.act(Action("craft", "#planks", times=2))
    assert outcome.inventory_delta == {"birch_log": -2, "birch_planks": 8}
    assert outcome.detail["item"] == "birch_planks"
    mixed = world.act(Action("craft", "#planks", times=2))  # 1 birch + 1 oak left: mixed pay
    assert mixed.inventory_delta == {"birch_log": -1, "birch_planks": 4, "oak_log": -1, "oak_planks": 4}
    assert world.act(Action("craft", "oak_planks")).error == "missing_ingredients"


def test_a_raw_planks_target_uses_only_its_species():
    world = _world(birch_log=2, oak_log=1)
    assert world.act(Action("craft", "oak_planks", times=2)).error == "missing_ingredients"
    assert world.act(Action("craft", "oak_planks")).inventory_delta == {"oak_log": -1, "oak_planks": 4}


def test_a_mixed_species_pay_takes_the_most_plentiful_first():
    world = _world(oak_planks=2, birch_planks=1, spruce_planks=1, stick=2, crafting_table=1)
    outcome = world.act(Action("craft", "wooden_pickaxe"))
    assert outcome.inventory_delta == {"oak_planks": -2, "birch_planks": -1, "stick": -2, "wooden_pickaxe": 1}
    assert outcome.detail["used_table"] and outcome.detail["table_retrieved"]
    assert outcome.inventory == {"spruce_planks": 1, "crafting_table": 1, "wooden_pickaxe": 1}


def test_cobblestone_variants_pay_stone_tools_and_furnaces():
    world = _world(cobblestone=2, cobbled_deepslate=7, blackstone=2, stick=2, crafting_table=1)
    assert world.act(Action("craft", "furnace")).inventory_delta == {"cobbled_deepslate": -7, "blackstone": -1, "furnace": 1}
    pickaxe = world.act(Action("craft", "stone_pickaxe"))
    assert pickaxe.inventory_delta == {"blackstone": -1, "cobblestone": -2, "stick": -2, "stone_pickaxe": 1}


def test_crafting_rules_of_the_whitelist():
    world = _world(oak_planks=40, stick=20, iron_ingot=9, coal=1, crafting_table=1)
    assert world.act(Action("craft", "crafting_table")).inventory_delta == {"oak_planks": -4, "crafting_table": 1}
    deltas = {item: world.act(Action("craft", item)).inventory_delta for item in
              ("wooden_shovel", "wooden_sword", "torch", "ladder", "chest", "iron_sword", "shears", "bucket")}
    assert deltas["wooden_shovel"] == {"oak_planks": -1, "stick": -2, "wooden_shovel": 1}
    assert deltas["torch"] == {"coal": -1, "stick": -1, "torch": 4}
    assert deltas["ladder"] == {"stick": -7, "ladder": 3}
    assert deltas["chest"] == {"oak_planks": -8, "chest": 1}
    assert deltas["iron_sword"] == {"iron_ingot": -2, "stick": -1, "iron_sword": 1}
    assert deltas["shears"] == {"iron_ingot": -2, "shears": 1} and deltas["bucket"] == {"iron_ingot": -3, "bucket": 1}
    assert world.act(Action("craft", "diamond_pickaxe")).error == "craft_failed"
    assert _world(oak_planks=3, stick=2).act(Action("craft", "wooden_pickaxe")).error == "missing_crafting_table"
    assert _world().act(Action("craft", "#planks")).error == "missing_ingredients"


# --- smelting ----------------------------------------------------------------


def test_smelting_with_coal(monkeypatch):
    _quiet(monkeypatch)
    world = _world(raw_iron=3, coal=1, furnace=1)
    outcome = world.act(Action("smelt", "iron_ingot", times=3, fuel="coal", ingredient="raw_iron"))
    assert outcome.ok and outcome.inventory_delta == {"raw_iron": -3, "coal": -1, "iron_ingot": 3}
    assert outcome.detail["fuel"] == "coal" and outcome.detail["fuel_used"] == 1 and outcome.detail["furnace_retrieved"]
    assert outcome.inventory == {"iron_ingot": 3, "furnace": 1}
    assert outcome.detail["inventory_before_step"] == {"raw_iron": 3, "coal": 1, "furnace": 1}


def test_smelting_with_planks_uses_one_fuel_per_item():
    world = _world(raw_iron=2, birch_planks=2, furnace=1, birch_log=1)
    outcome = world.act(Action("smelt", "iron_ingot", times=2, fuel="#planks", ingredient="raw_iron"))
    assert outcome.inventory_delta == {"raw_iron": -2, "birch_planks": -2, "iron_ingot": 2}
    assert outcome.detail["fuel"] == "birch_planks" and outcome.detail["fuel_used"] == 2


def test_smelting_failures():
    action = Action("smelt", "iron_ingot", fuel="coal", ingredient="raw_iron")
    assert _world(raw_iron=1, furnace=1).act(action).error == "missing_ingredients"
    assert _world(raw_iron=1, coal=1).act(action).error == "missing_furnace"
    assert _world(raw_iron=1, coal=1, furnace=1).act(Action("smelt", "iron_ingot", fuel="dirt", ingredient="raw_iron")).error == "craft_failed"


# --- explore, look, probe ----------------------------------------------------


def test_directional_explore_moves_up_to_32_blocks_and_never_changes_the_world():
    for direction, (sx, sz) in {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}.items():
        world = _world()
        start = world.status().position
        outcome = world.act(Action("explore", direction=direction))
        end = world.status().position
        assert outcome.ok and outcome.travel["distance"] == 32.0 and outcome.blocks_walked == 32.0
        assert (end[0] - start[0], end[2] - start[2]) == (32 * sx, 32 * sz)
        assert outcome.detail["position"] == {"x": end[0], "y": end[1], "z": end[2]}
        assert world._changes == {}
    diagonal = _world()
    diagonal.act(Action("explore", direction="NE"))
    x, _, z = diagonal.status().position
    assert x > 0.5 and z < 0.5


def test_explore_stops_before_lava_and_reports_unreachable():
    world = _world()
    x, y, z = world._cell
    ground = world._column(x + 6, z)[0]
    world._changes[(x + 6, ground, z)] = "lava"
    outcome = world.act(Action("explore", direction="E"))
    assert outcome.ok and outcome.travel["distance"] == 5.0
    blocked = _world()
    cell = blocked._cell
    blocked._changes[(cell[0], blocked._column(cell[0], cell[2] - 1)[0], cell[2] - 1)] = "lava"
    assert blocked.act(Action("explore", direction="N")).error == "unreachable"


def test_the_v1_spiral_explore_still_works():
    world = _world()
    first = world.act(Action("explore"))
    assert first.ok and first.travel["distance"] == 10.0
    assert world.begin_episode() and world._spiral_leg == 0


def test_look_gives_the_nearest_block_per_direction():
    world = _world()
    x, y, z = world._cell
    world._changes[(x, y + 1, z - 12)] = "diamond_ore"
    world._changes[(x + 30, y, z)] = "diamond_ore"
    world._changes[(x + 5, y, z - 40)] = "diamond_ore"  # outside the radius
    world._changes[(x - 20, y, z + 21)] = "diamond_ore"
    looked = world.look(["diamond_ore"], radius=45)
    assert set(looked) == set(DIRECTIONS)
    assert looked["N"]["diamond_ore"] == {"x": x, "y": y + 1, "z": z - 12, "distance": 12.0, "exposed": True}
    assert looked["E"]["diamond_ore"]["x"] == x + 30
    assert looked["SW"]["diamond_ore"]["x"] == x - 20
    assert looked["NE"] == {} and looked["S"] == {} and looked["W"] == {}
    assert world.look(["diamond_ore"], radius=10) == {d: {} for d in DIRECTIONS}


def test_probe_consumes_nothing():
    world = _world(birch_planks=4, birch_log=1)
    before = world._inventory()
    assert world.probe([["birch_planks", None], ["birch_planks", None]])["output"] == {"item": "stick", "count": 4}
    assert world.probe([["birch_log", None], [None, None]])["output"] == {"item": "birch_planks", "count": 4}
    assert world.probe([["birch_planks"] * 2, ["birch_planks"] * 2])["output"] == {"item": "crafting_table", "count": 1}
    assert world.probe([["birch_log", "birch_log"], [None, None]])["error"] == "missing_ingredients"
    assert world.probe([["birch_planks", None], [None, None]])["output"] is None
    assert world.probe([["birch_planks"] * 3] * 3)["error"] == "missing_ingredients"
    assert world._inventory() == before
    world.set_world_rules(("stick2",))
    assert world.probe([["birch_planks", None], ["birch_planks", None]])["output"]["count"] == 2
    assert world._inventory() == before


def test_three_by_three_probe_needs_a_table():
    world = _world(oak_planks=3, stick=2)
    grid = [["oak_planks"] * 3, [None, "stick", None], [None, "stick", None]]
    assert world.probe(grid)["error"] == "missing_crafting_table"
    world.inventory["crafting_table"] = 1
    assert world.probe(grid)["output"] == {"item": "wooden_pickaxe", "count": 1}


# --- timing ------------------------------------------------------------------


def test_three_hundred_mixed_actions_are_fast():
    world = _world(11)
    started = time.perf_counter()
    for i in range(300):
        step = i % 6
        if step == 0:
            world.act(Action("mine", "#log"))
        elif step == 1:
            world.act(Action("craft", "#planks"))
        elif step == 2:
            world.act(Action("mine", "stone"))
        elif step == 3:
            world.act(Action("explore", direction=DIRECTIONS[(i // 6) % 8]))
        elif step == 4:
            world.act(Action("mine", "coal_ore"))
        else:
            world.look(["iron_ore", "deepslate_iron_ore"])
    assert time.perf_counter() - started < 10


# --- MineflayerBackend, natural mode, against a fake HTTP server --------------


class FakeBot:
    """Serves canned JSON per path and records every request."""

    def __init__(self):
        self.responses: dict[str, object] = {}
        self.requests: list[tuple[str, dict]] = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                bot.requests.append((self.path, body))
                response = bot.responses.get(self.path, {"ok": True})
                if isinstance(response, list):
                    response = response.pop(0)
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def paths(self):
        return [path for path, _ in self.requests]


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


TRAVEL = {"distance": 6.0, "dug": {"dirt": 2}, "placed": {"dirt": 1}, "picked_up": {"dirt": 2}}
DAMAGE = {"seq": 4, "t": 1.0, "type": "damage", "phase": "travel", "amount": 2.0, "health": 18.0}


def test_natural_mine_sends_the_v2_request_and_maps_the_outcome(bot):
    bot.responses["/mine"] = {
        "ok": True, "error": None, "block": "deepslate_coal_ore", "target": {"x": 1, "y": -3, "z": 2}, "tool": "wooden_pickaxe",
        "tool_used": "wooden_pickaxe", "block_removed": True, "drops": {"coal": 1}, "picked_up": {"coal": 1},
        "inventory": {"coal": 1, "wooden_pickaxe": 1}, "blocks_walked": 6.0, "travel": TRAVEL, "events": [DAMAGE],
        "inventory_before_step": {"wooden_pickaxe": 1},
    }
    backend = MineflayerBackend(bot.url, natural=True)
    outcome = backend.act(Action("mine", "coal_ore", tool="wooden_pickaxe"))
    assert bot.requests == [("/mine", {
        "block": "coal_ore", "blocks": ["coal_ore", "deepslate_coal_ore"], "tool": "wooden_pickaxe", "dig_through": True,
        "max_distance": 32, "scaffold": list(SCAFFOLD_BLOCKS),
    })]
    assert outcome.ok and outcome.block_removed and outcome.drops == {"coal": 1} and outcome.travel == TRAVEL
    assert outcome.events == [DAMAGE] and outcome.inventory == {"coal": 1, "wooden_pickaxe": 1}
    assert outcome.detail == {
        "block": "deepslate_coal_ore", "target": {"x": 1, "y": -3, "z": 2}, "tool": "wooden_pickaxe",
        "tool_used": "wooden_pickaxe", "inventory_before_step": {"wooden_pickaxe": 1},
    }


def test_natural_mine_keeps_a_null_tool_used_and_a_failure(bot):
    bot.responses["/mine"] = [
        {"ok": True, "block": "oak_log", "tool_used": None, "block_removed": True, "drops": {"oak_log": 1}, "inventory": {}},
        {"ok": False, "error": "hazard", "events": [{"seq": 1, "type": "hazard", "phase": "travel", "kind": "lava"}], "inventory": {}},
    ]
    backend = MineflayerBackend(bot.url, natural=True)
    first = backend.act(Action("mine", "#log"))
    assert first.detail["tool_used"] is None and bot.requests[0][1]["blocks"][0] == "acacia_log"
    failed = backend.act(Action("mine", "#log"))
    assert not failed.ok and failed.error == "hazard" and failed.events[0]["type"] == "hazard" and failed.travel == {}


def test_natural_craft_resolves_a_group_with_the_known_inventory(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {"birch_log": 2, "oak_log": 1}, "nearby_blocks": {}}
    bot.responses["/craft"] = {
        "ok": True, "item": "birch_planks", "used_table": False, "inventory_delta": {"birch_log": -1, "birch_planks": 4},
        "inventory": {"birch_log": 1, "birch_planks": 4, "oak_log": 1}, "travel": TRAVEL, "events": [DAMAGE],
        "inventory_before_step": {"birch_log": 2, "oak_log": 1}, "blocks_walked": 0.0,
    }
    backend = MineflayerBackend(bot.url, natural=True)
    outcome = backend.act(Action("craft", "#planks"))
    assert bot.requests == [("/observe", {}), ("/craft", {"item": "birch_planks", "times": 1})]
    assert outcome.ok and outcome.inventory_delta == {"birch_log": -1, "birch_planks": 4}
    assert outcome.travel == TRAVEL and outcome.events == [DAMAGE]
    assert outcome.detail == {"item": "birch_planks", "used_table": False, "inventory_before_step": {"birch_log": 2, "oak_log": 1}}
    backend.act(Action("craft", "#planks"))  # the reported inventory is now known: no /observe
    assert bot.paths() == ["/observe", "/craft", "/craft"]
    assert bot.requests[-1][1] == {"item": "birch_planks", "times": 1}


def test_natural_group_craft_splits_per_species_and_merges(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {"birch_log": 1, "oak_log": 1}, "nearby_blocks": {}}
    bot.responses["/craft"] = [
        {"ok": True, "item": "birch_planks", "inventory_delta": {"birch_log": -1, "birch_planks": 4}, "travel": TRAVEL,
         "events": [DAMAGE], "inventory": {"oak_log": 1, "birch_planks": 4}, "inventory_before_step": {"birch_log": 1, "oak_log": 1}},
        {"ok": True, "item": "oak_planks", "inventory_delta": {"oak_log": -1, "oak_planks": 4}, "travel": TRAVEL,
         "events": [], "inventory": {"birch_planks": 4, "oak_planks": 4}, "inventory_before_step": {"oak_log": 1, "birch_planks": 4}},
    ]
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("craft", "#planks", times=2))
    assert [body for path, body in bot.requests if path == "/craft"] == [
        {"item": "birch_planks", "times": 1}, {"item": "oak_planks", "times": 1}]
    assert outcome.ok
    assert outcome.inventory_delta == {"birch_log": -1, "birch_planks": 4, "oak_log": -1, "oak_planks": 4}
    assert outcome.travel == {"distance": 12.0, "dug": {"dirt": 4}, "placed": {"dirt": 2}, "picked_up": {"dirt": 4}}
    assert outcome.events == [DAMAGE] and outcome.inventory == {"birch_planks": 4, "oak_planks": 4}


def test_natural_group_craft_stops_at_a_failed_part(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {"birch_log": 1, "oak_log": 1}, "nearby_blocks": {}}
    bot.responses["/craft"] = {"ok": False, "error": "craft_failed", "detail": "no table", "inventory": {"birch_log": 1, "oak_log": 1}}
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("craft", "#planks", times=2))
    assert not outcome.ok and outcome.error == "craft_failed" and outcome.detail["detail"] == "no table"
    assert bot.paths().count("/craft") == 1


def test_natural_craft_with_one_species_that_can_pay_is_one_call(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {"birch_log": 3, "oak_log": 1}, "nearby_blocks": {}}
    MineflayerBackend(bot.url, natural=True).act(Action("craft", "#planks", times=2))
    assert [body for path, body in bot.requests if path == "/craft"] == [{"item": "birch_planks", "times": 2}]


def test_natural_craft_with_nothing_to_resolve_fails_without_a_call(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {}, "nearby_blocks": {}}
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("craft", "#planks"))
    assert outcome.error == "missing_ingredients" and bot.paths() == ["/observe"]


def test_natural_raw_craft_is_sent_as_is(bot):
    bot.responses["/craft"] = {"ok": True, "inventory_delta": {"stick": 4}, "inventory": {"stick": 4}}
    outcome = MineflayerBackend(bot.url, natural=True)
    outcome._latest_inventory = {"oak_planks": 2}
    assert outcome.act(Action("craft", "stick")).inventory_delta == {"stick": 4}
    assert bot.requests == [("/craft", {"item": "stick", "times": 1})]


def test_natural_smelt_request_timeout_and_outcome(bot, monkeypatch):
    timeouts = []
    real_post = MineflayerBackend._post

    def spy(self, path, payload=None, timeout=mineflayer_backend.REQUEST_TIMEOUT_S):
        timeouts.append((path, timeout))
        return real_post(self, path, payload, timeout)

    monkeypatch.setattr(MineflayerBackend, "_post", spy)
    bot.responses["/observe"] = {"ok": True, "inventory": {"raw_iron": 3, "coal": 1, "furnace": 1, "birch_log": 1}, "nearby_blocks": {}}
    bot.responses["/smelt"] = {
        "ok": True, "error": None, "ingredient": "raw_iron", "item": "iron_ingot", "times": 3, "fuel": "coal", "fuel_used": 1,
        "used_furnace": True, "furnace_retrieved": True, "inventory_delta": {"raw_iron": -3, "iron_ingot": 3, "coal": -1},
        "inventory": {"iron_ingot": 3, "furnace": 1}, "travel": TRAVEL, "events": [], "inventory_before_step": {"raw_iron": 3, "coal": 1, "furnace": 1},
    }
    outcome = MineflayerBackend(bot.url, natural=True).act(
        Action("smelt", "iron_ingot", times=3, fuel="coal", ingredient="raw_iron"))
    assert bot.requests[-1] == ("/smelt", {"ingredient": "raw_iron", "item": "iron_ingot", "times": 3, "fuel": "coal"})
    assert ("/smelt", 150 + 12 * 3) in timeouts
    assert outcome.ok and outcome.inventory_delta == {"raw_iron": -3, "iron_ingot": 3, "coal": -1}
    assert outcome.detail == {
        "used_furnace": True, "furnace_retrieved": True, "fuel": "coal", "fuel_used": 1, "item": "iron_ingot",
        "inventory_before_step": {"raw_iron": 3, "coal": 1, "furnace": 1},
    }
    assert outcome.travel == TRAVEL


def test_natural_smelt_resolves_group_fuel(bot):
    bot.responses["/observe"] = {"ok": True, "inventory": {"raw_iron": 1, "birch_planks": 2, "furnace": 1}, "nearby_blocks": {}}
    bot.responses["/smelt"] = {"ok": False, "error": "missing_furnace", "inventory": {}}
    outcome = MineflayerBackend(bot.url, natural=True).act(Action("smelt", "iron_ingot", fuel="#planks", ingredient="raw_iron"))
    assert bot.requests[-1][1]["fuel"] == "birch_planks" and outcome.error == "missing_furnace"


def test_natural_explore_requests(bot):
    bot.responses["/explore"] = [
        {"ok": True, "position": {"x": 1.5, "y": 70, "z": -31.5}, "blocks_walked": 32.0, "travel": TRAVEL, "events": [DAMAGE]},
        {"ok": True, "position": {"x": 10.5, "y": 70, "z": 0.5}, "blocks_walked": 42.0},
    ]
    bot.responses["/observe"] = {"ok": True, "inventory": {"dirt": 2}, "nearby_blocks": {}, "blocks_walked": 32.0}
    backend = MineflayerBackend(bot.url, natural=True)
    outcome = backend.act(Action("explore", direction="N"))
    assert bot.requests[0] == ("/explore", {"direction": "N", "distance": 32, "dig_through": True, "scaffold": list(SCAFFOLD_BLOCKS)})
    assert outcome.ok and outcome.inventory == {"dirt": 2} and outcome.blocks_walked == 32.0
    assert outcome.travel == TRAVEL and outcome.events == [DAMAGE] and outcome.detail == {"position": {"x": 1.5, "y": 70, "z": -31.5}}
    backend.act(Action("explore"))
    assert [req for req in bot.requests if req[0] == "/explore"][1] == ("/explore", {})


def test_natural_look_status_probe_and_begin_episode(bot):
    bot.responses["/look"] = {"ok": True, "radius": 64, "directions": {"N": {"coal_ore": {"x": 1, "y": 2, "z": 3, "distance": 4.0, "exposed": True}}}}
    bot.responses["/status"] = {
        "ok": True, "alive": True, "health": 18.0, "food": 19, "position": {"x": 1.5, "y": 70, "z": 2.5},
        "events": [DAMAGE], "last_seq": 4,
    }
    bot.responses["/probe"] = {"ok": True, "output": {"item": "stick", "count": 4}, "inventory_delta": {}, "events": []}
    bot.responses["/reset_episode"] = {"ok": True, "position": {"x": 0.5, "y": 70, "z": 0.5}, "inventory": {"oak_log": 1}, "nearby_blocks": {}, "blocks_walked": 0}
    backend = MineflayerBackend(bot.url, natural=True)

    looked = backend.look(["coal_ore"], radius=40)
    assert bot.requests[-1] == ("/look", {"blocks": ["coal_ore"], "radius": 40})
    assert set(looked) == set(DIRECTIONS) and looked["N"]["coal_ore"]["distance"] == 4.0 and looked["S"] == {}

    status = backend.status(since=3)
    assert bot.requests[-1] == ("/status", {"since": 3})
    assert (status.health, status.food, status.position, status.alive, status.events, status.last_seq) == (
        18.0, 19, (1.5, 70, 2.5), True, [DAMAGE], 4)

    grid = [["birch_planks", None], ["birch_planks", None]]
    assert backend.probe(grid)["output"] == {"item": "stick", "count": 4}
    assert bot.requests[-1] == ("/probe", {"grid": grid})

    observation = backend.begin_episode()
    assert bot.requests[-1] == ("/reset_episode", {}) and observation.inventory == {"oak_log": 1}
    assert backend.arena is None


def test_a_natural_backend_never_builds_an_arena():
    assert MineflayerBackend("http://127.0.0.1:1", natural=True).arena is None
    with pytest.raises(BackendError):
        MineflayerBackend("http://127.0.0.1:1", natural=True).status()


def test_v1_mode_requests_are_unchanged(bot):
    bot.responses["/mine"] = {"ok": True, "block_removed": True, "drops": {"oak_log": 1}, "picked_up": {"oak_log": 1}, "inventory": {"oak_log": 1}, "travel": TRAVEL}
    bot.responses["/craft"] = {"ok": True, "inventory_delta": {"oak_planks": 4}, "inventory": {"oak_planks": 4}, "item": "oak_planks"}
    bot.responses["/explore"] = {"ok": True, "position": {"x": 1, "y": 2, "z": 3}, "blocks_walked": 10.0, "travel": TRAVEL}
    bot.responses["/observe"] = {"ok": True, "inventory": {}, "nearby_blocks": {}}
    backend = MineflayerBackend(bot.url, arena=object())
    assert backend.natural is False
    mined = backend.act(Action("mine", "oak_log", tool="wooden_axe"))
    crafted = backend.act(Action("craft", "oak_planks", times=2))
    explored = backend.act(Action("explore"))
    assert bot.requests == [
        ("/mine", {"block": "oak_log", "tool": "wooden_axe"}),
        ("/craft", {"item": "oak_planks", "times": 2}),
        ("/explore", {}),
        ("/observe", {}),
    ]
    assert mined.travel == {} and mined.events == [] and crafted.detail == {} and explored.detail == {"position": {"x": 1, "y": 2, "z": 3}}
