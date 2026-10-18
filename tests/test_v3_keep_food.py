"""Raw food kept from auto-eat for a smelt (v3.0i): the backend call and the life's use of it."""

import pytest

from schema_voyager import life
from schema_voyager.curriculum import Goal
from schema_voyager.environment import NaturalWorld
from schema_voyager.goals import GoalInterrupted
from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend
from schema_voyager.models import GoalSpec, Rule

WORLD = "life-keep-food-test"


def smelt_rule(rule_id, ingredient, product):
    return Rule(
        id=rule_id,
        action={"type": "smelt", "item": product},
        preconditions={"consumes": {ingredient: 1}, "station": "furnace", "fuel_any": ["coal", "#planks"]},
        effects={"gains": {product: 1}},
        scope=WORLD,
    )


class RecordingWorld(NaturalWorld):
    def __init__(self):
        super().__init__(1)
        self.calls = []
        self.keep_food_error = None

    def keep_food(self, items):
        self.calls.append(("keep_food", list(items)))
        if self.keep_food_error and not items:
            raise self.keep_food_error


def new_life(tmp_path, world, resume=False):
    config = LifeConfig(run_dir=tmp_path, world_id=WORLD)
    return _Life(world, LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=resume)


def make_life(tmp_path, world):
    life_ = new_life(tmp_path, world)
    world.calls.clear()  # the start's own reset is tested apart
    return life_


def run_goal(life_, item, runner):
    life.run_schemavoyager = runner
    spec = GoalSpec("g001", "obtain", "curriculum", item=item, count=4)
    life_._run_goal(spec, Goal(item, 4, 1, (), "test goal"))


@pytest.fixture(autouse=True)
def restore_runner(monkeypatch):
    monkeypatch.setattr(life, "run_schemavoyager", life.run_schemavoyager)


def acting(world, success):
    def runner(episode, store, before_step, **kwargs):
        world.calls.append(("act", "mine"))
        return episode.finish(success=success)

    return runner


def interrupted(episode, store, before_step, **kwargs):
    raise GoalInterrupted("g001", "cancelled")


@pytest.mark.parametrize("ending", ["success", "failure", "interrupted"])
def test_a_food_goal_keeps_the_raw_item_until_the_goal_ends(tmp_path, ending):
    world = RecordingWorld()
    life_ = make_life(tmp_path, world)
    life_.store.add_rule(smelt_rule("smelt_beef", "beef", "cooked_beef"))
    runner = interrupted if ending == "interrupted" else acting(world, ending == "success")

    run_goal(life_, "cooked_beef", runner)

    keep_calls = [c for c in world.calls if c[0] == "keep_food"]
    assert keep_calls == [("keep_food", ["beef", "cooked_beef"]), ("keep_food", [])]
    if ending != "interrupted":
        assert world.calls.index(keep_calls[0]) < world.calls.index(("act", "mine"))
    assert world.calls[-1] == ("keep_food", [])


def test_every_usable_smelt_rule_for_the_food_adds_its_ingredient_but_not_the_fuel(tmp_path):
    world = RecordingWorld()
    life_ = make_life(tmp_path, world)
    life_.store.add_rule(smelt_rule("smelt_beef", "beef", "cooked_beef"))
    life_.store.add_rule(smelt_rule("smelt_mutton", "mutton", "cooked_beef"))
    life_.store.add_rule(smelt_rule("smelt_iron", "raw_iron", "iron_ingot"))

    run_goal(life_, "cooked_beef", acting(world, True))

    assert world.calls[0] == ("keep_food", ["beef", "cooked_beef", "mutton"])


def test_a_goal_for_a_non_food_item_calls_nothing(tmp_path):
    world = RecordingWorld()
    life_ = make_life(tmp_path, world)
    life_.store.add_rule(smelt_rule("smelt_iron", "raw_iron", "iron_ingot"))

    run_goal(life_, "iron_ingot", acting(world, True))

    assert [c for c in world.calls if c[0] == "keep_food"] == []


def test_a_food_with_no_smelt_rule_keeps_just_itself(tmp_path):
    world = RecordingWorld()
    life_ = make_life(tmp_path, world)

    run_goal(life_, "bread", acting(world, True))

    assert [c for c in world.calls if c[0] == "keep_food"] == [("keep_food", ["bread"]), ("keep_food", [])]


def test_with_the_bot_gone_the_clear_is_skipped(tmp_path):
    world = RecordingWorld()
    life_ = make_life(tmp_path, world)
    life_.store.add_rule(smelt_rule("smelt_beef", "beef", "cooked_beef"))

    def runner(episode, store, before_step, **kwargs):
        life_._bot_gone = True
        return episode.finish(success=False)

    run_goal(life_, "cooked_beef", runner)

    assert [c for c in world.calls if c[0] == "keep_food"] == [("keep_food", ["beef", "cooked_beef"])]


def test_a_failed_clear_is_only_printed(tmp_path, capsys):
    world = RecordingWorld()
    world.keep_food_error = BackendError("bot down")
    life_ = make_life(tmp_path, world)
    life_.store.add_rule(smelt_rule("smelt_beef", "beef", "cooked_beef"))

    run_goal(life_, "cooked_beef", acting(world, True))

    assert "could not clear keep_food" in capsys.readouterr().out


@pytest.mark.parametrize("resume", [False, True])
def test_a_life_starts_by_clearing_what_an_earlier_life_kept(tmp_path, resume):
    if resume:
        first = new_life(tmp_path, RecordingWorld())  # the state to resume
        first._save_state()
        first.store.save(first.store_path)
    world = RecordingWorld()

    new_life(tmp_path, world, resume=resume)

    assert world.calls == [("keep_food", [])]


def test_a_failed_clear_at_the_start_is_only_printed(tmp_path, capsys):
    world = RecordingWorld()
    world.keep_food_error = BackendError("bot down")

    new_life(tmp_path, world)

    assert "could not clear keep_food at the start" in capsys.readouterr().out


def test_the_backend_posts_the_items(monkeypatch):
    backend = MineflayerBackend.__new__(MineflayerBackend)
    posted = []
    monkeypatch.setattr(backend, "_post", lambda path, body: posted.append((path, body)) or {"ok": True}, raising=False)

    backend.keep_food(("beef", "mutton"))
    backend.keep_food([])

    assert posted == [("/keep_food", {"items": ["beef", "mutton"]}), ("/keep_food", {"items": []})]
