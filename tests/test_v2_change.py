"""The M3 trigger and its acceptance report: the trigger fires once and at the right
step, the live path sends the right RCON commands to a fake client, the datapacks
exist, and a simulated life per change passes all five criteria of change_report."""

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from schema_voyager import change_report, demo
from schema_voyager.environment import NaturalWorld
from schema_voyager.life import (
    ChangeTrigger, LifeConfig, LifeHooks, check_pack_ready, rcon_factory, rcon_switch, run_life,
)
from schema_voyager.llm import LLM
from schema_voyager.models import Action

ROOT = Path(__file__).resolve().parents[1]
PACKS = {"r2": "file/v2_coal_tool", "stick2": "file/v2_stick2"}


# -- the trigger on hand-made steps ---------------------------------------------------


def _step(kind, target, tool=None, status="certified", confirmed=("t1",)):
    rule = SimpleNamespace(status=status, evidence={"confirmed": list(confirmed)})
    return SimpleNamespace(action=Action(kind, target, tool=tool), rule=rule)


def _episode(goal="g008"):
    events = []
    log = SimpleNamespace(event=lambda kind, **data: events.append((kind, data)))
    return SimpleNamespace(task=SimpleNamespace(task_id=goal), log=log), events


def _trigger(tmp_path, change, resume=False):
    switched = []
    return ChangeTrigger(change, tmp_path, switched.append, resume), switched


@pytest.mark.parametrize("change, step", [
    ("r2", _step("mine", "coal_ore", "wooden_pickaxe")),
    ("r2", _step("mine", "deepslate_coal_ore", "wooden_pickaxe")),
    ("stick2", _step("craft", "stick")),
])
def test_the_trigger_fires_once_before_the_step_it_changes(tmp_path, change, step):
    trigger, switched = _trigger(tmp_path, change)
    episode, events = _episode("g012")
    trigger(episode, _step("craft", "planks"), [])
    assert switched == []
    trigger(episode, step, [step])
    trigger(episode, step, [step])
    assert switched == [change]

    (line,) = [json.loads(text) for text in (tmp_path / "changes.jsonl").read_text().splitlines()]
    assert set(line) == {"t", "change", "action", "subject", "goal"}
    assert (line["change"], line["goal"]) == (change, "g012")
    assert (line["action"], line["subject"]) == (("mine", "coal_ore") if change == "r2" else ("craft", "stick"))
    assert [kind for kind, _ in events] == ["world_change"]


@pytest.mark.parametrize("change, step", [
    ("r2", _step("mine", "coal_ore", "stone_pickaxe")),
    ("r2", _step("mine", "coal_ore", "wooden_pickaxe", status="hypothesis", confirmed=())),
    ("r2", _step("mine", "iron_ore", "wooden_pickaxe")),
    ("stick2", _step("craft", "stick", status="hypothesis")),
    ("stick2", _step("craft", "torch")),
])
def test_the_trigger_ignores_steps_the_change_does_not_touch(tmp_path, change, step):
    trigger, switched = _trigger(tmp_path, change)
    trigger(_episode()[0], step, [step])
    assert switched == []
    assert not (tmp_path / "changes.jsonl").exists()


def test_a_resumed_life_does_not_fire_again_but_a_fresh_one_does(tmp_path):
    step = _step("craft", "stick")
    first, _ = _trigger(tmp_path, "stick2")
    first(_episode()[0], step, [step])

    resumed, switched = _trigger(tmp_path, "stick2", resume=True)
    resumed(_episode()[0], step, [step])
    assert switched == []

    fresh, switched = _trigger(tmp_path, "stick2")
    fresh(_episode()[0], step, [step])
    assert switched == ["stick2"]
    assert len((tmp_path / "changes.jsonl").read_text().splitlines()) == 1


# -- the live path, with a fake RCON client -------------------------------------------


class FakeRcon:
    """Answers `datapack list ...` from the packs it was told about; records every command."""

    def __init__(self, enabled=(), available=(), lag_polls=0):
        self.enabled, self.available, self.lag_polls = list(enabled), list(available), lag_polls
        self.commands = []

    def run(self, command):
        self.commands.append(command)
        if command.startswith("datapack enable"):
            pack = command.split('"')[1]
            self.available.remove(pack)
            self.pending = pack
            return f"Enabled data pack {pack}"
        if command == "datapack list enabled":
            if getattr(self, "pending", None) and self.lag_polls == 0:
                self.enabled.append(self.pending)
                self.pending = None
            self.lag_polls = max(self.lag_polls - 1, 0)
            return "There are %d data pack(s) enabled: %s" % (len(self.enabled), ", ".join(f"[{p}]" for p in self.enabled))
        if command == "datapack list available":
            return "There are %d data pack(s) available: %s" % (len(self.available), ", ".join(f"[{p}]" for p in self.available))
        raise AssertionError(command)

    def factory(self):
        @contextmanager
        def connect():
            yield self

        return connect


