"""v3.3g: the simulator kills mobs with vanilla drops, smelts raw meat, looks for mobs and follows a player."""

import pytest

from schema_voyager.environment import MOB_DROPS, NaturalWorld
from schema_voyager.models import Action


def world_with(mob, difficulty="peaceful", count=None):
    """A world whose spawn region holds `mob` (searching seeds deterministically)."""
    for seed in range(500):
        world = NaturalWorld(seed, difficulty=difficulty)
        entity = world.observe().entities.get(mob)
        if entity and (count is None or entity["count"] == count):
            return world
    raise AssertionError(f"no seed with {mob}")


def test_a_cow_kill_drops_within_vanilla_ranges_and_fills_the_inventory():
    world = world_with("cow")
    outcome = world.act(Action("kill", target="cow"))

    assert outcome.ok and outcome.block_removed is False
    assert 1 <= outcome.drops["beef"] <= 3
    assert 0 <= outcome.drops.get("leather", 0) <= 2
    assert outcome.picked_up == outcome.drops == outcome.inventory_delta
    assert outcome.inventory == outcome.drops
    assert outcome.detail["killed"] is True
    assert outcome.detail["mob"] == "cow"
    assert outcome.detail["weapon_used"] is None
    assert 0 < outcome.blocks_walked <= 32
    start, end = [event for event in outcome.events if event["type"].startswith("fight_")]
    assert start["type"] == "fight_start" and start["target"] == "cow" and start["weapon"] is None
    assert end["type"] == "fight_end" and end["outcome"] == "killed" and end["id"] == start["id"]
    assert end["damage_taken"] == 0


def test_the_best_held_sword_else_axe_is_the_weapon():
    world = world_with("cow")
    world.inventory.update({"wooden_sword": 1, "iron_axe": 1, "stone_sword": 1})
    assert world.act(Action("kill", target="cow")).detail["weapon_used"] == "stone_sword"
    world = world_with("cow")
    world.inventory.update({"iron_axe": 1})
    assert world.act(Action("kill", target="cow")).detail["weapon_used"] == "iron_axe"


def test_every_mob_drops_within_its_ranges():
    for mob, ranges in MOB_DROPS.items():
        difficulty = "peaceful" if mob in ("cow", "pig", "chicken", "sheep") else "easy"
        world = world_with(mob, difficulty)
        world.health = 20.0
        outcome = world.act(Action("kill", target=mob))
        assert outcome.ok, mob
        assert set(outcome.drops) <= set(ranges)
        for item, count in outcome.drops.items():
            assert ranges[item][0] <= count <= ranges[item][1]


def test_kills_run_out_and_the_entity_count_drops():
    world = world_with("cow")
    count = world.observe().entities["cow"]["count"]
    kills = 0
    while kills < count:
        outcome = world.act(Action("kill", target="cow"))
        if outcome.ok:
            kills += 1
            left = world.observe().entities.get("cow", {}).get("count", 0)
            assert left == count - kills
        else:
            assert outcome.error == "escaped"
    assert "cow" not in world.observe().entities
    assert world.act(Action("kill", target="cow")).error == "not_found"


def test_players_and_unknown_mobs_are_not_found():
    world = world_with("cow")
    assert world.act(Action("kill", target="player")).error == "not_found"
    assert world.act(Action("kill", target="dragon")).error == "not_found"


def test_a_zombie_is_absent_on_peaceful_and_costs_damage_on_easy():
    peaceful = NaturalWorld(1)
    assert peaceful.act(Action("kill", target="zombie")).error == "not_found"

    world = world_with("zombie", "easy")
    world.health = 20.0
    outcome = world.act(Action("kill", target="zombie"))
    assert outcome.ok and set(outcome.drops) <= {"rotten_flesh"}
    damages = [event for event in outcome.events if event["type"] == "damage"]
    assert len(damages) == 1 and damages[0]["phase"] == "step"
    assert 1 <= damages[0]["amount"] <= 3
    assert world.health == 20.0 - damages[0]["amount"]
    assert outcome.events[-1]["damage_taken"] == damages[0]["amount"]


def test_animals_sometimes_escape():
    escaped = total = 0
    for seed in range(400):
        world = NaturalWorld(seed)
        if "cow" not in world.observe().entities:
            continue
        total += 1
        escaped += world.act(Action("kill", target="cow")).error == "escaped"
    assert total > 100
    assert 0.02 < escaped / total < 0.10


def test_a_full_inventory_keeps_only_what_fits():
    world = world_with("pig")
    world.inventory.update({f"item{i}": 1 for i in range(36)})
    outcome = world.act(Action("kill", target="pig"))
    if outcome.ok:
        assert outcome.picked_up == {}
        assert outcome.detail["inventory_full"] is True
        assert outcome.drops["porkchop"] >= 1


def test_raw_meat_smelts_to_cooked():
    world = NaturalWorld(1)
    world.inventory.update({"beef": 2, "furnace": 1, "coal": 1})
    outcome = world.act(Action("smelt", target="cooked_beef", ingredient="beef", fuel="coal", times=2))
    assert outcome.ok
    assert outcome.inventory["cooked_beef"] == 2
    for raw, cooked in (("porkchop", "cooked_porkchop"), ("chicken", "cooked_chicken"), ("mutton", "cooked_mutton")):
        world.inventory.update({raw: 1, "coal": 1})
        assert world.act(Action("smelt", target=cooked, ingredient=raw, fuel="coal")).ok


def test_look_for_mobs_gives_positions_by_sector_and_blocks_alone_is_unchanged():
    world = world_with("cow")
    plain = world.look(["oak_log"])
    result = world.look(["oak_log"], mobs=["cow", "zombie"])
    assert world.look(["oak_log"], mobs=[]) == plain
    for direction, sector in result.items():
        assert {k: v for k, v in sector.items() if k != "cow"} == plain[direction]
        assert set(sector["cow"]) == {"x", "y", "z", "distance"}
        assert 0 < sector["cow"]["distance"] <= 33
        assert "zombie" not in sector
    assert result == world.look(["oak_log"], mobs=["cow", "zombie"])


def test_look_stops_reporting_a_mob_once_it_is_all_killed():
    world = world_with("cow", count=1)
    while not world.act(Action("kill", target="cow")).ok:
        pass
    assert all("cow" not in sector for sector in world.look([], mobs=["cow"]).values())


def test_follow_and_stop_follow_show_in_status():
    world = NaturalWorld(1)
    assert world.status().follow is None
    assert world.follow("Steve", 5) == "Steve"
    assert world.status().follow == {"player": "Steve", "distance": 5.0}
    assert world.follow(None) == "Otopo"
    assert world.status().follow == {"player": "Otopo", "distance": 3.0}
    world.stop_follow()
    assert world.status().follow is None


def test_kills_are_deterministic():
    def run():
        world = world_with("cow")
        return [world.act(Action("kill", target="cow")) for _ in range(4)]

    assert run() == run()


def test_kills_do_not_move_the_mine_drops():
    def mine_drops(interleave_kills):
        world = world_with("cow")
        drops = []
        for _ in range(6):
            if interleave_kills:
                world.act(Action("kill", target="cow"))
            drops.append(world.act(Action("mine", target="oak_log")).drops)
        return drops

    assert mine_drops(False) == mine_drops(True)
