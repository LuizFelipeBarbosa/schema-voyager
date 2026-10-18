"""Builds and resets the walled arena on the real server over RCON."""

from __future__ import annotations

import os
import time

from rcon.source import Client

from .layouts import ARENA_RADIUS, GROUND_Y, Layout

DATAPACKS = {"r2": "file/r2_coal_tool"}
GAMERULES = {
    "doMobSpawning": "false",
    "doDaylightCycle": "false",
    "doWeatherCycle": "false",
    "doFireTick": "false",
    "randomTickSpeed": "0",
    "doTileDrops": "true",
    "announceAdvancements": "false",
    "doImmediateRespawn": "true",
}
_CLEAR_TOP = GROUND_Y + 6
_WALL_HEIGHT = 3


class Arena:
    def __init__(self, bot_username: str | None = None):
        self.host = os.environ.get("MC_HOST", "127.0.0.1")
        self.port = int(os.environ.get("RCON_PORT", "25575"))
        self.password = os.environ.get("RCON_PASSWORD", "schemavoyager")
        self.bot = bot_username or os.environ.get("BOT_USERNAME", "SchemaBot")
        self._world_prepared = False

    def run(self, *commands: str) -> list[str]:
        with Client(self.host, self.port, passwd=self.password) as client:
            return [client.run(command) for command in commands]

    def prepare_world(self) -> None:
        if self._world_prepared:
            return
        self.run(
            *(f"gamerule {rule} {value}" for rule, value in GAMERULES.items()),
            "time set day",
            "weather clear",
            "setworldspawn 0 -60 0",
        )
        self._world_prepared = True

    def set_world_rules(self, world_rules: tuple[str, ...]) -> None:
        """Switch changed-rule datapacks. Enabling one reloads data without a restart."""
        enabled = self.run("datapack list enabled")[0]
        changed = False
        for rule, pack in DATAPACKS.items():
            want = rule in world_rules
            if want != (pack in enabled):
                self.run(f'datapack {"enable" if want else "disable"} "{pack}"')
                changed = True
        if changed:
            time.sleep(2.0)

    def build(self, layout: Layout) -> None:
        r = ARENA_RADIUS
        wall_top = GROUND_Y + _WALL_HEIGHT - 1
        self.run(
            "kill @e[type=item]",
            "kill @e[type=experience_orb]",
            f"tp {self.bot} 0.5 {GROUND_Y} 0.5 0 0",
            f"fill {-r} {GROUND_Y} {-r} {r} {_CLEAR_TOP} {r} air",
            f"fill {-r} {GROUND_Y - 1} {-r} {r} {GROUND_Y - 1} {r} grass_block",
            f"fill {-r - 1} {GROUND_Y} {-r - 1} {r + 1} {wall_top} {-r - 1} barrier",
            f"fill {-r - 1} {GROUND_Y} {r + 1} {r + 1} {wall_top} {r + 1} barrier",
            f"fill {-r - 1} {GROUND_Y} {-r - 1} {-r - 1} {wall_top} {r + 1} barrier",
            f"fill {r + 1} {GROUND_Y} {-r - 1} {r + 1} {wall_top} {r + 1} barrier",
            *(f"setblock {b['x']} {b['y']} {b['z']} {_block_state(str(b['block']))}" for b in layout.blocks),
        )

    def reset_player(self, inventory: dict[str, int]) -> None:
        self.run(
            f"clear {self.bot}",
            *(f"give {self.bot} minecraft:{item} {count}" for item, count in inventory.items()),
            f"tp {self.bot} 0.5 {GROUND_Y} 0.5 0 0",
            "kill @e[type=item]",
            "kill @e[type=experience_orb]",
        )

    def spectate(self, player: str) -> list[str]:
        return self.run(f"gamemode spectator {player}", f"tp {player} 0 {GROUND_Y + 12} -18 0 35")


def _block_state(block: str) -> str:
    if block.endswith("_leaves"):
        return f"minecraft:{block}[persistent=true]"
    return f"minecraft:{block}"
