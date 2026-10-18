"""Several rule changes per life: the schedule of ChangeTrigger, the planks2 pack, and a
whole simulated life under `scramble` that passes change_report for every change."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from schema_voyager import change_report
from schema_voyager.environment import NaturalWorld
from schema_voyager.life import ChangeTrigger, LifeConfig, LifeHooks, parse_changes, run_life
from schema_voyager.llm import LLM
from schema_voyager.models import Action
from schema_voyager.schema_store import SchemaStore

ROOT = Path(__file__).resolve().parents[1]
STICK = SimpleNamespace(action=Action("craft", "stick"), rule=SimpleNamespace(status="certified", evidence={}))
PLANKS = SimpleNamespace(action=Action("craft", "oak_planks"), rule=SimpleNamespace(status="certified", evidence={}))


def _episode(goal="g001"):
    events = []
    log = SimpleNamespace(event=lambda kind, **data: events.append(kind))
    return SimpleNamespace(task=SimpleNamespace(task_id=goal), log=log), events


def _revision(action, subject):
    return SimpleNamespace(source="revision", status="certified", epoch=1, kind=action, subject=subject)


def test_parse_changes_expands_scramble_and_rejects_unknown_names():
    assert parse_changes("scramble") == ["stick2", "planks2", "r2"]
    assert parse_changes("r2") == ["r2"]
    assert parse_changes("planks2, r2") == ["planks2", "r2"]
    with pytest.raises(ValueError, match="nope"):
        parse_changes("r2,nope")


def test_a_change_waits_for_the_open_one_to_be_revised(tmp_path):
    switched = []
    trigger = ChangeTrigger(["stick2", "planks2"], tmp_path, switched.append)
    store = SchemaStore()
    trigger.bind_store(store)
    episode, events = _episode()

    trigger(episode, STICK, [STICK])
    trigger(episode, PLANKS, [PLANKS])  # stick2 is still open
    assert switched == ["stick2"]

    store.rules["craft_stick.rev1"] = _revision("craft", "stick")
    trigger(episode, PLANKS, [PLANKS])
    assert switched == ["stick2", "planks2"]
    assert events == ["world_change"] * 2
    assert [json.loads(line)["change"] for line in (tmp_path / "changes.jsonl").read_text().splitlines()] == switched


def test_a_change_opens_the_way_after_the_window(tmp_path, monkeypatch):
    switched = []
    trigger = ChangeTrigger(["stick2", "planks2"], tmp_path, switched.append)
    trigger(_episode()[0], STICK, [STICK])
    trigger(_episode()[0], PLANKS, [PLANKS])
    assert switched == ["stick2"]

    real_time = time.time
    monkeypatch.setattr("schema_voyager.life.time.time", lambda: real_time() + 121)
    trigger(_episode()[0], PLANKS, [PLANKS])
    assert switched == ["stick2", "planks2"]


def test_list_order_does_not_matter_for_the_step_that_bites_and_resume_keeps_the_fired(tmp_path):
    first = ChangeTrigger(["r2", "stick2", "planks2"], tmp_path, lambda change: None)
    first(_episode()[0], PLANKS, [PLANKS])
    assert first.fired == ["planks2"]

    switched = []
    resumed = ChangeTrigger(["r2", "stick2", "planks2"], tmp_path, switched.append, resume=True)
    assert resumed.fired == ["planks2"]
    resumed(_episode()[0], PLANKS, [PLANKS])
    assert switched == []


def test_the_planks2_pack_overrides_every_four_plank_recipe_with_two():
    recipes = sorted((ROOT / "mc/datapacks/v2_planks2/data/minecraft/recipe").glob("*_planks.json"))
    assert len(recipes) == 10 and "bamboo_planks.json" not in {path.name for path in recipes}
    for path in recipes:
        recipe = json.loads(path.read_text())
        assert recipe["result"] == {"id": f"minecraft:{path.stem}", "count": 2}
        assert recipe["type"] == "minecraft:crafting_shapeless" and recipe["group"] == "planks"


def test_a_simulated_scramble_life_fires_all_three_and_passes_each(tmp_path, capsys):
    world = NaturalWorld(seed=1)
    trigger = ChangeTrigger("scramble", tmp_path, lambda name: world.set_world_rules((*trigger.fired, name)))
    hooks = LifeHooks(before_step=trigger, bind_store=trigger.bind_store)
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim1-test", max_goals=40)
    run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, hooks)

    capsys.readouterr()
    assert change_report.main([str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert sorted(trigger.fired) == ["planks2", "r2", "stick2"]
    assert lines[-1] == "changes: 3/3 PASS"
    assert not [line for line in lines if line.startswith("FAIL")]
