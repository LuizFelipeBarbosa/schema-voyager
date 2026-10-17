from __future__ import annotations

from .models import Position, TaskSpec


def _task(
    task_id: str,
    split: str,
    goal_item: str,
    start: tuple[int, int],
    inventory: dict[str, int],
    resources: list[tuple[tuple[int, int], str]],
    description: str,
    action_budget: int = 220,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        split=split,  # type: ignore[arg-type]
        goal_item=goal_item,
        goal_count=1,
        start=Position(*start),
        start_inventory=inventory,
        resources={Position(*position): block for position, block in resources},
        action_budget=action_budget,
        description=description,
    )


def training_tasks() -> list[TaskSpec]:
    return [
        _task(
            "t1_wooden_pickaxe",
            "train",
            "wooden_pickaxe",
            (0, 0),
            {},
            [((1, 0), "log"), ((1, 1), "log")],
            "Gather logs and craft a wooden pickaxe.",
        ),
        _task(
            "t2_stone_pickaxe",
            "train",
            "stone_pickaxe",
            (0, 0),
            {"wooden_pickaxe": 1, "sticks": 2},
            [((2, 0), "stone"), ((2, 1), "stone"), ((3, 0), "stone")],
            "Locate stone and craft a stone pickaxe.",
        ),
        _task(
            "t3_furnace",
            "train",
            "furnace",
            (0, 0),
            {"stone_pickaxe": 1},
            [((1, 0), "stone"), ((1, 1), "stone"), ((2, 0), "stone"), ((2, 1), "stone"),
             ((3, 0), "stone"), ((3, 1), "stone"), ((4, 0), "stone"), ((4, 1), "stone")],
            "Mine eight cobblestone and craft a furnace.",
        ),
    ]


def holdout_tasks() -> list[TaskSpec]:
    return [
        _task(
            "h1_wooden_pickaxe_new_spawn",
            "holdout",
            "wooden_pickaxe",
            (-2, 1),
            {},
            [((-1, 1), "log"), ((-1, 2), "log")],
            "From a new spawn, gather logs and craft a wooden pickaxe.",
        ),
        _task(
            "h2_stone_pickaxe_hidden_resource",
            "holdout",
            "stone_pickaxe",
            (0, 0),
            {"wooden_pickaxe": 1, "sticks": 1, "logs": 1},
            [((4, 0), "stone"), ((4, 1), "stone"), ((5, 0), "stone")],
            "Find distant stone and craft a stone pickaxe with partial supplies.",
            action_budget=260,
        ),
        _task(
            "h3_starter_kit",
            "holdout",
            "furnace",
            (-1, -1),
            {"wooden_pickaxe": 1, "sticks": 1},
            [((1, -1), "stone"), ((1, 0), "stone"), ((2, -1), "stone"), ((2, 0), "stone"),
             ((3, -1), "stone"), ((3, 0), "stone"), ((4, -1), "stone"), ((4, 0), "stone")],
            "Build the next stage of the starter kit by crafting a furnace.",
            action_budget=300,
        ),
    ]


def all_tasks() -> list[TaskSpec]:
    return training_tasks() + holdout_tasks()
