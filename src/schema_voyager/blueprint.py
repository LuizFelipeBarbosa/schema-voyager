"""Blueprints (v3.4a): the format Claude writes for a build, its checks, the materials list and the cells.

A blueprint is a plain dict: {"name", "size": [width, height, depth], "palette", "layers"}, read as
layers[y][z][x]. Layers go bottom-up, rows north to south, characters west to east, and the origin is the
north-west bottom corner. "." is always air and a space is always any. Contract: docs/v3-interfaces.md, v3.4a.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterator

from . import normalize

Pos = tuple[int, int, int]
Box = tuple[Pos, tuple[int, int, int]]  # (origin, size), as scan takes them (v3.4b)

MAX_SIZE: tuple[int, int, int] = (16, 12, 16)  # width (x), height (y), depth (z)
GROUPS: tuple[str, ...] = ("#planks", "#log", "#cobblestone")
FULL_BLOCKS: frozenset[str] = frozenset(
    [member for group in GROUPS for member in normalize.members(group)]
    + ["mossy_cobblestone", "stone", "smooth_stone", "stone_bricks", "bricks", "sandstone",
       "polished_andesite", "polished_diorite", "polished_granite"]
)
DOORS: frozenset[str] = frozenset(f"{species}_door" for species in normalize.SPECIES)
PLACEABLE: frozenset[str] = FULL_BLOCKS | DOORS | {"glass", "glass_pane", "torch"}
AIR_BLOCKS: frozenset[str] = frozenset({"air", "cave_air", "void_air"})
# Plants a placement replaces: with AIR_BLOCKS, a cell that is free to place in (the bot and the simulator).
REPLACEABLE: frozenset[str] = frozenset({"short_grass", "tall_grass", "fern", "large_fern", "dead_bush", "snow"})


_BUILT_IN = {".": "air", " ": "any"}
_MAX_MESSAGES = 20
_NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def validate(bp: Any) -> list[str]:
    """Every error, each starting with the JSON path it is about; [] when bp is valid. At most 20."""
    if not isinstance(bp, dict):
        return ["blueprint: must be a JSON object"]
    errors = [f"blueprint: missing key '{key}'" for key in ("name", "size", "palette", "layers") if key not in bp]
    errors += [f"blueprint: unknown key '{key}'" for key in sorted(set(bp) - {"name", "size", "palette", "layers"})]

    if "name" in bp and not (isinstance(bp["name"], str) and 1 <= len(bp["name"]) <= 60):
        errors.append("name: must be a string of 1 to 60 characters")
    size = _check_size(bp["size"], errors) if "size" in bp else None
    palette_ok = "palette" in bp and _check_palette(bp["palette"], errors)
    layers_ok = "layers" in bp and _is_list_of_layers(bp["layers"])
    if "layers" in bp and not layers_ok:
        errors.append("layers: must be a list of layers, each a list of row strings")
    if layers_ok and size:
        errors += _shape_errors(bp["layers"], size)
    if layers_ok and palette_ok:
        errors += _unknown_character_errors(bp["layers"], bp["palette"])

    if not errors:
        errors += _support_errors(bp)
    if len(errors) > _MAX_MESSAGES:
        errors = errors[: _MAX_MESSAGES - 1] + [f"... and {len(errors) - _MAX_MESSAGES + 1} more"]
    return errors


def _check_size(size: Any, errors: list[str]) -> list[int] | None:
    """Appends the size errors; the size when it is valid, else None."""
    is_triple = (
        isinstance(size, list)
        and len(size) == 3
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in size)
    )
    if not is_triple:
        errors.append("size: must be [width, height, depth], three integers")
        return None
    if any(n > limit for n, limit in zip(size, MAX_SIZE)):
        errors.append(f"size: {size} is over the limit {MAX_SIZE[0]} x {MAX_SIZE[1]} x {MAX_SIZE[2]}")
        return None
    return size


def _check_palette(palette: Any, errors: list[str]) -> bool:
    """Appends the palette errors; whether the palette is a dict (its keys can then be looked up)."""
    if not isinstance(palette, dict):
        errors.append("palette: must be an object of one-character keys")
        return False
    allowed = PLACEABLE | set(GROUPS) | {"air", "any"}
    for key, value in palette.items():
        if len(key) != 1:
            errors.append(f"palette: key '{key}' must be one character")
        elif key in _BUILT_IN and value != _BUILT_IN[key]:
            errors.append(f"palette['{key}']: '{key}' always means {_BUILT_IN[key]}")
        elif not isinstance(value, str) or value not in allowed:
            errors.append(f"palette['{key}']: '{value}' is not a block this version can place")
    return True


def _is_list_of_layers(layers: Any) -> bool:
    return isinstance(layers, list) and all(
        isinstance(layer, list) and all(isinstance(row, str) for row in layer) for layer in layers
    )


def _shape_errors(layers: list[list[str]], size: list[int]) -> list[str]:
    width, height, depth = size
    row_counts = {len(layer) for layer in layers}
    row_lengths = {len(row) for layer in layers for row in layer}
    if len(row_counts) == 1 and len(row_lengths) == 1 and 0 not in row_counts | row_lengths:
        actual = [row_lengths.pop(), len(layers), row_counts.pop()]
        if actual == size:
            return []
        return [f"size: says {size} but the layers are {actual[0]} wide, {actual[1]} high and {actual[2]} deep: {actual}"]

    errors = [f"layers: {len(layers)} layers, size says {height}"] if len(layers) != height else []
    for y, layer in enumerate(layers):
        if len(layer) != depth:
            errors.append(f"layers[{y}]: {len(layer)} rows, size says {depth}")
        errors += [
            f"layers[{y}][{z}]: '{row}' has {len(row)} characters, size says {width}"
            for z, row in enumerate(layer)
            if len(row) != width
        ]
    return errors


def _unknown_character_errors(layers: list[list[str]], palette: dict[str, Any]) -> list[str]:
    first_cell: dict[str, str] = {}
    for y, layer in enumerate(layers):
        for z, row in enumerate(layer):
            for x, char in enumerate(row):
                if char not in palette and char not in _BUILT_IN:
                    first_cell.setdefault(char, f"layers[{y}][{z}][{x}]")
    return [f"{cell}: '{char}' is not in the palette" for char, cell in first_cell.items()]


def _grid(bp: dict[str, Any]) -> dict[Pos, tuple[str, str]]:
    """(x, y, z) -> (palette key, palette value) for every cell, in layer, row, character order."""
    palette = {**bp["palette"], **_BUILT_IN}
    return {
        (x, y, z): (char, palette[char])
        for y, layer in enumerate(bp["layers"])
        for z, row in enumerate(layer)
        for x, char in enumerate(row)
    }


def _support_errors(bp: dict[str, Any]) -> list[str]:
    """Doors, then torches, then the connection to the ground, each kind in cell order; bp has a valid shape."""
    grid = _grid(bp)
    if all(value in ("air", "any") for _, value in grid.values()):
        return ["layers: the blueprint places no blocks"]

    def value_at(x: int, y: int, z: int) -> str | None:
        return grid[x, y, z][1] if (x, y, z) in grid else None

    def name(x: int, y: int, z: int) -> str:
        return f"layers[{y}][{z}][{x}]"

    door_errors, torch_errors = [], []
    for (x, y, z), (char, value) in grid.items():
        if value in DOORS and value_at(x, y - 1, z) != value:
            height = 1
            while value_at(x, y + height, z) == value:
                height += 1
            if height == 1:
                door_errors.append(f"{name(x, y, z)}: the door '{char}' needs the same door in the cell above")
            elif height > 2:
                door_errors.append(f"{name(x, y, z)}: the door '{char}' is {height} cells tall, a door is 2")
            elif y > 0 and not is_full(value_at(x, y - 1, z) or ""):
                door_errors.append(f"{name(x, y, z)}: the door '{char}' needs a full block below")
        if value == "torch":
            supports = [(x, y - 1, z), (x + 1, y, z), (x - 1, y, z), (x, y, z + 1), (x, y, z - 1)]
            if y > 0 and not any(is_full(value_at(*pos) or "") for pos in supports):
                torch_errors.append(f"{name(x, y, z)}: the torch '{char}' needs a full block below or beside it")

    return door_errors + torch_errors + _connection_errors(grid)


def _connection_errors(grid: dict[Pos, tuple[str, str]]) -> list[str]:
    """One message per group of carrying cells that doesn't reach layer 0 face to face."""
    carrying = {pos for pos, (_, value) in grid.items() if carries(value)}

    def flood(start: list[Pos], within: set[Pos]) -> set[Pos]:
        seen, queue = set(start), deque(start)
        while queue:
            x, y, z = queue.popleft()
            for dx, dy, dz in _NEIGHBOURS:
                pos = (x + dx, y + dy, z + dz)
                if pos in within and pos not in seen:
                    seen.add(pos)
                    queue.append(pos)
        return seen

    grounded = flood([pos for pos in carrying if pos[1] == 0], carrying)
    floating = carrying - grounded
    errors = []
    for pos in grid:  # cell order, so each group is named by its first cell
        if pos in floating:
            group = flood([pos], floating)
            floating -= group
            x, y, z = pos
            cell = f"layers[{y}][{z}][{x}]: '{grid[pos][0]}'"
            if len(group) == 1:
                errors.append(f"{cell} is not connected to the ground")
            else:
                errors.append(f"{cell} and {len(group) - 1} other cells are not connected to the ground")
    return errors


