from __future__ import annotations

from collections import Counter
from typing import Protocol

from .models import Action, ActionResult, Observation, Position, TaskSpec


class MinecraftAdapter(Protocol):
    """Runtime surface required by the agent loop.

    A MineDojo, Mineflayer, or Voyager-backed implementation can satisfy this
    protocol without changing planning, schema persistence, or evaluation.
    """

    task: TaskSpec
    position: Position
    inventory: Counter

    @property
    def is_done(self) -> bool: ...

    @property
    def is_success(self) -> bool: ...

    def reset(self) -> Observation: ...

    def observe(self) -> Observation: ...

    def step(self, action: Action) -> ActionResult: ...

    def nearest_block(self, block: str) -> Position | None: ...
