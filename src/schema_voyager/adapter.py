from __future__ import annotations

from typing import Protocol

from .layouts import Layout
from .models import Action, Observation, Outcome, Task


class Backend(Protocol):
    """The action surface every system uses, in the simulator or on a real server.

    Agents only see observations and outcomes. They never read the world's
    recipe or loot tables.
    """

    name: str

    def reset(self, task: Task, layout: Layout) -> Observation: ...

    def observe(self) -> Observation: ...

    def act(self, action: Action) -> Outcome: ...

    def say(self, message: str) -> None: ...
