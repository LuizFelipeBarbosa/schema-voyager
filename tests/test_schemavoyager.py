import ast
from pathlib import Path

from schema_voyager.agent import run_schemavoyager, seed_prior_rules
from schema_voyager.baseline import SkillLibrary, run_skill_only
from schema_voyager.demo import Runner, _fake_baseline_planner
from schema_voyager.environment import SimulatedWorld
from schema_voyager.llm import LLM, fixture_prior_rules
from schema_voyager.models import Action, Outcome
from schema_voyager.planner import make_plan
from schema_voyager.schema_store import SchemaStore, Transition
from schema_voyager.tasks import get_task, training_tasks
from schema_voyager.verifier import check

SRC = Path(__file__).resolve().parents[1] / "src" / "schema_voyager"


def _trained(tmp_path, layouts=(1, 2)):
    runner = Runner(SimulatedWorld(), LLM(mode="fake"), tmp_path)
    store = SchemaStore()
    seed_prior_rules(store, runner.llm)
    for task in training_tasks():
        for seed in layouts:
            assert runner.run("D", task, seed, store=store).success
    return runner, store


def test_nothing_is_certified_after_one_episode(tmp_path):
    runner = Runner(SimulatedWorld(), LLM(mode="fake"), tmp_path)
    store = SchemaStore()
    seed_prior_rules(store, runner.llm)
    runner.run("D", get_task("t1"), 1, store=store)
    assert all(rule.status == "hypothesis" for rule in store.rules.values())


def test_training_certifies_the_rules_h3_relies_on(tmp_path):
    _, store = _trained(tmp_path)
    for rule_id in ("mine_coal_ore", "craft_torch", "craft_stone_pickaxe", "mine_stone"):
        assert store.rules[rule_id].status == "certified", rule_id
    assert "mine_coal_ore" in store.skills["skill_torch"].rule_ids


def test_holdout_transitions_never_count_for_train_rules():
    store = SchemaStore()
    rule = next(r for r in fixture_prior_rules() if r.id == "mine_coal_ore")
    store.add_rule(rule)
    action = Action("mine", "coal_ore", tool="wooden_pickaxe")
    store.record_transition(Transition("x/1", "x", "w123", action, {"block_removed": True, "drops": {}}))
    confirmed, contradicted = store.replay(rule)
    assert confirmed == [] and contradicted == []


def test_demotion_flags_dependent_skills_only(tmp_path):
    _, store = _trained(tmp_path)
    flagged = store.demote("mine_coal_ore", "D/h3/L6/10", "w1")
    assert flagged == ["skill_torch"]
    assert store.skills["skill_wooden_pickaxe"].status == "ok"
    assert "mine_coal_ore" not in {r.id for r in store.usable_rules("w1")}
    assert "mine_coal_ore" in {r.id for r in store.usable_rules("w2")}


def test_verifier_flags_missing_drop_and_tolerates_pickup():
    rule = next(r for r in fixture_prior_rules() if r.id == "mine_coal_ore")
    action = Action("mine", "coal_ore", tool="wooden_pickaxe")
    no_drop = Outcome(ok=True, block_removed=True, drops={}, picked_up={})
    assert check(rule, action, no_drop).status == "mismatch"
    rolled_away = Outcome(ok=True, block_removed=True, drops={"coal": 1}, picked_up={})
    assert check(rule, action, rolled_away).status == "match"


def test_planner_skips_steps_whose_preconditions_hold():
    # 5 planks is exactly enough: 3 for the wooden pickaxe, 2 for four sticks.
    rules = fixture_prior_rules()
    plan = make_plan("stone_pickaxe", 1, {"crafting_table": 1, "oak_planks": 5}, rules)
    assert not any(step.action == Action("mine", "oak_log") for step in plan)
    assert not any(step.action.target == "crafting_table" for step in plan)


def test_d_catches_r2_at_the_mine_step_and_recovers(tmp_path):
    runner, store = _trained(tmp_path)
    result = runner.run("D", get_task("h3"), 6, store=store)
    assert result.success
    assert result.contradictions == 1 and result.revisions == 1
    assert result.actions_after_contradiction == 0
    assert result.repeated_failures == 0
    assert store.skills["skill_torch"].status == "flagged"
    revision = next(r for r in store.rules.values() if r.revision_of == "mine_coal_ore" and r.rank == 0)
    assert revision.status == "hypothesis"  # confirmed once in this world only
    assert store.replay(revision)[0], "the revision was confirmed in its world"


def test_fake_baseline_repeats_the_failure_in_h3(tmp_path):
    runner = Runner(SimulatedWorld(), LLM(mode="fake"), tmp_path)
    library = SkillLibrary()
    result = runner.run("B", get_task("h3"), 6, library=library)
    assert not result.success
    assert result.repeated_failures >= 1


def test_planner_and_agent_never_import_the_simulator():
    for module in ("planner.py", "agent.py", "schema_store.py", "verifier.py", "baseline.py"):
        tree = ast.parse((SRC / module).read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert "environment" not in imported, module


def test_b_plus_feedback_reports_each_actions_inventory_change(tmp_path):
    runner = Runner(SimulatedWorld(), LLM(mode="fake"), tmp_path)
    result = runner.run("B+", get_task("h3"), 6, library=SkillLibrary())
    feedback = [e["summary"] for e in _events(tmp_path, "B+_h3_L6") if e["kind"] == "feedback"]
    assert not result.success
    assert "mine coal_ore with wooden_pickaxe (inventory change: none)" in feedback[0]


def _events(run_dir, name):
    import json

    return [json.loads(line) for line in (run_dir / "episodes" / f"{name}.jsonl").read_text().splitlines()]
