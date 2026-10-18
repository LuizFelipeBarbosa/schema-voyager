"""Blueprints (v3.4a): validate's messages, materials, cells, door_tops, matches, render."""

import copy

from schema_voyager import blueprint

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


def make(layers, palette=None, name="b"):
    """A blueprint whose size is read off the layers (which must form a box)."""
    return {
        "name": name,
        "size": [len(layers[0][0]), len(layers), len(layers[0])],
        "palette": {"P": "#planks", "G": "glass", "D": "oak_door", "T": "torch", **(palette or {})},
        "layers": layers,
    }


def test_house_is_valid_and_its_materials_are_the_contracts():
    assert blueprint.validate(HOUSE) == []
    assert blueprint.materials(HOUSE) == {"#planks": 74, "#log": 12, "glass": 3, "torch": 1, "oak_door": 1}


def test_door_tops_of_house():
    assert blueprint.door_tops(HOUSE, (10, 64, -3)) == {(13, 65, 1)}


def test_cells_order_count_and_content():
    found = list(blueprint.cells(HOUSE, (10, 64, -3)))
    assert found[0] == ((10, 64, -3), "#log")
    assert found[1] == ((11, 64, -3), "#planks")
    assert found[8] == ((11, 64, -2), "air")
    positions = [pos for pos, _ in found]
    assert positions == sorted(positions, key=lambda p: (p[1], p[2], p[0]))
    # 4 layers of 35 cells, minus the 4 any corners of the roof.
    assert len(found) == 140 - 4
    assert ((13, 64, 1), "oak_door") in found and ((13, 65, 1), "oak_door") in found
    assert ((10, 67, -3), "any") not in found and not any(v == "any" for _, v in found)


def test_not_a_dict():
    assert blueprint.validate([]) == ["blueprint: must be a JSON object"]
    assert blueprint.validate(None) == ["blueprint: must be a JSON object"]


def test_missing_and_unknown_keys():
    complete = {"name": "x", "size": [1, 1, 1], "palette": {"P": "#planks"}, "layers": [["P"]]}
    assert blueprint.validate({**complete, "notes": 1, "a": 2}) == [
        "blueprint: unknown key 'a'",
        "blueprint: unknown key 'notes'",
    ]
    assert blueprint.validate({"name": "x"}) == [
        "blueprint: missing key 'size'",
        "blueprint: missing key 'palette'",
        "blueprint: missing key 'layers'",
    ]


def test_name():
    for bad in ("", "x" * 61, 3, None):
        assert blueprint.validate({**HOUSE, "name": bad}) == ["name: must be a string of 1 to 60 characters"]
    assert blueprint.validate({**HOUSE, "name": "x" * 60}) == []


def test_size_shape_and_limit():
    shape = "size: must be [width, height, depth], three integers"
    for bad in ([7, 4], "7x4x5", [7, 4, 5.0], [7, True, 5], [0, 4, 5], [7, 4, -1], None):
        assert blueprint.validate({**HOUSE, "size": bad}) == [shape]
    assert blueprint.validate({**HOUSE, "size": [20, 5, 7]}) == ["size: [20, 5, 7] is over the limit 16 x 12 x 16"]


def test_palette_errors():
    assert blueprint.validate({**HOUSE, "palette": []}) == ["palette: must be an object of one-character keys"]
    assert "palette: key 'AB' must be one character" in blueprint.validate(
        {**HOUSE, "palette": {**HOUSE["palette"], "AB": "glass"}})
    assert "palette: key '' must be one character" in blueprint.validate(
        {**HOUSE, "palette": {**HOUSE["palette"], "": "glass"}})
    assert "palette['.']: '.' always means air" in blueprint.validate(
        {**HOUSE, "palette": {**HOUSE["palette"], ".": "glass"}})
    assert "palette[' ']: ' ' always means any" in blueprint.validate(
        {**HOUSE, "palette": {**HOUSE["palette"], " ": "air"}})
    assert blueprint.validate({**HOUSE, "palette": {**HOUSE["palette"], ".": "air", " ": "any"}}) == []
    assert blueprint.validate({**HOUSE, "palette": {**HOUSE["palette"], "X": "oak_stairs"}}) == [
        "palette['X']: 'oak_stairs' is not a block this version can place"]
    assert blueprint.validate({**HOUSE, "palette": {**HOUSE["palette"], "X": 7}}) == [
        "palette['X']: '7' is not a block this version can place"]


def test_layers_not_a_list_of_lists_of_strings():
    message = "layers: must be a list of layers, each a list of row strings"
    for bad in ("abc", [["a"], "b"], [[1]], {}):
        assert blueprint.validate({**HOUSE, "layers": bad}) == [message]


