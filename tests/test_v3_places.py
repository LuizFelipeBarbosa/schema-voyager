import copy

import pytest

from schema_voyager import blueprint, places
from schema_voyager.blueprint import merge_boxes
from schema_voyager.inventory import junk_to_store, junk_to_toss
from schema_voyager.llm import FAKE_HUT
from schema_voyager.models import HomeStock, Rule
from schema_voyager.places import Home, Places

ORIGIN = (4, 76, 31)
SIZE = (5, 4, 5)
HOME = Home("u003", "small house", ORIGIN, SIZE, (6, 76, 35))

EXAMPLE = {
    "home": {"goal_id": "u003", "name": "small house", "origin": [4, 76, 31], "size": [5, 4, 5], "door": [6, 76, 35]},
    "stations": {"chest": [5, 76, 36], "crafting_table": [7, 76, 36]},
    "stored": {"coal": 64, "dirt": 120},
}


# -- Places ----------------------------------------------------------------

def test_places_round_trip_of_the_contract_example():
    loaded = Places.from_json({**EXAMPLE, "chest_goal": "u004"})
    assert loaded.home == HOME
    assert loaded.stations == {"chest": (5, 76, 36), "crafting_table": (7, 76, 36)}
    assert loaded.stored == {"coal": 64, "dirt": 120}
    assert loaded.chest_goal == "u004"
    assert loaded.to_json() == {**EXAMPLE, "chest_goal": "u004", "bad_slots": [], "moved": [], "retired": []}


def test_places_without_a_home_or_a_door_round_trip():
    empty = Places()
    assert empty.to_json() == {
        "home": None, "stations": {}, "stored": {}, "chest_goal": None, "bad_slots": [], "moved": [], "retired": [],
    }
    assert Places.from_json(empty.to_json()) == empty
    doorless = Places(home=Home("u1", "hut", ORIGIN, SIZE, None))
    assert doorless.to_json()["home"]["door"] is None
    assert Places.from_json(doorless.to_json()) == doorless


@pytest.mark.parametrize("data", [None, {}])
def test_from_json_of_nothing_is_an_empty_places(data):
    assert Places.from_json(data) == Places()


def test_stock_keeps_the_crafting_table_and_furnace_and_copies_stored():
    stored = {"coal": 3}
    stock = Places(stations={"chest": (1, 2, 3), "crafting_table": (4, 5, 6), "furnace": (7, 8, 9)}, stored=stored).stock()
    assert stock == HomeStock(stations={"crafting_table": (4, 5, 6), "furnace": (7, 8, 9)}, chest=(1, 2, 3), stored={"coal": 3})
    assert stock.stored is not stored
    assert Places().stock() == HomeStock()


# -- door_cell, home_from_*, boxes ------------------------------------------

def test_door_cell_of_the_fake_hut_is_the_lower_door_cell():
    assert places.door_cell(FAKE_HUT, ORIGIN) == (6, 76, 35)


def test_door_cell_without_a_door_is_none():
    no_door = copy.deepcopy(FAKE_HUT)
    no_door["palette"]["D"] = "#planks"
    assert places.door_cell(no_door, ORIGIN) is None


def test_door_cell_takes_the_first_door_bottom_up_then_z_then_x():
    bp = {
        "name": "two doors", "size": [3, 2, 2],
        "palette": {"D": "oak_door", "P": "#planks"},
        "layers": [["D.D", "PPP"], ["D.D", "PPP"]],
    }
    assert places.door_cell(bp, (0, 10, 0)) == (0, 10, 0)


def test_home_box_and_yard_scan_box():
    assert places.home_box(HOME) == ((2, 75, 29), (9, 5, 9))
    assert places.yard_scan_box(HOME) == ((2, 74, 29), (9, 5, 9))


def entry(goal_id, status, origin=ORIGIN):
    return {"goal_id": goal_id, "name": f"house {goal_id}", "origin": list(origin), "size": [5, 4, 5], "status": status}


def test_home_from_protected_takes_the_first_built_entry():
    entries = [entry("a", "partial"), entry("b", "built", (10, 70, 10)), entry("c", "building"), entry("d", "built")]
    home = places.home_from_protected(entries, lambda goal_id: FAKE_HUT)
    assert home == Home("b", "house b", (10, 70, 10), SIZE, (12, 70, 14))


def test_home_from_protected_is_none_without_a_built_entry():
    assert places.home_from_protected([entry("a", "partial"), entry("b", "building")], lambda goal_id: FAKE_HUT) is None
    assert places.home_from_protected([], lambda goal_id: FAKE_HUT) is None