def materials(bp: dict[str, Any]) -> dict[str, int]:
    """Palette value -> count over the placed cells; air and any left out, a two-cell door counted once."""
    counts: dict[str, int] = {}
    for _, value in _grid(bp).values():
        if value not in ("air", "any"):
            counts[value] = counts.get(value, 0) + 1
    return {value: count // 2 if value in DOORS else count for value, count in counts.items()}


def cells(bp: dict[str, Any], origin: Pos) -> Iterator[tuple[Pos, str]]:
    """(world position, palette value) for every cell that isn't any, air and both door cells included."""
    ox, oy, oz = origin
    for (x, y, z), (_, value) in _grid(bp).items():
        if value != "any":
            yield (ox + x, oy + y, oz + z), value


def door_tops(bp: dict[str, Any], origin: Pos) -> set[Pos]:
    """The upper cell of each door: never placed, since /place on the lower cell fills it."""
    ox, oy, oz = origin
    grid = _grid(bp)
    return {
        (ox + x, oy + y, oz + z)
        for (x, y, z), (_, value) in grid.items()
        if value in DOORS and grid.get((x, y - 1, z), ("", ""))[1] == value
    }


def matches(expected: str, found: str | None) -> bool:
    """Whether a scanned raw id (None: a cell the bot can't see) satisfies a palette value; a door value takes any
    wooden door."""
    if expected == "any":
        return True
    if found is None:
        return False
    if expected == "air":
        return found in AIR_BLOCKS
    if expected in GROUPS:
        return found in normalize.members(expected)
    if expected in DOORS:
        return found in DOORS
    if expected == "torch":
        return found in ("torch", "wall_torch")
    return found == expected


def render(bp: dict[str, Any]) -> list[list[str]]:
    """The layer slices for the panel, bottom-up, rows north to south; air keys as ".", any keys as " "."""
    palette = {**bp["palette"], **_BUILT_IN}
    shown = {"air": ".", "any": " "}
    return [
        ["".join(shown.get(palette[char], char) for char in row) for row in layer]
        for layer in bp["layers"]
    ]


def is_full(value: str) -> bool:
    """A FULL_BLOCKS member or a group: what a door or a torch needs to stand on or hang from."""
    return value in FULL_BLOCKS or value in GROUPS


def carries(value: str) -> bool:
    """Whether a placed cell with this value carries its neighbours: a full block, glass or a pane."""
    return is_full(value) or value in ("glass", "glass_pane")


def site_box(bp: dict, origin: Pos) -> Box:
    """v3.4b: the blueprint's box plus the layer under it, which the builder fills: what a build scans and protects."""
    (ox, oy, oz), (width, height, depth) = origin, bp["size"]
    return (ox, oy - 1, oz), (width, height + 1, depth)


def merge_boxes(boxes: list[Box], limit: int = 32) -> list[Box]:
    """v3.5e: at most `limit` boxes covering all of them. While there are more, the pair whose bounding box adds the
    least volume (ties: the lower index pair) is replaced by that bounding box, at the lower index."""
    boxes = list(boxes)
    while len(boxes) > limit:
        added, first, second = min(
            (_volume(_bounding_box(a, b)) - _volume(a) - _volume(b), i, j)
            for i, a in enumerate(boxes)
            for j, b in enumerate(boxes) if i < j
        )
        boxes[first] = _bounding_box(boxes[first], boxes[second])
        del boxes[second]
    return boxes


def _volume(box: Box) -> int:
    width, height, depth = box[1]
    return width * height * depth


def _bounding_box(a: Box, b: Box) -> Box:
    low = tuple(min(a[0][axis], b[0][axis]) for axis in range(3))
    high = tuple(max(a[0][axis] + a[1][axis], b[0][axis] + b[1][axis]) for axis in range(3))
    return low, tuple(high[axis] - low[axis] for axis in range(3))
