"""Home stations and chest contents in the planner and the agent (v3.5d): HomeStock.near, make_plan's stations,
withdrawals, and pursue in the simulator with the fake LLM."""

import time

import pytest

from schema_voyager.agent import pursue
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.models import Action, HomeStock, Task
from schema_voyager.planner import make_plan, withdrawals
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog

WORLD_ID = "w"
FAMILIES = ["#planks", "#log", "stick", "torch", "dirt", "coal", "coal_ore", "crafting_table", "wooden_pickaxe",
            "grass_block"]


@pytest.fixture
def llm(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl")


@pytest.fixture
def store(llm):
    store = SchemaStore()
    for rule in llm.propose_prior_rules_v2(FAMILIES, [], WORLD_ID):
        store.add_rule(rule)
    return store


# --- HomeStock.near ---------------------------------------------------------------------


def _stock(**changes):
    fields = {
        "stations": {"crafting_table": (10, 64, 0), "furnace": (100, 64, 0)}, "chest": (0, 64, 10),
        "stored": {"coal": 4},
    }
    return HomeStock(**{**fields, **changes})


def test_near_returns_the_kinds_and_the_stock_within_reach():
    assert _stock().near((0.0, 64.0, 0.0)) == (frozenset({"crafting_table"}), {"coal": 4})


def test_near_measures_in_3d_to_the_cell_centre():
    stock = _stock(stations={"crafting_table": (3, 64, 0)})
    assert stock.near((0.0, 64.0, 0.0), reach=3.6)[0] == frozenset({"crafting_table"})  # 3.57 to the centre
    assert stock.near((0.0, 64.0, 0.0), reach=3.5)[0] == frozenset()
    assert stock.near((0.0, 70.0, 0.0), reach=5.6)[0] == frozenset()  # 6 up and 3.5 along: 6.9 away


def test_near_leaves_out_a_chest_beyond_reach():
    assert _stock().near((0.0, 64.0, 100.0)) == (frozenset(), {})


def test_near_never_returns_a_lost_or_skipped_kind():
    assert _stock(lost=frozenset({"crafting_table"})).near((0.0, 64.0, 0.0)) == (frozenset(), {"coal": 4})
    assert _stock(skipped=frozenset({"chest"})).near((0.0, 64.0, 0.0)) == (frozenset({"crafting_table"}), {})
    assert _stock(lost=frozenset({"chest"})).near((0.0, 64.0, 0.0))[1] == {}


def test_near_without_a_chest_has_no_stock():
    assert _stock(chest=None).near((0.0, 64.0, 0.0))[1] == {}


# --- make_plan's stations ---------------------------------------------------------------


def _craft_targets(plan):
    return [step.action.target for step in plan if step.action.kind == "craft"]


def test_a_home_table_needs_no_crafting_table_step(store):
    rules = store.usable_rules(WORLD_ID)
    held = {"#planks": 3, "stick": 2}

    with_table = make_plan("wooden_pickaxe", 1, held, rules, store.skill_for, stations=frozenset({"crafting_table"}))
    without = make_plan("wooden_pickaxe", 1, held, rules, store.skill_for)

    assert _craft_targets(with_table) == ["wooden_pickaxe"]
    assert "crafting_table" in _craft_targets(without)
    assert without == make_plan("wooden_pickaxe", 1, held, rules, store.skill_for, stations=frozenset())


def test_a_held_table_stays_available_with_stations(store):
    plan = make_plan("wooden_pickaxe", 1, {"#planks": 3, "stick": 2, "crafting_table": 1}, store.usable_rules(WORLD_ID),
                     store.skill_for, stations=frozenset({"furnace"}))
    assert _craft_targets(plan) == ["wooden_pickaxe"]


# --- withdrawals ------------------------------------------------------------------------


def test_withdrawals_draws_an_ingredient_below_held(store):
    start = {"coal": 3, "stick": 1}
    plan = make_plan("torch", 1, start, store.usable_rules(WORLD_ID), store.skill_for)
    assert withdrawals(plan, start, {"stick": 1}, {"torch": 1}) == {"coal": 1}


def test_withdrawals_takes_the_goal_item_sitting_in_the_chest():
    assert withdrawals([], {"torch": 2}, {}, {"torch": 2}) == {"torch": 2}


def test_withdrawals_is_empty_when_held_covers_the_plan(store):
    start = {"coal": 3, "stick": 1}
    plan = make_plan("torch", 1, start, store.usable_rules(WORLD_ID), store.skill_for)
    assert withdrawals(plan, start, start, {"torch": 1}) == {}


def test_withdrawals_are_capped_at_what_is_stored():
    assert withdrawals([], {"coal": 3, "dirt": 5}, {"dirt": 5}, {"coal": 5}) == {"coal": 3}


# --- pursue in the simulator --------------------------------------------------------------


def _home_world():
    """A world with a 7x7 grass floor and air above it, whose cells come back as (x, y, z)."""
    world = NaturalWorld(7)
    ground = world._column(0, 0)[0]
    for x in range(-2, 8):
        for z in range(-2, 8):
            world._changes[(x, ground, z)] = "grass_block"
            for y in range(ground + 1, ground + 5):
                world._changes[(x, y, z)] = "air"
    return world, (2, ground + 1, 2), (4, ground + 1, 2)


def _episode(world, llm, goal_item, goal_count=1, budget=60):
    observation = world.observe()
    task = Task("g001", "train", goal_item, goal_count, dict(observation.inventory), (), budget, "test")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id=WORLD_ID, backend=world,
        llm=llm, log=EpisodeLog(None, "test", say=world.say), inventory=dict(observation.inventory), natural=True,
        deadline=time.monotonic() + 3000,
    )


