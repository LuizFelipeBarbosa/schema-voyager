"""Step verification.

Only effects the server decides deterministically can contradict a rule:
whether a mined block was removed, what item entities it dropped, and the
inventory change of a craft. Pickup and pathing are tolerant: they can fail
for reasons that say nothing about the world's rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import Action, Outcome, Rule

# v2 vocabulary (docs/v2-interfaces.md): quarantined steps are never evidence;
# stochastic steps count toward a rule's allowed set only, never as a contradiction.
CheckStatus = Literal["match", "mismatch", "failed", "quarantined", "stochastic"]
CheckKind = Literal["exact", "stochastic", "tolerant", "quarantined"]
Verdict = Literal["confirm", "contradict", "neutral"]

# Wave 0 stubs, filled in by the rules lane.
EXACT_FAMILIES: frozenset[str] = frozenset()
STOCHASTIC_FAMILIES: frozenset[str] = frozenset()
QUARANTINE_EVENTS: frozenset[str] = frozenset()


@dataclass
class Check:
    status: CheckStatus
    predicted: dict[str, Any]
    observed: dict[str, Any]
    detail: str = ""
    kind: CheckKind = "exact"


def kind_of(rule: Rule, action: Action) -> CheckKind:
    raise NotImplementedError("verifier.kind_of: rules lane")


def evaluate(rule: Rule, action: Action, observed: dict[str, Any]) -> Verdict:
    """Replay verdict for a recorded observation (normalizes it again)."""
    raise NotImplementedError("verifier.evaluate: rules lane")


def predict(rule: Rule, action: Action) -> dict[str, Any]:
    if rule.kind == "mine":
        return {"block_removed": True, "drops": _clean(rule.effects.get("drops", {}))}
    delta: dict[str, int] = {}
    for item, count in rule.preconditions.get("consumes", {}).items():
        delta[item] = delta.get(item, 0) - count * action.times
    for item, count in rule.effects.get("gains", {}).items():
        delta[item] = delta.get(item, 0) + count * action.times
    return {"inventory_delta": _clean(delta)}


def check(rule: Rule, action: Action, outcome: Outcome) -> Check:
    predicted = predict(rule, action)
    if not outcome.ok:
        return Check("failed", predicted, {"error": outcome.error, **outcome.detail}, outcome.error or "")

    if rule.kind == "mine":
        observed = {"block_removed": bool(outcome.block_removed), "drops": _clean(outcome.drops)}
        detail = "" if outcome.picked_up == outcome.drops else "pickup incomplete (tolerant)"
    else:
        delta = dict(outcome.inventory_delta)
        detail = ""
        station = rule.preconditions.get("station")
        if station and outcome.detail.get("table_retrieved") is False:
            # Picking the placed table back up is pickup, which is tolerant.
            delta[station] = delta.get(station, 0) + 1
            detail = f"{station} not picked back up (tolerant)"
        observed = {"inventory_delta": _clean(delta)}

    if observed != predicted:
        return Check("mismatch", predicted, observed, _describe_mismatch(predicted, observed))
    return Check("match", predicted, observed, detail)


def _describe_mismatch(predicted: dict[str, Any], observed: dict[str, Any]) -> str:
    parts = []
    for key in predicted:
        if predicted[key] != observed.get(key):
            parts.append(f"{key}: expected {_fmt(predicted[key])}, saw {_fmt(observed.get(key))}")
    return "; ".join(parts)


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in value.items()) + "}" if value else "nothing"
    return str(value)


def _clean(counts: dict[str, int]) -> dict[str, int]:
    return {item: count for item, count in sorted(counts.items()) if count}
