"""Seeded arena layouts shared by the simulator and the real-server builder.

Coordinates are absolute world coordinates on the superflat server: the grass
surface is y=-61 and the bot stands at y=-60. The arena is the square
|x|, |z| <= ARENA_RADIUS around the spawn at (0, 0), walled with barriers.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

GROUND_Y = -60
ARENA_RADIUS = 24
SPAWN = (0, 0)


@dataclass
class Layout:
    seed: int
    blocks: list[dict[str, int | str]] = field(default_factory=list)  # {"x","y","z","block"}

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Layout":
        payload = json.loads(text)
        return cls(seed=payload["seed"], blocks=payload["blocks"])


def make_layout(seed: int) -> Layout:
    """Three oak trees 5-9 blocks from spawn and a stone patch with exposed
    coal ore 8-11 blocks away, roughly at a right angle to the trees.

    The trees and the patch are within the 16-block view of each other and of
    spawn, so failures come from rules, not from searching.
    """
    rng = random.Random(seed)
    layout = Layout(seed=seed)
    tree_heading = rng.uniform(0, 2 * math.pi)

    for index in range(3):
        angle = tree_heading + (index - 1) * rng.uniform(0.4, 0.6)
        distance = rng.uniform(5, 9)
        _add_tree(layout, round(distance * math.cos(angle)), round(distance * math.sin(angle)))

    stone_angle = tree_heading + rng.choice((-1, 1)) * (math.pi / 2 + rng.uniform(-0.3, 0.3))
    stone_distance = rng.uniform(8, 11)
    _add_stone_patch(
        layout,
        rng,
        round(stone_distance * math.cos(stone_angle)),
        round(stone_distance * math.sin(stone_angle)),
    )
    return layout


def _add_tree(layout: Layout, x: int, z: int) -> None:
    for dy in range(4):
        layout.blocks.append({"x": x, "y": GROUND_Y + dy, "z": z, "block": "oak_log"})
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            layout.blocks.append({"x": x + dx, "y": GROUND_Y + 4, "z": z + dz, "block": "oak_leaves"})
    layout.blocks.append({"x": x, "y": GROUND_Y + 5, "z": z, "block": "oak_leaves"})


def _add_stone_patch(layout: Layout, rng: random.Random, cx: int, cz: int) -> None:
    """A 4x3 patch, two blocks high. Three coal ores replace top-layer edge blocks."""
    cells = [(cx + dx, cz + dz) for dx in range(-2, 2) for dz in range(-1, 2)]
    edge_cells = [cell for cell in cells if cell[1] != cz]
    coal_cells = set(rng.sample(edge_cells, 3))
    for x, z in cells:
        layout.blocks.append({"x": x, "y": GROUND_Y, "z": z, "block": "stone"})
        top = "coal_ore" if (x, z) in coal_cells else "stone"
        layout.blocks.append({"x": x, "y": GROUND_Y + 1, "z": z, "block": top})


def load_layout(seed: int, directory: Path | None = None) -> Layout:
    """Read a layout file if one was written, otherwise generate it."""
    if directory is not None:
        path = directory / f"layout_{seed}.json"
        if path.exists():
            return Layout.from_json(path.read_text(encoding="utf-8"))
    return make_layout(seed)
