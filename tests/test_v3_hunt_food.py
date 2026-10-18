"""Food from hunting (v3.3h): observed mobs join the priors, cooked meats are whitelisted and the hunger goal
prefers foods reached through kills over rare drops, in the simulator with the fake LLM."""

import json
import time

import pytest

from schema_voyager import life
from schema_voyager.curriculum import WHITELIST
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalQueue
from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.llm import LLM
from schema_voyager.models import GoalSpec, Rule

WORLD_ID = "life-sim-hunt-food-test"
COW_SEED, NO_MOB_SEED = 2, 8


def new_life(tmp_path, seed=COW_SEED, resume=False, mode="learn"):
    config = LifeConfig(run_dir=tmp_path, world_id=WORLD_ID, mode=mode)
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    return _Life(NaturalWorld(seed), llm, config, LifeHooks(), resume=resume)


def record_prior_calls(life_):
    """The (families, mobs) of every prior call the life makes."""
    calls = []
    ask = life_.llm.propose_prior_rules_v2

    def recording(families, known_blocks, world_id, mobs=()):
        calls.append((list(families), list(mobs)))
        return ask(families, known_blocks, world_id, mobs=mobs)

    life_.llm.propose_prior_rules_v2 = recording
    return calls


def queue_kill(life_, text, mob, count=1):
    goal_id = life_.queue.submit(text, "user")
    life_.queue.accept(GoalSpec(id=goal_id, kind="kill", source="user", text=text, target=mob, count=count))


def system_goals(run_dir):
    return [g for g in GoalQueue(run_dir / "goals.jsonl").goals() if g.source == "system"]


def saved_state(run_dir):
    return json.loads((run_dir / "life_state.json").read_text(encoding="utf-8"))


# -- the whitelist -------------------------------------------------------------


def test_the_cooked_meats_are_whitelisted_but_not_bread_or_leather():
    assert {"cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton"} <= set(WHITELIST)
    assert not {"bread", "leather", "leather_helmet"} & set(WHITELIST)


# -- the priors ------------------------------------------------------------------


def test_observed_cows_are_asked_once(tmp_path):
    cowherd = new_life(tmp_path)
    calls = record_prior_calls(cowherd)

    cowherd._learn_priors()
    cowherd._learn_priors()

    assert [mobs for _, mobs in calls].count(["cow"]) == 1
    assert sum(bool(mobs) for _, mobs in calls) == 1
    assert "kill_cow" in cowherd.store.rules
    assert cowherd.asked_mobs == {"cow"}


def test_cooked_beef_is_asked_once_kill_cow_is_known(tmp_path):
    cowherd = new_life(tmp_path, mode="play")
    calls = record_prior_calls(cowherd)

    cowherd._learn_priors()

    assert "cooked_beef" in [family for families, _ in calls[1:] for family in families]
    assert "smelt_cooked_beef" in cowherd.store.rules


def test_a_kill_goals_target_is_asked_as_a_mob(tmp_path):
    hunter = new_life(tmp_path, seed=NO_MOB_SEED)
    queue_kill(hunter, "kill 2 zombies", "zombie", count=2)
    calls = record_prior_calls(hunter)

    hunter._learn_priors()

    assert [mobs for _, mobs in calls] == [["zombie"]]
    assert "kill_zombie" in hunter.store.rules


def test_mobs_alone_still_make_a_prior_call_with_no_families(tmp_path):
    hunter = new_life(tmp_path, seed=NO_MOB_SEED, mode="play")
    hunter._learn_priors()  # asks the observed blocks
    queue_kill(hunter, "kill a zombie", "zombie")
    calls = record_prior_calls(hunter)

    hunter._learn_priors()

    assert calls[0] == ([], ["zombie"])


def test_observed_and_asked_mobs_persist_and_resume(tmp_path):
    cowherd = new_life(tmp_path)
    cowherd._learn_priors()
    cowherd._save_state()
    cowherd.store.save(tmp_path / "store.json")

    state = saved_state(tmp_path)
    assert list(state["observed_mobs"]) == ["cow"] and state["asked_mobs"] == ["cow"]
    assert abs(state["observed_mobs"]["cow"] - time.time()) < 60

    resumed = new_life(tmp_path, resume=True)
    calls = record_prior_calls(resumed)
    assert resumed.observed_mobs == state["observed_mobs"] and resumed.asked_mobs == {"cow"}
    resumed._learn_priors()
    assert all(not mobs for _, mobs in calls)


def test_a_state_file_without_observed_mobs_resumes(tmp_path):
    saved = new_life(tmp_path)
    saved._save_state()
    saved.store.save(tmp_path / "store.json")
    state = saved_state(tmp_path)
    del state["observed_mobs"], state["asked_mobs"]
    (tmp_path / "life_state.json").write_text(json.dumps(state), encoding="utf-8")

    resumed = new_life(tmp_path, resume=True)

    assert resumed.observed_mobs == {} and resumed.asked_mobs == set()


