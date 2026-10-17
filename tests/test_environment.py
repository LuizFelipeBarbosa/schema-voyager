from schema_voyager.environment import SimulatedWorld
from schema_voyager.layouts import COAL_ORES, make_layout
from schema_voyager.models import Action
from schema_voyager.tasks import get_task


def _world(task_id: str, inventory: dict[str, int] | None = None) -> SimulatedWorld:
    world = SimulatedWorld()
    world.reset(get_task(task_id), make_layout(6))
    if inventory:
        world.inventory.update(inventory)
    return world


def test_same_seed_gives_same_layout():
    assert make_layout(3).to_json() == make_layout(3).to_json()
    assert make_layout(3).to_json() != make_layout(4).to_json()


def test_everything_is_in_view_from_spawn():
    for seed in range(1, 11):
        nearby = _world_with_layout(seed).observe().nearby
        assert nearby.get("oak_log", 0) >= 9, seed
        assert nearby.get("coal_ore", 0) == COAL_ORES, seed


def _world_with_layout(seed: int) -> SimulatedWorld:
    world = SimulatedWorld()
    world.reset(get_task("t1"), make_layout(seed))
    return world


def test_r2_wooden_pickaxe_removes_coal_ore_and_drops_nothing():
    world = _world("h3", {"wooden_pickaxe": 1})
    outcome = world.act(Action("mine", "coal_ore", tool="wooden_pickaxe"))
    assert outcome.ok and outcome.block_removed
    assert outcome.drops == {}


def test_r2_stone_pickaxe_drops_coal():
    world = _world("h3", {"stone_pickaxe": 1})
    outcome = world.act(Action("mine", "coal_ore", tool="stone_pickaxe"))
    assert outcome.drops == {"coal": 1}


def test_vanilla_wooden_pickaxe_drops_coal():
    world = _world("t3", {"wooden_pickaxe": 1})
    assert world.act(Action("mine", "coal_ore", tool="wooden_pickaxe")).drops == {"coal": 1}


def test_crafting_needs_a_table_for_3x3_recipes():
    world = _world("t1", {"oak_planks": 3, "stick": 2})
    assert world.act(Action("craft", "wooden_pickaxe")).error == "missing_crafting_table"
    world.inventory.update({"crafting_table": 1})
    outcome = world.act(Action("craft", "wooden_pickaxe"))
    assert outcome.inventory_delta == {"oak_planks": -3, "stick": -2, "wooden_pickaxe": 1}
