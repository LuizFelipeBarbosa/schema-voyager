"""NaturalWorld's building actions (v3.4a): place, clear, scan and set_keep_out."""

import pytest

from schema_voyager.environment import TERRAIN_BOTTOM, THREATENED_ACTIONS, NaturalWorld
from schema_voyager.models import Action

SEED = 7


def _world(**inventory):
    world = NaturalWorld(SEED)
    world.inventory.update(inventory)
    return world


def _site(world, dx=2, dz=2):
    """(x, y, z) of an open air cell on flat made-up ground: a 5x5 grass floor with air above it."""
    ground = world._column(0, 0)[0]
    for x in range(dx - 2, dx + 3):
        for z in range(dz - 2, dz + 3):
            world._changes[(x, ground, z)] = "grass_block"
            for y in range(ground + 1, ground + 5):
                world._changes[(x, y, z)] = "air"
    return (dx, ground + 1, dz)


def _place(world, target, pos):
    return world.act(Action("place", target=target, pos=pos))


def _clear(world, pos):
    return world.act(Action("clear", pos=pos))


# --- place -------------------------------------------------------------------


def test_place_on_the_ground_sets_the_cell_and_spends_one():
    world = _world(oak_planks=3)
    pos = _site(world)
    outcome = _place(world, "oak_planks", pos)
    assert outcome.ok
    assert world._changes[pos] == "oak_planks"
    assert world.inventory["oak_planks"] == 2
    assert world.scan(pos, (1, 1, 1)) == {pos: "oak_planks"}
    assert outcome.inventory == {"oak_planks": 2}
    assert outcome.inventory_delta == {"oak_planks": -1}
    assert outcome.detail == {
        "block": "oak_planks", "placed": True, "found": "oak_planks",
        "target": {"x": pos[0], "y": pos[1], "z": pos[2]}, "inventory_before_step": {"oak_planks": 3},
    }
    assert set(outcome.travel) >= {"distance", "dug", "placed", "picked_up"}


def test_placing_what_is_already_there_spends_nothing():
    world = _world(oak_planks=1)
    pos = _site(world)
    world._changes[pos] = "oak_planks"
    outcome = _place(world, "oak_planks", pos)
    assert outcome.ok
    assert outcome.detail["placed"] is False
    assert outcome.inventory_delta == {}
    assert world.inventory["oak_planks"] == 1


def test_a_torch_cell_already_holding_a_wall_torch_counts_as_there():
    world = _world(torch=1)
    pos = _site(world)
    world._changes[pos] = "wall_torch"
    outcome = _place(world, "torch", pos)
    assert outcome.ok and outcome.detail["placed"] is False
    assert world.inventory["torch"] == 1


def test_place_without_the_item_is_missing_item():
    world = _world()
    outcome = _place(world, "oak_planks", _site(world))
    assert not outcome.ok
    assert outcome.error == "missing_item"


def test_place_into_a_solid_cell_is_occupied_and_names_it():
    world = _world(oak_planks=1)
    pos = _site(world)
    world._changes[pos] = "stone"
    outcome = _place(world, "oak_planks", pos)
    assert outcome.error == "occupied"
    assert outcome.detail["found"] == "stone"
    assert world.inventory["oak_planks"] == 1


def test_a_door_with_a_block_above_is_occupied():
    world = _world(oak_door=1)
    pos = _site(world)
    world._changes[(pos[0], pos[1] + 1, pos[2])] = "stone"
    outcome = _place(world, "oak_door", pos)
    assert outcome.error == "occupied"
    assert outcome.detail["found"] == "stone"


def test_place_in_mid_air_has_no_support():
    world = _world(oak_planks=1)
    x, y, z = _site(world)
    outcome = _place(world, "oak_planks", (x, y + 3, z))
    assert outcome.error == "no_support"
    assert world.inventory["oak_planks"] == 1


def test_place_replaces_short_grass():
    world = _world(oak_planks=1)
    pos = _site(world)
    world._changes[pos] = "short_grass"
    assert _place(world, "oak_planks", pos).ok
    assert world.block_at(pos) == "oak_planks"


def test_a_liquid_or_a_torch_is_no_support():
    world = _world(oak_planks=2)
    x, y, z = _site(world)
    world._changes[(x, y + 2, z)] = "water"
    world._changes[(x + 1, y + 2, z)] = "torch"
    assert _place(world, "oak_planks", (x, y + 3, z)).error == "no_support"
    assert _place(world, "oak_planks", (x + 1, y + 3, z)).error == "no_support"


def test_a_torch_on_the_ground_is_a_torch():
    world = _world(torch=1)
    pos = _site(world)
    assert _place(world, "torch", pos).ok
    assert world.block_at(pos) == "torch"


