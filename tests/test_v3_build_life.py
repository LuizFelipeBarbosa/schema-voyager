"""An approved build goal runs inside the life (v3.4b): the fake hut in the simulator, the protected boxes in
life_state.json, and a later goal that must leave the house alone.

Chat commands come from a NaturalWorld whose `status()` emits on the Nth call (copied from test_v3_life_queue.py);
`ChatWorld.on_place` runs after the Nth place action so a test can interrupt a build mid-way.
"""

import json

import pytest

from schema_voyager import blueprint, builder
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import FAKE_HUT, LLM
from schema_voyager.models import GoalSpec

BUILD_AND_APPROVE = {2: {"type": "chat_command", "phase": "step", "player": "Otopo", "command": "goal", "args": "build a small house"}}


class ChatWorld(NaturalWorld):
    """`events` maps a `status()` call number to the event (or list of events) emitted just before that call answers."""

    def __init__(self, events=None, seed=2):
        super().__init__(seed)
        self.said: list[str] = []
        self.status_calls = 0
        self.events = {**BUILD_AND_APPROVE, 6: chat("yes")} if events is None else events
        self.protected_calls: list[list] = []
        self.first_episode_after_protected: bool | None = None
        self.places = 0
        self.on_place = None  # (number of places so far) -> None

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

    def begin_episode(self):
        if self.first_episode_after_protected is None:
            self.first_episode_after_protected = bool(self.protected_calls)
        return super().begin_episode()

    def act(self, action):
        outcome = super().act(action)
        if action.kind == "place":
            self.places += 1
            if self.on_place is not None:
                self.on_place(self.places)
        return outcome


def chat(command, args=""):
    return {"type": "chat_command", "phase": "step", "player": "Otopo", "command": command, "args": args}


def run(tmp_path, world, resume=False, **config):
    config.setdefault("idle_poll_s", 0)
    config.setdefault("mode", "play")
    config.setdefault("max_goals", 1)
    config.setdefault("wall_clock_s", 30.0)
    life_config = LifeConfig(run_dir=tmp_path, world_id="life-sim-build-test", **config)
    return run_life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), life_config, resume=resume)


def entry(run_dir, goal_id):
    return GoalQueue(run_dir / "goals.jsonl").get(goal_id)


def statuses(run_dir, goal_id):
    lines = (run_dir / "goals.jsonl").read_text().splitlines()
    return [line["status"] for line in map(json.loads, lines) if line["id"] == goal_id]


def protected(run_dir):
    return json.loads((run_dir / "life_state.json").read_text())["protected"]


