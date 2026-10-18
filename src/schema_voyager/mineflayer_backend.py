"""Backend that drives the real Mineflayer bot through its HTTP API
(contracts/bot_api.md) and resets the arena over RCON."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from . import normalize
from .adapter import DIRECTIONS, LookResult
from .arena import Arena
from .layouts import Layout
from .models import Action, Observation, Outcome, Status, Task

# Longer than the bot's own 120 s action timeout, so a slow action comes back
# as an error the episode can retry instead of a dropped connection.
REQUEST_TIMEOUT_S = 150
SMELT_TIMEOUT_S = 150  # plus SMELT_TIMEOUT_PER_ITEM_S per item smelted
SMELT_TIMEOUT_PER_ITEM_S = 12

# v2: the only blocks natural-mode pathing may place. Not cobblestone, which plans spend.
SCAFFOLD_BLOCKS = ("dirt", "cobbled_deepslate", "netherrack", "andesite", "diorite", "granite", "tuff")
MINE_MAX_DISTANCE = 32
EXPLORE_DISTANCE = 32


class BackendError(RuntimeError):
    pass


class MineflayerBackend:
    name = "minecraft"

    def __init__(self, api_url: str | None = None, arena: Arena | None = None, natural: bool = False):
        self.api_url = (api_url or os.environ.get("BOT_API_URL", "http://127.0.0.1:3000")).rstrip("/")
        self.natural = natural
        # A natural-mode life never resets an arena.
        self.arena = None if natural else (arena or Arena())
        self._latest_inventory: dict[str, int] | None = None

    def reset(self, task: Task, layout: Layout) -> Observation:
        self.arena.prepare_world()
        self.arena.set_world_rules(task.world_rules)
        self.arena.build(layout)
        self.arena.reset_player(task.start_inventory)
        return _observation(self._post("/reset_episode"))

    def observe(self) -> Observation:
        observation = _observation(self._post("/observe"))
        self._latest_inventory = observation.inventory
        return observation

    def act(self, action: Action) -> Outcome:
        if self.natural:
            return self._act_natural(action)
        if action.kind == "mine":
            body = self._post("/mine", {"block": action.target, "tool": action.tool})
            return Outcome(
                ok=body["ok"],
                error=body.get("error"),
                block_removed=body.get("block_removed"),
                drops=body.get("drops") or {},
                picked_up=body.get("picked_up") or {},
                inventory_delta=body.get("picked_up") or {},
                inventory=self._inventory(body),
                blocks_walked=body.get("blocks_walked", 0.0),
                detail=_detail(body, "target", "tool", "detail"),
            )
        if action.kind == "craft":
            body = self._post("/craft", {"item": action.target, "times": action.times})
            return Outcome(
                ok=body["ok"],
                error=body.get("error"),
                inventory_delta=body.get("inventory_delta") or {},
                inventory=self._inventory(body),
                blocks_walked=body.get("blocks_walked", 0.0),
                detail=_detail(body, "missing", "used_table", "table_retrieved", "detail"),
            )
        body = self._post("/explore")
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory=self.observe().inventory,
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=_detail(body, "position"),
        )

    def _inventory(self, body: dict[str, Any]) -> dict[str, int]:
        """The inventory an action reported; failures such as a timeout report none, so ask."""
        if "inventory" in body:
            self._latest_inventory = body["inventory"]
            return body["inventory"]
        return self.observe().inventory

    def say(self, message: str) -> None:
        try:
            self._post("/chat", {"message": message})
        except BackendError:
            pass  # narration is best effort

    # --- v2: natural mode ----------------------------------------------------

    def begin_episode(self) -> Observation:
        observation = _observation(self._post("/reset_episode"))
        self._latest_inventory = observation.inventory
        return observation

    def status(self, since: int = 0) -> Status:
        body = self._post("/status", {"since": since})
        position = body.get("position") or {"x": 0, "y": 0, "z": 0}
        return Status(
            health=body.get("health", 0.0),
            food=body.get("food", 0),
            position=(position["x"], position["y"], position["z"]),
            alive=body.get("alive", True),
            events=body.get("events") or [],
            last_seq=body.get("last_seq", 0),
        )

    def look(self, blocks: list[str], radius: int = 64) -> LookResult:
        body = self._post("/look", {"blocks": blocks, "radius": radius})
        directions = body.get("directions") or {}
        return {direction: directions.get(direction) or {} for direction in DIRECTIONS}

    def probe(self, grid: list[list[str | None]]) -> dict[str, Any]:
        return self._post("/probe", {"grid": grid})

    def _act_natural(self, action: Action) -> Outcome:
        if action.kind == "mine":
            return self._mine_natural(action)
        if action.kind == "craft":
            return self._craft_natural(action)
        if action.kind == "smelt":
            return self._smelt_natural(action)
        return self._explore_natural(action)

    def _mine_natural(self, action: Action) -> Outcome:
        blocks = normalize.members(action.target or "")
        body = self._post("/mine", {
            "block": blocks[0],
            "blocks": blocks,
            "tool": action.tool,
            "dig_through": True,
            "max_distance": MINE_MAX_DISTANCE,
            "scaffold": list(SCAFFOLD_BLOCKS),
        })
        detail = _detail(body, "target", "block", "tool", "inventory_before_step", "detail")
        if "tool_used" in body:  # null is meaningful: an empty hand
            detail["tool_used"] = body["tool_used"]
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            block_removed=body.get("block_removed"),
            drops=body.get("drops") or {},
            picked_up=body.get("picked_up") or {},
            inventory_delta=body.get("picked_up") or {},
            inventory=self._inventory(body),
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=detail,
            travel=body.get("travel") or {},
            events=body.get("events") or [],
        )

    def _craft_natural(self, action: Action) -> Outcome:
        inventory = self._known_inventory()
        try:
            parts = self._craft_parts(action, inventory)
        except ValueError:
            return Outcome(ok=False, error="missing_ingredients", inventory=inventory)

        bodies: list[dict[str, Any]] = []
        for item, times in parts:
            body = self._post("/craft", {"item": item, "times": times})
            bodies.append(body)
            self._inventory(body)
            if not body["ok"]:
                break

        delta: Counter[str] = Counter()
        for body in bodies:
            delta.update(body.get("inventory_delta") or {})
        failed = next((body for body in bodies if not body["ok"]), None)
        detail = _detail(bodies[0], "item", "inventory_before_step")
        for key in ("missing", "used_table", "table_retrieved", "detail"):
            values = [body[key] for body in bodies if body.get(key) is not None]
            if values:
                detail[key] = values[-1] if key != "table_retrieved" else all(values)
        return Outcome(
            ok=failed is None,
            error=failed.get("error") if failed else None,
            inventory_delta={item: count for item, count in delta.items() if count},
            inventory=self._inventory(bodies[-1]),
            blocks_walked=bodies[-1].get("blocks_walked", 0.0),
            detail=detail,
            travel=_merge_travel([body.get("travel") or {} for body in bodies]),
            events=[event for body in bodies for event in body.get("events") or []],
        )

    def _craft_parts(self, action: Action, inventory: dict[str, int]) -> list[tuple[str, int]]:
        """(raw item, times) bot calls for a craft. A group target names a species
        per call: planks come from logs, so the logs held decide how it splits."""
        target = action.target or ""
        if not target.startswith("#"):
            return [(target, action.times)]
        logs: Counter[str] = Counter()
        for item, count in inventory.items():
            if target == "#planks" and normalize.family(item) == "#log":
                logs[normalize.species(item)] += count
        if not logs:
            return [(normalize.resolve(target, inventory), action.times)]
        parts, remaining = [], action.times
        for species, count in sorted(logs.items(), key=lambda held: (-held[1], held[0])):
            if remaining:
                parts.append((f"{species}_planks", min(count, remaining)))
                remaining -= parts[-1][1]
        if remaining:  # not enough logs of any mix: let the bot report it
            parts[0] = (parts[0][0], parts[0][1] + remaining)
        return parts

    def _smelt_natural(self, action: Action) -> Outcome:
        inventory = self._known_inventory()
        try:
            request = {
                "ingredient": _held_member(action.ingredient or "", inventory),
                "item": normalize.resolve(action.target or "", inventory),
                "times": action.times,
                "fuel": _held_member(action.fuel or "", inventory),
            }
        except ValueError:
            return Outcome(ok=False, error="missing_ingredients", inventory=inventory)
        body = self._post("/smelt", request, timeout=SMELT_TIMEOUT_S + SMELT_TIMEOUT_PER_ITEM_S * action.times)
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory_delta=body.get("inventory_delta") or {},
            inventory=self._inventory(body),
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=_detail(
                body, "missing", "used_furnace", "furnace_retrieved", "fuel", "fuel_used", "item", "inventory_before_step", "detail"
            ),
            travel=body.get("travel") or {},
            events=body.get("events") or [],
        )

    def _explore_natural(self, action: Action) -> Outcome:
        request = None
        if action.direction == "up":  # back to open sky: the bot decides how far
            request = {"direction": "up", "dig_through": True, "scaffold": list(SCAFFOLD_BLOCKS)}
        elif action.direction:
            request = {
                "direction": action.direction,
                "distance": EXPLORE_DISTANCE,
                "dig_through": True,
                "scaffold": list(SCAFFOLD_BLOCKS),
            }
        body = self._post("/explore", request)
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory=self.observe().inventory,
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=_detail(body, "position", "sky"),
            travel=body.get("travel") or {},
            events=body.get("events") or [],
        )

    def _known_inventory(self) -> dict[str, int]:
        """The latest inventory the bot reported; ask once if none has been."""
        return self._latest_inventory if self._latest_inventory is not None else self.observe().inventory

    def _post(self, path: str, payload: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT_S) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise BackendError(f"bot server returned {error.code} for {path}: {error.read().decode()[:200]}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise BackendError(
                f"bot server not reachable at {self.api_url} ({error}). Start it with: npm --prefix mc/bot start"
            ) from error


def _observation(body: dict[str, Any]) -> Observation:
    position = body.get("position") or {"x": 0, "y": 0, "z": 0}
    return Observation(
        position=(position["x"], position["y"], position["z"]),
        inventory=body.get("inventory") or {},
        nearby={block: info["count"] for block, info in (body.get("nearby_blocks") or {}).items()},
        blocks_walked=body.get("blocks_walked", 0.0),
    )


def _detail(body: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The listed response fields that are set. "detail" is the bot's own
    explanation of a failure, such as why a crafting table could not be placed."""
    return {key: body[key] for key in keys if body.get(key) is not None}


def _merge_travel(travels: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum the travel reports of the bot calls that made up one action."""
    if not any(travels):
        return {}
    merged: dict[str, Any] = {"distance": 0.0, "dug": Counter(), "placed": Counter(), "picked_up": Counter()}
    for travel in travels:
        merged["distance"] += travel.get("distance", 0.0)
        for key in ("dug", "placed", "picked_up"):
            merged[key].update(travel.get(key) or {})
    return {key: dict(value) if isinstance(value, Counter) else value for key, value in merged.items()}


def _held_member(name: str, inventory: dict[str, int]) -> str:
    """A smelt's fuel or ingredient: for a group, the member held most (ties by name)."""
    if not name.startswith("#"):
        return name
    held = {member: inventory.get(member, 0) for member in normalize.members(name) if inventory.get(member, 0) > 0}
    if not held:
        raise ValueError(f"cannot resolve {name}: no matching item is held")
    return min(held, key=lambda member: (-held[member], member))
