"""The M4 per-seed table (seed_report.py) on small synthetic run directories, and on a
short simulator life. The shell scripts need a live instance and are not run here."""

import json
from pathlib import Path

from schema_voyager import seed_report
from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def step(ok=True, error=None, block=None, inventory=None) -> dict:
    return {"t": 1.0, "kind": "step", "ok": ok, "error": error, "inventory": inventory or {},
            "detail": {"block": block} if block else {}}


def rule(rule_id: str, status: str, action: dict) -> dict:
    return {"id": rule_id, "status": status, "action": action}


def make_run(root: Path, name: str, *, goals=(), steps=(), events=(), rules=(), changes=None, world=None,
             skipped=None, life_out=None) -> Path:
    """goals: (item, outcome) pairs. events: extra trace events beside the steps."""
    run = root / name
    write_jsonl(run / "life.jsonl", [
        {"index": i, "goal": item, "outcome": outcome, "llm_calls": 1, "tokens": 100, "spend_usd": 0.01 * i}
        for i, (item, outcome) in enumerate(goals, 1)
    ])
    write_jsonl(run / "episodes" / "D_life_g001_x.jsonl", [*steps, *events])
    (run / "store.json").write_text(json.dumps({"rules": list(rules)}), encoding="utf-8")
    (run / "life_state.json").write_text(
        json.dumps({"world_id": f"life-{name}-1", "curriculum": {"skipped": skipped or {}}}), encoding="utf-8")
    if changes is not None:
        write_jsonl(run / "changes.jsonl", changes)
    if world is not None:
        (run / "world.json").write_text(json.dumps(world), encoding="utf-8")
    if life_out is not None:
        (run / "life.out").write_text(life_out, encoding="utf-8")
    return run


COAL = rule("mine_coal_ore", "certified", {"type": "mine", "block": "coal_ore"})
CHANGE = {"t": 50.0, "change": "r2", "action": "mine", "subject": "coal_ore", "goal": "g008"}


def contradiction(t: float, rule_id="mine_coal_ore") -> dict:
    return {"t": t, "kind": "contradiction", "rule": rule_id}


def test_row_columns(tmp_path):
    run = make_run(
        tmp_path, "m4-11",
        goals=[("stone_pickaxe", "success"), ("iron_pickaxe", "failed")],
        steps=[
            step(block="oak_log"), step(block="birch_log"), step(block="stone"), step(block="oak_log"),
            step(ok=False, error="no_path: blocked"), step(ok=False, error="no_path"), step(ok=False, error="timeout"),
        ],
        events=[contradiction(100.0), {"t": 2.0, "kind": "quarantined", "reason": "x"},
                {"t": 3.0, "kind": "quarantined", "reason": "y"}],
        rules=[COAL, rule("craft_stick", "certified", {"type": "craft", "item": "stick"}),
               rule("craft_x", "hypothesis", {"type": "craft", "item": "x"})],
        changes=[CHANGE], world={"seed": 11, "instance": "a", "biome": "plains"},
        skipped={"furnace": "failed 2 attempts"}, life_out="...\nLife over: wall_clock\n",
    )
    row = seed_report.run_row(run)
    assert (row["seed"], row["biome"], row["wood"]) == ("11", "plains", "birch,oak")
    assert row["highest"] == "stone_pickaxe"
    assert (row["certified"], row["contradictions"], row["false_contradictions"], row["quarantined"]) == (2, 1, 0, 2)
    assert row["stuck"] == "no_path x2; timeout x1; skipped furnace; stop wall_clock"
    assert (row["llm_calls"], row["tokens"], row["spend_usd"]) == (2, 200, 0.02)
    assert row["has_changes"] and row["change_caught"]


def test_defaults_without_world_or_life_out(tmp_path):
    row = seed_report.run_row(make_run(tmp_path, "sim", goals=[("stick", "success")]))
    assert (row["seed"], row["biome"], row["wood"], row["highest"]) == ("life-sim-1", "?", "-", "none")
    assert row["stuck"] == "stop ?"


