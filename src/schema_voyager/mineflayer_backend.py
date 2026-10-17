"""Backend that drives the real Mineflayer bot through its HTTP API
(contracts/bot_api.md) and resets the arena over RCON."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .arena import Arena
from .layouts import Layout
from .models import Action, Observation, Outcome, Task

# Longer than the bot's own 120 s action timeout, so a slow action comes back
# as an error the episode can retry instead of a dropped connection.
REQUEST_TIMEOUT_S = 150


class BackendError(RuntimeError):
    pass


class MineflayerBackend:
    name = "minecraft"

    def __init__(self, api_url: str | None = None, arena: Arena | None = None):
        self.api_url = (api_url or os.environ.get("BOT_API_URL", "http://127.0.0.1:3000")).rstrip("/")
        self.arena = arena or Arena()

    def reset(self, task: Task, layout: Layout) -> Observation:
        self.arena.prepare_world()
        self.arena.set_world_rules(task.world_rules)
        self.arena.build(layout)
        self.arena.reset_player(task.start_inventory)
        return _observation(self._post("/reset_episode"))

    def observe(self) -> Observation:
        return _observation(self._post("/observe"))

    def act(self, action: Action) -> Outcome:
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
        return body["inventory"] if "inventory" in body else self.observe().inventory

    def say(self, message: str) -> None:
        try:
            self._post("/chat", {"message": message})
        except BackendError:
            pass  # narration is best effort

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
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
