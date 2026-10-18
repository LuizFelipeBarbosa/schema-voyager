from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import normalize
from .adapter import DIRECTIONS, LookResult
from .blueprint import AIR_BLOCKS, REPLACEABLE, Box
from .inventory import slots_used, stack_size
from .layouts import ARENA_RADIUS, GROUND_Y, Layout
from .models import Action, Observation, Outcome, Site, Status, Task

# Vanilla 1.21 rules for the items in the benchmark. Only the simulator reads
# these; the planner and agents must learn them through actions.
RECIPES: dict[str, tuple[dict[str, int], int, bool]] = {
    # item: (ingredients, output count, needs crafting table)
    "oak_planks": ({"oak_log": 1}, 4, False),
    "stick": ({"oak_planks": 2}, 4, False),
    "crafting_table": ({"oak_planks": 4}, 1, False),
    "wooden_pickaxe": ({"oak_planks": 3, "stick": 2}, 1, True),
    "stone_pickaxe": ({"cobblestone": 3, "stick": 2}, 1, True),
    "torch": ({"coal": 1, "stick": 1}, 4, False),
    "furnace": ({"cobblestone": 8}, 1, True),
}

PICKAXE_TIER = {
    "wooden_pickaxe": 1,
    "golden_pickaxe": 1,
    "stone_pickaxe": 2,
    "iron_pickaxe": 3,
    "diamond_pickaxe": 4,
    "netherite_pickaxe": 4,
}

INVENTORY_SLOTS = 36
VIEW_RADIUS = 16
EXPLORE_LEG = 10
_NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
_SPIRAL = ((1, 0), (0, 1), (-1, 0), (0, -1))


def loot(block: str, tool: str | None, r2: bool) -> dict[str, int]:
    tier = PICKAXE_TIER.get(tool or "", 0)
    if block == "oak_log":
        return {"oak_log": 1}
    if block == "stone":
        return {"cobblestone": 1} if tier >= 1 else {}
    if block == "coal_ore":
        return {"coal": 1} if tier >= (2 if r2 else 1) else {}
    return {}