@pytest.mark.parametrize("change", PACKS)
def test_the_live_switch_enables_the_pack_and_waits_until_it_is_listed(change):
    client = FakeRcon(enabled=["vanilla"], available=[PACKS[change]], lag_polls=1)
    rcon_switch(client.factory(), settle_s=0)(change)
    assert client.commands[0] == f'datapack enable "{PACKS[change]}"'
    assert client.commands.count("datapack list enabled") == 2
    assert all(command.startswith("datapack") for command in client.commands)


def test_the_live_switch_gives_up_when_the_pack_never_shows(monkeypatch):
    client = FakeRcon(available=[PACKS["r2"]], lag_polls=10**6)
    monkeypatch.setattr("schema_voyager.life.PACK_POLL_TIMEOUT_S", 0.0)
    with pytest.raises(TimeoutError):
        rcon_switch(client.factory(), settle_s=0)("r2")


def test_the_pack_check_refuses_a_missing_or_enabled_pack():
    check_pack_ready("r2", FakeRcon(enabled=["vanilla"], available=[PACKS["r2"]]).factory())
    with pytest.raises(ValueError, match="scripts/v2-change.sh install"):
        check_pack_ready("r2", FakeRcon(enabled=["vanilla"], available=[]).factory())
    with pytest.raises(ValueError, match="already enabled"):
        check_pack_ready("r2", FakeRcon(enabled=["vanilla", PACKS["r2"]]).factory())


def test_the_live_path_never_opens_the_demos_rcon_port():
    with pytest.raises(ValueError, match="25575"):
        rcon_factory(25575)


def _life_args(tmp_path, **overrides):
    values = dict(backend="mc", llm="fake", run_dir=tmp_path / "run", resume=False, goals=1, minutes=1, cap_usd=1,
                  seed="s", sim_seed=1, change="r2")
    return argparse.Namespace(**{**values, **overrides})


def test_the_life_command_refuses_to_start_when_the_pack_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("SV2_BOT_PORT", "3200")
    monkeypatch.setenv("SV2_RCON_PORT", "25576")
    monkeypatch.setattr(demo, "rcon_factory", lambda port: FakeRcon(enabled=["vanilla"]).factory())
    with pytest.raises(SystemExit, match="v2-change.sh install"):
        demo.run_life_command(_life_args(tmp_path))
    with pytest.raises(SystemExit, match="unknown change"):
        demo.run_life_command(_life_args(tmp_path, change="nope"))


def test_the_change_comes_from_the_environment_when_the_flag_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("SV2_M3_CHANGE", "stick2")
    run_dir = tmp_path / "run"
    demo.run_life_command(_life_args(tmp_path, backend="sim", seed=None, goals=14, sim_seed=2, change=None))
    assert json.loads((run_dir / "changes.jsonl").read_text().splitlines()[0])["change"] == "stick2"


# -- the datapacks -------------------------------------------------------------------


def test_the_datapacks_exist_for_1_21_1():
    packs = ROOT / "mc" / "datapacks"
    for name in ("v2_coal_tool", "v2_stick2"):
        assert json.loads((packs / name / "pack.mcmeta").read_text())["pack"]["pack_format"] == 48

    for ore in ("coal_ore", "deepslate_coal_ore"):
        table = json.loads((packs / "v2_coal_tool/data/minecraft/loot_table/blocks" / f"{ore}.json").read_text())
        tools = table["pools"][0]["conditions"][0]["predicate"]["items"]
        assert "minecraft:wooden_pickaxe" not in tools and "minecraft:stone_pickaxe" in tools

    recipe = json.loads((packs / "v2_stick2/data/minecraft/recipe/stick.json").read_text())
    assert recipe["result"] == {"id": "minecraft:stick", "count": 2}
    assert recipe["pattern"] == ["#", "#"] and recipe["key"] == {"#": {"tag": "minecraft:planks"}}


# -- a whole simulated life per change ------------------------------------------------


@pytest.mark.parametrize("change, goals", [("r2", 25), ("stick2", 25)])
def test_a_simulated_life_passes_all_five_criteria(tmp_path, capsys, change, goals):
    world = NaturalWorld(seed=2)
    trigger = ChangeTrigger(change, tmp_path, lambda name: world.set_world_rules((name,)))
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim2-test", max_goals=goals)
    run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(before_step=trigger))

    capsys.readouterr()  # the life's own trace
    assert change_report.main([str(tmp_path)]) == 0
    verdicts = [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]]
    assert verdicts == ["PASS"] * 5


def test_the_report_fails_without_a_change(tmp_path, capsys):
    assert change_report.main([str(tmp_path)]) == 1
    assert capsys.readouterr().out.startswith("FAIL")
