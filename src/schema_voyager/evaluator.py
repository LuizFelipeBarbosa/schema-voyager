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


def summarize(results: list[EpisodeResult]) -> str:
    """Markdown table: one row per system and task, mean and sd over layouts."""
    groups: dict[tuple[str, str], list[EpisodeResult]] = {}
    for result in results:
        groups.setdefault((result.system, result.task_id), []).append(result)

    lines = [
        "| System | Task | Success | LLM tokens | LLM calls | Actions | Blocks walked | Repeated failures | Actions after contradiction |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (system, task_id), group in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0])):
        after = [r.actions_after_contradiction for r in group if r.actions_after_contradiction is not None]
        lines.append(
            f"| {system} | {task_id} | {sum(r.success for r in group)}/{len(group)} "
            f"| {_mean_sd([r.llm_tokens for r in group])} | {_mean_sd([r.llm_calls for r in group])} "
            f"| {_mean_sd([r.actions for r in group])} | {_mean_sd([r.blocks_walked for r in group])} "
            f"| {_mean_sd([r.repeated_failures for r in group])} | {_mean_sd(after) if after else '-'} |"
        )
    lines += [
        "",
        "Mean ± sd over layouts. LLM tokens are input plus output from API usage, per episode. "
        "D's one-time prior-rule call before training is not charged to any episode. "
        "Repeated failures count any identical action that failed or mined a block that dropped nothing "
        "more than once, including `not_found` retries after the visible ore ran out.",
    ]
    return "\n".join(lines) + "\n"


def _mean_sd(values: list[float]) -> str:
    if not values:
        return "-"
    mean = statistics.fmean(values)
    if len(values) == 1:
        return f"{mean:.0f}" if mean >= 100 else f"{mean:.1f}"
    sd = statistics.stdev(values)
    return f"{mean:.0f} ± {sd:.0f}" if mean >= 100 else f"{mean:.1f} ± {sd:.1f}"
