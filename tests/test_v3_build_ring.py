"""The ring scan over a goal's attempts (v3.0i): the first attempt scans before gathering and the life keeps the scan,
so scaffold an earlier, requeued attempt left in the ring is cleared by the attempt whose build matches."""

import pytest

from schema_voyager import blueprint
from schema_voyager.build_runner import run_build
from schema_voyager.goals import GoalInterrupted
from tests.test_v3_build_run import NO_HOOK, _episode, _events, _prepare, llm, store  # noqa: F401
from tests.test_v3_build_scaffold import RingWorld, _clears


def _attempts(world, llm, store, forget=False):
    """Two attempts at one goal: the first is preempted right after the world set its pillars, the second finishes.
    `forget` gives the second attempt a life that
    kept no scan (a resumed life). Returns the second's episode and outcome, and the origins of the ring scans taken before the build or after it."""
    plan = _prepare(world, store)
    x, _, z = plan.origin
    protected = [((x - 4, 0, z - 4), (3, 255, 3))]
    plan = _prepare(world, store, origin=plan.origin, protected=protected)
    world.plan = plan
    (ox, oy, oz), (sx, sy, sz) = blueprint.site_box(plan.spec.blueprint, plan.origin)
    ring = ((ox - 5, oy - 1, oz - 5), (sx + 10, sy + 2, sz + 10))
    ring_scans = {}
    taken = []
    scan = world.scan

    def counting_scan(origin, size):
        if (origin, size) == ring:
            taken.append(origin)
        return scan(origin, size)

    world.scan = counting_scan

    def preempt_once_placed():
        if world.placed:
            raise GoalInterrupted(plan.spec.id, "preempted")

    with pytest.raises(GoalInterrupted):
        run_build(_episode(world, llm, plan), store, plan, before_step=NO_HOOK, check_interrupts=preempt_once_placed,
                  excluded=frozenset(), ring_scans=ring_scans)
    assert len(taken) == 1  # the first attempt scanned once, before gathering

    again = _prepare(world, store, origin=plan.origin, protected=protected)
    episode = _episode(world, llm, again)
    outcome = run_build(episode, store, again, before_step=NO_HOOK, check_interrupts=lambda: None,
                        excluded=frozenset(), ring_scans={} if forget else ring_scans)
    return episode, outcome, taken


def test_scaffold_an_earlier_attempt_left_is_cleared_by_the_attempt_that_matches(llm, store):
    world = RingWorld(7, reported=False)
    episode, outcome, taken = _attempts(world, llm, store)
    assert outcome.result.success
    assert len(taken) == 2  # one before the build across both attempts, plus the final one
    assert all(world.block_at(cell) == "air" for cell in world.gathering + world.pillar)
    done = _events(episode, "build_done")[0]
    assert done["scaffold_from"]["ring"] == 6
    assert (done["scaffold_cleared"], done["scaffold_left"]) == (6, 0)


def test_a_second_attempt_that_scans_again_takes_the_leftovers_for_natural(llm, store):
    """The gap: a resumed life has no stored scan, so it scans again and the first attempt's pillars stay."""
    world = RingWorld(7, reported=False)
    episode, outcome, taken = _attempts(world, llm, store, forget=True)
    assert outcome.result.success and len(taken) == 3
    assert all(world.block_at(cell) == "dirt" for cell in world.pillar)


def test_a_cell_that_held_dirt_before_the_first_attempt_is_never_cleared(llm, store):
    world = RingWorld(7, reported=False)
    plan = _prepare(world, store)
    old_dirt = [(plan.origin[0] + 4, world._column(plan.origin[0] + 4, plan.origin[2] - 5)[0] + 1, plan.origin[2] - 5)]
    world.set(old_dirt, "dirt")
    episode, outcome, _ = _attempts(world, llm, store)
    assert outcome.result.success
    assert world.block_at(old_dirt[0]) == "dirt"
    assert old_dirt[0] not in _clears(episode)


def test_a_cell_in_a_protected_box_is_never_cleared_across_attempts(llm, store):
    world = RingWorld(7, reported=False)
    episode, outcome, _ = _attempts(world, llm, store)
    assert outcome.result.success
    assert world.block_at(world.protected_pillar[0]) == "dirt"
    assert world.protected_pillar[0] not in _clears(episode)
