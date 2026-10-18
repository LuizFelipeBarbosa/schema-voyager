"""The goal queue (v3.1): goals typed in the panel or in chat, and the life's own
system goals, in an append-only `runs/<life>/goals.jsonl` that the panel and the
life share. Each line is one goal's full state after a change; the latest valid
line per id wins. The contract is `docs/v3-interfaces.md`, v3.1b and v3.1c.

Stubs only: S06's `goal-queue` lane writes the behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .models import GoalSource, GoalSpec, GoalStatus

InterruptReason = Literal["cancelled", "preempted", "death"]


class GoalInterrupted(Exception):
    """Raised by the life's before_step hook: the running goal ends before its next step."""

    def __init__(self, goal_id: str, reason: InterruptReason) -> None:
        super().__init__(f"goal {goal_id} interrupted: {reason}")
        self.goal_id = goal_id
        self.reason = reason


@dataclass(frozen=True)
class GoalEntry:
    """One goal as rebuilt from goals.jsonl."""

    id: str
    status: GoalStatus
    source: GoalSource
    text: str
    t: float  # time of its latest line
    spec: GoalSpec | None = None  # set once parsed
    reason: str = ""  # why it is failed, cancelled or unsupported (the reply)
    target_count: int | None = None  # obtain: the count to hold, fixed when it first runs (D5)
    deaths: int = 0  # the bot's deaths during this goal

    def to_json(self) -> dict[str, Any]:
        """The goals.jsonl line, also served by the panel's GET /goals.json."""
        raise NotImplementedError


class GoalQueue:
    def __init__(self, path: Path) -> None:
        self.path = path

    def submit(self, text: str, source: GoalSource = "user") -> str:
        """Append a new goal as `submitted` and return its id ("u003", "s001")."""
        raise NotImplementedError

    def accept(self, spec: GoalSpec) -> bool:
        """submitted -> queued, with the parsed fields; spec.id is the goal's id."""
        raise NotImplementedError

    def pending(self) -> list[GoalEntry]:
        """The `submitted` goals, oldest first: what the life must parse."""
        raise NotImplementedError

    def next(self) -> GoalSpec | None:
        """The queued goal to run: system before user, then oldest first. Writes nothing."""
        raise NotImplementedError

    def mark(self, goal_id: str, status: GoalStatus, **fields: Any) -> bool:
        """Move a goal to `status`; False (and nothing written) for an unknown id or a refused transition."""
        raise NotImplementedError

    def cancel(self, goal_id: str) -> bool:
        """Cancel a goal that isn't final; False otherwise."""
        raise NotImplementedError

    def get(self, goal_id: str) -> GoalEntry | None:
        raise NotImplementedError

    def goals(self) -> list[GoalEntry]:
        """Every goal, in submission order."""
        raise NotImplementedError

    def requeue_running(self) -> list[str]:
        """On --resume: every running goal goes back to queued. Returns their ids."""
        raise NotImplementedError
