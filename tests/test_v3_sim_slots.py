"""NaturalWorld's 36-slot inventory (v3.0c): free_slots, toss, and the limits on mining and crafting."""

from schema_voyager.environment import INVENTORY_SLOTS, NaturalWorld
from schema_voyager.models import Action

SEED = 7


def _world(**inventory):
    world = NaturalWorld(SEED)
    world.inventory.update(inventory)
    return world


def _filled_but_one_slot(world):
    """Fills 35 slots with gravel."""
    world.inventory["gravel"] = 35 * 64


# --- free_slots --------------------------------------------------------------


def test_an_empty_bot_has_every_slot_free():
    assert _world().observe().free_slots == INVENTORY_SLOTS == 36


def test_free_slots_counts_stack_sizes():
    world = _world(dirt=130, iron_pickaxe=1, bucket=20)  # 3 + 1 + 2 slots
    assert world.observe().free_slots == 30


def test_an_overfull_bot_has_zero_free_slots_not_a_negative_number():
    world = _world(cobblestone=40 * 64)
    assert world.observe().free_slots == 0


# --- toss --------------------------------------------------------------------


def test_toss_removes_up_to_the_counts_and_returns_what_left():
    world = _world(dirt=10, gravel=3)
    assert world.toss({"dirt": 4, "gravel": 5}) == {"dirt": 4, "gravel": 3}
    assert world.inventory["dirt"] == 6 and world.inventory["gravel"] == 0
    assert world.observe().inventory == {"dirt": 6}


def test_toss_ignores_names_not_held_and_an_empty_request():
    world = _world(dirt=10)
    assert world.toss({"diamond": 3, "dirt": 0}) == {}
    assert world.toss({}) == {}
    assert world.observe().inventory == {"dirt": 10}


def test_toss_makes_room():
    world = _world(cobblestone=36 * 64)
    assert world.observe().free_slots == 0
    world.toss({"cobblestone": 128})
    assert world.observe().free_slots == 2


# --- mining ------------------------------------------------------------------


def test_a_mine_step_with_a_full_inventory_keeps_the_drops_but_picks_nothing_up():
    world = _world(cobblestone=36 * 64)
    outcome = world.act(Action("mine", "#log"))
    assert outcome.ok and outcome.block_removed
    assert sum(outcome.drops.values()) == 1
    assert outcome.picked_up == {} and outcome.inventory_delta == {}
    assert outcome.detail["inventory_full"] is True
    assert outcome.inventory == {"cobblestone": 36 * 64}


def test_a_mine_step_with_room_does_not_flag_the_inventory():
    outcome = _world().act(Action("mine", "#log"))
    assert outcome.picked_up == outcome.drops == outcome.inventory_delta
    assert "inventory_full" not in outcome.detail


def test_a_mine_step_tops_up_a_partly_filled_stack_with_no_free_slot():
    world = _world(cobblestone=63, wooden_pickaxe=1)  # the pickaxe takes the 36th slot
    world.inventory["gravel"] = 34 * 64
    assert world.observe().free_slots == 0
    outcome = world.act(Action("mine", "stone", tool="wooden_pickaxe"))
    assert outcome.drops == outcome.picked_up == outcome.inventory_delta == {"cobblestone": 1}
    assert "inventory_full" not in outcome.detail
    assert outcome.inventory["cobblestone"] == 64


def test_a_full_stack_leaves_the_next_drop_on_the_ground():
    world = _world(cobblestone=64, wooden_pickaxe=1)
    world.inventory["gravel"] = 34 * 64
    outcome = world.act(Action("mine", "stone", tool="wooden_pickaxe"))
    assert outcome.drops == {"cobblestone": 1} and outcome.picked_up == {}
    assert outcome.detail["inventory_full"] is True
    assert outcome.inventory["cobblestone"] == 64


def test_climbing_with_a_full_inventory_reports_only_what_was_kept():
    world = _world(cobblestone=36 * 64)
    outcome = world.act(Action("explore", direction="up"))
    assert outcome.travel["picked_up"] == {}
    assert outcome.inventory == {"cobblestone": 36 * 64}


# --- crafting ----------------------------------------------------------------


def test_a_craft_whose_output_does_not_fit_fails_and_changes_nothing():
    world = _world(oak_planks=5)  # the table leaves 1 plank, and needs a slot of its own
    _filled_but_one_slot(world)
    before = world.observe().inventory
    outcome = world.act(Action("craft", "crafting_table"))
    assert not outcome.ok and outcome.error == "craft_failed"
    assert outcome.detail["inventory_full"] is True
    assert outcome.inventory == before == world.observe().inventory


def test_a_craft_that_consumes_a_whole_stack_succeeds_with_no_free_slot():
    world = _world(oak_planks=4)
    _filled_but_one_slot(world)
    assert world.observe().free_slots == 0
    outcome = world.act(Action("craft", "crafting_table"))
    assert outcome.ok
    assert outcome.inventory_delta == {"crafting_table": 1, "oak_planks": -4}
    assert world.observe().free_slots == 0
