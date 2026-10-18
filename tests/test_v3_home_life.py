"""The home in the life loop (v3.5b-e, life side): the first house built is home, its yard is protected and furnished
with the chest, crafting table and furnace the bot holds, junk goes in the chest instead of the ground, and every
goal's Episode knows the home's stock. Whole simulator lives with the fake LLM (helpers copied from
test_v3_build_life.py)."""

import dataclasses
import json

from schema_voyager import blueprint
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import FAKE_HUT, LLM
from schema_voyager.models import Action
from schema_voyager.places import Places, door_cell, home_box, yard_scan_box, yard_slots

BUILD_AND_APPROVE = {2: {"type": "chat_command", "phase": "step", "player": "Otopo", "command": "goal", "args": "build a small house"}}


class ChatWorld(NaturalWorld):
    """`events` maps a `status()` call number to the event (or list of events) emitted just before that call answers."""

    def __init__(self, events=None, seed=2):
        super().__init__(seed)
        self.said: list[str] = []
        self.status_calls = 0
        self.events = {**BUILD_AND_APPROVE, 6: chat("yes")} if events is None else events
        self.protected_calls: list[list] = []
        self.stations_calls: list[dict] = []
        self.tosses: list[dict] = []
        self.lie_air_at: set = set()  # cells scan() reports as air although they are not
        self.place_error: str | None = None  # every place of a home station fails with it
        self.far_away = False  # observations put the bot 500 blocks from anywhere

    def say(self, message):
        self.said.append(message)

    def status(self, since=0):
        self.status_calls += 1
        new = self.events.get(self.status_calls, [])
        for event in [new] if isinstance(new, dict) else new:
            self._emit(event)
        return super().status(since)

    def set_protected(self, boxes):
        self.protected_calls.append(list(boxes))
        super().set_protected(boxes)

    def set_stations(self, stations):
        self.stations_calls.append(dict(stations))
        super().set_stations(stations)

    def toss(self, items):
        self.tosses.append(dict(items))
        return super().toss(items)

    def observe(self):
        observation = super().observe()
        return dataclasses.replace(observation, position=(500.5, 70.0, 500.5)) if self.far_away else observation

    def scan(self, origin, size):
        return {cell: "air" if cell in self.lie_air_at else block for cell, block in super().scan(origin, size).items()}

    def act(self, action):
        if action.kind == "place" and action.scaffold is False and self.place_error:
            return self._fail(self.place_error)
        return super().act(action)


def chat(command, args=""):
    return {"type": "chat_command", "phase": "step", "player": "Otopo", "command": command, "args": args}


def run(tmp_path, world, resume=False, **config):
    config.setdefault("idle_poll_s", 0)
    config.setdefault("mode", "play")
    config.setdefault("max_goals", 1)
    config.setdefault("wall_clock_s", 30.0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-home-test", **config)
    return run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), life_config, resume=resume)


def state(run_dir):
    return json.loads((run_dir / "life_state.json").read_text())


def places(run_dir) -> Places:
    return Places.from_json(state(run_dir)["places"])


def write_places(run_dir, **changes):
    saved = state(run_dir)
    saved["places"] = {**saved["places"], **changes}
    (run_dir / "life_state.json").write_text(json.dumps(saved))


def life_events(run_dir, kind):
    lines = (run_dir / "life_events.jsonl").read_text().splitlines()
    return [event for event in map(json.loads, lines) if event["kind"] == kind]


