"""Reaching home (v3.0i, yard-reach): a slot the bot can't walk to is remembered, not stalled on goal after goal.
`blames_slot`, the new `Places` fields, furnishing through `reach`, the move-then-drop of an unreachable station or
chest, and the chest goal waiting for a composite parent. Whole simulator lives with the fake LLM."""

import dataclasses
import json

import pytest

from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec, Outcome
from schema_voyager.places import SLOT_BLAME_BLOCKS, Places, blames_slot, yard_scan_box, yard_slots
from test_v3_home_life import (
    ChatWorld, ask, build_home, episode_events, furnished, life_events, places, run, state, write_places,
)

CELL = (10, 70, 20)
CENTRE = {"x": 10.5, "y": 70.5, "z": 20.5}


class ReachWorld(ChatWorld):
    """A world where `reach` stalls at `stalls[cell]` (an end position), and a chest or station walk to an
    `unreachable_cells` cell fails with `stall_end` as its position."""

    def __init__(self):
        super().__init__()
        self.stalls: dict[tuple, dict] = {}
        self.stall_end: dict | None = None
        self.reach_calls: list[tuple] = []

    def reach(self, pos):
        self.reach_calls.append(pos)
        if pos in self.stalls:
            return Outcome(ok=False, error="unreachable", detail={"position": self.stalls[pos]})
        return super().reach(pos)

    def act(self, action):
        outcome = super().act(action)
        if outcome.error == "unreachable" and self.stall_end:
            outcome.detail["position"] = self.stall_end
        return outcome

    def deposit(self, pos, items):
        result = super().deposit(pos, items)
        if result.error == "unreachable" and self.stall_end:
            return dataclasses.replace(result, detail={**result.detail, "position": self.stall_end})
        return result


def near(cell, dx=1.0):
    return {"x": cell[0] + 0.5 + dx, "y": cell[1] + 0.5, "z": cell[2] + 0.5}


# -- blames_slot and Places ----------------------------------------------------------------------


@pytest.mark.parametrize("position, blamed", [
    (CENTRE, True),
    ({"x": 10.5 + SLOT_BLAME_BLOCKS, "y": 70.5, "z": 20.5}, True),
    ({"x": 10.5 + SLOT_BLAME_BLOCKS + 0.1, "y": 70.5, "z": 20.5}, False),
    ({"x": 10.5, "y": 70.5 + 5.9, "z": 20.5}, True),  # 3D: height counts
    ({"x": 10.5, "y": 70.5 + 6.5, "z": 20.5}, False),
    ((10.5, 70.5, 22.0), True),
    ((500.0, 70.0, 500.0), False),
    (None, False),
    ({"x": 10.5}, False),
    ((1.0, 2.0), False),
    ("far", False),
])
def test_blames_slot(position, blamed):
    assert blames_slot(CELL, position) is blamed


def test_places_round_trips_the_reach_fields_and_an_old_state_loads_them_empty():
    saved = Places(bad_slots=[(1, 2, 3), (4, 5, 6)], moved={"furnace", "chest"}, retired={"chest"})
    data = json.loads(json.dumps(saved.to_json()))
    assert data["moved"] == ["chest", "furnace"]
    assert Places.from_json(data) == saved
    old = {"home": None, "stations": {}, "stored": {}, "chest_goal": None}
    assert Places.from_json(old) == Places()


def test_yard_slots_skips_the_bad_ones(tmp_path):
    world = ReachWorld()
    home = build_home(tmp_path, world)
    scan = world.scan(*yard_scan_box(home))
    first, second = yard_slots(home, scan)[:2]
    assert yard_slots(home, scan, bad_slots=[first])[0] == second
    assert first not in yard_slots(home, scan, taken=[], bad_slots=[first])


# -- furnishing ------------------------------------------------------------------------------------