def test_layer_row_and_character_counts():
    short = copy.deepcopy(HOUSE)
    del short["layers"][3]
    assert blueprint.validate(short) == [
        "size: says [7, 4, 5] but the layers are 7 wide, 3 high and 5 deep: [7, 3, 5]"]

    fewer_rows = copy.deepcopy(HOUSE)
    fewer_rows["layers"][1] = fewer_rows["layers"][1][:4]
    assert blueprint.validate(fewer_rows) == ["layers[1]: 4 rows, size says 5"]

    short_row = copy.deepcopy(HOUSE)
    short_row["layers"][1][3] = "P....P"
    assert blueprint.validate(short_row) == ["layers[1][3]: 'P....P' has 6 characters, size says 7"]


def test_layer_count_with_ragged_rows():
    ragged = copy.deepcopy(HOUSE)
    ragged["layers"][0][0] = "LPPPPP"
    del ragged["layers"][3]
    assert blueprint.validate(ragged) == [
        "layers: 3 layers, size says 4",
        "layers[0][0]: 'LPPPPP' has 6 characters, size says 7",
    ]


def test_size_mismatch_with_clean_box_is_one_message():
    assert blueprint.validate({**HOUSE, "size": [7, 5, 7]}) == [
        "size: says [7, 5, 7] but the layers are 7 wide, 4 high and 5 deep: [7, 4, 5]"]


def test_invalid_size_skips_the_counts():
    assert blueprint.validate({**HOUSE, "size": [7, 4]}) == ["size: must be [width, height, depth], three integers"]


def test_unknown_characters_one_message_per_character():
    bp = make([["PPP", "PXP", "PPP"], ["PXY", "PPP", "PPX"]])
    assert blueprint.validate(bp) == [
        "layers[0][1][1]: 'X' is not in the palette",
        "layers[1][0][2]: 'Y' is not in the palette",
    ]


def test_unknown_characters_are_reported_with_a_shape_error():
    bp = make([["PPP", "PXP", "PPP"]])
    bp["size"] = [3, 1, 4]
    assert blueprint.validate(bp) == [
        "size: says [3, 1, 4] but the layers are 3 wide, 1 high and 3 deep: [3, 1, 3]",
        "layers[0][1][1]: 'X' is not in the palette",
    ]


def test_no_blocks():
    bp = make([["...", ". .", "..."]])
    assert blueprint.validate(bp) == ["layers: the blueprint places no blocks"]


def test_one_cell_door():
    bp = make([["PPP", "PDP", "PPP"], ["...", "...", "..."]])
    assert blueprint.validate(bp) == ["layers[0][1][1]: the door 'D' needs the same door in the cell above"]


def test_three_cell_door():
    bp = make([["PPP", "PDP", "PPP"], ["...", ".D.", "..."], ["...", ".D.", "..."]])
    assert blueprint.validate(bp) == ["layers[0][1][1]: the door 'D' is 3 cells tall, a door is 2"]


def test_door_needs_a_full_block_below():
    bp = make([["PPP", "PGP", "PPP"], ["...", ".D.", "..."], ["...", ".D.", "..."]])
    assert blueprint.validate(bp) == ["layers[1][1][1]: the door 'D' needs a full block below"]
    on_ground = make([["...", ".D.", "..."], ["...", ".D.", "..."]])
    assert blueprint.validate(on_ground) == []


def test_door_runs_go_by_value_not_key():
    bp = make([["PPP", "PDP", "PPP"], ["...", ".E.", "..."]], {"E": "oak_door"})
    assert blueprint.validate(bp) == []
    assert blueprint.materials(bp) == {"#planks": 8, "oak_door": 1}


def test_different_doors_do_not_pair():
    bp = make([["...", ".D.", "..."], ["...", ".E.", "..."]], {"E": "birch_door"})
    assert blueprint.validate(bp) == [
        "layers[0][1][1]: the door 'D' needs the same door in the cell above",
        "layers[1][1][1]: the door 'E' needs the same door in the cell above",
    ]


def test_torch_support():
    hanging = make([["PPP", "PPP", "PPP"], ["...", ".T.", "..."]])
    assert blueprint.validate(hanging) == []  # a full block below
    floating = make([["PPP", "P.P", "PPP"], ["...", "...", "..."], ["...", ".T.", "..."]])
    assert blueprint.validate(floating) == [
        "layers[2][1][1]: the torch 'T' needs a full block below or beside it"]
    on_ground = make([["T.P"]])
    assert blueprint.validate(on_ground) == []
    wall = make([["PPP", "P.P", "PPP"], ["PT.", "...", "..."]])
    assert blueprint.validate(wall) == []  # beside the plank to its west