def episode_events(run_dir, index):
    path = next((run_dir / "episodes").glob(f"D_life_g{index:03d}_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def logs_held(world):
    return sum(count for item, count in world.observe().inventory.items() if item.endswith("_log"))


def site_box_of(saved):
    return blueprint.site_box(FAKE_HUT, tuple(saved["origin"]))


def test_a_build_goal_runs_from_submission_to_done(tmp_path):
    world = ChatWorld()

    summary = run(tmp_path, world)

    assert summary["stop_reason"] == "max_goals"
    assert statuses(tmp_path, "u001") == ["submitted", "awaiting_approval", "queued", "running", "done"]
    [saved] = protected(tmp_path)
    assert (saved["goal_id"], saved["name"], saved["size"], saved["status"]) == ("u001", "small hut", [5, 4, 5], "built")
    origin = tuple(saved["origin"])
    scan = world.scan(*blueprint.site_box(FAKE_HUT, origin))
    assert builder.mismatches(FAKE_HUT, origin, scan) == []
    assert world.protected_calls[-1] == [site_box_of(saved)]
    assert any(line.startswith("Plan for u001: small hut") for line in world.said)
    assert "Approved u001: small hut." in world.said and "Done: small hut" in world.said
    transitions = json.loads((tmp_path / "store.json").read_text())["transitions"]
    assert not {t["action"]["kind"] for t in transitions} & {"place", "clear", "goto"}
    record = json.loads((tmp_path / "life.jsonl").read_text().splitlines()[0])
    assert (record["build"]["name"], record["build"]["origin"]) == ("small hut", saved["origin"])
    assert record["build"]["placed"] == record["build"]["total"] > 0


@pytest.mark.parametrize("seed", range(1, 9))
def test_a_build_goal_is_done_and_matches_on_every_seed(tmp_path, seed):
    world = ChatWorld(seed=seed)

    run(tmp_path, world)

    assert entry(tmp_path, "u001").status == "done"
    [saved] = protected(tmp_path)
    origin = tuple(saved["origin"])
    scan = world.scan(*site_box_of(saved))
    assert builder.mismatches(FAKE_HUT, origin, scan) == []


def test_a_later_log_goal_never_takes_a_block_of_the_house(tmp_path):
    world = ChatWorld()
    run(tmp_path, world)
    [saved] = protected(tmp_path)
    box = site_box_of(saved)
    before = world.scan(*box)
    logs_before = logs_held(world)
    world.events = {world.status_calls + 2: chat("goal", "10 logs")}

    run(tmp_path, world, resume=True, max_goals=2)

    assert entry(tmp_path, "u002").status == "done"
    assert logs_held(world) > logs_before
    assert world.scan(*box) == before


def test_resume_sends_the_saved_boxes_before_any_goal(tmp_path):
    run(tmp_path, ChatWorld())
    [saved] = protected(tmp_path)
    resumed = ChatWorld(events={})

    run(tmp_path, resumed, resume=True, max_goals=2, wall_clock_s=0.2)

    assert resumed.protected_calls[0] == [site_box_of(saved)]
    assert resumed.first_episode_after_protected is not False


def test_a_fresh_life_sends_no_boxes_first(tmp_path):
    world = ChatWorld(events={})

    run(tmp_path, world, wall_clock_s=0.1)

    assert world.protected_calls == [[]]


def test_a_cancel_mid_build_leaves_a_partial_house(tmp_path):
    world = ChatWorld()
    world.on_place = lambda n: world._emit(chat("cancel")) if n == 10 else None

    run(tmp_path, world)

    assert statuses(tmp_path, "u001")[-1] == "cancelled"
    [saved] = protected(tmp_path)
    assert saved["status"] == "partial"
    assert world.protected_calls[-1] == [site_box_of(saved)]
    record = json.loads((tmp_path / "life.jsonl").read_text().splitlines()[0])
    assert 0 < record["build"]["placed"] < record["build"]["total"]


def test_a_discarded_plan_leaves_no_entry(tmp_path):
    world = ChatWorld({**BUILD_AND_APPROVE, 6: chat("no")})

    run(tmp_path, world, wall_clock_s=0.3, max_goals=None)

    assert entry(tmp_path, "u001").status == "cancelled"
    assert world.protected_calls == [[]]  # sent once at the start, never a box


def test_a_preempted_build_resumes_on_the_same_origin(tmp_path):
    world = ChatWorld()

    def system_goal_arrives(n):
        if n == 10:
            queue = GoalQueue(tmp_path / "goals.jsonl")
            goal_id = queue.submit("hunger: 4 bread", "system")
            queue.accept(GoalSpec(goal_id, "obtain", "system", item="stick", count=1))

    world.on_place = system_goal_arrives

    run(tmp_path, world, max_goals=3)

    assert statuses(tmp_path, "u001")[-1] == "done"
    assert [e["status"] for e in protected(tmp_path)] == ["built"]
    sites = [
        e["origin"] for i in (1, 3) for e in episode_events(tmp_path, i) if e["kind"] == "build_site"
    ]
    assert len(sites) == 2 and sites[0] == sites[1] == protected(tmp_path)[0]["origin"]


def test_a_site_that_cannot_be_found_fails_the_goal_and_leaves_no_entry(tmp_path):
    world = ChatWorld()
    world.survey = lambda size, radius=24: []

    run(tmp_path, world)

    failed = entry(tmp_path, "u001")
    assert (failed.status, failed.reason) == ("failed", "no site")
    assert "Couldn't build small hut: no site" in world.said
    assert protected(tmp_path) == []
    assert any(e["kind"] == "build_failed" and e["reason"] == "no site" for e in episode_events(tmp_path, 1))
