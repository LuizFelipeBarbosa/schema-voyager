"""Scaffold left beside a build (v3.0h): the cells a build's walks placed outside the house are cleared once the final
scan matches. The simulator places no scaffold, so a NaturalWorld double reports (and sets) some pillars."""

import dataclasses

import pytest

from schema_voyager import blueprint, builder
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend, _merge_travel
from schema_voyager.inventory import SCAFFOLD_BLOCKS
from schema_voyager.models import Action
from tests.test_v3_build_backend import PLACED
from tests.test_v3_build_run import (
    HUT, SPEC, WORLD_ID, _episode, _events, _prepare, _site_scan, _steps_of, llm, store, world,  # noqa: F401
)
from schema_voyager.build_runner import run_build
from schema_voyager.environment import NaturalWorld
from tests.test_v3_toss_backend import FakeBot


class ScaffoldWorld(NaturalWorld):
    """Reports scaffold pillars: during the first mine (gathering), on a failed third place and on the fifth."""

    def __init__(self, seed):
        super().__init__(seed)
        self.plan = None
        self.protected_cell = None
        self.places = 0
        self.mined = False
        self.changed_cell = None
        self.gathering_cells = []
        self.failed_cells = []
        self.pillar_cells = []
        self.inside_cell = None
        self.inside_block = None

    def column(self, dx, dz):
        """Three air cells standing on the ground, `dx` east of the site origin and `dz` south of it."""
        x, _, z = self.plan.origin
        x, z = x + dx, z + dz
        ground = self._column(x, z)[0]
        return [(x, ground + level, z) for level in (1, 2, 3)]

    def raise_pillar(self, cells, block="dirt"):
        for cell in cells:
            self._changes[cell] = block
        return [[*cell, block] for cell in cells]

    def act(self, action):
        if action.kind == "mine" and not self.mined and self.plan:
            self.mined = True
            self.gathering_cells = self.column(-9, 0)
            return self.report(super().act(action), self.raise_pillar(self.gathering_cells, "cobblestone"))  # not dirt: the bot mines dirt
        if action.kind == "place" and self.plan and self.mined:
            self.places += 1
            if self.places == 3:  # a tower attempt that fails: the retry follows
                self.failed_cells = self.column(9, 0)
                return self.report(self._fail("timeout"), self.raise_pillar(self.failed_cells))
            if self.places == 5:
                self.pillar_cells = self.column(9, 3)
                cells = self.raise_pillar(self.pillar_cells[:2])
                self.changed_cell = self.pillar_cells[2]
                self._changes[self.changed_cell] = "stone"  # recorded as dirt, but dirt no more
                cells.append([*self.changed_cell, "dirt"])
                self.protected_cell = self.column(-9, 3)[0]
                self._changes[self.protected_cell] = "dirt"
                cells.append([*self.protected_cell, "dirt"])
                self.inside_cell, self.inside_block = action.pos, action.target  # in the house box, and holds the block recorded for it
                cells.append([*action.pos, action.target])
                return self.report(super().act(action), cells)
        return super().act(action)

    @staticmethod
    def report(outcome, cells):
        return dataclasses.replace(outcome, travel={**outcome.travel, "placed_cells": cells})


@pytest.fixture
def scaffold_world():
    return ScaffoldWorld(7)


def _build(scaffold_world, llm, store, check=None):
    plan = _prepare(scaffold_world, store)
    x, y, z = plan.origin
    protected = [((x - 10, 0, z + 2), (3, 255, 3))]
    plan = _prepare(scaffold_world, store, origin=plan.origin, protected=protected)
    scaffold_world.plan = plan
    if check:
        check(scaffold_world, plan)
    episode = _episode(scaffold_world, llm, plan)
    outcome = run_build(episode, store, plan, before_step=lambda *args: None, check_interrupts=lambda: None,
                        excluded=frozenset())
    return plan, episode, outcome


def test_the_scaffold_the_build_placed_outside_the_house_is_cleared(scaffold_world, llm, store):
    world = scaffold_world
    plan, episode, outcome = _build(world, llm, store)

    assert outcome.reason == "" and outcome.result.success
    assert builder.mismatches(HUT, plan.origin, _site_scan(world, plan)) == []
    swept = world.failed_cells + world.pillar_cells[:2]
    assert len(swept) == 5
    assert all(world.block_at(cell) == "air" for cell in swept)
    done = _events(episode, "build_done")[0]
    assert done["scaffold_cleared"] == 5
    assert done["scaffold_left"] == 1  # the stone where dirt was recorded
    assert world.block_at(world.changed_cell) == "stone"


