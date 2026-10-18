from __future__ import annotations

from typing import Any, Protocol

from .layouts import Layout
from .models import Action, Observation, Outcome, Site, Status, Task

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

    def toss(self, items: dict[str, int]) -> dict[str, int]:
        """v3: drop up to these counts (raw ids) to make room; returns what left the inventory. See docs/v3-interfaces.md."""
        ...

    def scan(self, origin: tuple[int, int, int], size: tuple[int, int, int]) -> dict[tuple[int, int, int], str | None]:
        """v3.4: the block in every cell of the box, keyed by world position; None where the bot can't see.
        size is [width, height, depth] (x, y, z). See docs/v3-interfaces.md, v3.4a."""
        ...

    def set_keep_out(self, box: tuple[tuple[int, int, int], tuple[int, int, int]] | None) -> None:
        """v3.4: an (origin, size) box the walks of place and clear never put scaffold in; None clears it."""
        ...

    def set_protected(self, boxes: list[tuple[tuple[int, int, int], tuple[int, int, int]]]) -> None:
        """v3.4b: (origin, size) boxes of everything built: never mined, dug through by a walk, or given scaffold.
        Sent with every request that walks, so a bot restart loses nothing. See docs/v3-interfaces.md, v3.4b."""
        ...

    def survey(self, size: tuple[int, int], radius: int = 24) -> list[Site]:
        """v3.4b: candidate build sites for a (width, depth) footprint near the bot, best first."""
        ...
