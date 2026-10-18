"""v3.5: the bot's home (the first house it built), the yard around it where its crafting table, furnace and chest
stand, and what the chest holds. Pure functions over blueprints and scans. Contract: docs/v3-interfaces.md, v3.5b."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from . import blueprint
from .blueprint import Box, Pos
from .builder import _is_solid
from .models import HomeStock

YARD = 2  # blocks around the house's footprint where its stations stand
HOME_STATION_KINDS = ("chest", "crafting_table", "furnace")  # HOME_RANGE = 48 is in models.py, for HomeStock
SLOT_BLAME_BLOCKS = 6.0  # a walk that ended this close to a cell (centre) blames the cell, not where the bot is (v3.0i)


@dataclass(frozen=True)
class Home:
    goal_id: str
    name: str
    origin: Pos
    size: tuple[int, int, int]
    door: Pos | None


@dataclass
class Places:
    home: Home | None = None
    stations: dict[str, Pos] = field(default_factory=dict)
    stored: dict[str, int] = field(default_factory=dict)
    chest_goal: str | None = None  # the id of the one chest goal asked for this home (v3.5c)
    bad_slots: list[Pos] = field(default_factory=list)  # yard cells the bot couldn't walk to (v3.0i)
    moved: set[str] = field(default_factory=set)  # kinds already moved once from an unreachable slot
    retired: set[str] = field(default_factory=set)  # kinds dropped for good: never furnished again

    def to_json(self) -> dict:
        home = self.home
        return {
            "home": None if home is None else {
                "goal_id": home.goal_id, "name": home.name, "origin": list(home.origin), "size": list(home.size),
                "door": None if home.door is None else list(home.door),
            },
            "stations": {kind: list(pos) for kind, pos in self.stations.items()},
            "stored": dict(self.stored),
            "chest_goal": self.chest_goal,
            "bad_slots": [list(cell) for cell in self.bad_slots],
            "moved": sorted(self.moved),
            "retired": sorted(self.retired),
        }

    @classmethod
    def from_json(cls, data: dict | None) -> Places:
        data = data or {}
        home = data.get("home")
        return cls(
            home=None if home is None else Home(
                home["goal_id"], home["name"], tuple(home["origin"]), tuple(home["size"]),
                None if home.get("door") is None else tuple(home["door"]),
            ),
            stations={kind: tuple(pos) for kind, pos in (data.get("stations") or {}).items()},
            stored=dict(data.get("stored") or {}),
            chest_goal=data.get("chest_goal"),
            bad_slots=[tuple(cell) for cell in data.get("bad_slots") or []],
            moved=set(data.get("moved") or []),
            retired=set(data.get("retired") or []),
        )

    def stock(self) -> HomeStock:
        return HomeStock(
            stations={kind: pos for kind, pos in self.stations.items() if kind in ("crafting_table", "furnace")},
            chest=self.stations.get("chest"),
            stored=dict(self.stored),
        )


def door_cell(bp: dict, origin: Pos) -> Pos | None:
    """The lower cell of the blueprint's first door value: layers bottom-up, then rows (z), then characters (x)."""
    ox, oy, oz = origin
    for y, layer in enumerate(bp["layers"]):
        for z, row in enumerate(layer):
            for x, char in enumerate(row):
                if bp["palette"].get(char) in blueprint.DOORS:
                    return ox + x, oy + y, oz + z
    return None


def home_from_build(entry: dict, bp: dict | None) -> Home:
    """The Home of a protected entry (goal_id, name, origin, size) and its blueprint (the door; None without one)."""
    origin = tuple(entry["origin"])
    return Home(
        entry["goal_id"], entry["name"], origin, tuple(entry["size"]),
        None if bp is None else door_cell(bp, origin),
    )


def home_from_protected(entries: list[dict], blueprint_for: Callable[[str], dict | None]) -> Home | None:
    """The home of a life saved before v3.5: its first `built` protected entry, else None."""
    built = next((entry for entry in entries if entry["status"] == "built"), None)
    return None if built is None else home_from_build(built, blueprint_for(built["goal_id"]))


def home_box(home: Home) -> Box:
    """The site box (the footprint and the layer under it) grown by YARD in x and z: what is protected."""
    (ox, oy, oz), (width, height, depth) = home.origin, home.size
    return (ox - YARD, oy - 1, oz - YARD), (width + 2 * YARD, height + 1, depth + 2 * YARD)


def yard_scan_box(home: Home) -> Box:
    """The yard, its support layer and headroom: what furnishing scans."""
    (ox, oy, oz), (width, _, depth) = home.origin, home.size
    return (ox - YARD, oy - 2, oz - YARD), (width + 2 * YARD, 5, depth + 2 * YARD)


def blames_slot(cell: Pos, position: dict | Sequence[float] | None) -> bool:
    """Whether a walk that ended at position (an answer's {x, y, z}, or a 3-sequence) stopped within SLOT_BLAME_BLOCKS
    of the cell's centre: the slot is to blame. Farther away, the bot's own spot is (a hole, a cave). None or
    malformed is False."""
    try:
        if isinstance(position, dict):
            position = (position["x"], position["y"], position["z"])
        end = tuple(float(value) for value in position)
        if len(end) != 3:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return math.dist(end, (cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5)) <= SLOT_BLAME_BLOCKS


def yard_slots(
    home: Home, scan: dict[Pos, str | None], taken: Iterable[Pos] = (), bad_slots: Iterable[Pos] = ()
) -> list[Pos]:
    """The free cells for a station, best first: in the one-block ring outside the footprint, standing on solid
    ground with air above, never the door's outside cell or a taken cell. Per column only the lowest such cell,
    ordered by distance (x, z) to the door's outside cell, then y, z, x. Bad slots (v3.0i) are skipped."""
    (ox, oy, oz), (width, _, depth) = home.origin, home.size
    door_outside = _door_outside(home)
    taken = set(taken) | set(bad_slots)

    def is_slot(cell: Pos) -> bool:
        x, y, z = cell
        return (
            scan.get(cell) in blueprint.AIR_BLOCKS | blueprint.REPLACEABLE
            and _stands_on_ground(scan.get((x, y - 1, z)))
            and scan.get((x, y + 1, z)) in blueprint.AIR_BLOCKS
            and cell not in taken
        )

    slots = []
    for x in range(ox - 1, ox + width + 1):
        for z in range(oz - 1, oz + depth + 1):
            in_footprint = ox <= x < ox + width and oz <= z < oz + depth
            if in_footprint or (x, z) == door_outside:
                continue
            lowest = next((cell for cell in ((x, y, z) for y in (oy - 1, oy, oy + 1)) if is_slot(cell)), None)
            if lowest is not None:
                slots.append(lowest)

    anchor = door_outside or (ox, oz)
    return sorted(slots, key=lambda cell: (math.dist((cell[0], cell[2]), anchor), cell[1], cell[2], cell[0]))


def _door_outside(home: Home) -> tuple[int, int] | None:
    """(x, z) one step from the door's lower cell, away from the footprint (z first at a corner)."""
    if home.door is None:
        return None
    (ox, _, oz), (width, _, depth) = home.origin, home.size
    x, _, z = home.door
    if z == oz:
        return x, z - 1
    if z == oz + depth - 1:
        return x, z + 1
    if x == ox:
        return x - 1, z
    if x == ox + width - 1:
        return x + 1, z
    return None


def _stands_on_ground(found: str | None) -> bool:
    """Whether a scanned block can carry a station: solid, with a collision box (a carpet doesn't count)."""
    return _is_solid(found) and not found.endswith("_carpet")
