from __future__ import annotations

from .models import Task


TRAIN_LAYOUTS = (1, 2)
HOLDOUT_LAYOUTS = (6, 7, 8)

_TASKS = {
    "t1": Task(
        task_id="t1",
        split="train",
        goal_item="wooden_pickaxe",
        goal_count=1,
        start_inventory={},
        world_rules=(),
        budget=40,
        description="From an empty inventory, craft a wooden pickaxe.",
    ),
    "t2": Task(
        task_id="t2",
        split="train",
        goal_item="stone_pickaxe",
        goal_count=1,
        start_inventory={},
        world_rules=(),
        budget=60,
        description="From an empty inventory, craft a stone pickaxe.",
    ),
    "t3": Task(
        task_id="t3",
        split="train",
        goal_item="torch",
        goal_count=4,
        start_inventory={},
        world_rules=(),
        budget=70,
        description="From an empty inventory, craft 4 torches.",
    ),
    # H3 runs in a world where coal ore drops coal only for a stone-tier
    # pickaxe (datapack r2_coal_tool). The agent is not told.
    "h3": Task(
        task_id="h3",
        split="holdout",
        goal_item="torch",
        goal_count=4,
        start_inventory={},
        world_rules=("r2",),
        budget=90,
        description="From an empty inventory, craft 4 torches.",
    ),
}


def get_task(task_id: str) -> Task:
    return _TASKS[task_id]


def training_tasks() -> list[Task]:
    return [_TASKS["t1"], _TASKS["t2"], _TASKS["t3"]]


def holdout_tasks() -> list[Task]:
    return [_TASKS["h3"]]
