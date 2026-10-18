"""Build order (v3.4b): the steps that turn a scanned site into a blueprint, and how far a build has got.

The steps clear what must go top-down, fill the layer under the blueprint, place full blocks bottom-up as support
allows, then torches, then doors. Cells that already match are skipped, so a resumed build just continues.
Contract: docs/v3-interfaces.md, v3.4b "Build order".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .blueprint import Pos

BuildPhase = Literal["clear", "fill", "place", "torch", "door"]


@dataclass(frozen=True)
class BuildStep:
    kind: Literal["clear", "place"]
    pos: Pos
    value: str  # place: the palette value (a raw id or a group); clear: "air"
    phase: BuildPhase


def build_steps(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[BuildStep]:
    """The steps left to build `bp` at `origin`, given a scan of `blueprint.site_box(bp, origin)`."""
    raise NotImplementedError


def fill_value(bp: dict) -> str:
    """The value that fills holes under layer 0: the blueprint's most used full-block value."""
    raise NotImplementedError


def progress(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> tuple[int, int]:
    """(placed, total) over the blueprint's placed cells, each door once."""
    raise NotImplementedError


def mismatches(bp: dict, origin: Pos, scan: dict[Pos, str | None]) -> list[Pos]:
    """Every non-any cell that doesn't match, plus every cell under the footprint that isn't solid."""
    raise NotImplementedError


def needed(steps: list[BuildStep]) -> dict[str, int]:
    """Place steps' values -> count: what is left to place."""
    raise NotImplementedError