def test_a_slot_the_walk_stalls_near_is_bad_and_the_next_one_is_used(tmp_path):
    world = ReachWorld()
    home = build_home(tmp_path, world)
    first, second = yard_slots(home, world.scan(*yard_scan_box(home)))[:2]
    world.stalls[first] = near(first)
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=2)

    assert places(tmp_path).stations == {"chest": second}
    assert places(tmp_path).bad_slots == [first]
    assert [(e["station"], tuple(e["pos"])) for e in life_events(tmp_path, "home_slot_bad")] == [("chest", first)]
    assert not life_events(tmp_path, "home_place_failed")


def test_a_walk_that_stalls_far_away_places_nothing_and_stops_furnishing(tmp_path):
    world = ReachWorld()
    home = build_home(tmp_path, world)
    first = yard_slots(home, world.scan(*yard_scan_box(home)))[0]
    world.stalls[first] = {"x": first[0] + 40.0, "y": 70.0, "z": first[2]}
    world.inventory["crafting_table"] += 1
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=2)

    assert places(tmp_path).stations == {}
    assert places(tmp_path).bad_slots == []
    failed = life_events(tmp_path, "home_place_failed")
    assert [(e["station"], tuple(e["pos"]), e["error"]) for e in failed] == [("chest", first, "unreachable")]
    assert world.inventory["chest"] == 1 and world.inventory["crafting_table"] == 1


# -- an unreachable station or chest ---------------------------------------------------------------


def stall_on(world, cell):
    world.unreachable_cells.add(cell)
    world.stall_end = near(cell)


def test_an_unreachable_station_is_moved_once_then_dropped_and_retired(tmp_path):
    world = ReachWorld()
    _, furnishing = furnished(tmp_path, world, "crafting_table")
    table = furnishing.stations["crafting_table"]
    stall_on(world, table)
    ask(world, "a chest")

    run(tmp_path, world, resume=True, max_goals=3)

    assert [(e["station"], tuple(e["pos"]), e["action"]) for e in life_events(tmp_path, "home_unreachable")] == [
        ("crafting_table", table, "moved")
    ]
    saved = places(tmp_path)
    assert "crafting_table" not in saved.stations and saved.bad_slots == [table] and saved.moved == {"crafting_table"}
    assert world.stations_calls[-1] == {}
    stalls = [s for s in episode_events(tmp_path, 3) if s["kind"] == "step" and s["detail"].get("station_unreachable")]
    assert len(stalls) >= 1

    # the next goal doesn't plan the forgotten station: no second stall
    ask(world, "2 chests")
    run(tmp_path, world, resume=True, max_goals=4)
    assert not [s for s in episode_events(tmp_path, 4) if s["kind"] == "step" and s["detail"].get("station_unreachable")]

    # a new table goes in another slot; unreachable again, it is dropped for good
    world.inventory["crafting_table"] += 1
    ask(world, "a chest")
    run(tmp_path, world, resume=True, max_goals=5)
    second = places(tmp_path).stations["crafting_table"]
    assert second != table
    world.inventory["crafting_table"] = 0  # the home's table is the only one
    stall_on(world, second)
    ask(world, "a chest")
    run(tmp_path, world, resume=True, max_goals=6)

    assert [e["action"] for e in life_events(tmp_path, "home_unreachable")][-1] == "dropped"
    assert [(e["station"], tuple(e["pos"])) for e in life_events(tmp_path, "home_lost")] == [("crafting_table", second)]
    saved = places(tmp_path)
    assert "crafting_table" not in saved.stations and saved.retired == {"crafting_table"}
    placed = len(life_events(tmp_path, "home_placed"))

    world.inventory["crafting_table"] += 1
    ask(world, "1 stick")
    run(tmp_path, world, resume=True, max_goals=7)
    assert len(life_events(tmp_path, "home_placed")) == placed
    assert "crafting_table" not in places(tmp_path).stations


