from __future__ import annotations

from collections import Counter

from .models import Action, ActionResult, Observation, Position, TaskSpec


RECIPES: dict[str, tuple[dict[str, int], int]] = {
    "planks": ({"logs": 1}, 4),
    "sticks": ({"planks": 2}, 4),
    "wooden_pickaxe": ({"planks": 3, "sticks": 2}, 1),
    "stone_pickaxe": ({"cobblestone": 3, "sticks": 2}, 1),
    "furnace": ({"cobblestone": 8}, 1),
    "torches": ({"coal": 1, "sticks": 1}, 4),
}

MINING_RULES: dict[str, tuple[str, int]] = {
    "log": ("logs", 1),
    "stone": ("cobblestone", 1),
    "coal_ore": ("coal", 1),
}


class MinecraftLikeEnv:
    """Small deterministic environment with a Minecraft-shaped action space.

    The class is intentionally independent from an actual Minecraft server. A
    MineDojo, Mineflayer, or Voyager adapter can implement the same reset,
    observe, and step boundary later without changing the agent or evaluator.
    """

    def __init__(self, task: TaskSpec):
        self.task = task
        self.position = task.start
        self.inventory = Counter(task.start_inventory)
        self.blocks = dict(task.resources)
        self.action_count = 0
        self.last_message = ""

    def reset(self) -> Observation:
        self.position = self.task.start
        self.inventory = Counter(self.task.start_inventory)
        self.blocks = dict(self.task.resources)
        self.action_count = 0
        self.last_message = ""
        return self.observe()

    def observe(self) -> Observation:
        visible = Counter(
            block
            for position, block in self.blocks.items()
            if abs(position.x - self.position.x) + abs(position.z - self.position.z) <= 1
        )
        return Observation(
            task_id=self.task.task_id,
            position=self.position,
            inventory={item: count for item, count in self.inventory.items() if count > 0},
            visible_blocks=dict(visible),
            action_count=self.action_count,
            action_budget=self.task.action_budget,
            goal_item=self.task.goal_item,
            goal_count=self.task.goal_count,
            done=self.is_done,
            success=self.is_success,
            last_message=self.last_message,
        )

    @property
    def is_success(self) -> bool:
        return self.inventory[self.task.goal_item] >= self.task.goal_count

    @property
    def is_done(self) -> bool:
        return self.is_success or self.action_count >= self.task.action_budget

    def step(self, action: Action) -> ActionResult:
        if self.is_done:
            return ActionResult(False, "The task is already complete.", self.observe())

        self.action_count += 1
        if action.name == "inspect":
            self.last_message = "Observation refreshed."
            return ActionResult(True, self.last_message, self.observe())

        if action.name == "move":
            return self._move(action.target)
        if action.name == "mine":
            return self._mine()
        if action.name == "craft":
            return self._craft(action.target)
        self.last_message = f"Unknown action: {action.name}"
        return ActionResult(False, self.last_message, self.observe())

    def _move(self, direction: str | None) -> ActionResult:
        if direction not in {"north", "south", "east", "west"}:
            self.last_message = "Move requires a cardinal direction."
            return ActionResult(False, self.last_message, self.observe())
        self.position = self.position.moved(direction)  # type: ignore[arg-type]
        self.last_message = f"Moved {direction}."
        return ActionResult(True, self.last_message, self.observe())

    def _mine(self) -> ActionResult:
        block = self.blocks.get(self.position)
        if block is None:
            self.last_message = "There is no mineable block here."
            return ActionResult(False, self.last_message, self.observe())

        if block == "stone" and self.inventory["wooden_pickaxe"] < 1 and self.inventory["stone_pickaxe"] < 1:
            self.last_message = "Stone requires a pickaxe."
            return ActionResult(False, self.last_message, self.observe())
        if block == "coal_ore" and self.inventory["stone_pickaxe"] < 1:
            self.last_message = "Coal ore requires a stone pickaxe."
            return ActionResult(False, self.last_message, self.observe())

        item, amount = MINING_RULES[block]
        del self.blocks[self.position]
        self.inventory[item] += amount
        self.last_message = f"Mined {block} and received {amount} {item}."
        return ActionResult(True, self.last_message, self.observe())

    def _craft(self, item: str | None) -> ActionResult:
        if item not in RECIPES:
            self.last_message = f"Unknown recipe: {item}."
            return ActionResult(False, self.last_message, self.observe())

        ingredients, output_count = RECIPES[item]
        missing = {
            ingredient: count - self.inventory[ingredient]
            for ingredient, count in ingredients.items()
            if self.inventory[ingredient] < count
        }
        if missing:
            self.last_message = f"Missing ingredients for {item}: {missing}."
            return ActionResult(False, self.last_message, self.observe())

        for ingredient, count in ingredients.items():
            self.inventory[ingredient] -= count
        self.inventory[item] += output_count
        self.last_message = f"Crafted {output_count} {item}."
        return ActionResult(True, self.last_message, self.observe())

    def nearest_block(self, block: str) -> Position | None:
        candidates = [position for position, value in self.blocks.items() if value == block]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda position: abs(position.x - self.position.x)
            + abs(position.z - self.position.z),
        )

