"""The M4 per-seed table: one row per life run directory, plus an "all" row with
the acceptance check.

    python -m schema_voyager.seed_report runs/m4-<seed> ... [--json out.json]

A run directory holds life.jsonl, episodes/*.jsonl, store.json, life_state.json
and, when the scripts made it, world.json (seed, spawn biome), changes.jsonl
(injected rule changes) and life.out (the life command's output, for the stop
reason). Design: docs/v2-interfaces.md, "The life loop".
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import normalize

# Best first.
PICKAXE_TIERS = ("iron_pickaxe", "stone_pickaxe", "wooden_pickaxe")
STONE_TIER_OR_BETTER = ("iron_pickaxe", "stone_pickaxe")
MIN_STONE_TIER_SHARE = 0.75

COLUMNS = (
    ("seed", "seed"), ("biome", "spawn biome"), ("wood", "wood species"), ("highest", "highest goal"),
    ("certified", "rules certified"), ("contradictions", "contradictions"),
    ("false_contradictions", "false contradictions"), ("quarantined", "quarantined"),
    ("stuck", "stuck causes"), ("llm_calls", "LLM calls"), ("tokens", "tokens"), ("spend_usd", "spend USD"),
)


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _subject_key(action: str, subject: str) -> tuple[str, str]:
    return action, normalize.family(subject)


def _rule_key(rule_id: str, rules_by_id: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    """(action, normalized subject) of the rule a contradiction names, or None if unknown."""
    rule = rules_by_id.get(rule_id) or rules_by_id.get(rule_id.split(".")[0])
    if rule is None:
        return None
    action = rule["action"]
    subject = action["block"] if action["type"] == "mine" else action["item"]
    return _subject_key(action["type"], subject)


def _stop_reason(run_dir: Path) -> str:
    out = run_dir / "life.out"
    if not out.exists():
        return "?"
    found = re.findall(r"Life over: (\w+)", out.read_text(encoding="utf-8", errors="replace"))
    return found[-1] if found else "?"


def _highest_goal(goals: list[dict[str, Any]], steps: list[dict[str, Any]]) -> str:
    reached = {goal["goal"] for goal in goals if goal.get("outcome") == "success"}
    for step in steps:
        reached |= set(step.get("inventory") or {})
    return next((tier for tier in PICKAXE_TIERS if tier in reached), "none")


def _stuck_causes(steps: list[dict[str, Any]], skipped: dict[str, str], stop_reason: str) -> str:
    errors = Counter(str(step["error"]).split(":")[0].strip() for step in steps if not step.get("ok") and step.get("error"))
    parts = [f"{code} x{count}" for code, count in errors.most_common(3)]
    if skipped:
        parts.append("skipped " + ",".join(sorted(skipped)))
    parts.append(f"stop {stop_reason}")
    return "; ".join(parts)


def run_row(run_dir: Path) -> dict[str, Any]:
    """The table row of one run directory, with the facts the "all" row needs."""
    world = _read_json(run_dir / "world.json", {})
    goals = _read_jsonl(run_dir / "life.jsonl")
    events = [event for path in sorted((run_dir / "episodes").glob("*.jsonl")) for event in _read_jsonl(path)]
    changes = _read_jsonl(run_dir / "changes.jsonl")
    rules = _read_json(run_dir / "store.json", {}).get("rules", [])
    rules_by_id = {rule["id"]: rule for rule in rules}
    state = _read_json(run_dir / "life_state.json", {})

    steps = [event for event in events if event["kind"] == "step"]
    wood = sorted({
        normalize.species(block) for step in steps
        if (block := (step.get("detail") or {}).get("block")) and block.endswith("_log") and normalize.species(block)
    })

    contradictions = [event for event in events if event["kind"] == "contradiction"]
    changed_at: dict[tuple[str, str], list[float]] = {}
    for change in changes:
        changed_at.setdefault(_subject_key(change["action"], change["subject"]), []).append(change["t"])
    matching = [
        c for c in contradictions
        if any(t <= c["t"] for t in changed_at.get(_rule_key(c["rule"], rules_by_id), []))
    ]

    stop_reason = _stop_reason(run_dir)
    skipped = state.get("curriculum", {}).get("skipped", {})
    return {
        "seed": str(world.get("seed", state.get("world_id", run_dir.name))),
        "biome": world.get("biome", "?"),
        "wood": ",".join(wood) or "-",
        "highest": _highest_goal(goals, steps),
        "certified": sum(1 for rule in rules if rule.get("status") == "certified"),
        "contradictions": len(contradictions),
        "false_contradictions": len(contradictions) - len(matching),
        "quarantined": sum(1 for event in events if event["kind"] == "quarantined"),
        "stuck": _stuck_causes(steps, skipped, stop_reason),
        "llm_calls": sum(goal.get("llm_calls", 0) for goal in goals),
        "tokens": sum(goal.get("tokens", 0) for goal in goals),
        "spend_usd": goals[-1]["spend_usd"] if goals else 0.0,
        "has_changes": bool(changes),
        "change_caught": bool(matching),
    }


def acceptance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The M4 checks over all runs."""
    stone = sum(1 for row in rows if row["highest"] in STONE_TIER_OR_BETTER)
    with_changes = [row for row in rows if row["has_changes"]]
    return {
        "false_contradictions_zero": all(row["false_contradictions"] == 0 for row in rows),
        "stone_tier_runs": stone,
        "stone_tier_ok": bool(rows) and (stone >= 3 or stone / len(rows) >= MIN_STONE_TIER_SHARE),
        "change_caught": any(row["change_caught"] for row in with_changes) if with_changes else None,
    }


def _yes_no(value: bool | None) -> str:
    return "n/a" if value is None else "yes" if value else "NO"


def render_table(rows: list[dict[str, Any]]) -> str:
    verdict = acceptance(rows)
    all_row = {key: "" for key, _ in COLUMNS} | {
        "seed": "all",
        "highest": f"stone+ on {verdict['stone_tier_runs']}/{len(rows)}: {_yes_no(verdict['stone_tier_ok'])}",
        "false_contradictions": f"0 everywhere: {_yes_no(verdict['false_contradictions_zero'])}",
        "stuck": f"change caught: {_yes_no(verdict['change_caught'])}",
        "contradictions": sum(row["contradictions"] for row in rows),
        "quarantined": sum(row["quarantined"] for row in rows),
        "certified": sum(row["certified"] for row in rows),
        "llm_calls": sum(row["llm_calls"] for row in rows),
        "tokens": sum(row["tokens"] for row in rows),
        "spend_usd": sum(row["spend_usd"] for row in rows),
    }
    lines = ["| " + " | ".join(title for _, title in COLUMNS) + " |", "|" + " --- |" * len(COLUMNS)]
    for row in [*rows, all_row]:
        cells = [f"{row[key]:.4f}" if key == "spend_usd" else str(row[key]) for key, _ in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Per-seed M4 table from life run directories.")
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="also write the rows and the acceptance here")
    args = parser.parse_args(argv)

    rows = [run_row(run_dir) for run_dir in args.run_dirs]
    print(render_table(rows))
    if args.json:
        args.json.write_text(json.dumps({"rows": rows, "acceptance": acceptance(rows)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