def _chest_in(world, cell, **items):
    world.inventory["chest"] += 1
    assert world.act(Action("place", target="chest", pos=cell)).ok
    world.inventory.update(items)
    assert world.deposit(cell, items).error is None


def _steps(episode, kind):
    return [event for event in episode.log.events if event["kind"] == "step" and event["action_obj"]["kind"] == kind]


def _recorded_kinds(store):
    return {transition.action.kind for transition in store.transitions}


def test_pursue_withdraws_what_only_the_chest_has_then_crafts(llm, store):
    world, chest, _table = _home_world()
    world.inventory["stick"] = 1
    _chest_in(world, chest, coal=3)
    episode = _episode(world, llm, "torch")
    episode.home = HomeStock(chest=chest, stored={"coal": 3})

    pursue(episode, store, {"torch": 1})

    assert episode.goal_met
    withdraws = _steps(episode, "withdraw")
    assert [step["action"] for step in withdraws] == [f"withdraw coal from {chest[0]} {chest[1]} {chest[2]}"]
    assert episode.home.stored == {"coal": 2}
    assert [step["action_obj"]["kind"] for step in episode.log.events if step["kind"] == "step"] == ["withdraw", "craft"]
    assert "withdraw" not in _recorded_kinds(store)


def test_pursue_crafts_with_the_home_table(llm, store):
    world, _chest, table = _home_world()
    world._changes[table] = "crafting_table"
    world.set_stations({"crafting_table": table})
    world.inventory.update({"oak_planks": 3, "stick": 2})
    episode = _episode(world, llm, "wooden_pickaxe")
    episode.home = HomeStock(stations={"crafting_table": table})

    pursue(episode, store, {"wooden_pickaxe": 1})

    assert episode.goal_met
    crafts = _steps(episode, "craft")
    assert [step["action_obj"]["target"] for step in crafts] == ["wooden_pickaxe"]
    assert crafts[0]["detail"]["used_home_station"] is True


def test_pursue_gathers_instead_when_the_chest_is_unreachable(llm, store):
    world, chest, _table = _home_world()
    _chest_in(world, chest, oak_log=4)
    world.unreachable_cells.add(chest)
    episode = _episode(world, llm, "#log", goal_count=2)
    episode.home = HomeStock(chest=chest, stored={"oak_log": 4})

    pursue(episode, store, {"#log": 2})

    assert episode.goal_met
    assert episode.home.skipped == {"chest"}
    assert len({step["action"] for step in _steps(episode, "withdraw")}) == 1  # one withdraw (and its retry), no loop
    assert _steps(episode, "mine")


def test_pursue_crafts_a_table_when_the_home_table_is_gone(llm, store):
    world, _chest, table = _home_world()
    world.set_stations({"crafting_table": table})  # the cell holds no table
    world.inventory.update({"oak_planks": 7, "stick": 2})
    episode = _episode(world, llm, "wooden_pickaxe")
    episode.home = HomeStock(stations={"crafting_table": table})

    pursue(episode, store, {"wooden_pickaxe": 1})

    assert episode.goal_met
    assert episode.home.lost == {"crafting_table"}
    assert [step["action_obj"]["target"] for step in _steps(episode, "craft")] == [
        "wooden_pickaxe", "crafting_table", "wooden_pickaxe"
    ]


def test_pursue_does_not_ask_again_for_what_a_withdraw_came_back_short_of(llm, store):
    world, chest, _table = _home_world()
    _chest_in(world, chest, oak_log=1)
    episode = _episode(world, llm, "#log", goal_count=2)
    episode.home = HomeStock(chest=chest, stored={"oak_log": 5})  # the life believes the chest holds more

    pursue(episode, store, {"#log": 2})

    assert episode.goal_met
    assert len(_steps(episode, "withdraw")) == 1
    assert episode.home.stored == {}
    assert "withdraw" not in _recorded_kinds(store)


def test_pursue_without_a_home_never_withdraws(llm, store):
    world, _chest, _table = _home_world()
    episode = _episode(world, llm, "#log", goal_count=2)

    pursue(episode, store, {"#log": 2})

    assert episode.goal_met and not _steps(episode, "withdraw")