def test_home_from_protected_without_a_blueprint_has_no_door():
    home = places.home_from_protected([entry("a", "built")], lambda goal_id: None)
    assert home == Home("a", "house a", ORIGIN, SIZE, None)


def test_home_from_build_reads_the_door_from_the_blueprint():
    assert places.home_from_build(entry("u003", "built"), FAKE_HUT) == Home("u003", "house u003", ORIGIN, SIZE, (6, 76, 35))


# -- yard_slots ------------------------------------------------------------

def flat_scan(home=HOME, ground_top=None):
    """Grass up to y = oy - 1 (or ground_top per (x, z)), air above, over the yard scan box."""
    (sx, sy, sz), (w, h, d) = places.yard_scan_box(home)
    oy = home.origin[1]
    return {
        (x, y, z): "grass_block" if y <= (ground_top or {}).get((x, z), oy - 1) else "air"
        for x in range(sx, sx + w) for y in range(sy, sy + h) for z in range(sz, sz + d)
    }


def test_yard_slots_on_flat_ground_are_ordered_from_the_doors_outside_cell():
    slots = places.yard_slots(HOME, flat_scan())
    assert slots[:4] == [(5, 76, 36), (7, 76, 36), (4, 76, 36), (8, 76, 36)]
    assert (6, 76, 36) not in slots  # the door's outside cell
    assert len(slots) == 7 * 7 - 5 * 5 - 1
    assert {y for _, y, _ in slots} == {76}


def test_yard_slots_without_a_door_sort_from_the_origin_corner():
    doorless = Home("u1", "hut", ORIGIN, SIZE, None)
    slots = places.yard_slots(doorless, flat_scan(doorless))
    assert slots[:2] == [(4, 76, 30), (3, 76, 31)]
    assert len(slots) == 7 * 7 - 5 * 5


def test_yard_slots_skip_taken_cells():
    assert places.yard_slots(HOME, flat_scan(), taken=[(5, 76, 36)])[0] == (7, 76, 36)


def test_the_door_outside_cell_follows_the_wall_the_door_is_in():
    north = Home("u1", "hut", ORIGIN, SIZE, (6, 76, 31))
    west = Home("u1", "hut", ORIGIN, SIZE, (4, 76, 33))
    east = Home("u1", "hut", ORIGIN, SIZE, (8, 76, 33))
    assert (6, 76, 30) not in places.yard_slots(north, flat_scan(north))
    assert (3, 76, 33) not in places.yard_slots(west, flat_scan(west))
    assert (9, 76, 33) not in places.yard_slots(east, flat_scan(east))


def test_a_flower_under_a_cell_disqualifies_it():
    scan = flat_scan()
    scan[(5, 75, 36)] = "poppy"
    slots = places.yard_slots(HOME, scan)
    assert (5, 76, 36) not in slots
    assert slots[0] == (7, 76, 36)


def test_a_cell_with_a_block_above_or_a_solid_block_in_it_is_not_a_slot():
    scan = flat_scan()
    scan[(7, 77, 36)] = "stone"
    scan[(4, 76, 36)] = "stone"
    slots = places.yard_slots(HOME, scan)
    assert (7, 76, 36) not in slots
    assert (4, 76, 36) not in slots


def test_replaceable_grass_in_the_cell_is_a_slot():
    scan = flat_scan()
    scan[(5, 76, 36)] = "short_grass"
    assert places.yard_slots(HOME, scan)[0] == (5, 76, 36)


def test_a_column_with_ground_one_lower_gives_its_lower_slot():
    scan = flat_scan(ground_top={(5, 36): 74})
    slots = places.yard_slots(HOME, scan)
    assert slots[0] == (5, 75, 36)
    assert (5, 76, 36) not in slots


def test_one_slot_per_column_the_lowest():
    scan = flat_scan(ground_top={(5, 36): 74})
    scan[(5, 76, 36)] = "stone"  # stone fills the 75 column's cell above the lowest slot
    assert all(cell != (5, 76, 36) for cell in places.yard_slots(HOME, scan))
    columns = [(x, z) for x, _, z in places.yard_slots(HOME, flat_scan())]
    assert len(columns) == len(set(columns))


