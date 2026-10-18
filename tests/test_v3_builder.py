"""v3.4b build order: the steps, the fill value, progress, mismatches and what is needed."""

from schema_voyager import blueprint, builder
from schema_voyager.builder import BuildStep

ORIGIN = (10, 64, -3)
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
HOUSE = {
    "name": "small house",
    "size": [7, 4, 5],
    "palette": {"L": "#log", "P": "#planks", "G": "glass", "D": "oak_door", "T": "torch"},
    "layers": [
        ["LPPPPPL", "P.....P", "P.....P", "P.....P", "LPPDPPL"],
        ["LPPGPPL", "PT....P", "G.....G", "P.....P", "LPPDPPL"],
        ["LPPPPPL", "P.....P", "P.....P", "P.....P", "LPPPPPL"],
        [" PPPPP ", "PPPPPPP", "PPPPPPP", "PPPPPPP", " PPPPP "],
    ],
}


def flat_ground(bp=HOUSE, origin=ORIGIN):
    """A scan of the site box: grass_block under layer 0, air everywhere else."""
    (ox, oy, oz), (width, height, depth) = blueprint.site_box(bp, origin)
    return {
        (ox + x, oy + y, oz + z): "grass_block" if y == 0 else "air"
        for y in range(height) for z in range(depth) for x in range(width)
    }


def matching_scan(bp=HOUSE, origin=ORIGIN):
    """The finished build: wall_torch for the torch, birch_planks for #planks, oak_log for #log."""
    concrete = {"#planks": "birch_planks", "#log": "oak_log", "torch": "wall_torch"}
    scan = flat_ground(bp, origin)
    for pos, value in blueprint.cells(bp, origin):
        scan[pos] = concrete.get(value, value)
    return scan


def test_house_on_flat_ground_orders_place_then_torch_then_door():
    steps = builder.build_steps(HOUSE, ORIGIN, flat_ground())
    phases = [step.phase for step in steps]
    assert "clear" not in phases and "fill" not in phases
    assert phases == ["place"] * (len(steps) - 2) + ["torch", "door"]
    assert steps[-2] == BuildStep("place", (11, 65, -2), "torch", "torch")
    assert steps[-1] == BuildStep("place", (13, 64, 1), "oak_door", "door")

    solid = {pos for pos, found in flat_ground().items() if found == "grass_block"}
    placed_ys = []
    for step in steps[:-2]:
        x, y, z = step.pos
        assert any((x + dx, y + dy, z + dz) in solid for dx, dy, dz in FACES)
        solid.add(step.pos)
        placed_ys.append(y)
    assert placed_ys == sorted(placed_ys)
    assert builder.needed(steps) == blueprint.materials(HOUSE)


def test_overhang_is_placed_after_its_support():
    bp = {"name": "ledge", "size": [3, 2, 1], "palette": {"P": "#planks"}, "layers": [["P.."], ["PPP"]]}
    origin = (0, 10, 0)
    steps = builder.build_steps(bp, origin, flat_ground(bp, origin))
    assert [step.pos for step in steps] == [(0, 10, 0), (0, 11, 0), (1, 11, 0), (2, 11, 0)]


def test_half_built_scan_gives_steps_for_the_rest_and_counts_progress():
    scan = matching_scan()
    for pos, _ in blueprint.cells(HOUSE, ORIGIN):
        if pos[1] >= ORIGIN[1] + 2:
            scan[pos] = "air"
    steps = builder.build_steps(HOUSE, ORIGIN, scan)
    assert steps and all(step.pos[1] >= ORIGIN[1] + 2 for step in steps)
    placed, total = builder.progress(HOUSE, ORIGIN, scan)
    assert total == 74 + 12 + 3 + 1 + 1 and placed == total - len(steps)
    assert builder.progress(HOUSE, ORIGIN, flat_ground()) == (0, total)


def test_grass_in_interior_is_cleared_but_not_under_a_placed_cell():
    scan = flat_ground()
    scan[(12, 64, -2)] = "short_grass"  # interior air cell
    scan[(11, 64, -3)] = "short_grass"  # a plank goes here
    steps = builder.build_steps(HOUSE, ORIGIN, scan)
    assert [step for step in steps if step.kind == "clear"] == [BuildStep("clear", (12, 64, -2), "air", "clear")]
    assert steps[0].kind == "clear"


def test_hole_and_flower_under_layer_0_are_filled_flower_cleared_first():
    scan = flat_ground()
    scan[(12, 63, -2)] = "air"
    scan[(14, 63, -1)] = "poppy"
    steps = builder.build_steps(HOUSE, ORIGIN, scan)
    assert steps[:3] == [
        BuildStep("clear", (14, 63, -1), "air", "clear"),
        BuildStep("place", (12, 63, -2), "#planks", "fill"),
        BuildStep("place", (14, 63, -1), "#planks", "fill"),
    ]
    assert builder.needed(steps)["#planks"] == 74 + 2


def test_stone_bump_where_a_plank_goes_is_cleared_then_placed():
    scan = flat_ground()
    scan[(11, 64, -3)] = "stone"
    steps = builder.build_steps(HOUSE, ORIGIN, scan)
    assert steps[0] == BuildStep("clear", (11, 64, -3), "air", "clear")
    assert BuildStep("place", (11, 64, -3), "#planks", "place") in steps


def test_unknown_cells_get_no_step():
    scan = flat_ground()
    scan[(11, 64, -3)] = None
    del scan[(12, 64, -3)]
    positions = {step.pos for step in builder.build_steps(HOUSE, ORIGIN, scan)}
    assert (11, 64, -3) not in positions and (12, 64, -3) not in positions


def test_fill_value_is_the_most_used_full_block_ties_by_group_order():
    assert builder.fill_value(HOUSE) == "#planks"
    tie = {"name": "t", "size": [2, 1, 1], "palette": {"L": "#log", "C": "#cobblestone"}, "layers": [["LC"]]}
    assert builder.fill_value(tie) == "#log"


def test_mismatches_empty_for_a_matching_scan():
    assert builder.mismatches(HOUSE, ORIGIN, matching_scan()) == []
    assert builder.build_steps(HOUSE, ORIGIN, matching_scan()) == []
    assert builder.progress(HOUSE, ORIGIN, matching_scan()) == (91, 91)


def test_mismatches_list_wrong_cells_door_tops_and_holes_in_order():
    scan = matching_scan()
    scan[(13, 65, 1)] = "air"  # door top
    scan[(10, 63, -3)] = "air"  # hole
    scan[(11, 64, -2)] = "stone"  # air cell with stone
    assert builder.mismatches(HOUSE, ORIGIN, scan) == [(10, 63, -3), (11, 64, -2), (13, 65, 1)]
