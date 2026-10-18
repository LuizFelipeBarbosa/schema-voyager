"""The simulator's seeded threats, food, entities and deaths (v3.2i), and its terrain-block search."""

import json

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM
from schema_voyager.models import Action

FIGHT_EVENTS = ["threat", "fight_start", "damage", "fight_end"]


def _script(index: int) -> Action:
    """A fixed mix of actions, none of which needs anything held."""
    return [Action("mine", "oak_log"), Action("explore"), Action("craft", "oak_planks"), Action("mine", "dirt")][index % 4]


def _run(world: NaturalWorld, count: int) -> list:
    return [world.act(_script(index)) for index in range(count)]


@pytest.mark.parametrize("seed", range(8))
def test_a_rocky_column_does_not_break_the_search_for_grass_or_dirt(seed):
    world = NaturalWorld(seed)

    assert all(block in {"dirt", "grass_block"} for _, _, block in world._candidates({"dirt", "grass_block"}, 32))
    assert world.look(["grass_block"])
    assert world.act(Action("mine", "dirt")).ok


def test_an_unknown_difficulty_is_refused():
    with pytest.raises(ValueError):
        NaturalWorld(1, difficulty="brutal")


def test_peaceful_is_the_default_and_nothing_happens_on_it():
    default, peaceful = NaturalWorld(3), NaturalWorld(3, difficulty="peaceful")

    assert _run(default, 60) == _run(peaceful, 60)
    assert default.food == peaceful.food == 20
    assert not any(event["type"] in ("threat", "ate") for event in peaceful.status().events)
    assert all(entity["kind"] == "animal" for entity in peaceful.observe().entities.values())


def test_easy_interrupts_some_actions_with_a_fight_and_leaves_the_inventory_alone():
    world = NaturalWorld(3, difficulty="easy")
    interrupted = []
    for index in range(200):
        before = world._inventory()
        outcome = world.act(_script(index))
        if outcome.error == "interrupted":
            interrupted.append(outcome)
            assert outcome.inventory == before

    assert 5 <= len(interrupted) <= 40
    for outcome in interrupted:
        types = [event["type"] for event in outcome.events]
        threat = outcome.events[0]
        assert threat["reason"] == "near" and threat["action"].startswith("/")
        assert outcome.detail["threat"] == threat["mob"]
        if threat["mob"] == "creeper":
            assert types == ["threat", "fled"] and outcome.detail["outcome"] == "fled"
        else:
            assert types[:2] == FIGHT_EVENTS[:2] and types[-1] == "fight_end"
            assert set(types[2:-1]) == {"damage"}
            assert outcome.events[-1]["outcome"] == outcome.detail["outcome"]
    assert {event["seq"] for event in world.status().events} >= {e["seq"] for o in interrupted[-3:] for e in o.events}


def test_a_fight_that_takes_all_health_kills_the_bot():
    world = NaturalWorld(3, difficulty="hard")
    world.inventory["apple"] = 2
    for index in range(300):
        world.health = 1.0
        outcome = world.act(_script(index))
        if outcome.error == "quarantined":
            break
    else:
        pytest.fail("no fight killed the bot")

    assert [event["type"] for event in outcome.events][-3:] == ["fight_end", "death", "respawn"]
    assert outcome.events[-3]["outcome"] == "died"
    assert world.health == 20 and world._cell == world._spawn


def test_food_falls_and_a_held_apple_is_eaten_at_14_or_less():
    world = NaturalWorld(3, difficulty="easy")
    foods = []
    for index in range(80):
        world.act(_script(index))
        foods.append(world.food)
    assert foods[-1] < 20 and foods == sorted(foods, reverse=True)

    world.food = 14
    world.inventory["apple"] = 2
    world.act(Action("explore"))

    assert world.inventory["apple"] == 1 and world.food >= 17
    ate = [event for event in world.status().events if event["type"] == "ate"]
    assert ate[-1]["item"] == "apple" and ate[-1]["food"] == world.food
    assert world.status().food == world.food


def test_inedible_food_is_not_eaten():
    world = NaturalWorld(3, difficulty="easy")
    world.food = 3
    world.inventory.update({"rotten_flesh": 4, "spider_eye": 1})

    world.act(Action("explore"))

    assert world.inventory["rotten_flesh"] == 4 and world.food <= 3


def test_entities_depend_only_on_the_seed_difficulty_and_position():
    world = NaturalWorld(5, difficulty="normal")
    first = world.observe().entities
    assert world.observe().entities == first
    assert NaturalWorld(5, difficulty="normal").observe().entities == first

    seen = set()
    for step in range(40):
        world._cell = (step * 16, 12, 0)
        seen |= {(name, entity["kind"]) for name, entity in world.observe().entities.items()}
    assert {kind for _, kind in seen} == {"animal", "hostile"}


def test_die_respawns_at_the_spawn_and_keeps_the_inventory():
    world = NaturalWorld(2)
    spawn = world.observe().position
    world.inventory["cobblestone"] = 7
    world.act(Action("explore", direction="E"))
    world.health, world.food = 3.0, 4
    assert world.observe().position != spawn

    world.die()

    assert [event["type"] for event in world.status().events[-2:]] == ["death", "respawn"]
    assert world.observe().position == spawn
    assert (world.status().health, world.status().food) == (20, 20)
    assert world.inventory["cobblestone"] == 7


def _life(tmp_path, difficulty):
    world = NaturalWorld(1, difficulty=difficulty)
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-threats-test", max_goals=15)
    run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config)
    goals = [json.loads(line) for line in (tmp_path / "life.jsonl").read_text().splitlines()]
    events = [json.loads(line) for path in sorted((tmp_path / "episodes").glob("*.jsonl")) for line in path.read_text().splitlines()]
    return goals, events


def test_a_fifteen_goal_life_on_easy_survives_its_interrupts(tmp_path):
    peaceful_goals, _ = _life(tmp_path / "peaceful", "peaceful")
    goals, events = _life(tmp_path / "easy", "easy")

    assert {goal["outcome"] for goal in peaceful_goals} == {"success"}
    assert {goal["outcome"] for goal in goals} == {"success"}
    interrupted = [e for e in events if e["kind"] == "step" and e["error"] == "interrupted"]
    assert interrupted
    # The verifier either sets the step aside (quarantined, failed) or never checked it (a retry's explore).
    assert all(step.get("check") not in ("match", "mismatch", "stochastic") for step in interrupted)
    assert not any(event["kind"] == "contradiction" for event in events)
    store = json.loads((tmp_path / "easy" / "store.json").read_text())
    assert not any(demoted for demoted in store["demoted_in"].values())
    assert all(rule["status"] != "demoted" for rule in store["rules"].values()) if isinstance(store.get("rules"), dict) else True