# -- the hunger ranking ------------------------------------------------------------


def leaves_rule():
    return Rule(
        id="mine_oak_leaves", action={"type": "mine", "block": "oak_leaves"}, preconditions={"tool_any": []},
        effects={"block_removed": True, "drops": {"apple": 1}, "allowed": {"apple": [0, 1]}},
        source="prior", scope=WORLD_ID, check="stochastic",
    )


@pytest.fixture
def hungry(tmp_path):
    """A hungry life holding a furnace and coal, that knows the kill and smelt rules of every meat and apples
    from leaves. `seen` sets when each mob was last seen, in seconds ago."""
    hungry = new_life(tmp_path)
    rules = hungry.llm.propose_prior_rules_v2(
        ["cooked_beef", "cooked_porkchop", "cooked_chicken"], [], WORLD_ID, mobs=["cow", "pig", "chicken"]
    )
    for rule in [*rules, leaves_rule()]:
        hungry.store.add_rule(rule)
    hungry._inventory_seen = {"furnace": 1, "coal": 8}
    hungry._food = 6
    hungry.seen = lambda **ago: hungry.observed_mobs.update({mob: time.time() - s for mob, s in ago.items()})
    return hungry


def queued_food(tmp_path, hungry):
    hungry._queue_hunger_goal()
    return [g.text for g in system_goals(tmp_path)]


def test_cooked_beef_through_kill_and_smelt_beats_apples_from_leaves(tmp_path, hungry, monkeypatch):
    monkeypatch.setattr(life, "vanilla_foods", lambda: {"apple": 10, "cooked_beef": 8})  # apple would win on points
    hungry.seen(cow=1)

    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_beef"]


def test_apples_from_leaves_are_a_stochastic_plan(hungry):
    plan = hungry._reachable_plan("apple", life.HUNGER_FOOD_COUNT, life.HUNGER_RETRY_S)

    assert [step.rule.id for step in plan] == ["mine_oak_leaves"] * 4


def test_a_meat_whose_mob_was_not_seen_for_600_s_loses_to_one_whose_mob_was(tmp_path, hungry):
    hungry.seen(cow=601, pig=5)  # beef and porkchop are both 8 points: only the sighting separates them

    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_porkchop"]


def test_a_mob_never_observed_counts_as_unseen(tmp_path, hungry):
    hungry.seen(pig=5)

    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_porkchop"]


def test_a_stale_meat_still_beats_apples_when_it_is_the_only_unseen_one(tmp_path, hungry, monkeypatch):
    monkeypatch.setattr(life, "vanilla_foods", lambda: {"apple": 10, "cooked_beef": 8})
    hungry.seen(cow=3000)

    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_beef"]  # the stochastic mine ranks first


def test_points_then_name_break_ties(tmp_path, hungry):
    hungry.seen(cow=1, pig=1, chicken=1)
    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_beef"]  # 8 points, before cooked_porkchop by name

    hungry.queue.mark(system_goals(tmp_path)[0].id, "running")
    hungry.queue.mark(system_goals(tmp_path)[0].id, "done")
    hungry._system_failed_at["cooked_beef"] = time.monotonic()
    hungry._system_failed_at["cooked_porkchop"] = time.monotonic()
    hungry._queue_hunger_goal()
    assert [g.text for g in system_goals(tmp_path)][-1] == "hunger: 4 cooked_chicken"  # 6 points


def test_the_900_s_skip_still_applies(tmp_path, hungry):
    hungry.seen(cow=1, pig=1)
    hungry._system_failed_at["cooked_beef"] = time.monotonic()

    assert queued_food(tmp_path, hungry) == ["hunger: 4 cooked_porkchop"]


def test_the_hungry_log_stays_when_nothing_is_reachable(tmp_path):
    starving = new_life(tmp_path)
    starving._food = 6

    starving._queue_hunger_goal()

    assert system_goals(tmp_path) == []
    assert [e for e in map(json.loads, (tmp_path / "life_events.jsonl").read_text().splitlines()) if e["kind"] == "hungry"]


# -- end to end ----------------------------------------------------------------------


def test_a_hungry_life_with_cows_in_view_queues_cooked_beef_through_kill_and_smelt(tmp_path):
    world = NaturalWorld(COW_SEED)
    world.food = 6
    config = LifeConfig(run_dir=tmp_path, world_id=WORLD_ID)
    hunter = _Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=False)

    hunter._poll_bot()
    hunter._learn_priors()
    hunter._queue_hunger_goal()

    (goal,) = system_goals(tmp_path)
    assert (goal.text, goal.spec.kind, goal.spec.item, goal.source) == ("hunger: 4 cooked_beef", "obtain", "cooked_beef", "system")
    plan = hunter._reachable_plan("cooked_beef", life.HUNGER_FOOD_COUNT, life.HUNGER_RETRY_S)
    assert {"kill", "smelt"} <= {step.action.kind for step in plan}
