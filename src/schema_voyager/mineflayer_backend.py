"""Backend that drives the real Mineflayer bot through its HTTP API
(contracts/bot_api.md) and resets the arena over RCON."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from . import normalize
from .adapter import DIRECTIONS, LookResult
from .arena import Arena
from .blueprint import Box
from .inventory import SCAFFOLD_BLOCKS
from .layouts import Layout
from .models import Action, Observation, Outcome, Site, Status, Task

# Longer than an interrupted action's worst case (the bot's own 120 s timeout, up to
# 10 s for the handler to settle, then up to 30 s of fighting), so a slow action comes
# back as an error the episode can retry instead of a dropped connection.
REQUEST_TIMEOUT_S = 180
SMELT_TIMEOUT_S = 150  # plus SMELT_TIMEOUT_PER_ITEM_S per item smelted
SMELT_TIMEOUT_PER_ITEM_S = 12

# A 409 busy (an action or the guard's fight holds the bot) is retried: after the first
# wait, then every second one, until this long after the first try.
BUSY_FIRST_WAIT_S = 0.25
BUSY_WAIT_S = 0.5
BUSY_GIVE_UP_S = 60

# Injectable for tests.
_sleep = time.sleep
_monotonic = time.monotonic

MINE_MAX_DISTANCE = 32
EXPLORE_DISTANCE = 32
GOTO_RANGE = 3


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
        self._keep_out: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
        self._protected: list[Box] = []

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
        if action.kind in ("place", "clear"):
            return self._build_step(action)
        if action.kind == "goto":
            return self._goto(action)
        if self.natural:
            return self._act_natural(action)
        if action.kind == "mine":
            body = self._post("/mine", self._with_protect({"block": action.target, "tool": action.tool}))
            return Outcome(
                ok=body["ok"],
                error=body.get("error"),
                block_removed=body.get("block_removed"),
                drops=body.get("drops") or {},
                picked_up=body.get("picked_up") or {},
                inventory_delta=body.get("picked_up") or {},
                inventory=self._inventory(body),
                blocks_walked=body.get("blocks_walked", 0.0),
                detail=_detail(body, "position", "target", "tool", "detail"),
            )
        if action.kind == "craft":
            body = self._post("/craft", {"item": action.target, "times": action.times})
            return Outcome(
                ok=body["ok"],
                error=body.get("error"),
                inventory_delta=body.get("inventory_delta") or {},
                inventory=self._inventory(body),
                blocks_walked=body.get("blocks_walked", 0.0),
                detail=_detail(body, "position", "missing", "used_table", "table_retrieved", "detail"),
            )
        body = self._post("/explore", self._with_protect({}))
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory=self.observe().inventory,
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=_detail(body, "position"),
        )

    # --- v3.4: building -------------------------------------------------------

    def set_keep_out(self, box: tuple[tuple[int, int, int], tuple[int, int, int]] | None) -> None:
        self._keep_out = box

    def set_protected(self, boxes: list[Box]) -> None:
        self._protected = list(boxes)

    def _with_protect(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The payload with the protected boxes (v3.4b), when there are any."""
        if not self._protected:
            return payload
        boxes = [{"origin": list(origin), "size": list(size)} for origin, size in self._protected]
        return payload | {"protect": boxes}

    def scan(self, origin: tuple[int, int, int], size: tuple[int, int, int]) -> dict[tuple[int, int, int], str | None]:
        body = self._post("/scan", {"origin": list(origin), "size": list(size)})
        ox, oy, oz = origin
        return {
            (ox + x, oy + y, oz + z): name
            for y, layer in enumerate(body["layers"])
            for z, row in enumerate(layer)
            for x, name in enumerate(row)
        }

    def survey(self, size: tuple[int, int], radius: int = 24) -> list[Site]:
        payload: dict[str, Any] = {"size": list(size), "radius": radius}
        body = self._post("/survey", self._with_protect(payload))
        return [
            Site(tuple(site["origin"]), site["cut"], site["fill"], float(site["distance"])) for site in body["sites"]
        ]

    def _goto(self, action: Action) -> Outcome:
        """A walk near a cell, the same in both modes."""
        if action.pos is None:
            raise ValueError("a goto action needs a pos")
        x, y, z = action.pos
        body = self._post("/goto", self._with_protect({"x": x, "y": y, "z": z, "range": GOTO_RANGE}))
        inventory = self._inventory(body)
        before = body.get("inventory_before_step")
        travel = body.get("travel") or {}
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory_delta=_difference(inventory, before) if before is not None else {},
            inventory=inventory,
            blocks_walked=travel.get("distance", 0.0),
            detail=_detail(body, "arrived", "inventory_before_step", "detail") | {"target": {"x": x, "y": y, "z": z}},
            travel=travel,
            events=body.get("events") or [],
        )

    def _build_step(self, action: Action) -> Outcome:
        """A place or a clear of one cell, the same in both modes."""
        if action.pos is None:
            raise ValueError(f"a {action.kind} action needs a pos")
        x, y, z = action.pos
        payload: dict[str, Any] = {"x": x, "y": y, "z": z, "scaffold": list(SCAFFOLD_BLOCKS)}
        if self._keep_out is not None:
            origin, size = self._keep_out
            payload["keep_out"] = {"origin": list(origin), "size": list(size)}
        payload = self._with_protect(payload)
        if action.kind == "place":
            body = self._post("/place", {"block": action.target} | payload)
            detail_keys = ("block", "placed", "found", "inventory_before_step", "detail")
        else:
            body = self._post("/clear", payload)
            detail_keys = ("cleared", "removed", "found", "inventory_before_step", "detail")
        inventory = self._inventory(body)
        before = body.get("inventory_before_step")
        travel = body.get("travel") or {}
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory_delta=_difference(inventory, before) if before is not None else {},
            inventory=inventory,
            blocks_walked=travel.get("distance", 0.0),
            detail=_detail(body, *detail_keys) | {"target": {"x": x, "y": y, "z": z}},
            travel=travel,
            events=body.get("events") or [],
        )

    def _inventory(self, body: dict[str, Any]) -> dict[str, int]:
        """The inventory an action reported; failures such as a timeout report none, so ask."""
        if "inventory" in body:
            self._latest_inventory = body["inventory"]
            return body["inventory"]
        return self.observe().inventory

    def say(self, message: str) -> None:
        try:
            self._post("/chat", {"message": message}, retry_busy=False)  # a busy bot drops it
        except BackendError:
            pass  # narration is best effort

    # --- v2: natural mode ----------------------------------------------------

    def begin_episode(self) -> Observation:
        observation = _observation(self._post("/reset_episode"))
        self._latest_inventory = observation.inventory
        return observation

    def toss(self, items: dict[str, int]) -> dict[str, int]:
        if not items:
            return {}
        body = self._post("/toss", {"items": items})
        if "inventory" in body:
            self._latest_inventory = body["inventory"]
        return {item: count for item, count in (body.get("tossed") or {}).items() if count > 0}

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
        body = self._post("/look", self._with_protect({"blocks": blocks, "radius": radius}))
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
        body = self._post("/mine", self._with_protect({
            "block": blocks[0],
            "blocks": blocks,
            "tool": action.tool,
            "dig_through": True,
            "max_distance": MINE_MAX_DISTANCE,
            "scaffold": list(SCAFFOLD_BLOCKS),
        }))
        detail = _detail(body, "position", "target", "block", "tool", "inventory_before_step", "detail")
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
        detail.update(_detail(bodies[-1], "position"))  # where the last call left the bot
        for key in ("missing", "used_table", "table_retrieved", "detail"):
            values = [body[key] for body in bodies if body.get(key) is not None]
            if values:
                detail[key] = values[-1] if key != "table_retrieved" else all(values)
        if failed and (interruption := _interruption(failed)):
            detail.pop("detail", None)
            detail.update(interruption)
        tools_broken = [item for body in bodies for item in body.get("tools_broken") or []]
        if tools_broken:
            detail["tools_broken"] = tools_broken
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
        detail = _detail(
            body, "position", "missing", "used_furnace", "furnace_retrieved", "fuel", "fuel_used", "smelted", "item", "inventory_before_step", "detail"
        )
        if body.get("tools_broken"):
            detail["tools_broken"] = body["tools_broken"]
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory_delta=body.get("inventory_delta") or {},
            inventory=self._inventory(body),
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=detail,
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
        body = self._post("/explore", self._with_protect(request or {}))
        return Outcome(
            ok=body["ok"],
            error=body.get("error"),
            inventory=self.observe().inventory,
            blocks_walked=body.get("blocks_walked", 0.0),
            detail=_detail(body, "position", "sky", "took"),
            travel=body.get("travel") or {},
            events=body.get("events") or [],
        )

    def _known_inventory(self) -> dict[str, int]:
        """The latest inventory the bot reported; ask once if none has been."""
        return self._latest_inventory if self._latest_inventory is not None else self.observe().inventory

    def _post(
        self, path: str, payload: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT_S, *, retry_busy: bool = True
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        first_try = _monotonic()
        wait = BUSY_FIRST_WAIT_S
        while True:
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                text = error.read().decode()
                if retry_busy and error.code == 409 and _is_busy(text) and _monotonic() - first_try < BUSY_GIVE_UP_S:
                    _sleep(wait)
                    wait = BUSY_WAIT_S
                    continue
                raise BackendError(f"bot server returned {error.code} for {path}: {text[:200]}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                raise BackendError(
                    f"bot server not reachable at {self.api_url} ({error}). Start it with: npm --prefix mc/bot start"
                ) from error


def _is_busy(error_text: str) -> bool:
    try:
        body = json.loads(error_text)
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("error") == "busy"


def _observation(body: dict[str, Any]) -> Observation:
    position = body.get("position") or {"x": 0, "y": 0, "z": 0}
    free_slots = body.get("free_slots")
    return Observation(
        position=(position["x"], position["y"], position["z"]),
        inventory=body.get("inventory") or {},
        nearby={block: info["count"] for block, info in (body.get("nearby_blocks") or {}).items()},
        blocks_walked=body.get("blocks_walked", 0.0),
        free_slots=free_slots if isinstance(free_slots, int) else None,
        entities=_entities(body.get("nearby_entities")),
    )


def _entities(nearby_entities: Any) -> dict[str, dict[str, Any]]:
    """/observe's nearby_entities, without the entries that are malformed (it is never part of a check)."""
    if not isinstance(nearby_entities, dict):
        return {}
    entities = {}
    for entity_type, info in nearby_entities.items():
        if not isinstance(info, dict):
            continue
        count, nearest, kind = info.get("count"), info.get("nearest"), info.get("kind")
        if _is_number(count, int) and _is_number(nearest, (int, float)) and isinstance(kind, str):
            entities[entity_type] = {"count": count, "nearest": float(nearest), "kind": kind}
    return entities


def _is_number(value: Any, types: type | tuple[type, ...]) -> bool:
    return isinstance(value, types) and not isinstance(value, bool)


def _detail(body: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The listed response fields that are set. "detail" is the bot's own
    explanation of a failure, such as why a crafting table could not be placed.
    An interrupted action's "detail" is a dict, which goes in flat (threat, outcome)."""
    interruption = _interruption(body)
    found = {key: body[key] for key in keys if body.get(key) is not None and not (key == "detail" and interruption)}
    return found | interruption


def _interruption(body: dict[str, Any]) -> dict[str, Any]:
    """The threat and outcome of an interrupted action's body; {} for any other."""
    if body.get("error") != "interrupted" or not isinstance(body.get("detail"), dict):
        return {}
    return {key: body["detail"][key] for key in ("threat", "outcome") if key in body["detail"]}


def _difference(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    """after minus before, without the items that did not change."""
    return {item: change for item in after.keys() | before.keys() if (change := after.get(item, 0) - before.get(item, 0))}


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