def test_torch_on_top_of_glass_is_unsupported():
    bp = make([["PPP", "...", "..."], ["G..", "...", "..."], ["T..", "...", "..."]])
    assert blueprint.validate(bp) == ["layers[2][0][0]: the torch 'T' needs a full block below or beside it"]


def test_connection_single_cell_and_group():
    single = make([["PPP", "...", "..."], ["...", "...", "..."], ["...", "...", "P.."]])
    assert blueprint.validate(single) == ["layers[2][2][0]: 'P' is not connected to the ground"]

    group = make([["PPP", "...", "..."], ["...", "...", "..."], [".PP", "PP.", "..."]])
    assert blueprint.validate(group) == ["layers[2][0][1]: 'P' and 3 other cells are not connected to the ground"]


def test_connection_components_and_glass_carry():
    bp = make([["PGP", "...", "..."], ["PGP", "...", "..."], ["PGP", "...", "..."]])
    bp["layers"][1] = ["...", "...", "..."]
    # layer 2 has a floating row of three cells: one component
    assert blueprint.validate(bp) == ["layers[2][0][0]: 'P' and 2 other cells are not connected to the ground"]
    bp["layers"][1] = ["P.P", "...", "..."]
    # the glass between the two planks above is joined through the plank at (0, 0) and (2, 0)
    assert blueprint.validate(bp) == []


def test_two_unconnected_components_get_two_messages():
    bp = make([["P..", "...", "..."], ["...", "...", "..."], ["P.P", "...", "..."]])
    assert blueprint.validate(bp) == [
        "layers[2][0][0]: 'P' is not connected to the ground",
        "layers[2][0][2]: 'P' is not connected to the ground",
    ]


def test_doors_and_torches_carry_nothing():
    bp = make([["...", ".D.", "..."], ["...", ".D.", "..."], ["...", ".P.", "..."]])
    assert blueprint.validate(bp) == ["layers[2][1][1]: 'P' is not connected to the ground"]


def test_message_cap_with_more_than_twenty():
    letters = "abcdefghijklmnopqrstuvwxyz"[:24]
    bp = {"name": "x", "size": [16, 1, 2], "palette": {"P": "#planks"},
          "layers": [[letters[:16], letters[16:] + "PPPPPPPP"]]}
    errors = blueprint.validate(bp)
    assert len(errors) == 20
    assert errors[:19] == [f"layers[0][{i // 16}][{i % 16}]: '{letters[i]}' is not in the palette" for i in range(19)]
    assert errors[19] == "... and 5 more"


def test_groups_in_materials_and_matches():
    bp = make([["PC"]], {"C": "#cobblestone", "P": "#planks"})
    assert blueprint.materials(bp) == {"#planks": 1, "#cobblestone": 1}
    assert blueprint.matches("#planks", "oak_planks")
    assert not blueprint.matches("#planks", "stone")
    assert blueprint.matches("#cobblestone", "blackstone")
    assert blueprint.matches("#log", "stripped_oak_wood")
    assert not blueprint.matches("#log", "oak_planks")


def test_materials_sum_keys_sharing_a_value():
    bp = make([["PQ."]], {"Q": "#planks"})
    assert blueprint.materials(bp) == {"#planks": 2}


def test_matches_variants():
    assert blueprint.matches("any", None) and blueprint.matches("any", "dirt")
    assert not blueprint.matches("air", None) and not blueprint.matches("oak_door", None)
    for air in ("air", "cave_air", "void_air"):
        assert blueprint.matches("air", air)
    assert not blueprint.matches("air", "stone")
    assert blueprint.matches("torch", "torch") and blueprint.matches("torch", "wall_torch")
    assert not blueprint.matches("torch", "air")
    assert blueprint.matches("glass", "glass") and not blueprint.matches("glass", "glass_pane")
    assert blueprint.matches("oak_door", "oak_door")


def test_render_maps_air_and_any_keys():
    bp = make([["PAN", "..."]], {"A": "air", "N": "any"})
    assert blueprint.render(bp) == [["P. ", "..."]]


def test_render_house():
    rendered = blueprint.render(HOUSE)
    assert len(rendered) == 4
    assert rendered[0][1] == "P.....P"
    assert rendered[3][0] == " PPPPP "


def test_is_full_and_carries():
    for value in ("oak_planks", "stone", "#planks", "#log", "#cobblestone", "bricks"):
        assert blueprint.is_full(value) and blueprint.carries(value)
    for value in ("glass", "glass_pane"):
        assert not blueprint.is_full(value) and blueprint.carries(value)
    for value in ("oak_door", "torch", "air", "any", "dirt"):
        assert not blueprint.is_full(value) and not blueprint.carries(value)
