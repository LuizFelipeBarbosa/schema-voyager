"""Blueprints (v3.4a): the format Claude writes for a build, its checks, the materials list and the cells.

A blueprint is a plain dict: {"name", "size": [width, height, depth], "palette", "layers"}, read as
layers[y][z][x]. Layers go bottom-up, rows north to south, characters west to east, and the origin is the
north-west bottom corner. "." is always air and a space is always any. Contract: docs/v3-interfaces.md, v3.4a.
"""

from __future__ import annotations

from typing import Any, Iterator

from . import normalize

Pos = tuple[int, int, int]

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


def validate(bp: Any) -> list[str]:
    """Every error, each starting with the JSON path it is about; [] when bp is valid. At most 20."""
    raise NotImplementedError


def materials(bp: dict[str, Any]) -> dict[str, int]:
    """Palette value -> count over the placed cells; air and any left out, a two-cell door counted once."""
    raise NotImplementedError


def cells(bp: dict[str, Any], origin: Pos) -> Iterator[tuple[Pos, str]]:
    """(world position, palette value) for every cell that isn't any, air and both door cells included."""
    raise NotImplementedError


def door_tops(bp: dict[str, Any], origin: Pos) -> set[Pos]:
    """The upper cell of each door: never placed, since /place on the lower cell fills it."""
    raise NotImplementedError


def matches(expected: str, found: str | None) -> bool:
    """Whether a scanned raw id (None: a cell the bot can't see) satisfies a palette value."""
    raise NotImplementedError


def render(bp: dict[str, Any]) -> list[list[str]]:
    """The layer slices for the panel, bottom-up, rows north to south; air keys as ".", any keys as " "."""
    raise NotImplementedError


def is_full(value: str) -> bool:
    """A FULL_BLOCKS member or a group: what a door or a torch needs to stand on or hang from."""
    raise NotImplementedError


def carries(value: str) -> bool:
    """Whether a placed cell with this value carries its neighbours: a full block, glass or a pane."""
    raise NotImplementedError