def episode_events(run_dir, index):
    path = next((run_dir / "episodes").glob(f"D_life_g{index:03d}_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def records(run_dir):
    return [json.loads(line) for line in (run_dir / "life.jsonl").read_text().splitlines()]


def build_home(run_dir, world, chest_held=True):
    """Run the fake hut to done. A held chest keeps the life from asking for one."""
    if chest_held:
        world.inventory["chest"] += 1
    run(run_dir, world)
    world.inventory["crafting_table"] = 0  # the build crafted one; the tests decide what the bag holds
    return places(run_dir).home


def ask(world, text, after=2):
    world.events = {world.status_calls + after: chat("goal", text)}


def furnished(tmp_path, world, *held):
    """A home with the held stations placed in its yard: one more goal, started with them in the bag."""
    home = build_home(tmp_path, world)
    for kind in held:
        world.inventory[kind] += 1
    ask(world, "1 stick")
    run(tmp_path, world, resume=True, max_goals=2)
    return home, places(tmp_path)


def crowd(world, stacks=30):
    """Fill the bag with junk stacks until it has under MIN_FREE_SLOTS free."""
    junk = ["gravel", "sand", "andesite", "diorite", "granite", "tuff", "netherrack", "red_sand", "clay", "mud", "dirt",
            "cobbled_deepslate", "deepslate", "calcite", "dripstone_block", "moss_block", "podzol", "mycelium",
            "coarse_dirt", "rooted_dirt", "snow_block", "ice", "packed_ice", "soul_sand", "end_stone", "basalt",
            "blackstone", "smooth_basalt", "magma_block", "bone_block"]
    world.inventory.update({name: 64 for name in junk[:stacks]})
    assert world.observe().free_slots < 4


# -- the home is set ------------------------------------------------------------------------------


def test_a_build_done_sets_the_home_and_protects_its_yard(tmp_path):
    world = ChatWorld()

    home = build_home(tmp_path, world, chest_held=False)

    [saved] = state(tmp_path)["protected"]
    assert (home.goal_id, home.name, home.origin, home.size) == ("u001", "small hut", tuple(saved["origin"]), (5, 4, 5))
    assert home.door == door_cell(FAKE_HUT, home.origin)
    assert [event["home"] for event in life_events(tmp_path, "home_set")] == [places(tmp_path).to_json()["home"]]
    x, y, z = home.origin
    assert f"This is my home now: small hut at {x} {y} {z}." in world.said
    assert world.protected_calls[-1] == [home_box(home)]


def test_resume_of_a_life_without_places_makes_its_first_built_house_home(tmp_path):
    home = build_home(tmp_path, ChatWorld())
    saved = state(tmp_path)
    del saved["places"]
    (tmp_path / "life_state.json").write_text(json.dumps(saved))
    resumed = ChatWorld(events={})

    run(tmp_path, resumed, resume=True, max_goals=2, wall_clock_s=0.2)

    migrated = places(tmp_path).home
    assert migrated == home and migrated.door == door_cell(FAKE_HUT, home.origin)
    assert life_events(tmp_path, "home_set")[-1]["home"] == places(tmp_path).to_json()["home"]
    assert any(line.startswith("This is my home now: small hut at") for line in resumed.said)
    assert resumed.protected_calls[0] == [home_box(home)]


def test_a_resume_with_places_keeps_them_and_says_nothing(tmp_path):
    home, _ = furnished(tmp_path, ChatWorld(), "crafting_table")
    resumed = ChatWorld(events={})

    run(tmp_path, resumed, resume=True, max_goals=3, wall_clock_s=0.2)

    assert places(tmp_path).home == home
    assert not any("my home now" in line for line in resumed.said)
    assert len(life_events(tmp_path, "home_set")) == 1


def test_without_a_home_nothing_changes(tmp_path):
    world = ChatWorld(events={2: chat("goal", "2 logs")})

    run(tmp_path, world)

    assert places(tmp_path) == Places()
    assert world.protected_calls[-1] == [] and world.stations_calls[-1] == {}
    assert not life_events(tmp_path, "home_set")
    assert GoalQueue(tmp_path / "goals.jsonl").get("s001") is None


# -- protected boxes ------------------------------------------------------------------------------


def test_more_than_32_protected_entries_are_sent_as_at_most_32_boxes(tmp_path):
    build_home(tmp_path, ChatWorld())
    saved = state(tmp_path)
    saved["protected"] += [
        {"goal_id": f"x{i:03d}", "name": "hut", "origin": [1000 + 20 * i, 70, 0], "size": [5, 4, 5], "status": "built"}
        for i in range(40)
    ]
    (tmp_path / "life_state.json").write_text(json.dumps(saved))
    resumed = ChatWorld(events={})

    run(tmp_path, resumed, resume=True, max_goals=2, wall_clock_s=0.2)

    assert len(resumed.protected_calls[0]) == 32
    assert len(state(tmp_path)["protected"]) == 41  # life_state.json keeps every entry


# -- furnishing -----------------------------------------------------------------------------------


def test_a_held_chest_and_table_are_put_in_the_yard(tmp_path):
    world = ChatWorld()
    home = build_home(tmp_path, world)

    world.inventory["crafting_table"] += 1
    ask(world, "1 stick")
    run(tmp_path, world, resume=True, max_goals=2)

    stations = places(tmp_path).stations
    assert set(stations) == {"chest", "crafting_table"}
    for kind, cell in stations.items():
        assert world.block_at(cell) == kind
        x, y, z = cell
        assert -1 <= x - home.origin[0] <= 5 and -1 <= z - home.origin[2] <= 5  # in the ring around the footprint
        assert f"I put a {kind.replace('_', ' ')} by my house at {x} {y} {z}." in world.said
    assert [(e["station"], tuple(e["pos"])) for e in life_events(tmp_path, "home_placed")] == [
        (kind, stations[kind]) for kind in ("chest", "crafting_table")
    ]
    assert world.stations_calls[-1] == {"crafting_table": stations["crafting_table"]}
    assert places(tmp_path).stored == {}
    assert world.inventory["chest"] == 0 and world.inventory["crafting_table"] == 0


def test_an_occupied_cell_tries_the_next_slot(tmp_path):
    world = ChatWorld()
    home = build_home(tmp_path, world)
    first, second = yard_slots(home, world.scan(*yard_scan_box(home)))[:2]
    world._changes[first] = "stone"
    world.lie_air_at = {first}
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=2)

    assert places(tmp_path).stations == {"chest": second}
    assert not life_events(tmp_path, "home_place_failed")


def test_a_place_error_stops_that_kind_and_is_logged(tmp_path):
    world = ChatWorld()
    home = build_home(tmp_path, world)
    first = yard_slots(home, world.scan(*yard_scan_box(home)))[0]
    world.place_error = "unreachable"
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=2)

    assert places(tmp_path).stations == {}
    assert [(e["station"], tuple(e["pos"]), e["error"]) for e in life_events(tmp_path, "home_place_failed")] == [
        ("chest", first, "unreachable")
    ]