def test_a_torch_beside_a_wall_is_a_wall_torch():
    world = _world(torch=1)
    x, y, z = _site(world)
    world._changes[(x, y + 1, z)] = "air"
    world._changes[(x + 1, y + 1, z)] = "stone"
    outcome = _place(world, "torch", (x, y + 1, z))
    assert outcome.ok
    assert world.block_at((x, y + 1, z)) == "wall_torch"
    assert outcome.inventory_delta == {"torch": -1}


def test_a_torch_with_only_a_block_above_has_no_support():
    world = _world(torch=1)
    x, y, z = _site(world)
    world._changes[(x, y + 2, z)] = "stone"
    outcome = _place(world, "torch", (x, y + 1, z))
    assert outcome.error == "no_support"


def test_a_door_fills_two_cells():
    world = _world(oak_door=1)
    x, y, z = _site(world)
    outcome = _place(world, "oak_door", (x, y, z))
    assert outcome.ok
    assert world.block_at((x, y, z)) == world.block_at((x, y + 1, z)) == "oak_door"
    assert world.inventory["oak_door"] == 0


def test_a_door_needs_a_block_below_not_beside():
    world = _world(oak_door=1)
    x, y, z = _site(world)
    world._changes[(x + 1, y + 1, z)] = "stone"
    assert _place(world, "oak_door", (x, y + 1, z)).error == "no_support"


def test_place_without_a_pos_is_a_programming_error():
    with pytest.raises(ValueError):
        _world(oak_planks=1).act(Action("place", target="oak_planks"))
    with pytest.raises(ValueError):
        _world().act(Action("clear"))


# --- clear -------------------------------------------------------------------


def test_clear_turns_a_block_into_air_without_drops():
    world = _world()
    pos = _site(world)
    world._changes[pos] = "stone"
    outcome = _clear(world, pos)
    assert outcome.ok
    assert world.block_at(pos) == "air"
    assert world.inventory == {}
    assert outcome.inventory_delta == {}
    assert outcome.detail == {
        "cleared": True, "removed": "stone", "found": "air",
        "target": {"x": pos[0], "y": pos[1], "z": pos[2]}, "inventory_before_step": {},
    }


def test_clearing_air_changes_nothing():
    world = _world()
    pos = _site(world)
    outcome = _clear(world, pos)
    assert outcome.ok
    assert outcome.detail["cleared"] is False
    assert outcome.detail["found"] == "air"
    assert not outcome.detail.get("removed")


def test_bedrock_and_liquids_are_unbreakable():
    world = _world()
    pos = _site(world)
    bedrock = (pos[0], TERRAIN_BOTTOM, pos[2])
    outcome = _clear(world, bedrock)
    assert outcome.error == "unbreakable"
    assert outcome.detail["found"] == "bedrock"
    world._changes[pos] = "water"
    outcome = _clear(world, pos)
    assert outcome.error == "unbreakable"
    assert outcome.detail["found"] == "water"
    assert world.block_at(pos) == "water"


@pytest.mark.parametrize("half", ["lower", "upper"])
def test_clearing_either_half_of_a_door_clears_both(half):
    world = _world(oak_door=1)
    x, y, z = _site(world)
    assert _place(world, "oak_door", (x, y, z)).ok
    _clear(world, (x, y if half == "lower" else y + 1, z))
    assert world.block_at((x, y, z)) == world.block_at((x, y + 1, z)) == "air"


# --- scan and keep_out -------------------------------------------------------


def test_scan_reads_every_cell_of_the_box():
    world = _world()
    x, y, z = _site(world)
    world._changes[(x + 1, y, z + 2)] = "stone"
    box = world.scan((x, y - 1, z), (2, 2, 3))
    assert set(box) == {(x + dx, y - 1 + dy, z + dz) for dx in range(2) for dy in range(2) for dz in range(3)}
    assert box[(x, y - 1, z)] == "grass_block"
    assert box[(x, y, z)] == "air"
    assert box[(x + 1, y, z + 2)] == "stone"
    assert all(box[cell] == world.block_at(cell) for cell in box)


def test_set_keep_out_stores_the_box_and_changes_nothing_else():
    world = _world(oak_planks=1)
    pos = _site(world)
    before = dict(world._changes)
    world.set_keep_out((pos, (3, 3, 3)))
    assert world._keep_out == (pos, (3, 3, 3))
    assert world._changes == before
    assert _place(world, "oak_planks", pos).ok
    world.set_keep_out(None)
    assert world._keep_out is None


def test_place_and_clear_are_threatened_actions():
    assert "place" in THREATENED_ACTIONS and "clear" in THREATENED_ACTIONS


def test_a_buried_cell_is_unreachable_and_nothing_is_dug():
    world = _world()
    ground = world._column(0, 0)[0]
    buried = (0, ground - 3, 0)
    before = dict(world._changes)
    outcome = _clear(world, buried)
    assert (outcome.ok, outcome.error) == (False, "unreachable")
    assert world._changes == before