class SimulatedWorld:
    """Deterministic stand-in for the Mineflayer backend, built from the same
    layout files. Pickup always succeeds and walking is straight-line."""

    name = "sim"

    def __init__(self) -> None:
        self.blocks: dict[tuple[int, int, int], str] = {}
        self.position = (0.5, float(GROUND_Y), 0.5)
        self.inventory: Counter[str] = Counter()
        self.r2 = False
        self.blocks_walked = 0.0
        self.spiral_leg = 0

    def reset(self, task: Task, layout: Layout) -> Observation:
        self.blocks = {(int(b["x"]), int(b["y"]), int(b["z"])): str(b["block"]) for b in layout.blocks}
        self.position = (0.5, float(GROUND_Y), 0.5)
        self.inventory = Counter(task.start_inventory)
        self.r2 = "r2" in task.world_rules
        self.blocks_walked = 0.0
        self.spiral_leg = 0
        return self.observe()

    def observe(self) -> Observation:
        nearby = Counter(block for pos, block in self.blocks.items() if self._distance(pos) <= VIEW_RADIUS)
        return Observation(
            position=self.position,
            inventory=self._inventory(),
            nearby=dict(nearby),
            blocks_walked=round(self.blocks_walked, 1),
        )

    def say(self, message: str) -> None:
        pass

    def act(self, action: Action) -> Outcome:
        if action.kind == "mine":
            return self._mine(action)
        if action.kind == "craft":
            return self._craft(action)
        if action.kind == "explore":
            return self._explore()
        return self._fail("unknown_action")

    def _mine(self, action: Action) -> Outcome:
        target = self._nearest_exposed(action.target or "")
        if target is None:
            return self._fail("not_found")
        if action.tool is not None and self.inventory[action.tool] < 1:
            return self._fail("missing_tool")

        self._walk_to(target)
        del self.blocks[target]
        drops = loot(action.target or "", action.tool, self.r2)
        self.inventory.update(drops)
        return Outcome(
            ok=True,
            block_removed=True,
            drops=dict(drops),
            picked_up=dict(drops),
            inventory_delta=dict(drops),
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
        )

    def _craft(self, action: Action) -> Outcome:
        recipe = RECIPES.get(action.target or "")
        if recipe is None:
            return self._fail("craft_failed")
        ingredients, output, needs_table = recipe
        missing = {
            item: count * action.times - self.inventory[item]
            for item, count in ingredients.items()
            if self.inventory[item] < count * action.times
        }
        if missing:
            return self._fail("missing_ingredients", missing=missing)
        if needs_table and self.inventory["crafting_table"] < 1:
            return self._fail("missing_crafting_table")

        delta: Counter[str] = Counter()
        for item, count in ingredients.items():
            delta[item] -= count * action.times
        delta[action.target or ""] += output * action.times
        self.inventory.update(delta)
        return Outcome(
            ok=True,
            inventory_delta={item: count for item, count in delta.items() if count},
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail={"used_table": needs_table},
        )

    def _explore(self) -> Outcome:
        dx, dz = _SPIRAL[self.spiral_leg % 4]
        leg_length = EXPLORE_LEG * (1 + self.spiral_leg // 2)
        self.spiral_leg += 1
        x, y, z = self.position
        # The real arena is walled, so a leg stops at the barrier.
        nx = max(-ARENA_RADIUS + 0.5, min(ARENA_RADIUS - 0.5, x + dx * leg_length))
        nz = max(-ARENA_RADIUS + 0.5, min(ARENA_RADIUS - 0.5, z + dz * leg_length))
        self.position = (nx, y, nz)
        self.blocks_walked += abs(nx - x) + abs(nz - z)
        return Outcome(ok=True, inventory=self._inventory(), blocks_walked=round(self.blocks_walked, 1))

    def _nearest_exposed(self, block: str) -> tuple[int, int, int] | None:
        candidates = [
            pos
            for pos, kind in self.blocks.items()
            if kind == block and self._distance(pos) <= VIEW_RADIUS and self._exposed(pos)
        ]
        return min(candidates, key=self._distance, default=None)

    def _exposed(self, pos: tuple[int, int, int]) -> bool:
        for dx, dy, dz in _NEIGHBOURS:
            neighbour = (pos[0] + dx, pos[1] + dy, pos[2] + dz)
            if neighbour[1] >= GROUND_Y and neighbour not in self.blocks:
                return True
        return False

    def _walk_to(self, target: tuple[int, int, int]) -> None:
        """Stand next to the target: walk the straight-line distance minus reach."""
        distance = max(0.0, self._distance(target) - 2.0)
        self.blocks_walked += distance
        x, _, z = self.position
        tx, tz = target[0] + 0.5, target[2] + 0.5
        full = math.hypot(tx - x, tz - z) or 1.0
        fraction = distance / full
        self.position = (x + (tx - x) * fraction, float(GROUND_Y), z + (tz - z) * fraction)

    def _distance(self, pos: tuple[int, int, int]) -> float:
        x, _, z = self.position
        return math.hypot(pos[0] + 0.5 - x, pos[2] + 0.5 - z)

    def _inventory(self) -> dict[str, int]:
        return {item: count for item, count in sorted(self.inventory.items()) if count > 0}

    def _fail(self, error: str, **detail) -> Outcome:
        return Outcome(
            ok=False,
            error=error,
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail=detail,
        )


# --- NaturalWorld (v2) -------------------------------------------------------
# A lazily generated, seeded stand-in for a naturally generated world. The
# terrain is a pair of seeded height functions plus sparse per-chunk feature
# lists (trees, ores, pools...) and a dict of changes; nothing dense is stored.

Pos = tuple[int, int, int]

# Vanilla 1.21.1 rules of docs/v2-interfaces.md, in normalized names.
# item: (ingredients, output count, needs crafting table)
WHITELIST_RECIPES: dict[str, tuple[dict[str, int], int, bool]] = {
    "#planks": ({"#log": 1}, 4, False),
    "#door": ({"#planks": normalize.DOOR_PLANKS}, 3, True),
    "stick": ({"#planks": 2}, 4, False),
    "crafting_table": ({"#planks": 4}, 1, False),
    "wooden_pickaxe": ({"#planks": 3, "stick": 2}, 1, True),
    "wooden_axe": ({"#planks": 3, "stick": 2}, 1, True),
    "wooden_shovel": ({"#planks": 1, "stick": 2}, 1, True),
    "wooden_sword": ({"#planks": 2, "stick": 1}, 1, True),
    "stone_pickaxe": ({"#cobblestone": 3, "stick": 2}, 1, True),
    "stone_axe": ({"#cobblestone": 3, "stick": 2}, 1, True),
    "stone_shovel": ({"#cobblestone": 1, "stick": 2}, 1, True),
    "stone_sword": ({"#cobblestone": 2, "stick": 1}, 1, True),
    "furnace": ({"#cobblestone": 8}, 1, True),
    "torch": ({"coal": 1, "stick": 1}, 4, False),
    "chest": ({"#planks": 8}, 1, True),
    "ladder": ({"stick": 7}, 3, True),
    "iron_pickaxe": ({"iron_ingot": 3, "stick": 2}, 1, True),
    "iron_axe": ({"iron_ingot": 3, "stick": 2}, 1, True),
    "iron_shovel": ({"iron_ingot": 1, "stick": 2}, 1, True),
    "iron_sword": ({"iron_ingot": 2, "stick": 1}, 1, True),
    "bucket": ({"iron_ingot": 3}, 1, True),
    "shears": ({"iron_ingot": 2}, 1, False),
    "iron_helmet": ({"iron_ingot": 5}, 1, True),
    "iron_chestplate": ({"iron_ingot": 8}, 1, True),
    "iron_leggings": ({"iron_ingot": 7}, 1, True),
    "iron_boots": ({"iron_ingot": 4}, 1, True),
    "shield": ({"#planks": 6, "iron_ingot": 1}, 1, True),
}
SMELTS = {"raw_iron": "iron_ingot"}
# Items smelted per fuel item (burn time / 200 ticks), by family; a nether `*_stem` burns like a log.
FUEL_ITEMS_PER_FUEL = {"coal": 8, "charcoal": 8, "#planks": 1.5, "#log": 1.5, "stick": 0.5}

TERRAIN_BOTTOM = -40  # bedrock; deepslate is -39..-1, stone 0 and up
MAX_DISTANCE = 32
SKY_SCAN_HEIGHT = 24  # cells above the ground that can still hold a tree
REACH = 4.5
WORLD_BORDER = 512
SPAWN_CLEAR = 8  # no pools this close to the spawn
TRAVEL_DAMAGE_CHANCE = 0.03
STEP_DAMAGE_CHANCE = 0.005

# Threats, food and entities (off peaceful, except the animals).
THREAT_CHANCE = {"peaceful": 0.0, "easy": 0.06, "normal": 0.10, "hard": 0.15}
FIGHT_DAMAGE = {"easy": (1, 3), "normal": (2, 5), "hard": (3, 7)}  # total damage of a fight
THREATENED_ACTIONS = ("mine", "craft", "smelt", "explore", "place", "clear", "goto")
THREAT_MOBS = ("zombie", "skeleton", "spider", "creeper")
ANIMALS = ("cow", "sheep", "pig", "chicken")
ACTIONS_PER_FOOD = 10
EAT_BELOW = 14  # the bot eats at this food level or lower
FOOD_POINTS = {
    "apple": 4, "bread": 5, "carrot": 3, "baked_potato": 5, "cooked_beef": 8, "cooked_porkchop": 8,
    "cooked_mutton": 6, "cooked_chicken": 6, "cooked_cod": 5, "cooked_salmon": 6, "beef": 3,
    "porkchop": 3, "mutton": 2, "potato": 1, "sweet_berries": 2, "melon_slice": 2, "cookie": 2,
}
PASSABLE = frozenset({"air", "short_grass", "poppy", "dandelion"})
LIQUIDS = frozenset({"water", "lava"})
TERRAIN_BLOCKS = frozenset({"grass_block", "dirt", "stone", "deepslate"})
UNLISTED_NEARBY = frozenset({"air", "cave_air", "grass_block", "dirt", "bedrock", "barrier", "short_grass", "tall_grass"})
GOTO_RANGE = 3
SURVEY_WINDOW = 12  # a survey looks this far above and below the bot's feet
SURVEY_MAX_STEP = 3
SURVEY_MAX_SITES = 10
_TREE_SUFFIXES = ("_log", "_wood", "_leaves")
_DIRECTION_VECTORS = {
    "N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
    "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1),
}


@dataclass
class _Chunk:
    blocks: dict[Pos, str]
    by_id: dict[str, list[Pos]]


@dataclass
class _Target:
    pos: Pos
    block: str
    exposed: bool
    path: list[Pos]  # cells between the bot and a buried target, dug on the way


def _hash01(seed: int, salt: int, x: int, z: int) -> float:
    n = (x * 374761393 + z * 668265263 + seed * 2246822519 + salt * 3266489917) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n = ((n ^ (n >> 16)) * 2246822519) & 0xFFFFFFFF
    return (n ^ (n >> 15)) / 2**32


def _smooth_noise(seed: int, salt: int, x: int, z: int, scale: int) -> float:
    """Value noise in [0, 1): seeded lattice values, smoothly interpolated."""
    gx, gz = math.floor(x / scale), math.floor(z / scale)
    fx, fz = x / scale - gx, z / scale - gz
    fx, fz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)
    a, b = _hash01(seed, salt, gx, gz), _hash01(seed, salt, gx + 1, gz)
    c, d = _hash01(seed, salt, gx, gz + 1), _hash01(seed, salt, gx + 1, gz + 1)
    return (a * (1 - fx) + b * fx) * (1 - fz) + (c * (1 - fx) + d * fx) * fz