def test_no_furnishing_when_the_bot_is_far_from_home(tmp_path):
    world = ChatWorld()
    build_home(tmp_path, world)
    world.far_away = True
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=2)

    assert places(tmp_path).stations == {} and world.inventory["chest"] == 1


# -- the chest goal -------------------------------------------------------------------------------


def test_the_chest_goal_is_asked_once_per_home(tmp_path):
    world = ChatWorld()
    build_home(tmp_path, world, chest_held=False)
    assert places(tmp_path).chest_goal == "s001"
    queue = GoalQueue(tmp_path / "goals.jsonl")
    assert (queue.get("s001").spec.item, queue.get("s001").spec.count, queue.get("s001").text) == ("chest", 1, "a chest for my home")
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    queue = GoalQueue(tmp_path / "goals.jsonl")
    assert queue.get("s001").status == "done"
    assert [g.id for g in queue.goals() if g.text == "a chest for my home"] == ["s001"]
    assert "chest" in places(tmp_path).stations  # furnishing placed it at a later goal's start


def test_a_held_chest_means_no_chest_goal(tmp_path):
    build_home(tmp_path, ChatWorld())

    assert places(tmp_path).chest_goal is None
    assert GoalQueue(tmp_path / "goals.jsonl").get("s001") is None


# -- storing --------------------------------------------------------------------------------------


def test_junk_goes_in_the_home_chest_instead_of_the_ground(tmp_path):
    world = ChatWorld()
    home, _ = furnished(tmp_path, world)
    chest = places(tmp_path).stations["chest"]
    crowd(world)
    world.inventory["poppy"] += 3
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    assert world.tosses == []
    record = records(tmp_path)[-1]
    assert record["tossed"] == {} and record["stored"]
    stored_event = next(e for e in episode_events(tmp_path, 3) if e["kind"] == "stored")
    assert stored_event["items"] == record["stored"]
    assert stored_event["free_slots_before"] < 4 <= stored_event["free_slots_after"]
    contents = places(tmp_path).stored
    assert contents and all(contents[item] >= count for item, count in record["stored"].items())
    assert world.deposit(chest, {}).contents == contents
    listed = ", ".join(f"{item} x{count}" for item, count in record["stored"].items())
    assert f"Putting {listed} in my chest."[:240] in world.said  # chat is cut at 240 characters