def test_liquid_ground_and_unseen_cells_are_not_slots():
    scan = flat_scan()
    scan[(5, 75, 36)] = "water"
    scan[(7, 75, 36)] = None
    scan[(4, 76, 36)] = None
    slots = places.yard_slots(HOME, scan)
    assert not {(5, 76, 36), (7, 76, 36), (4, 76, 36)} & set(slots)
    assert slots[0] == (8, 76, 36)


def test_an_empty_scan_has_no_slots():
    assert places.yard_slots(HOME, {}) == []


# -- merge_boxes -----------------------------------------------------------

def unit_boxes(count):
    return [((10 * i, 0, 0), (2, 2, 2)) for i in range(count)]


def test_merge_boxes_leaves_up_to_the_limit_alone():
    boxes = unit_boxes(32)
    assert merge_boxes(boxes) == boxes
    assert merge_boxes(boxes[:3], limit=3) == boxes[:3]


def test_merge_boxes_merges_the_cheapest_pair_of_33():
    boxes = unit_boxes(33)
    boxes[10] = ((100, 0, 0), (2, 2, 2))
    boxes[11] = ((102, 0, 0), (2, 2, 2))  # touching box 10: merging them adds nothing
    merged = merge_boxes(boxes)
    assert len(merged) == 32
    assert merged[10] == ((100, 0, 0), (4, 2, 2))
    assert merged[11] == boxes[12]


def test_merge_boxes_ties_go_to_the_lowest_index_pair():
    boxes = unit_boxes(33)  # every neighbouring pair adds the same volume
    merged = merge_boxes(boxes)
    assert merged[0] == ((0, 0, 0), (12, 2, 2))
    assert merged[1:] == boxes[2:]


def test_merge_boxes_is_deterministic_and_does_not_touch_its_input():
    boxes = [((x * 7 % 40, x * 3 % 11, x * 5 % 23), (1 + x % 4, 2, 1 + x % 3)) for x in range(50)]
    before = list(boxes)
    assert merge_boxes(boxes) == merge_boxes(list(boxes))
    assert boxes == before


def test_merge_boxes_covers_every_input_box():
    boxes = [((x * 7 % 40, x * 3 % 11, x * 5 % 23), (1 + x % 4, 2, 1 + x % 3)) for x in range(50)]
    merged = merge_boxes(boxes, limit=8)
    assert len(merged) == 8
    for (origin, size) in boxes:
        assert any(
            all(m_origin[a] <= origin[a] and origin[a] + size[a] <= m_origin[a] + m_size[a] for a in range(3))
            for m_origin, m_size in merged
        )


# -- junk_to_store ---------------------------------------------------------

def craft_rule(consumes):
    return Rule(id="r", action={"type": "craft", "item": "x"}, preconditions={"consumes": consumes, "station": None},
                effects={"gains": {"x": 1}})


def test_junk_to_store_is_empty_unless_the_bot_is_short_of_slots():
    assert junk_to_store({"gravel": 640}, [], [], None) == {}
    assert junk_to_store({"gravel": 640}, [], [], 4) == {}
    assert junk_to_store({"gravel": 640}, [], [], 3) == {"gravel": 640}


def test_junk_to_store_includes_rule_surplus_even_when_the_junk_frees_enough():
    inventory = {"gravel": 640, "coal": 200}
    rules = [craft_rule({"coal": 1})]
    assert junk_to_toss(inventory, rules, [], 0) == {"gravel": 640}
    assert junk_to_store(inventory, rules, [], 0) == {"coal": 136, "gravel": 640}


def test_junk_to_store_orders_rule_surplus_first_then_junk_largest_first_ties_by_name():
    inventory = {"gravel": 100, "flint": 100, "sand": 300, "coal": 70, "stick": 90}
    rules = [craft_rule({"coal": 1, "stick": 1})]
    assert list(junk_to_store(inventory, rules, [], 0)) == ["stick", "coal", "sand", "flint", "gravel"]


def test_junk_to_store_never_stores_tools_kept_items_wanted_items_or_a_rules_first_stack():
    inventory = {"iron_pickaxe": 1, "crafting_table": 3, "oak_log": 300, "cobblestone": 90, "gravel": 10}
    rules = [craft_rule({"cobblestone": 1})]
    stored = junk_to_store(inventory, rules, ["oak_log"], 0)
    assert stored == {"cobblestone": 26, "gravel": 10}


def test_junk_to_store_holds_the_scaffold_reserve_back():
    assert junk_to_store({"dirt": 100}, [], [], 0) == {"dirt": 36}
    assert junk_to_store({"dirt": 100}, [], [], 0, scaffold_reserve=0) == {"dirt": 100}
