"""Difficulty and daylight settings of scripts/v2-env.sh (v3.2f). Only sources the file; never runs a server."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _bash(home: Path, script: str, **env) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith("SV2_")}
    return subprocess.run(
        ["bash", "-c", f"source {ROOT}/scripts/v2-env.sh; {script}"], cwd=ROOT, capture_output=True, text=True,
        env={**base, "SV2_HOME": str(home), **env}, timeout=30,
    )


def test_defaults_keep_peaceful_and_fixed_daylight(tmp_path):
    properties = _bash(tmp_path, "sv2_server_properties world 1001", RCON_PASSWORD="pw")
    commands = _bash(tmp_path, "sv2_gamerule_commands")
    assert properties.returncode == 0 and commands.returncode == 0
    assert "difficulty=peaceful" in properties.stdout.splitlines()
    assert commands.stdout.splitlines() == [
        "gamerule keepInventory true", "difficulty peaceful", "gamerule doDaylightCycle false", "time set day",
        "gamerule doWeatherCycle false", "gamerule spawnRadius 0", "gamerule announceAdvancements false",
        "weather clear",
    ]


def test_easy_with_daylight_on(tmp_path):
    env = {"SV2_DIFFICULTY": "easy", "SV2_DAYLIGHT": "on", "RCON_PASSWORD": "pw"}
    properties = _bash(tmp_path, "sv2_server_properties world 1001", **env)
    commands = _bash(tmp_path, "sv2_gamerule_commands", **env).stdout.splitlines()
    assert "difficulty=easy" in properties.stdout.splitlines()
    assert "difficulty easy" in commands
    assert "gamerule doDaylightCycle true" in commands
    assert "time set day" not in commands and "gamerule doDaylightCycle false" not in commands


def test_properties_text_is_otherwise_unchanged(tmp_path):
    env = {"SV2_MC_PORT": "25566", "SV2_RCON_PORT": "25576", "RCON_PASSWORD": "pw"}
    result = _bash(tmp_path, "sv2_server_properties world-7 7", **env)
    assert result.stdout.splitlines() == [
        "server-port=25566", "enable-rcon=true", "rcon.port=25576", "rcon.password=pw",
        "broadcast-rcon-to-ops=false", "online-mode=false", "difficulty=peaceful", "spawn-protection=0",
        "gamemode=survival", "level-name=world-7", "level-seed=7",
        "view-distance=8", "simulation-distance=6",
    ]


@pytest.mark.parametrize("name, value", [("SV2_DIFFICULTY", "nightmare"), ("SV2_DAYLIGHT", "maybe")])
def test_invalid_value_stops_at_source_time(tmp_path, name, value):
    result = _bash(tmp_path, "echo started", **{name: value})
    assert result.returncode != 0
    assert name in result.stderr
    assert "started" not in result.stdout