def test_cells_inside_the_house_inside_protected_boxes_or_from_gathering_stay(scaffold_world, llm, store):
    world = scaffold_world
    plan, episode, outcome = _build(world, llm, store)
    assert outcome.result.success
    assert world.block_at(world.inside_cell) == world.inside_block
    assert world.block_at(world.protected_cell) == "dirt"
    assert all(world.block_at(cell) == "cobblestone" for cell in world.gathering_cells)
    cleared = {event["action_obj"]["pos"] and tuple(event["action_obj"]["pos"])
               for event in _steps_of(episode, "clear")}
    assert not cleared & ({world.inside_cell, world.protected_cell} | set(world.gathering_cells))


def test_the_clear_actions_carry_scaffold_false_highest_first_after_the_build(scaffold_world, llm, store):
    world = scaffold_world
    plan, episode, outcome = _build(world, llm, store)
    swept = set(world.failed_cells + world.pillar_cells[:2])
    clears = [event for event in _steps_of(episode, "clear") if tuple(event["action_obj"]["pos"]) in swept]
    assert len(clears) == 5
    assert all(event["action_obj"]["scaffold"] is False for event in clears)
    heights = [event["action_obj"]["pos"][1] for event in clears]
    assert heights == sorted(heights, reverse=True)
    last_scan = max(event["index"] for event in _steps_of(episode, "place"))
    assert min(event["index"] for event in clears) > last_scan
    assert outcome.placed == outcome.total == 70


def test_a_build_that_does_not_match_clears_nothing(scaffold_world, llm, store):
    world = scaffold_world

    def never_match(world, plan):
        scan = world.scan
        cell = (plan.origin[0] + 1, plan.origin[1], plan.origin[2])
        world.scan = lambda origin, size: {**scan(origin, size), **({cell: "stone"} if size[1] > 1 else {})}
        world.inventory.update(oak_planks=70, oak_log=20, torch=1, dirt=16)

    plan, episode, outcome = _build(world, llm, store, check=never_match)
    assert outcome.reason == "1 cells don't match"
    assert all(world.block_at(cell) == "dirt" for cell in world.failed_cells + world.pillar_cells[:2])
    done = _events(episode, "build_done")[0]
    assert (done["scaffold_cleared"], done["scaffold_left"]) == (0, 0)


def test_a_failed_scan_or_clear_leaves_the_cell_and_goes_on(scaffold_world, llm, store):
    world = scaffold_world
    scan, act = world.scan, world.act

    def flaky(world, plan):
        def failing_scan(origin, size):
            if size == (1, 1, 1) and world.failed_cells and origin == world.failed_cells[2]:
                raise BackendError("bot gone")
            return scan(origin, size)

        def failing_act(action):
            if action.kind == "clear" and world.failed_cells and action.pos == world.failed_cells[1]:
                return world._fail("unreachable")
            return act(action)

        world.scan, world.act = failing_scan, failing_act

    plan, episode, outcome = _build(world, llm, store, check=flaky)
    assert outcome.result.success
    done = _events(episode, "build_done")[0]
    assert done["scaffold_cleared"] == 3 and done["scaffold_left"] == 3  # 2 failed + the changed one


# --- the backend ---------------------------------------------------------------------------


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def test_scaffold_false_sends_an_empty_list_and_none_the_usual_blocks(bot):
    backend = MineflayerBackend(api_url=bot.url, natural=True, arena=object())
    bot.responses["/clear"] = {"ok": True, "cleared": True, "removed": "dirt", "found": "air", "inventory": {}}
    bot.responses["/place"] = PLACED
    backend.act(Action("clear", pos=(1, 2, 3), scaffold=False))
    backend.act(Action("clear", pos=(1, 2, 3)))
    assert [request[1]["scaffold"] for request in bot.requests] == [[], list(SCAFFOLD_BLOCKS)]


def test_merge_travel_concatenates_placed_cells_in_order():
    first = {"distance": 1.0, "dug": {}, "placed": {"dirt": 1}, "picked_up": {}, "placed_cells": [[1, 2, 3, "dirt"]]}
    second = {"distance": 2.0, "dug": {}, "placed": {"dirt": 1}, "picked_up": {}, "placed_cells": [[4, 5, 6, "dirt"]]}
    older = {"distance": 0.5, "dug": {}, "placed": {}, "picked_up": {}}
    merged = _merge_travel([first, second, older])
    assert merged["placed_cells"] == [[1, 2, 3, "dirt"], [4, 5, 6, "dirt"]]
    assert merged["placed"] == {"dirt": 2} and merged["distance"] == 3.5
