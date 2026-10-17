from __future__ import annotations

import math
from collections import Counter

from .layouts import ARENA_RADIUS, GROUND_Y, Layout
from .models import Action, Observation, Outcome, Task

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
