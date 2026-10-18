import pytest

from schema_voyager.inventory import junk_to_toss, slots_used, stack_size
from schema_voyager.models import Rule


def craft_rule(consumes, station=None, tool_any=(), fuel_any=()):
    preconditions = {"consumes": consumes, "station": station}
    if tool_any:
        preconditions["tool_any"] = list(tool_any)
    if fuel_any:
        preconditions["fuel_any"] = list(fuel_any)
    return Rule(
        id="r", action={"type": "craft", "item": "x"}, preconditions=preconditions,
        effects={"gains": {"x": 1}},
    )


@pytest.mark.parametrize("item,size", [
    ("dirt", 64), ("oak_log", 64), ("cobblestone", 64),
    ("bucket", 16), ("snowball", 16), ("egg", 16), ("ender_pearl", 16), ("oak_sign", 16),
    ("oak_hanging_sign", 16), ("white_banner", 16),
    ("wooden_pickaxe", 1), ("iron_axe", 1), ("stone_hoe", 1), ("shears", 1), ("bow", 1),
    ("iron_helmet", 1), ("elytra", 1), ("water_bucket", 1), ("red_bed", 1), ("oak_boat", 1),
    ("bamboo_raft", 1), ("chest_minecart", 1), ("minecart", 1), ("potion", 1),
    ("mushroom_stew", 1), ("music_disc_cat", 1), ("enchanted_book", 1),
])
def test_stack_size(item, size):
    assert stack_size(item) == size


def test_slots_used_rounds_up_and_skips_empty_counts():
    inventory = {"dirt": 65, "bucket": 17, "iron_pickaxe": 1, "stick": 0, "oak_log": 64}
    assert slots_used(inventory) == 2 + 2 + 1 + 1
    assert slots_used({}) == 0


@pytest.mark.parametrize("free_slots", [None, 4, 10])
def test_nothing_is_tossed_when_free_slots_is_unknown_or_enough(free_slots):
    assert junk_to_toss({"dirt": 640}, [], [], free_slots) == {}


def test_tools_and_kept_items_are_never_tossed():
    inventory = {"iron_pickaxe": 1, "crafting_table": 1, "furnace": 1, "bucket": 3, "shears": 1, "poppy": 3}
    assert junk_to_toss(inventory, [], [], 0) == {"poppy": 3}


def test_wanted_items_are_never_tossed_by_raw_id_or_family():
    inventory = {"birch_log": 200, "stick": 100, "poppy": 5}
    assert junk_to_toss(inventory, [], ["#log", "stick"], 0) == {"poppy": 5}


def test_rule_items_keep_one_stack_and_surplus_is_tossed_only_when_junk_is_not_enough():
    rules = [craft_rule({"#planks": 4}, station="crafting_table")]
    inventory = {"oak_planks": 200, "poppy": 3}
    # Junk alone frees one slot, which reaches min_free of 2.
    assert junk_to_toss(inventory, rules, [], 1, min_free=2) == {"poppy": 3}
    # Not enough: 200 planks keep 64, the other 136 go (3 slots).
    assert junk_to_toss(inventory, rules, [], 0, min_free=4) == {"poppy": 3, "oak_planks": 136}


def test_station_tool_and_fuel_names_make_rule_items():
    rules = [craft_rule({}, tool_any=["#log"], fuel_any=["coal"])]
    inventory = {"oak_log": 100, "coal": 100}
    assert junk_to_toss(inventory, rules, [], 0, min_free=1) == {"coal": 36}  # both free a slot; coal sorts first
    assert junk_to_toss(inventory, rules, [], 0, min_free=2) == {"coal": 36, "oak_log": 36}


def test_cobbled_deepslate_is_a_rule_item_when_a_rule_consumes_cobblestone():
    rules = [craft_rule({"#cobblestone": 8}, station="crafting_table")]
    toss = junk_to_toss({"cobbled_deepslate": 100}, rules, [], 0, scaffold_reserve=0)
    assert toss == {"cobbled_deepslate": 36}


def test_scaffold_reserve_keeps_64_blocks_in_scaffold_order():
    inventory = {"dirt": 30 * 64, "granite": 20}
    assert junk_to_toss(inventory, [], [], 0) == {"dirt": 30 * 64 - 64, "granite": 20}


def test_scaffold_reserve_spills_to_the_next_block():
    inventory = {"dirt": 40, "granite": 50, "tuff": 50}
    assert junk_to_toss(inventory, [], [], 0) == {"granite": 26, "tuff": 50}


def test_kept_scaffold_blocks_count_toward_the_reserve_first():
    # Wanted dirt and the first stack of a rule item (netherrack) are 40 + 64 >= 64.
    rules = [craft_rule({"netherrack": 1})]
    inventory = {"dirt": 40, "netherrack": 64, "granite": 30}
    assert junk_to_toss(inventory, rules, ["dirt"], 0) == {"granite": 30}
    # Wanted dirt alone leaves room for 24 granite.
    assert junk_to_toss({"dirt": 40, "granite": 30}, [], ["dirt"], 0) == {"granite": 6}


def test_all_junk_is_tossed_once_triggered_even_past_min_free():
    inventory = {"poppy": 64, "dandelion": 64, "cactus": 64, "sand": 64}
    assert junk_to_toss(inventory, [], [], 3, min_free=4) == inventory


def test_rule_surplus_goes_by_slots_freed_then_name():
    rules = [craft_rule({"#planks": 1, "stick": 1, "coal": 1, "iron_ingot": 1})]
    inventory = {"oak_planks": 64 * 4, "coal": 64 * 3, "stick": 64 * 3, "iron_ingot": 64 * 2}
    # Surplus slots freed: planks 3, coal 2, stick 2, iron 1. Needs 4 slots from 0 free.
    assert junk_to_toss(inventory, rules, [], 0, min_free=4) == {"oak_planks": 192, "coal": 128}
    assert junk_to_toss(inventory, rules, [], 0, min_free=6) == {
        "oak_planks": 192, "coal": 128, "stick": 128,
    }


def test_it_may_fall_short_of_min_free():
    rules = [craft_rule({"#planks": 4})]
    inventory = {"oak_planks": 64, "iron_pickaxe": 1, "poppy": 1}
    assert junk_to_toss(inventory, rules, [], 0, min_free=4) == {"poppy": 1}