def test_an_unreachable_station_far_from_its_cell_is_only_skipped(tmp_path):
    world = ReachWorld()
    _, furnishing = furnished(tmp_path, world, "crafting_table")
    table = furnishing.stations["crafting_table"]
    world.unreachable_cells.add(table)
    world.stall_end = {"x": table[0] + 60.0, "y": 70.0, "z": table[2]}
    ask(world, "a chest")

    run(tmp_path, world, resume=True, max_goals=3)

    assert not life_events(tmp_path, "home_unreachable")
    saved = places(tmp_path)
    assert saved.stations["crafting_table"] == table and saved.bad_slots == [] and saved.moved == set()


def test_an_unreachable_deposit_near_the_chest_abandons_the_chest_at_once(tmp_path):
    world = ReachWorld()
    _, furnishing = furnished(tmp_path, world)
    chest = furnishing.stations["chest"]
    write_places(tmp_path, stored={"coal": 5})
    stall_on(world, chest)
    crowd_world(world)
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    events = life_events(tmp_path, "home_unreachable")
    assert [(e["station"], tuple(e["pos"]), e["action"], e["stored"]) for e in events] == [("chest", chest, "moved", {"coal": 5})]
    saved = places(tmp_path)
    assert "chest" not in saved.stations and saved.stored == {} and saved.bad_slots == [chest]
    assert saved.moved == {"chest"}
    assert saved.chest_goal not in (None, "s001") or saved.chest_goal is not None  # one more chest goal was queued
    assert len(GoalQueue(tmp_path / "goals.jsonl").goals()) >= 3


def crowd_world(world):
    from test_v3_home_life import crowd
    crowd(world)
    world.inventory["poppy"] += 3


# -- the chest goal and composite parents ----------------------------------------------------------


def open_life(tmp_path, world) -> _Life:
    """A resumed life over a built home that has no chest and holds none."""
    build_home(tmp_path, world, chest_held=False)
    queue = GoalQueue(tmp_path / "goals.jsonl")
    queue.cancel(places(tmp_path).chest_goal)
    write_places(tmp_path, chest_goal=None)
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-home-test", idle_poll_s=0, mode="play")
    life = _Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), True)
    life._inventory_seen = {}
    return life


def chest_goals(tmp_path):
    return [g for g in GoalQueue(tmp_path / "goals.jsonl").goals() if g.text == "a chest for my home" and g.status != "cancelled"]


def add_parent(tmp_path):
    queue = GoalQueue(tmp_path / "goals.jsonl")
    parent = queue.submit("4 sticks and 2 torches")
    return queue, parent, queue.accept_children(parent, [
        GoalSpec("", "obtain", "user", "4 sticks", item="stick", count=4),
        GoalSpec("", "obtain", "user", "2 torches", item="torch", count=2),
    ])


def test_the_chest_goal_waits_for_an_open_parent_and_comes_when_it_settles(tmp_path):
    life = open_life(tmp_path, ReachWorld())
    queue, parent, children = add_parent(tmp_path)

    life._queue_chest_goal()
    assert chest_goals(tmp_path) == []

    for child in children:
        queue.mark(child.id, "running")
        queue.mark(child.id, "done")
    life._settle_parent(life.queue.get(parent))
    assert len(chest_goals(tmp_path)) == 1


def test_the_chest_goal_comes_when_a_cancelled_parent_settles(tmp_path):
    life = open_life(tmp_path, ReachWorld())
    queue, parent, children = add_parent(tmp_path)
    life._queue_chest_goal()
    assert chest_goals(tmp_path) == []

    for child in children:
        queue.cancel(child.id)
    life._settle_parents()

    assert queue.get(parent).status == "cancelled"
    assert len(chest_goals(tmp_path)) == 1


def test_a_retired_chest_is_never_asked_for(tmp_path):
    life = open_life(tmp_path, ReachWorld())
    life.places.retired.add("chest")

    life._queue_chest_goal()

    assert chest_goals(tmp_path) == []
