"""Metrics computed the same way for every system, from logged step events."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from .models import EpisodeResult


def failure_signature(step: dict[str, Any]) -> tuple | None:
    """A step that failed outright, or mined a block that dropped nothing."""
    action = step["action_obj"]
    key = (action["kind"], action["target"], action["tool"])
    if not step["ok"]:
        return (*key, f"error:{step['error']}")
    if action["kind"] == "mine" and step.get("block_removed") and not step.get("drops"):
        return (*key, "no_drop")
    return None


def repeated_failures(events: list[dict[str, Any]]) -> int:
    signatures = Counter(sig for e in events if e["kind"] == "step" and (sig := failure_signature(e)))
    return sum(count - 1 for count in signatures.values() if count > 1)


def actions_after_contradiction(events: list[dict[str, Any]]) -> int | None:
    """Actions run after the first unexpected exact effect and before the next plan."""
    seen_contradiction = False
    count = 0
    for event in events:
        if event["kind"] == "step":
            if seen_contradiction:
                count += 1
            elif _unexpected(event):
                seen_contradiction = True
        elif event["kind"] in ("plan", "llm_plan") and seen_contradiction:
            return count
    return count if seen_contradiction else None


def _unexpected(step: dict[str, Any]) -> bool:
    if step.get("check") == "mismatch":
        return True
    signature = failure_signature(step)
    return signature is not None and signature[-1] == "no_drop"


def summary_rows(results: list[EpisodeResult]) -> list[dict[str, Any]]:
    """One row per system and task, ordered by task then system. Each metric
    is {"mean", "sd"} over layouts (sd is None for one layout, and the whole
    metric is None when no episode has a value)."""
    groups: dict[tuple[str, str], list[EpisodeResult]] = {}
    for result in results:
        groups.setdefault((result.system, result.task_id), []).append(result)

    rows = []
    for (system, task_id), group in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0])):
        after = [r.actions_after_contradiction for r in group if r.actions_after_contradiction is not None]
        rows.append({
            "system": system,
            "task": task_id,
            "successes": sum(r.success for r in group),
            "episodes": len(group),
            "llm_tokens": _stats([r.llm_tokens for r in group]),
            "llm_calls": _stats([r.llm_calls for r in group]),
            "actions": _stats([r.actions for r in group]),
            "blocks_walked": _stats([r.blocks_walked for r in group]),
            "repeated_failures": _stats([r.repeated_failures for r in group]),
            "actions_after_contradiction": _stats(after),
        })
    return rows


def summarize(results: list[EpisodeResult]) -> str:
    """Markdown table: one row per system and task, mean and sd over layouts."""
    lines = [
        "| System | Task | Success | LLM tokens | LLM calls | Actions | Blocks walked | Repeated failures | Actions after contradiction |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary_rows(results):
        lines.append(
            f"| {row['system']} | {row['task']} | {row['successes']}/{row['episodes']} "
            f"| {_mean_sd(row['llm_tokens'])} | {_mean_sd(row['llm_calls'])} "
            f"| {_mean_sd(row['actions'])} | {_mean_sd(row['blocks_walked'])} "
            f"| {_mean_sd(row['repeated_failures'])} | {_mean_sd(row['actions_after_contradiction'])} |"
        )
    lines += [
        "",
        "Mean ± sd over layouts. LLM tokens are input plus output from API usage, per episode. "
        "D's one-time prior-rule call before training is not charged to any episode. "
        "After a failed plan, B and B+ get the same \"rules may differ from vanilla\" sentence as D's revision prompt. "
        "Repeated failures count any identical action that failed or mined a block that dropped nothing "
        "more than once, including `not_found` retries after the visible ore ran out.",
    ]
    return "\n".join(lines) + "\n"


def _stats(values: list[float]) -> dict[str, float | None] | None:
    if not values:
        return None
    return {"mean": statistics.fmean(values), "sd": statistics.stdev(values) if len(values) > 1 else None}


def _mean_sd(stats: dict[str, float | None] | None) -> str:
    if stats is None:
        return "-"
    mean, sd = stats["mean"], stats["sd"]
    if sd is None:
        return f"{mean:.0f}" if mean >= 100 else f"{mean:.1f}"
    return f"{mean:.0f} ± {sd:.0f}" if mean >= 100 else f"{mean:.1f} ± {sd:.1f}"