class NaturalWorld:
    """Deterministic, cheap stand-in for a natural Minecraft world (LifeBackend).

    The world may know vanilla (drops, recipes); species come from the `species`
    argument or normalize.SPECIES. Inventories and outcomes use raw ids."""

    name = "natural"

    def __init__(
        self,
        seed: int,
        *,
        world_rules: tuple[str, ...] = (),
        species: tuple[str, ...] | None = None,
        difficulty: str = "peaceful",
    ):
        if difficulty not in THREAT_CHANCE:
            raise ValueError(f"unknown difficulty {difficulty!r}")
        self.seed = seed
        self.difficulty = difficulty
        pool = list(species if species is not None else normalize.SPECIES)
        self._tree_species = random.Random(f"{seed}:species").sample(pool, k=min(len(pool), 1 + seed % 3))
        self._rng = random.Random(f"{seed}:rolls")  # random drops and damage
        self._threat_rng = random.Random(f"{seed}:threats")  # separate, so peaceful results never move
        self._columns: dict[tuple[int, int], tuple[int, bool]] = {}
        self._chunks: dict[tuple[int, int], _Chunk] = {}
        self._changes: dict[Pos, str] = {}
        self._keep_out: tuple[Pos, Pos] | None = None
        self._protected: list[Box] = []
        self._skipped: set[Pos] = set()
        self._events: list[dict[str, Any]] = []
        self._seq = 0
        self._spiral_leg = 0
        self.inventory: Counter[str] = Counter()
        self.blocks_walked = 0.0
        self.health = 20.0
        self.food = 20
        self._actions = 0
        self.set_world_rules(world_rules)
        self._cell: Pos = (0, self._column(0, 0)[0] + 1, 0)  # the bot's feet cell
        self._spawn = self._cell

    # --- LifeBackend ---------------------------------------------------------

    def reset(self, task: Task, layout: Layout) -> Observation:
        """The terrain stays: only the inventory and the rules follow the task."""
        self.inventory = Counter(task.start_inventory)
        self.set_world_rules(task.world_rules)
        return self.begin_episode()

    def begin_episode(self) -> Observation:
        self._skipped.clear()
        self._spiral_leg = 0
        self.blocks_walked = 0.0
        return self.observe()

    def set_world_rules(self, rules: Iterable[str]) -> None:
        self.r2 = "r2" in rules
        self.stick2 = "stick2" in rules
        self.planks2 = "planks2" in rules

    def say(self, message: str) -> None:
        pass

    def observe(self) -> Observation:
        x, y, z = self._cell
        nearby: Counter[str] = Counter()
        for chunk in self._chunks_within(VIEW_RADIUS):
            for block, positions in chunk.by_id.items():
                if block not in UNLISTED_NEARBY:
                    nearby[block] += sum(
                        1 for pos in positions if self._changes.get(pos, block) == block and self._distance_to(pos) <= VIEW_RADIUS
                    )
        for column_x, column_z in self._disc(VIEW_RADIUS):
            for block, (low, high) in self._spans(*self._column(column_x, column_z)).items():
                if block in ("stone", "deepslate"):
                    nearby[block] += max(0, min(high, y + VIEW_RADIUS) - max(low, y - VIEW_RADIUS) + 1)
        return Observation(
            position=self._position(),
            inventory=self._inventory(),
            free_slots=_free_slots(self.inventory),
            nearby={block: count for block, count in sorted(nearby.items()) if count > 0},
            blocks_walked=round(self.blocks_walked, 1),
            entities=self._entities(),
        )

    def toss(self, items: dict[str, int]) -> dict[str, int]:
        """Drop up to each count of what is held; the items are gone. Returns what left."""
        tossed = {item: min(count, self.inventory[item]) for item, count in items.items()}
        tossed = {item: count for item, count in tossed.items() if count > 0}
        self.inventory.subtract(tossed)
        return tossed

    def status(self, since: int = 0) -> Status:
        return Status(
            health=self.health,
            food=self.food,
            position=self._position(),
            events=[event for event in self._events if event["seq"] > since],
            last_seq=self._seq,
        )

    def look(self, blocks: list[str], radius: int = 64) -> LookResult:
        x, _, z = self._cell
        result: LookResult = {direction: {} for direction in DIRECTIONS}
        for _, pos, block in sorted(self._candidates(set(blocks), min(radius, 96))):
            if self._is_protected(pos):
                continue
            angle = math.degrees(math.atan2(pos[0] - x, z - pos[2]))  # N is -z
            sector = result[DIRECTIONS[round(angle / 45) % 8]]
            if block not in sector:
                sector[block] = {
                    "x": pos[0], "y": pos[1], "z": pos[2],
                    "distance": round(self._distance_to(pos), 1),
                    "exposed": self._exposed(pos),
                }
        return result

    def probe(self, grid: list[list[str | None]]) -> dict[str, Any]:
        """The recipe output for a grid (shapeless, matched by tag), consuming nothing."""
        cells = [item for row in grid for item in row if item]
        held = Counter(cells)
        if any(self.inventory[item] < count for item, count in held.items()):
            return {"ok": False, "error": "missing_ingredients", "output": None, "inventory_delta": {}, "events": []}
        if len(grid) > 2 and self.inventory["crafting_table"] < 1:
            return {"ok": False, "error": "missing_crafting_table", "output": None, "inventory_delta": {}, "events": []}
        wanted = Counter(normalize.family(item) for item in cells)
        output = None
        for key, (ingredients, count, needs_table) in WHITELIST_RECIPES.items():
            if Counter(ingredients) == wanted and (len(grid) > 2 or not needs_table):
                item = {"#planks": f"{normalize.species(cells[0])}_planks", "#door": f"{normalize.species(cells[0])}_door"}.get(key, key)
                output = {"item": item, "count": self._output_count(key, count)}
                break
        return {"ok": True, "error": None, "output": output, "inventory_delta": {}, "events": []}

    def act(self, action: Action) -> Outcome:
        if action.kind in ("place", "clear", "goto") and action.pos is None:
            raise ValueError(f"{action.kind} needs a pos")
        self.health = min(20.0, self.health + 1)
        if self.difficulty != "peaceful":
            self._actions += 1
            if self._actions % ACTIONS_PER_FOOD == 0:
                self.food = max(0, self.food - 1)
            self._eat_if_hungry()
            if action.kind in THREATENED_ACTIONS and self._threat_rng.random() < THREAT_CHANCE[self.difficulty]:
                return self._threatened(action)
        if action.kind == "mine":
            return self._mine(action)
        if action.kind == "craft":
            return self._craft(action)
        if action.kind == "smelt":
            return self._smelt(action)
        if action.kind == "explore":
            return self._explore(action)
        if action.kind == "place":
            return self._place(action)
        if action.kind == "clear":
            return self._clear(action)
        if action.kind == "goto":
            return self._goto(action)
        return self._fail("unknown_action")

    def scan(self, origin: Pos, size: Pos) -> dict[Pos, str | None]:
        """Every block of the box at origin; size is (width x, height y, depth z)."""
        return {
            (origin[0] + x, origin[1] + y, origin[2] + z): self.block_at((origin[0] + x, origin[1] + y, origin[2] + z))
            for x in range(size[0])
            for y in range(size[1])
            for z in range(size[2])
        }

    def set_keep_out(self, box: tuple[Pos, Pos] | None) -> None:
        """Remembered only: the simulator has no scaffold to keep out of the box."""
        self._keep_out = box

    def set_protected(self, boxes: list[Box]) -> None:
        """What the bot built (v3.4b): never mined, dug through, looked at or surveyed over."""
        self._protected = list(boxes)

    def survey(self, size: tuple[int, int], radius: int = 24) -> list[Site]:
        return self._rank_sites(size, radius, protected=self._protected)

    def _rank_sites(self, size: tuple[int, int], radius: int, protected: list[Box]) -> list[Site]:
        """The ranking of docs/v3-interfaces.md v3.4b `/survey`, over this world's columns."""
        width, depth = size
        feet_x, feet_y, feet_z = self._cell
        columns: dict[tuple[int, int], int | str] = {}  # a column's ground y, or the reason it is ruled out

        def column(x: int, z: int) -> int | str:
            if (x, z) not in columns:
                columns[x, z] = self._survey_column(x, z, feet_y)
            return columns[x, z]

        sites = []
        for x0 in range(feet_x - radius, feet_x + radius - width + 2):
            for z0 in range(feet_z - radius, feet_z + radius - depth + 2):
                footprint = [column(x, z) for x in range(x0, x0 + width) for z in range(z0, z0 + depth)]
                if any(isinstance(ground, str) for ground in footprint):
                    continue
                ring = [(x, z) for x in range(x0 - 1, x0 + width + 1) for z in range(z0 - 1, z0 + depth + 1)]
                if any(column(x, z) == "liquid" for x, z in ring):
                    continue
                if any(
                    x0 - 1 < ox + sx and ox < x0 + width + 1 and z0 - 1 < oz + sz and oz < z0 + depth + 1
                    for (ox, _, oz), (sx, _, sz) in protected
                ):
                    continue
                floor = sorted(footprint)[(len(footprint) - 1) // 2] + 1
                if any(ground > floor - 1 + SURVEY_MAX_STEP or ground < floor - 2 for ground in footprint):
                    continue
                centre_x, centre_z = x0 + width / 2, z0 + depth / 2
                sites.append(Site(
                    origin=(x0, floor, z0),
                    cut=sum(max(0, ground - floor + 1) for ground in footprint),
                    fill=sum(max(0, floor - 1 - ground) for ground in footprint),
                    distance=round(math.hypot(centre_x - (feet_x + 0.5), centre_z - (feet_z + 0.5)), 1),
                ))
        sites.sort(key=lambda site: (site.cost, site.distance, site.origin[0], site.origin[2]))
        return sites[:SURVEY_MAX_SITES]

    def _survey_column(self, x: int, z: int, feet_y: int) -> int | str:
        """The y of the column's ground, or "unknown", "liquid" or "tree" when a site cannot use it."""
        if self.block_at((x, feet_y + SURVEY_WINDOW, z)) != "air":
            return "unknown"  # underground: rock or cave air up there is not sky
        for y in range(feet_y + SURVEY_WINDOW - 1, feet_y - SURVEY_WINDOW - 1, -1):
            block = self.block_at((x, y, z))
            if block in LIQUIDS:
                return "liquid"
            if not _is_free(block) and block not in PASSABLE:
                return "tree" if block.endswith(_TREE_SUFFIXES) else y
        return "unknown"

    # --- actions -------------------------------------------------------------

    def _mine(self, action: Action) -> Outcome:
        events: list[dict[str, Any]] = []
        target = self._choose_target(set(normalize.members(action.target or "")), events)
        if isinstance(target, str):
            return self._fail(target, events=events)
        if action.tool is not None and self.inventory[action.tool] < 1:
            return self._fail("missing_tool", events=events)

        travel = self._travel_to(target)
        if travel["distance"] or travel["dug"]:
            self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        before_step = self._inventory()
        drops = self._drops(target.block, action.tool)
        self._changes[target.pos] = "air"
        kept = self._keep(drops)
        self._roll_damage("step", STEP_DAMAGE_CHANCE, events)
        return Outcome(
            ok=True,
            block_removed=True,
            drops=dict(drops),
            picked_up=dict(kept),
            inventory_delta=dict(kept),
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail={
                "block": target.block,
                "target": {"x": target.pos[0], "y": target.pos[1], "z": target.pos[2]},
                "tool_used": action.tool,
                "inventory_before_step": before_step,
                **({"inventory_full": True} if kept != drops else {}),
            },
            travel=travel,
            events=events,
        )

    def _place(self, action: Action) -> Outcome:
        pos = action.pos
        block = action.target or ""
        is_door = block.endswith("_door")
        cells = [pos, (pos[0], pos[1] + 1, pos[2])] if is_door else [pos]
        found = self.block_at(pos)
        detail = {"block": block, "found": found, "target": _xyz(pos), "inventory_before_step": self._inventory()}

        if found == block or (block == "torch" and found == "wall_torch"):
            return self._built(placed=False, delta={}, travel=_empty_travel(), events=[], **detail)
        if self.inventory[block] < 1:
            return self._fail("missing_item", **detail)
        blocking = next((other for other in map(self.block_at, cells) if not _is_free(other)), None)
        if blocking is not None:
            return self._fail("occupied", **{**detail, "found": blocking})

        x, y, z = pos
        below = (x, y - 1, z)
        sides = [(x + dx, y + dy, z + dz) for dx, dy, dz in _NEIGHBOURS if dy == 0]
        if block == "torch":
            support = next((cell for cell in [below, *sides] if self._supports(cell)), None)
        elif is_door:
            support = below if self._supports(below) else None
        else:
            support = next((cell for cell in [below, *sides, (x, y + 1, z)] if self._supports(cell)), None)
        if support is None:
            return self._fail("no_support", **detail)
        if not self._exposed(pos):
            return self._fail("unreachable", **detail)

        events: list[dict[str, Any]] = []
        travel = self._travel_to_cell(pos)
        if travel["distance"] or travel["dug"]:
            self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        before_step = self._inventory()
        for cell in cells:
            self._changes[cell] = block
        if block == "torch" and support != below:
            self._changes[pos] = "wall_torch"
        self.inventory[block] -= 1
        self._roll_damage("step", STEP_DAMAGE_CHANCE, events)
        return self._built(
            placed=True, delta={block: -1}, travel=travel, events=events,
            **{**detail, "found": self.block_at(pos), "inventory_before_step": before_step},
        )

    def _clear(self, action: Action) -> Outcome:
        pos = action.pos
        found = self.block_at(pos)
        detail = {"found": found, "target": _xyz(pos), "inventory_before_step": self._inventory()}
        if found in AIR_BLOCKS:
            return self._built(cleared=False, delta={}, travel=_empty_travel(), events=[], removed=None, **{**detail, "found": "air"})
        if pos[1] <= TERRAIN_BOTTOM or found in LIQUIDS:
            return self._fail("unbreakable", **detail)
        if not self._exposed(pos):
            return self._fail("unreachable", **detail)

        events: list[dict[str, Any]] = []
        travel = self._travel_to_cell(pos)
        if travel["distance"] or travel["dug"]:
            self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        before_step = self._inventory()
        self._changes[pos] = "air"
        if found.endswith("_door"):
            for other in ((pos[0], pos[1] + 1, pos[2]), (pos[0], pos[1] - 1, pos[2])):
                if self.block_at(other) == found:
                    self._changes[other] = "air"
        self._roll_damage("step", STEP_DAMAGE_CHANCE, events)
        return self._built(
            cleared=True, delta={}, travel=travel, events=events, removed=found,
            **{**detail, "found": self.block_at(pos), "inventory_before_step": before_step},
        )

    def _goto(self, action: Action) -> Outcome:
        pos = action.pos
        detail = {"target": _xyz(pos), "inventory_before_step": self._inventory()}
        stands = [
            cell
            for dx, dy, dz in itertools.product(range(-GOTO_RANGE, GOTO_RANGE + 1), repeat=3)
            if math.sqrt(dx * dx + dy * dy + dz * dz) <= GOTO_RANGE and self._standable(cell := (pos[0] + dx, pos[1] + dy, pos[2] + dz))
        ]
        if not stands:
            return self._fail("unreachable", arrived=False, **detail)

        stand = min(stands, key=lambda cell: (self._distance_to(cell), cell))
        distance = math.hypot(stand[0] - self._cell[0], stand[2] - self._cell[2])
        self.blocks_walked += distance
        self._cell = stand
        travel = {**_empty_travel(), "distance": round(distance, 1)}
        events: list[dict[str, Any]] = []
        if distance:
            self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        return self._built(delta={}, travel=travel, events=events, arrived=True, **detail)

    def _standable(self, cell: Pos) -> bool:
        """Whether the bot can stand with its feet in the cell: room for its body, a floor under it."""
        x, y, z = cell
        floor = self.block_at((x, y - 1, z))
        return (
            self.block_at(cell) in PASSABLE
            and self.block_at((x, y + 1, z)) in PASSABLE
            and floor not in PASSABLE
            and floor not in LIQUIDS
        )

    def _built(self, *, delta: dict[str, int], travel: dict[str, Any], events: list[dict[str, Any]], **detail: Any) -> Outcome:
        """The ok Outcome of a place or clear."""
        return Outcome(
            ok=True,
            inventory_delta=delta,
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail=detail,
            travel=travel,
            events=events,
        )

    def _supports(self, cell: Pos) -> bool:
        """Whether a placement can lean on the cell: solid, not a liquid, a door or a torch."""
        block = self.block_at(cell)
        return not _is_free(block) and block not in LIQUIDS and not block.endswith("_door") and "torch" not in block

    def _travel_to_cell(self, pos: Pos) -> dict[str, Any]:
        """Travel as for a mine, to an exposed cell: the walks of place and clear never dig (the bot answers
        unreachable for a cell with no open neighbour)."""
        return self._travel_to(_Target(pos, self.block_at(pos), True, []))

    def _craft(self, action: Action) -> Outcome:
        name = action.target or ""
        is_group = name.startswith("#")
        key = name if is_group else normalize.family(name)
        recipe = WHITELIST_RECIPES.get(key)
        if recipe is None:
            return self._fail("craft_failed")
        ingredients, output, needs_table = recipe
        try:
            item = normalize.resolve(name, self._inventory()) if is_group else name
        except ValueError:
            return self._fail("missing_ingredients", missing={i: n * action.times for i, n in ingredients.items()})

        # A raw planks target is made from logs of its own species, a door from planks of its own; a planks group takes any.
        ingredient_species = normalize.species(item) if key == "#door" or (key == "#planks" and not is_group) else None
        payment, missing = self._pay(ingredients, action.times, ingredient_species)
        if missing:
            return self._fail("missing_ingredients", missing=missing)
        if needs_table and self.inventory["crafting_table"] < 1:
            return self._fail("missing_crafting_table")

        gains: Counter[str] = Counter()
        if key == "#planks":  # planks take the species of the log consumed
            for log, count in payment.items():
                gains[f"{normalize.species(log)}_planks"] += self._output_count(key, output) * count
            item = max(gains, key=lambda planks: gains[planks])
        else:
            gains[item] = self._output_count(key, output) * action.times
        return self._finish_recipe(payment, gains, item, {"used_table": needs_table, **({"table_retrieved": True} if needs_table else {})})

    def _smelt(self, action: Action) -> Outcome:
        try:
            ingredient = _held_member(action.ingredient or "", self._inventory())
            fuel = _held_member(action.fuel or "", self._inventory())
        except ValueError:
            return self._fail("missing_ingredients")
        item = SMELTS.get(ingredient)
        if item is None or item != action.target:
            return self._fail("craft_failed")
        fuel_family = "#log" if fuel.endswith("_stem") else normalize.family(fuel)
        if fuel_family not in FUEL_ITEMS_PER_FUEL:
            return self._fail("craft_failed", detail="not_fuel")

        fuel_used = math.ceil(action.times / FUEL_ITEMS_PER_FUEL[fuel_family])
        payment = Counter({ingredient: action.times})
        payment[fuel] += fuel_used
        missing = {name: need - self.inventory[name] for name, need in payment.items() if self.inventory[name] < need}
        if missing:
            return self._fail("missing_ingredients", missing=missing)
        if self.inventory["furnace"] < 1:
            return self._fail("missing_furnace")
        return self._finish_recipe(
            payment,
            Counter({item: action.times}),
            item,
            {"used_furnace": True, "furnace_retrieved": True, "fuel": fuel, "fuel_used": fuel_used},
        )

    def _explore(self, action: Action) -> Outcome:
        if action.direction == "up":
            return self._explore_up()
        if action.direction:
            ux, uz = _DIRECTION_VECTORS[action.direction]
            norm = math.hypot(ux, uz)
            unit, steps = (ux / norm, uz / norm), MAX_DISTANCE
        else:  # the v1 spiral
            unit, steps = _SPIRAL[self._spiral_leg % 4], EXPLORE_LEG * (1 + self._spiral_leg // 2)
            self._spiral_leg += 1

        x, _, z = self._cell
        reached, walked = None, 0
        for step in range(1, steps + 1):
            column_x, column_z = math.floor(x + 0.5 + unit[0] * step), math.floor(z + 0.5 + unit[1] * step)
            ground = self._column(column_x, column_z)[0]
            if max(abs(column_x), abs(column_z)) > WORLD_BORDER or self.block_at((column_x, ground, column_z)) == "lava":
                break
            reached, walked = (column_x, ground + 1, column_z), step
        if reached is None:
            return self._fail("unreachable")

        events: list[dict[str, Any]] = []
        self._cell = reached
        self.blocks_walked += walked
        self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        position = self._position()
        return Outcome(
            ok=True,
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail={"position": {"x": position[0], "y": position[1], "z": position[2]}},
            travel={"distance": float(walked), "dug": {}, "placed": {}, "picked_up": {}},
            events=events,
        )

    def _explore_up(self) -> Outcome:
        """Climb to open sky: dig up the bot's column (never down), then stand on the
        highest block of it. Leaves don't close the sky; a liquid overhead stops the climb."""
        x, y, z = self._cell
        overhead = {
            level: block
            for level in range(y + 1, self._column(x, z)[0] + SKY_SCAN_HEIGHT)
            if (block := self.block_at((x, level, z))) not in PASSABLE and not block.endswith("_leaves")
        }
        stopped_at = min(
            (level for level, block in overhead.items() if block in LIQUIDS or self._is_protected((x, level, z))),
            default=None,
        )
        climb = [level for level in overhead if stopped_at is None or level < stopped_at]
        sky = stopped_at is None
        if not climb and not sky:
            return self._fail("unreachable")

        best_pickaxe = max((tool for tool in PICKAXE_TIER if self.inventory[tool] > 0), key=PICKAXE_TIER.get, default=None)
        dug: Counter[str] = Counter()
        picked_up: Counter[str] = Counter()
        for level in climb:
            block = overhead[level]
            dug[block] += 1
            picked_up.update(self._drops(block, best_pickaxe))
            self._changes[(x, level, z)] = "air"
        kept = self._keep(picked_up)
        gained = (climb[-1] + 1 if sky else climb[-1]) - y if climb else 0  # stand on the top, or in the last cell dug
        self._cell = (x, y + gained, z)
        self.blocks_walked += gained

        events: list[dict[str, Any]] = []
        self._roll_damage("travel", TRAVEL_DAMAGE_CHANCE, events)
        position = self._position()
        return Outcome(
            ok=True,
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail={
                "position": {"x": position[0], "y": position[1], "z": position[2]},
                "sky": sky,
                **({"inventory_full": True} if kept != picked_up else {}),
            },
            travel={"distance": float(gained), "dug": dict(dug), "placed": {}, "picked_up": dict(kept)},
            events=events,
        )

    # --- threats, food and death ---------------------------------------------

    def die(self) -> list[dict[str, Any]]:
        """The bot dies now: it respawns at the spawn with full health and food, keeping its inventory."""
        death = self._emit({"type": "death", "phase": "step", "position": _xyz(self._position())})
        self._cell = self._spawn
        self.health, self.food = 20.0, 20
        return [death, self._emit({"type": "respawn", "position": _xyz(self._position())})]

    def _threatened(self, action: Action) -> Outcome:
        """A mob cuts into the action: it does not run, and the answer is `interrupted`."""
        rng = self._threat_rng
        mob = rng.choice(THREAT_MOBS)
        mob_id = 1000 + self._seq
        distance = round(rng.uniform(3, 8), 1)
        events = [self._emit({"type": "threat", "mob": mob, "id": mob_id, "distance": distance, "reason": "near", "action": f"/{action.kind}"})]

        if mob == "creeper":
            events.append(self._emit({"type": "fled", "from": mob, "reason": "creeper", "distance": distance, "health": self.health}))
            outcome = "fled"
        else:
            events.append(self._emit({"type": "fight_start", "target": mob, "id": mob_id, "distance": distance, "weapon": None}))
            low, high = FIGHT_DAMAGE[self.difficulty]
            total = rng.randint(low, high)
            damage_taken = 0.0
            while total > 0 and self.health > 0:
                amount = float(min(total, rng.randint(1, 3)))
                total -= int(amount)
                damage_taken += amount
                self.health = max(0.0, self.health - amount)
                events.append(self._emit({"type": "damage", "amount": amount, "health": self.health, "phase": "step"}))
            outcome = "died" if self.health <= 0 else "fled" if self.health < 6 else "killed"
            events.append(self._emit({
                "type": "fight_end", "target": mob, "outcome": outcome,
                "damage_taken": damage_taken, "duration_s": round(rng.uniform(1, 4), 1),
            }))
            if outcome == "died":
                return self._fail("quarantined", events=[*events, *self.die()])
        return self._fail("interrupted", events=events, threat=mob, outcome=outcome)

    def _eat_if_hungry(self) -> None:
        if self.food > EAT_BELOW:
            return
        held = [item for item in FOOD_POINTS if self.inventory[item] > 0]
        if not held:
            return
        item = max(held, key=lambda item: (FOOD_POINTS[item], item))
        self.inventory[item] -= 1
        self.food = min(20, self.food + FOOD_POINTS[item])
        self._emit({"type": "ate", "item": item, "food": self.food})

    def _entities(self) -> dict[str, dict[str, Any]]:
        """Mobs in view: a pure function of the seed, the difficulty and the region the bot stands in."""
        region = (self._cell[0] >> 4, self._cell[2] >> 4)
        entities: dict[str, dict[str, Any]] = {}
        for index, animal in enumerate(ANIMALS):
            roll = _hash01(self.seed, 10 + index, *region)
            if roll < 0.4:
                entities[animal] = {"count": 1 + int(roll * 10) % 3, "nearest": round(4 + roll * 40, 1), "kind": "animal"}
        roll = _hash01(self.seed, 20, *region)
        if self.difficulty != "peaceful" and roll < 0.3:
            mob = THREAT_MOBS[int(roll * 100) % len(THREAT_MOBS)]
            entities[mob] = {"count": 1, "nearest": round(10 + roll * 20, 1), "kind": "hostile"}
        return entities

    # --- crafting helpers ----------------------------------------------------

    def _pay(self, ingredients: dict[str, int], times: int, log_species: str | None) -> tuple[Counter[str], dict[str, int]]:
        """Which inventory items pay the ingredients; tags take mixed species, most plentiful first."""
        payment: Counter[str] = Counter()
        missing: dict[str, int] = {}
        for ingredient, count in ingredients.items():
            need = count * times
            if ingredient.startswith("#"):
                held = sorted(
                    (item for item, n in self.inventory.items()
                     if n > 0 and normalize.family(item) == ingredient
                     and (log_species is None or normalize.species(item) == log_species)),
                    key=lambda item: (-self.inventory[item], item),
                )
            else:
                held = [ingredient]
            for item in held:
                taken = min(self.inventory[item], need)
                payment[item] += taken
                need -= taken
            if need:
                missing[ingredient] = need
        return payment, missing

    def _finish_recipe(self, payment: Counter[str], gains: Counter[str], item: str, detail: dict[str, Any]) -> Outcome:
        events: list[dict[str, Any]] = []
        before_step = self._inventory()
        delta: Counter[str] = Counter(gains)
        delta.subtract(payment)
        if _free_slots(self.inventory + delta, clamp=False) < 0:
            return self._fail("craft_failed", inventory_full=True, detail="inventory_full")
        self.inventory.update(delta)
        self._roll_damage("step", STEP_DAMAGE_CHANCE, events)
        return Outcome(
            ok=True,
            inventory_delta={name: count for name, count in sorted(delta.items()) if count},
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail={**detail, "item": item, "inventory_before_step": before_step},
            travel=_empty_travel(),
            events=events,
        )

    def _keep(self, gains: dict[str, int]) -> Counter[str]:
        """Add as much of each gain as fits (the rest is left on the ground); returns what was kept."""
        kept: Counter[str] = Counter()
        for item in sorted(gains):
            size = stack_size(item)
            room_in_last_stack = -self.inventory[item] % size
            fits = min(gains[item], room_in_last_stack + _free_slots(self.inventory) * size)
            if fits > 0:
                self.inventory[item] += fits
                kept[item] = fits
        return kept

    def _output_count(self, item: str, count: int) -> int:
        if (item == "stick" and self.stick2) or (item == "#planks" and self.planks2):
            return 2
        return count

    # --- mining helpers ------------------------------------------------------

    def _drops(self, block: str, tool: str | None) -> dict[str, int]:
        tier = PICKAXE_TIER.get(tool or "", 0)
        if block.endswith("_log") or block in ("poppy", "dandelion"):
            return {block: 1}
        if block in ("grass_block", "dirt"):
            return {"dirt": 1}
        if block == "stone":
            return {"cobblestone": 1} if tier >= 1 else {}
        if block == "deepslate":
            return {"cobbled_deepslate": 1} if tier >= 1 else {}
        if block in ("coal_ore", "deepslate_coal_ore"):
            return {"coal": 1} if tier >= (2 if self.r2 else 1) else {}
        if block in ("iron_ore", "deepslate_iron_ore"):
            return {"raw_iron": 1} if tier >= 2 else {}
        if block == "gravel":
            return {"flint": 1} if self._rng.random() < 0.10 else {"gravel": 1}
        if block == "short_grass":
            return {"wheat_seeds": 1} if self._rng.random() < 0.125 else {}
        if block.endswith("_leaves"):
            species = block.removesuffix("_leaves")
            drops: Counter[str] = Counter()
            if self._rng.random() < 0.05:
                drops[f"{species}_sapling"] += 1
            if self._rng.random() < 0.02:
                drops["stick"] += self._rng.randint(1, 2)
            if species in ("oak", "dark_oak") and self._rng.random() < 0.005:
                drops["apple"] += 1
            return dict(drops)
        return {}

    def _choose_target(self, blocks: set[str], events: list[dict[str, Any]]) -> _Target | str:
        """The nearest exposed candidate, else the nearest buried one; an error code if none is usable."""
        candidates = sorted(self._candidates(blocks, MAX_DISTANCE))
        hazard_seen = False
        buried: list[tuple[float, Pos, str]] = []
        for pass_exposed in (True, False):
            for candidate in candidates if pass_exposed else buried:
                _, pos, block = candidate
                if self._is_protected(pos):
                    continue
                if pass_exposed and not self._exposed(pos):
                    buried.append(candidate)
                    continue
                path = [] if pass_exposed else self._dig_path(pos)
                if any(self._is_protected(cell) for cell in path):
                    continue
                if pos in self._skipped or any(self._near_lava(cell) for cell in [pos, *path]):
                    hazard_seen = True
                    if pos not in self._skipped:
                        self._skipped.add(pos)
                        events.append(self._emit(
                            {"type": "hazard", "phase": "travel", "kind": "lava", "position": {"x": pos[0], "y": pos[1], "z": pos[2]}}
                        ))
                    continue
                return _Target(pos, block, pass_exposed, path)
        return "hazard" if hazard_seen else "not_found"

    def _travel_to(self, target: _Target) -> dict[str, Any]:
        """Walk (and dig, for a buried target) next to the target; returns the travel report."""
        dug: Counter[str] = Counter()
        picked_up: Counter[str] = Counter()
        if target.exposed:
            stand = self._cell if self._distance_to(target.pos) <= REACH else self._stand_next_to(target.pos)
        else:
            best_pickaxe = max((tool for tool in PICKAXE_TIER if self.inventory[tool] > 0), key=PICKAXE_TIER.get, default=None)
            for cell in target.path:
                block = self.block_at(cell)
                if block not in PASSABLE and block not in LIQUIDS:
                    dug[block] += 1
                    picked_up.update(self._drops(block, best_pickaxe))
                    self._changes[cell] = "air"
            stand = target.path[-1] if target.path else self._cell
        kept = self._keep(picked_up)

        distance = math.hypot(stand[0] - self._cell[0], stand[2] - self._cell[2])
        self.blocks_walked += distance
        self._cell = stand
        return {
            "distance": round(distance, 1), "dug": dict(dug), "placed": {}, "picked_up": dict(kept),
            **({"inventory_full": True} if kept != picked_up else {}),
        }

    def _dig_path(self, pos: Pos) -> list[Pos]:
        """The straight line of cells between the bot and pos, both excluded."""
        x, y, z = self._cell
        dx, dy, dz = pos[0] - x, pos[1] - y, pos[2] - z
        steps = max(abs(dx), abs(dy), abs(dz))
        return [(x + round(dx * i / steps), y + round(dy * i / steps), z + round(dz * i / steps)) for i in range(1, steps)]

    def _is_protected(self, pos: Pos) -> bool:
        return any(
            ox <= pos[0] < ox + w and oy <= pos[1] < oy + h and oz <= pos[2] < oz + d
            for (ox, oy, oz), (w, h, d) in self._protected
        )

    def _stand_next_to(self, pos: Pos) -> Pos:
        """The free cell beside pos nearest to the bot, dropped to the floor."""
        free = [
            (pos[0] + dx, pos[1] + dy, pos[2] + dz)
            for dx, dy, dz in _NEIGHBOURS
            if self.block_at((pos[0] + dx, pos[1] + dy, pos[2] + dz)) in PASSABLE
        ]
        x, y, z = min(free, key=self._distance_to)
        while y > TERRAIN_BOTTOM and self.block_at((x, y - 1, z)) in PASSABLE:
            y -= 1
        return (x, y, z)

    def _roll_damage(self, phase: str, chance: float, events: list[dict[str, Any]]) -> None:
        if self._rng.random() >= chance:
            return
        amount = float(self._rng.choice((1, 2, 3)))
        self.health = max(1.0, self.health - amount)
        events.append(self._emit({"type": "damage", "phase": phase, "amount": amount, "health": self.health}))

    def _emit(self, event: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "t": 1_760_000_000.0 + self._seq * 0.5, **event}
        self._events = [*self._events[-199:], event]
        return event

    def _near_lava(self, pos: Pos) -> bool:
        x, y, z = pos
        return self.block_at(pos) == "lava" or any(self.block_at((x + dx, y + dy, z + dz)) == "lava" for dx, dy, dz in _NEIGHBOURS)

    def _exposed(self, pos: Pos) -> bool:
        x, y, z = pos
        return any(self.block_at((x + dx, y + dy, z + dz)) in PASSABLE for dx, dy, dz in _NEIGHBOURS)

    # --- the world -----------------------------------------------------------

    def block_at(self, pos: Pos) -> str:
        if pos in self._changes:
            return self._changes[pos]
        feature = self._chunk(pos[0] >> 4, pos[2] >> 4).blocks.get(pos)
        if feature is not None:
            return feature
        height, rocky = self._column(pos[0], pos[2])
        y = pos[1]
        if y > height:
            return "air"
        if y <= TERRAIN_BOTTOM:
            return "bedrock"
        if y == height:
            return "stone" if rocky else "grass_block"
        if y >= height - 3 and not rocky:
            return "dirt"
        return "stone" if y >= 0 else "deepslate"

    def _column(self, x: int, z: int) -> tuple[int, bool]:
        """(surface height, whether the surface is bare stone) of a column."""
        column = self._columns.get((x, z))
        if column is None:
            height = 11 + 10 * (_smooth_noise(self.seed, 1, x, z, 28) - 0.5) + 5 * (_smooth_noise(self.seed, 2, x, z, 9) - 0.5)
            rocky = _smooth_noise(self.seed, 3, x, z, 22) > 0.68
            column = self._columns[(x, z)] = (max(6, min(18, round(height))), rocky)
        return column

    @staticmethod
    def _spans(height: int, rocky: bool) -> dict[str, tuple[int, int]]:
        """The y range of each terrain block in a column."""
        if rocky:
            return {"stone": (0, height), "deepslate": (TERRAIN_BOTTOM + 1, -1)}
        return {
            "grass_block": (height, height),
            "dirt": (height - 3, height - 1),
            "stone": (0, height - 4),
            "deepslate": (TERRAIN_BOTTOM + 1, -1),
        }

    def _candidates(self, blocks: set[str], radius: float) -> list[tuple[float, Pos, str]]:
        """(distance, position, block) of the wanted blocks within radius of the bot.

        Terrain blocks are too many to list, so each column offers its topmost
        block of the type and the one level with the bot."""
        found = []
        for chunk in self._chunks_within(radius):
            for block in blocks & chunk.by_id.keys():
                for pos in chunk.by_id[block]:
                    distance = self._distance_to(pos)
                    if 0 < distance <= radius and self._changes.get(pos, block) == block:
                        found.append((distance, pos, block))
        for pos, block in self._changes.items():  # placed blocks are not in any chunk
            distance = self._distance_to(pos)
            if block in blocks and 0 < distance <= radius:
                found.append((distance, pos, block))
        terrain = blocks & TERRAIN_BLOCKS
        if terrain:
            bot_y = self._cell[1]
            for x, z in self._disc(radius):
                spans = self._spans(*self._column(x, z))
                for block in terrain & spans.keys():
                    low, high = spans[block]
                    for y in {high, min(max(bot_y, low), high)}:
                        distance = self._distance_to((x, y, z))
                        if 0 < distance <= radius and self.block_at((x, y, z)) == block:
                            found.append((distance, (x, y, z), block))
        return found

    def _disc(self, radius: float) -> Iterable[tuple[int, int]]:
        """The columns within a horizontal radius of the bot."""
        x, _, z = self._cell
        reach = int(radius)
        return [(x + dx, z + dz) for dx in range(-reach, reach + 1) for dz in range(-reach, reach + 1) if dx * dx + dz * dz <= radius * radius]

    def _chunks_within(self, radius: float) -> list[_Chunk]:
        x, _, z = self._cell
        reach = int(radius)
        return [
            self._chunk(cx, cz)
            for cx in range((x - reach) >> 4, ((x + reach) >> 4) + 1)
            for cz in range((z - reach) >> 4, ((z + reach) >> 4) + 1)
        ]

    def _chunk(self, cx: int, cz: int) -> _Chunk:
        chunk = self._chunks.get((cx, cz))
        if chunk is None:
            chunk = self._chunks[(cx, cz)] = self._generate_chunk(cx, cz)
        return chunk

    def _generate_chunk(self, cx: int, cz: int) -> _Chunk:
        rng = random.Random(f"{self.seed}:{cx}:{cz}")
        x0, z0 = cx * 16, cz * 16
        blocks: dict[Pos, str] = {}
        self._add_pools(blocks, rng, x0, z0)
        self._add_trees(blocks, rng, x0, z0)
        self._add_gravel(blocks, rng, x0, z0)
        self._add_ores(blocks, rng, x0, z0)
        self._add_ground_cover(blocks, rng, x0, z0)
        by_id: dict[str, list[Pos]] = {}
        for pos, block in blocks.items():
            by_id.setdefault(block, []).append(pos)
        return _Chunk(blocks, by_id)

    def _add_pools(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int) -> None:
        for fluid, chance, (smallest, largest) in (("water", 0.3, (2, 3)), ("lava", 0.12, (1, 2))):
            center_x, center_z = x0 + rng.randint(3, 12), z0 + rng.randint(3, 12)
            radius = rng.randint(smallest, largest)
            if rng.random() >= chance or math.hypot(center_x, center_z) < SPAWN_CLEAR:
                continue
            for x in range(center_x - radius, center_x + radius + 1):
                for z in range(center_z - radius, center_z + radius + 1):
                    if math.hypot(x - center_x, z - center_z) <= radius:
                        height = self._column(x, z)[0]
                        blocks[(x, height, z)] = blocks[(x, height - 1, z)] = fluid

    def _add_trees(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int) -> None:
        for _ in range(rng.randint(0, 5)):
            x, z = x0 + rng.randint(2, 13), z0 + rng.randint(2, 13)  # leaves stay inside the chunk
            species = rng.choice(self._tree_species)
            top_height = rng.randint(4, 6)
            height, rocky = self._column(x, z)
            if rocky or (x, height, z) in blocks:
                continue
            top = height + top_height
            for y in range(top - 2, top + 2):
                radius = 2 if y < top else 1
                for dx in range(-radius, radius + 1):
                    for dz in range(-radius, radius + 1):
                        if not (radius == 2 and abs(dx) == abs(dz) == 2):
                            blocks.setdefault((x + dx, y, z + dz), f"{species}_leaves")
            for y in range(height + 1, top + 1):
                blocks[(x, y, z)] = f"{species}_log"

    def _add_gravel(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int) -> None:
        center_x, center_z = x0 + rng.randint(3, 12), z0 + rng.randint(3, 12)
        if rng.random() >= 0.5:
            return
        for x in range(center_x - 2, center_x + 3):
            for z in range(center_z - 2, center_z + 3):
                if math.hypot(x - center_x, z - center_z) <= 2:
                    height = self._column(x, z)[0]
                    blocks.setdefault((x, height, z), "gravel")
                    blocks.setdefault((x, height - 1, z), "gravel")

    def _add_ores(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int) -> None:
        # Outcrops of coal at the surface.
        if rng.random() < 0.6:
            x, z = x0 + rng.randint(2, 13), z0 + rng.randint(2, 13)
            for _ in range(rng.randint(1, 3)):
                column_x, column_z = x + rng.randint(-1, 1), z + rng.randint(-1, 1)
                blocks.setdefault((column_x, self._column(column_x, column_z)[0], column_z), "coal_ore")
        for _ in range(rng.randint(1, 3)):
            self._add_vein(blocks, rng, x0, z0, "coal", rng.randint(3, 6), (-30, 10))
        for _ in range(rng.randint(1, 2)):
            self._add_vein(blocks, rng, x0, z0, "iron", rng.randint(1, 4), (-28, 6))

    def _add_vein(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int, ore: str, size: int, y_range: tuple[int, int]) -> None:
        """A random-walk vein under the surface; sometimes a pocket of lava touches it."""
        x, z = x0 + rng.randrange(16), z0 + rng.randrange(16)
        y = rng.randint(y_range[0], max(y_range[0], min(y_range[1], self._column(x, z)[0] - 4)))
        cells = []
        for _ in range(size):
            if x0 <= x < x0 + 16 and z0 <= z < z0 + 16 and TERRAIN_BOTTOM < y <= self._column(x, z)[0] - 4 and (x, y, z) not in blocks:
                blocks[(x, y, z)] = f"{'deepslate_' if y < 0 else ''}{ore}_ore"
                cells.append((x, y, z))
            dx, dy, dz = rng.choice(_NEIGHBOURS)
            x, y, z = x + dx, y + dy, z + dz
        if cells and rng.random() < 0.3:
            dx, dy, dz = rng.choice(_NEIGHBOURS)
            lava = (cells[0][0] + dx, cells[0][1] + dy, cells[0][2] + dz)
            if x0 <= lava[0] < x0 + 16 and z0 <= lava[2] < z0 + 16 and lava[1] > TERRAIN_BOTTOM:
                blocks.setdefault(lava, "lava")

    def _add_ground_cover(self, blocks: dict[Pos, str], rng: random.Random, x0: int, z0: int) -> None:
        for plant in ["short_grass"] * 10 + [rng.choice(("poppy", "dandelion")) for _ in range(3)]:
            x, z = x0 + rng.randrange(16), z0 + rng.randrange(16)
            height, rocky = self._column(x, z)
            if not rocky and (x, height, z) not in blocks and (x, height + 1, z) not in blocks:
                blocks[(x, height + 1, z)] = plant

    # --- small helpers -------------------------------------------------------

    def _position(self) -> tuple[float, float, float]:
        x, y, z = self._cell
        return (x + 0.5, float(y), z + 0.5)

    def _distance_to(self, pos: Pos) -> float:
        x, y, z = self._cell
        return math.sqrt((pos[0] - x) ** 2 + (pos[1] - y) ** 2 + (pos[2] - z) ** 2)

    def _inventory(self) -> dict[str, int]:
        return {item: count for item, count in sorted(self.inventory.items()) if count > 0}

    def _fail(self, error: str, events: list[dict[str, Any]] | None = None, **detail: Any) -> Outcome:
        return Outcome(
            ok=False,
            error=error,
            inventory=self._inventory(),
            blocks_walked=round(self.blocks_walked, 1),
            detail=detail,
            travel=_empty_travel(),
            events=events or [],
        )


def _xyz(position: tuple[float, float, float]) -> dict[str, float]:
    return {"x": position[0], "y": position[1], "z": position[2]}


def _free_slots(inventory: dict[str, int], *, clamp: bool = True) -> int:
    """Empty slots of an inventory; negative (unless clamped to 0) when it holds more than fits."""
    free = INVENTORY_SLOTS - slots_used(inventory)
    return max(0, free) if clamp else free


def _is_free(block: str) -> bool:
    return block in AIR_BLOCKS or block in REPLACEABLE


def _empty_travel() -> dict[str, Any]:
    return {"distance": 0.0, "dug": {}, "placed": {}, "picked_up": {}}


def _held_member(name: str, inventory: dict[str, int]) -> str:
    """A smelt's fuel or ingredient: for a group, the member held most (ties by name)."""
    if not name.startswith("#"):
        return name
    held = {member: inventory.get(member, 0) for member in normalize.members(name) if inventory.get(member, 0) > 0}
    if not held:
        raise ValueError(f"cannot resolve {name}: no matching item is held")
    return min(held, key=lambda member: (-held[member], member))