def test_false_contradiction_classification(tmp_path):
    rules = [COAL]
    no_file = seed_report.run_row(make_run(tmp_path, "a", events=[contradiction(100.0)], rules=rules))
    assert (no_file["false_contradictions"], no_file["has_changes"]) == (1, False)

    caught = seed_report.run_row(make_run(tmp_path, "b", events=[contradiction(100.0)], rules=rules, changes=[CHANGE]))
    assert (caught["false_contradictions"], caught["change_caught"]) == (0, True)

    # The change comes after the contradiction: the contradiction was not caused by it.
    late = seed_report.run_row(make_run(tmp_path, "c", events=[contradiction(10.0)], rules=rules, changes=[CHANGE]))
    assert (late["false_contradictions"], late["change_caught"]) == (1, False)

    # A different action or subject does not match; a deepslate variant of the subject does.
    other = {**CHANGE, "subject": "iron_ore"}
    assert seed_report.run_row(make_run(tmp_path, "d", events=[contradiction(100.0)], rules=rules,
                                        changes=[other]))["false_contradictions"] == 1
    variant = {**CHANGE, "subject": "deepslate_coal_ore"}
    assert seed_report.run_row(make_run(tmp_path, "e", events=[contradiction(100.0)], rules=rules,
                                        changes=[variant]))["false_contradictions"] == 0
    craft = {**CHANGE, "action": "craft"}
    assert seed_report.run_row(make_run(tmp_path, "f", events=[contradiction(100.0)], rules=rules,
                                        changes=[craft]))["false_contradictions"] == 1

    # A revised rule id falls back to the rule it revises; an unknown rule is false.
    revised = seed_report.run_row(make_run(tmp_path, "g", events=[contradiction(100.0, "mine_coal_ore.rev1@w")],
                                           rules=rules, changes=[CHANGE]))
    assert revised["false_contradictions"] == 0
    unknown = seed_report.run_row(make_run(tmp_path, "h", events=[contradiction(100.0, "nope")], rules=rules,
                                           changes=[CHANGE]))
    assert unknown["false_contradictions"] == 1


def test_highest_goal_ordering(tmp_path):
    def highest(name, goals=(), inventory=None):
        steps = [step(inventory=inventory)] if inventory else []
        return seed_report.run_row(make_run(tmp_path, name, goals=goals, steps=steps))["highest"]

    assert highest("none", goals=[("stick", "success")]) == "none"
    assert highest("failed", goals=[("stone_pickaxe", "failed")]) == "none"
    assert highest("wood", inventory={"wooden_pickaxe": 1}) == "wooden_pickaxe"
    assert highest("stone", goals=[("wooden_pickaxe", "success"), ("stone_pickaxe", "success")]) == "stone_pickaxe"
    assert highest("mixed", goals=[("stone_pickaxe", "success")], inventory={"iron_pickaxe": 1}) == "iron_pickaxe"


def test_all_row_and_acceptance(tmp_path):
    good = [seed_report.run_row(make_run(tmp_path, f"s{i}", goals=[("stone_pickaxe", "success")])) for i in range(3)]
    weak = seed_report.run_row(make_run(tmp_path, "weak", goals=[("wooden_pickaxe", "success")]))

    verdict = seed_report.acceptance([*good, weak])
    assert verdict == {"false_contradictions_zero": True, "stone_tier_runs": 3, "stone_tier_ok": True,
                       "change_caught": None}
    assert not seed_report.acceptance([good[0], weak])["stone_tier_ok"]

    false_run = seed_report.run_row(make_run(tmp_path, "bad", goals=[("stone_pickaxe", "success")],
                                             events=[contradiction(1.0)], rules=[COAL]))
    assert not seed_report.acceptance([*good, false_run])["false_contradictions_zero"]

    caught = seed_report.run_row(make_run(tmp_path, "caught", events=[contradiction(100.0)], rules=[COAL],
                                          changes=[CHANGE]))
    assert seed_report.acceptance([*good, caught])["change_caught"] is True

    table = seed_report.render_table([*good, weak, caught]).splitlines()
    assert len(table) == 2 + 5 + 1  # header, separator, 5 runs, "all"
    assert table[-1].startswith("| all |")
    assert "stone+ on 3/5: yes" in table[-1] and "change caught: yes" in table[-1]


def test_main_prints_table_and_writes_json(tmp_path, capsys):
    run = make_run(tmp_path, "m4-5", goals=[("stone_pickaxe", "success")], world={"seed": 5, "biome": "desert"})
    out = tmp_path / "report.json"
    seed_report.main([str(run), "--json", str(out)])
    assert "| 5 | desert |" in capsys.readouterr().out
    assert json.loads(out.read_text())["rows"][0]["seed"] == "5"


def test_report_of_a_simulator_life(tmp_path):
    run_dir = tmp_path / "sim-life"
    summary = run_life(
        NaturalWorld(seed=1), LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl"),
        LifeConfig(run_dir=run_dir, world_id="life-sim1-test", max_goals=6),
    )
    assert summary["goals"] == 6

    row = seed_report.run_row(run_dir)
    assert row["seed"] == "life-sim1-test" and row["biome"] == "?"
    assert row["false_contradictions"] == row["contradictions"]  # no changes.jsonl
    assert row["llm_calls"] > 0
    assert seed_report.render_table([row]).splitlines()[-1].startswith("| all |")
