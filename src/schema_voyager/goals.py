"""The goal queue (v3.1): goals typed in the panel or in chat, and the life's own
system goals, in an append-only `runs/<life>/goals.jsonl` that the panel and the
life share. Each line is one goal's full state after a change; the latest valid
line per id wins. The contract is `docs/v3-interfaces.md`, v3.1b and v3.1c.
"""

from __future__ import annotations

import fcntl
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .models import GoalSource, GoalSpec, GoalStatus

InterruptReason = Literal["cancelled", "preempted", "death", "bot"]

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "submitted": {"queued", "awaiting_approval", "unsupported", "cancelled"},
    "queued": {"running", "cancelled", "awaiting_approval"},
    "awaiting_approval": {"queued", "cancelled"},
    "running": {"done", "failed", "cancelled", "queued"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
    "unsupported": set(),
}
_MARK_FIELDS = {"reason", "target_count", "deaths"}
_ID_PREFIX = {"user": "u", "system": "s"}
_UNCOUNTABLE = {"coal", "charcoal", "redstone", "glass", "sand", "gravel", "dirt", "cobblestone", "stone", "flint"}


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
        spec = self.spec
        return {
            "id": self.id,
            "t": self.t,
            "status": self.status,
            "source": self.source,
            "text": self.text,
            "kind": spec.kind if spec else None,
            "item": spec.item if spec else None,
            "count": spec.count if spec else None,
            "target": spec.target if spec else None,
            "blueprint": spec.blueprint if spec else None,
            "reason": self.reason,
            "target_count": self.target_count,
            "deaths": self.deaths,
        }


def _entry_from_json(record: dict[str, Any]) -> GoalEntry:
    source = record.get("source", "user")
    text = record.get("text", "")
    spec = None
    if record.get("kind") is not None:
        spec = GoalSpec(
            record["id"],
            record["kind"],
            source,
            text,
            item=record.get("item"),
            count=record.get("count", 1),
            target=record.get("target"),
            blueprint=record.get("blueprint"),
        )
    return GoalEntry(
        id=record["id"],
        status=record["status"],
        source=source,
        text=text,
        t=record.get("t", 0.0),
        spec=spec,
        reason=record.get("reason", ""),
        target_count=record.get("target_count"),
        deaths=record.get("deaths", 0),
    )


def _rebuild(data: bytes) -> dict[str, GoalEntry]:
    """The latest valid line per id, in the order of each id's first line. Corrupt lines are skipped."""
    goals: dict[str, GoalEntry] = {}
    for line in data.splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or "id" not in record or "status" not in record:
                continue
            goals[record["id"]] = _entry_from_json(record)
        except (ValueError, TypeError):
            continue
    return goals


class GoalQueue:
    def __init__(self, path: Path) -> None:
        self.path = path

    def submit(self, text: str, source: GoalSource = "user") -> str:
        """Append a new goal as `submitted` and return its id ("u003", "s001")."""
        prefix = _ID_PREFIX.get(source)
        if prefix is None:
            raise ValueError(f"{source} goals never go in the queue file")

        def build(goals: dict[str, GoalEntry]) -> GoalEntry:
            numbers = [int(g[1:]) for g in goals if g[0] == prefix and g[1:].isdigit()]
            goal_id = f"{prefix}{max(numbers, default=0) + 1:03d}"
            return GoalEntry(goal_id, "submitted", source, text, time.time())

        return self._append(build).id

    def accept(self, spec: GoalSpec) -> bool:
        """submitted -> queued (a build: awaiting_approval), with the parsed fields; spec.id is the goal's id."""

        def build(goals: dict[str, GoalEntry]) -> GoalEntry | None:
            entry = goals.get(spec.id)
            if entry is None or entry.status != "submitted":
                return None
            parsed = replace(spec, source=entry.source, text=entry.text)
            status = "awaiting_approval" if spec.kind == "build" else "queued"
            return replace(entry, status=status, t=time.time(), spec=parsed)

        return self._append(build) is not None

    def pending(self) -> list[GoalEntry]:
        """The `submitted` goals, oldest first: what the life must parse."""
        return [g for g in self.goals() if g.status == "submitted"]

    def next(self) -> GoalSpec | None:
        """The queued goal to run: system before user, then oldest first. Writes nothing."""
        queued = [g for g in self.goals() if g.status == "queued" and g.spec is not None]
        for source in ("system", "user"):
            for goal in queued:
                if goal.source == source:
                    return goal.spec
        return None

    def mark(self, goal_id: str, status: GoalStatus, **fields: Any) -> bool:
        """Move a goal to `status`; False (and nothing written) for an unknown id or a refused transition."""
        unknown = fields.keys() - _MARK_FIELDS
        if unknown:
            raise ValueError(f"mark cannot set {sorted(unknown)}; only {sorted(_MARK_FIELDS)}")

        def build(goals: dict[str, GoalEntry]) -> GoalEntry | None:
            entry = goals.get(goal_id)
            if entry is None or status not in _ALLOWED_TRANSITIONS[entry.status]:
                return None
            return replace(entry, status=status, t=time.time(), **fields)

        return self._append(build) is not None

    def cancel(self, goal_id: str) -> bool:
        """Cancel a goal that isn't final; False otherwise."""
        return self.mark(goal_id, "cancelled")

    def get(self, goal_id: str) -> GoalEntry | None:
        return self._read().get(goal_id)

    def goals(self) -> list[GoalEntry]:
        """Every goal, in submission order."""
        return list(self._read().values())

    def requeue_running(self) -> list[str]:
        """On --resume: every running goal goes back to queued. Returns their ids."""
        running = [g.id for g in self.goals() if g.status == "running"]
        return [goal_id for goal_id in running if self.mark(goal_id, "queued")]

    def _read(self) -> dict[str, GoalEntry]:
        try:
            return _rebuild(self.path.read_bytes())
        except FileNotFoundError:
            return {}

    def _append(self, build: Callable[[dict[str, GoalEntry]], GoalEntry | None]) -> GoalEntry | None:
        """Under the file lock, rebuild the state and append the line `build` makes from it (None: write nothing)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                data = f.read()
                entry = build(_rebuild(data))
                if entry is None:
                    return None
                separator = b"\n" if data and not data.endswith(b"\n") else b""
                f.write(separator + (json.dumps(entry.to_json()) + "\n").encode())
                f.flush()
                return entry
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _display_name(item: str, count: int) -> str:
    """"#planks" -> "plank" or "planks"; "iron_ingot" -> "iron ingot" or "iron ingots"."""
    name = item.lstrip("#").replace("_", " ")
    if count == 1:
        return name[:-1] if item.startswith("#") and name.endswith("s") else name
    last_word = name.rsplit(" ", 1)[-1]
    if last_word in _UNCOUNTABLE or name.endswith("s"):
        return name
    return name + ("es" if name.endswith(("ch", "sh", "x")) else "s")


def goal_label(spec: GoalSpec | None, fallback: str = "") -> str:
    """What chat calls a goal: "10 torches", "1 plank", "3 coal" for an obtain goal; else `fallback` or the
    spec's text (v3.1c)."""
    if spec is not None and spec.kind == "build" and spec.blueprint:
        return spec.blueprint["name"]
    if spec is not None and spec.kind == "obtain" and spec.item:
        return f"{spec.count} {_display_name(spec.item, spec.count)}"
    return fallback or (spec.text if spec else "")
