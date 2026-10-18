"""A life drops junk at the start of a goal when the bot is nearly full (simulator)."""

import dataclasses
import json

from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM


class RecordingWorld(NaturalWorld):
    def __init__(self, seed):
        super().__init__(seed)
        self.said = []

    def say(self, message):
        self.said.append(message)


class SilentSlotsWorld(RecordingWorld):
    """Reports no free_slots, like an older bot; a toss is a bug."""

    def observe(self):
        return dataclasses.replace(super().observe(), free_slots=None)

    def toss(self, items):
        raise AssertionError("toss called without free_slots")


def _life(tmp_path, world, max_goals):
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-junk-test", max_goals=max_goals)
    run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config)
    records = [json.loads(line) for line in (tmp_path / "life.jsonl").read_text().splitlines()]
    episodes = sorted((tmp_path / "episodes").glob("*.jsonl"))
    events = [[json.loads(line) for line in path.read_text().splitlines()] for path in episodes]
    return records, events


def test_a_nearly_full_bot_drops_junk_before_its_first_goal(tmp_path):
    world = RecordingWorld(seed=1)
    world.inventory.update({"dirt": 33 * 64, "poppy": 3})
    assert world.observe().free_slots < 4

    records, events = _life(tmp_path, world, max_goals=2)

    tossed = records[0]["tossed"]
    assert "poppy" in tossed and tossed["dirt"] > 0
    assert world.inventory["dirt"] >= 64  # the scaffold reserve stays
    assert events[0][0]["kind"] == "goal"
    assert events[0][1]["kind"] == "tossed" and events[0][1]["items"] == tossed
    assert events[0][1]["free_slots_before"] < 4 <= events[0][1]["free_slots_after"]
    assert any("to make room" in message for message in world.said)
    assert all(not any(e["kind"] == "tossed" for e in episode) for episode in events[1:])


def test_a_bot_with_room_tosses_nothing(tmp_path):
    world = RecordingWorld(seed=1)

    records, events = _life(tmp_path, world, max_goals=2)

    assert [record["tossed"] for record in records] == [{}] * len(records)
    assert not any(event["kind"] == "tossed" for episode in events for event in episode)
    assert not any("to make room" in message for message in world.said)


def test_a_backend_without_free_slots_never_tosses(tmp_path):
    world = SilentSlotsWorld(seed=1)
    world.inventory.update({"dirt": 33 * 64, "poppy": 3})

    records, events = _life(tmp_path, world, max_goals=1)

    assert [record["tossed"] for record in records] == [{}]
    assert not any(event["kind"] == "tossed" for episode in events for event in episode)