def test_a_full_chest_falls_back_to_the_toss(tmp_path):
    world = ChatWorld()
    home, _ = furnished(tmp_path, world)
    chest = places(tmp_path).stations["chest"]
    world.inventory["cobblestone"] += 27 * 64
    assert world.deposit(chest, {"cobblestone": 27 * 64}).detail["chest_full"] is False
    crowd(world)
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    record = records(tmp_path)[-1]
    assert record["stored"] == {} and record["tossed"]
    assert world.tosses and "tossed" in {e["kind"] for e in episode_events(tmp_path, 3)}


def test_a_chest_that_is_gone_is_dropped_and_the_junk_is_tossed(tmp_path):
    world = ChatWorld()
    home, _ = furnished(tmp_path, world)
    chest = places(tmp_path).stations["chest"]
    world._changes[chest] = "air"
    crowd(world)
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    assert "chest" not in places(tmp_path).stations and places(tmp_path).stored == {}
    assert [(e["station"], tuple(e["pos"])) for e in life_events(tmp_path, "home_lost")] == [("chest", chest)]
    assert records(tmp_path)[-1]["tossed"] and records(tmp_path)[-1]["stored"] == {}


def test_a_chest_beyond_range_means_the_old_toss(tmp_path):
    world = ChatWorld()
    furnished(tmp_path, world)
    crowd(world)
    world.far_away = True
    ask(world, "1 stick")

    run(tmp_path, world, resume=True, max_goals=3)

    assert records(tmp_path)[-1]["stored"] == {} and world.tosses


# -- the planner sees the home --------------------------------------------------------------------


def test_a_goal_withdraws_the_coal_only_the_home_chest_has(tmp_path):
    world = ChatWorld()
    home, _ = furnished(tmp_path, world)
    chest = places(tmp_path).stations["chest"]
    world.inventory["coal"] += 5
    assert world.deposit(chest, {"coal": 5}).error is None
    write_places(tmp_path, stored={"coal": 5})
    ask(world, "2 coal")

    run(tmp_path, world, resume=True, max_goals=3)

    steps = [e for e in episode_events(tmp_path, 3) if e["kind"] == "step"]
    assert [s["action_obj"]["kind"] for s in steps][:1] == ["withdraw"]
    assert places(tmp_path).stored == {"coal": 5 - steps[0]["action_obj"]["times"]}
    assert world.inventory["coal"] >= 2


def test_a_goal_uses_the_home_crafting_table_instead_of_crafting_one(tmp_path):
    world = ChatWorld()
    _, furnishing = furnished(tmp_path, world, "crafting_table")
    assert "crafting_table" in furnishing.stations
    ask(world, "a chest")

    run(tmp_path, world, resume=True, max_goals=3)

    steps = [e for e in episode_events(tmp_path, 3) if e["kind"] == "step"]
    crafted = [s["action_obj"]["target"] for s in steps if s["action_obj"]["kind"] == "craft"]
    assert "chest" in crafted and "crafting_table" not in crafted
    assert any(s["detail"].get("used_home_station") for s in steps)


def test_a_home_station_found_gone_is_dropped_from_places(tmp_path):
    world = ChatWorld()
    _, furnishing = furnished(tmp_path, world, "crafting_table")
    table = furnishing.stations["crafting_table"]
    world._changes[table] = "air"
    ask(world, "a chest")

    run(tmp_path, world, resume=True, max_goals=3)

    assert "crafting_table" not in places(tmp_path).stations
    assert [(e["station"], tuple(e["pos"])) for e in life_events(tmp_path, "home_lost")] == [("crafting_table", table)]
    assert world.stations_calls[-1] == {}


# -- parsing --------------------------------------------------------------------------------------


def test_with_a_home_get_ready_for_night_has_no_build_child(tmp_path):
    world = ChatWorld()
    build_home(tmp_path, world)
    ask(world, "get ready for night")

    run(tmp_path, world, resume=True, max_goals=2)

    queue = GoalQueue(tmp_path / "goals.jsonl")
    children = queue.children_of("u002")
    assert children and all(child.spec.kind != "build" for child in children)
