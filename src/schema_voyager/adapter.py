from __future__ import annotations

from typing import Any, Protocol

from .layouts import Layout
from .models import Action, Observation, Outcome, Status, Task

DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")  # N is -z, E is +x
# direction -> raw block id -> {"x", "y", "z", "distance", "exposed"}
LookResult = dict[str, dict[str, dict[str, Any]]]


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


class LifeBackend(Backend, Protocol):
    """A backend for one continuous life (v2): no world reset between goals.

    act() also accepts group and family targets ("#log", "coal_ore"), which the
    backend expands or resolves through normalize.py. See docs/v2-interfaces.md.
    """

    def begin_episode(self) -> Observation:
        """Per-goal bookkeeping only (the bot's skip list and walked distance); never touches the world."""
        ...

    def status(self, since: int = 0) -> Status: ...

    def look(self, blocks: list[str], radius: int = 64) -> LookResult: ...

    def probe(self, grid: list[list[str | None]]) -> dict[str, Any]:
        """The recipe output for a crafting grid, without spending anything: {"output": {"item", "count"} | None}."""
        ...
