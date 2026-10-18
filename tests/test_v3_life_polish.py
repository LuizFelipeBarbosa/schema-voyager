"""v3.0h small fixes in the life: play mode leaves the frontier out of the retests, a goal cancelled after its
last step says "Cancelled", and `!status` reports the queue between goals."""

from types import SimpleNamespace

from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec
from test_v3_life_queue import ChatWorld, chat, entry, run
from test_v3_retest_life import WORLD, SMELT, SMELT_MATCH, SMELT_WORN_PICKAXE, demote_after_confirmations, smelt_rule

# -- play mode and the frontier ----------------------------------------------------------------


def life_with_a_demoted_smelt(tmp_path, mode):
    """Nothing consumes iron_ingot, so only the frontier can make it needed."""
    life = _Life(
        SimpleNamespace(say=lambda message: None), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"),
        LifeConfig(run_dir=tmp_path / "run", world_id=WORLD, mode=mode), LifeHooks(), resume=False,
    )
    demote_after_confirmations(life.store, smelt_rule(), SMELT, SMELT_MATCH, SMELT_WORN_PICKAXE)
    life.curriculum.frontier = ["iron_ingot"]
    life.asked.add("iron_ingot")
    return life


def test_play_mode_does_not_retest_a_frontier_item_nobody_asked_for(tmp_path):
    life = life_with_a_demoted_smelt(tmp_path, "play")

    assert life._retest_demoted() is False
    assert not [rule for rule in life.store.rules.values() if rule.source == "retest"]


def test_learn_mode_retests_a_frontier_item(tmp_path):
    life = life_with_a_demoted_smelt(tmp_path, "learn")

    assert life._retest_demoted() is True
    assert [rule.revision_of for rule in life.store.rules.values() if rule.source == "retest"] == ["smelt_iron_ingot"]


# -- cancelled after the last step -------------------------------------------------------------


def cancel_when_torches_are_made(world, cancel):
    """Wrap `world.act`: `cancel()` runs right after the step that completes "4 torches"."""
    target = world.inventory["torch"] + 4
    real_act = world.act

    def act(action):
        outcome = real_act(action)
        if world.inventory["torch"] >= target:
            cancel()
        return outcome

    world.act = act


def test_a_goal_cancelled_in_the_queue_after_its_last_step_says_cancelled(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches")})
    cancel_when_torches_are_made(world, lambda: GoalQueue(tmp_path / "goals.jsonl").cancel("u001"))

    run(tmp_path, world, max_goals=1)

    assert entry(tmp_path, "u001").status == "cancelled"
    assert "Cancelled: 4 torches" in world.said
    assert "Done: 4 torches" not in world.said


def test_a_goal_cancelled_from_chat_after_its_last_step_says_cancelled_once(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches")})
    cancel_when_torches_are_made(world, lambda: world._emit(chat("cancel")))

    run(tmp_path, world, max_goals=1)

    assert entry(tmp_path, "u001").status == "cancelled"
    assert "Done: 4 torches" not in world.said
    assert [s for s in world.said if s.startswith("Cancelled")] == ["Cancelled u001: 4 torches"]


# -- !status between goals ---------------------------------------------------------------------


def status_life(tmp_path):
    return _Life(
        SimpleNamespace(say=lambda message: None), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"),
        LifeConfig(run_dir=tmp_path / "run", world_id=WORLD), LifeHooks(), resume=False,
    )


def test_status_with_goals_queued_names_the_next_one(tmp_path):
    life = status_life(tmp_path)
    life.queue.submit("4 torches")
    life.queue.accept(GoalSpec("u001", "obtain", "user", item="torch", count=4))
    life.queue.submit("2 sticks")
    life.queue.accept(GoalSpec("u002", "obtain", "user", item="stick", count=2))

    assert life._status_line() == "Next: u001: 4 torches. Queued: 2."


def test_status_names_a_queued_system_goal_before_a_user_goal(tmp_path):
    life = status_life(tmp_path)
    life.queue.submit("4 torches")
    life.queue.accept(GoalSpec("u001", "obtain", "user", item="torch", count=4))
    life.queue.submit("hunger: eat", source="system")
    life.queue.accept(GoalSpec("s001", "obtain", "system", item="stick", count=2))

    assert life._status_line() == "Next: s001: 2 sticks. Queued: 2."


def test_status_with_nothing_queued_is_idle(tmp_path):
    life = status_life(tmp_path)
    life.queue.submit("4 torches")
    life.queue.accept(GoalSpec("u001", "obtain", "user", item="torch", count=4))
    life.queue.mark("u001", "cancelled")

    assert life._status_line() == "Idle."
