"""Build order (v3.4b): the steps that turn a scanned site into a blueprint, and how far a build has got.

The steps clear what must go top-down, fill the layer under the blueprint, place full blocks bottom-up as support
allows, then torches, then doors. Cells that already match are skipped, so a resumed build just continues.
Contract: docs/v3-interfaces.md, v3.4b "Build order".
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Literal

from . import blueprint
from .blueprint import Pos

BuildPhase = Literal["clear", "fill", "place", "torch", "door"]


@dataclass(frozen=True)
class BuildStep:
    kind: Literal["clear", "place"]
    pos: Pos
    value: str  # place: the palette value (a raw id or a group); clear: "air"
    phase: BuildPhase


_FREE = blueprint.AIR_BLOCKS | blueprint.REPLACEABLE
_NOT_SUPPORTING = frozenset({"water", "lava", "torch", "wall_torch"}) | blueprint.DOORS
_NO_COLLISION_BOX = frozenset(
    {"dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip", "white_tulip",
     "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley", "torchflower", "sunflower", "lilac",
     "rose_bush", "peony", "pink_petals", "brown_mushroom", "red_mushroom", "sugar_cane", "sweet_berry_bush", "vine"}
    | {f"{species}_sapling" for species in ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "cherry")}
)
_FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _is_solid(found: str | None) -> bool:
    """Whether a scanned block gives support: known, with a collision box, and not in the way of a placement."""
    return found is not None and not (found in _FREE or found in _NOT_SUPPORTING or found in _NO_COLLISION_BOX)


def _by_position(pos: Pos) -> tuple[int, int, int]:
    x, y, z = pos
    return (y, z, x)


def _foundation(bp: dict, origin: Pos) -> list[Pos]:
    """The layer -1 cells under the footprint."""
    (ox, oy, oz), (width, _, depth) = origin, bp["size"]
    return [(ox + x, oy - 1, oz + z) for z in range(depth) for x in range(width)]


def build_steps(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[BuildStep]:
    """The steps left to build `bp` at `origin`, given a scan of `blueprint.site_box(bp, origin)`."""
    door_tops = blueprint.door_tops(bp, origin)
    # Unknown cells (missing or None) get no step; a cell that already matches gets none either.
    todo = {
        pos: value
        for pos, value in blueprint.cells(bp, origin)
        if scan.get(pos) is not None and not blueprint.matches(value, scan[pos])
    }

    # Phase 1: clear, top-down. A cell to place only needs clearing when it holds something that isn't free.
    clears = {pos for pos, value in todo.items() if value == "air" or scan[pos] not in _FREE}
    fills = [pos for pos in _foundation(bp, origin) if scan.get(pos) is not None and not _is_solid(scan[pos])]
    clears |= {pos for pos in fills if scan[pos] not in _FREE}
    steps = [
        BuildStep("clear", pos, "air", "clear")
        for pos in sorted(clears, key=lambda pos: (-pos[1], pos[2], pos[0]))
    ]

    # Phase 2: fill the holes under layer 0.
    fill = fill_value(bp)
    steps += [BuildStep("place", pos, fill, "fill") for pos in sorted(fills, key=_by_position)]

    # Phase 3: full blocks, glass and panes, each once something solid is next to it.
    solid = {pos for pos, found in scan.items() if found is not None and _is_solid(found)} - clears
    solid |= set(fills)
    pending = {pos for pos, value in todo.items() if blueprint.carries(value)}
    ready = [_by_position(pos) for pos in pending if _is_ready(pos, solid)]
    heapq.heapify(ready)
    queued = {(x, y, z) for (y, z, x) in ready}
    while ready:
        y, z, x = heapq.heappop(ready)
        steps.append(BuildStep("place", (x, y, z), todo[x, y, z], "place"))
        solid.add((x, y, z))
        for dx, dy, dz in _FACES:
            neighbour = (x + dx, y + dy, z + dz)
            if neighbour in pending and neighbour not in queued:
                queued.add(neighbour)
                heapq.heappush(ready, _by_position(neighbour))
    stuck = pending - queued
    steps += [BuildStep("place", pos, todo[pos], "place") for pos in sorted(stuck, key=_by_position)]

    # Phases 4 and 5: torches, then the lower cell of each door.
    torches = sorted((pos for pos in todo if todo[pos] == "torch"), key=_by_position)
    doors = sorted((pos for pos in todo if todo[pos] in blueprint.DOORS and pos not in door_tops), key=_by_position)
    steps += [BuildStep("place", pos, todo[pos], "torch") for pos in torches]
    steps += [BuildStep("place", pos, todo[pos], "door") for pos in doors]
    return steps


def _is_ready(pos: Pos, solid: set[Pos]) -> bool:
    x, y, z = pos
    return any((x + dx, y + dy, z + dz) in solid for dx, dy, dz in _FACES)


def fill_value(bp: dict) -> str:
    """The value that fills holes under layer 0: the blueprint's most used full-block value."""
    counts = {value: count for value, count in blueprint.materials(bp).items() if blueprint.is_full(value)}
    if not counts:  # a glass-only blueprint: any full block will do
        return blueprint.GROUPS[0]
    groups = blueprint.GROUPS
    return min(counts, key=lambda value: (-counts[value], groups.index(value) if value in groups else len(groups), value))


def progress(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> tuple[int, int]:
    """(placed, total) over the blueprint's placed cells, each door once."""
    door_tops = blueprint.door_tops(bp, origin)
    placed_cells = [
        (pos, value)
        for pos, value in blueprint.cells(bp, origin)
        if value != "air" and pos not in door_tops
    ]
    placed = sum(1 for pos, value in placed_cells if blueprint.matches(value, scan.get(pos)))
    return placed, len(placed_cells)


def mismatches(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[Pos]:
    """Every non-any cell that doesn't match, plus every cell under the footprint that isn't solid."""
    wrong = [pos for pos, value in blueprint.cells(bp, origin) if not blueprint.matches(value, scan.get(pos))]
    wrong += [pos for pos in _foundation(bp, origin) if not _is_solid(scan.get(pos))]
    return sorted(wrong, key=_by_position)


def needed(steps: list[BuildStep]) -> dict[str, int]:
    """Place steps' values -> count: what is left to place."""
    counts: dict[str, int] = {}
    for step in steps:
        if step.kind == "place":
            counts[step.value] = counts.get(step.value, 0) + 1
    return counts
