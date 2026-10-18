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
from .normalize import counts, family, species_mismatch

# v2 vocabulary (docs/v2-interfaces.md): quarantined steps are never evidence;
# stochastic steps count toward a rule's allowed set only, never as a contradiction.
CheckStatus = Literal["match", "mismatch", "failed", "quarantined", "stochastic"]
CheckKind = Literal["exact", "stochastic", "tolerant", "quarantined"]
Verdict = Literal["confirm", "contradict", "neutral"]

# Mine subjects (family names or plain raw ids) by how their drops behave.
EXACT_FAMILIES: frozenset[str] = frozenset({
    "#log", "stone", "deepslate", "#cobblestone", "dirt", "grass_block", "sand", "andesite", "diorite",
    "granite", "coal_ore", "iron_ore", "gold_ore", "diamond_ore", "emerald_ore", "crafting_table", "furnace",
})
STOCHASTIC_FAMILIES: frozenset[str] = frozenset({
    "gravel", "#leaves", "short_grass", "tall_grass", "fern", "large_fern", "dead_bush",
    "dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip", "white_tulip",
    "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley", "sunflower", "lilac", "rose_bush", "peony",
    "copper_ore", "redstone_ore", "lapis_ore",
})
# Step-phase events that make a step's outcome say nothing about the rules.
# stray_pickup does too, but only for craft and smelt (a mine's drops are measured per cell).
QUARANTINE_EVENTS: frozenset[str] = frozenset({"damage", "death", "respawn", "target_despawned", "interference"})
_INVENTORY_STEP_EVENTS = frozenset({"stray_pickup"})
_STATION_RETRIEVED = {"craft": "table_retrieved", "smelt": "furnace_retrieved"}


@dataclass
class Check:
    status: CheckStatus
    predicted: dict[str, Any]
    observed: dict[str, Any]
    detail: str = ""
    kind: CheckKind = "exact"


def kind_of(rule: Rule, action: Action) -> CheckKind:
    """exact or stochastic; quarantine is decided per step by check()."""
    if rule.kind != "mine":
        return "exact"
    subject = family(rule.subject)
    if subject in EXACT_FAMILIES:
        return "exact"
    if subject in STOCHASTIC_FAMILIES:
        return "stochastic"
    return rule.check


def evaluate(rule: Rule, action: Action, observed: dict[str, Any]) -> Verdict:
    """Replay verdict for a recorded observation (normalizes it again)."""
    observed = {
        key: counts(value) if key in ("drops", "inventory_delta") else value for key, value in observed.items()
    }
    predicted = predict(rule, action)
    if kind_of(rule, action) == "stochastic":
        return "confirm" if _in_range(predicted["allowed"], observed) else "neutral"
    return "confirm" if observed == predicted else "contradict"


def predict(rule: Rule, action: Action) -> dict[str, Any]:
    if rule.kind == "mine":
        if kind_of(rule, action) == "stochastic":
            return {"block_removed": True, "allowed": _allowed(rule)}
        return {"block_removed": True, "drops": counts(rule.effects.get("drops", {}))}
    delta: dict[str, int] = {}
    for item, count in rule.preconditions.get("consumes", {}).items():
        delta[item] = delta.get(item, 0) - count * action.times
    for item, count in rule.effects.get("gains", {}).items():
        delta[item] = delta.get(item, 0) + count * action.times
    return {"inventory_delta": counts(delta)}


def check(rule: Rule, action: Action, outcome: Outcome) -> Check:
    predicted = predict(rule, action)
    kind = kind_of(rule, action)

    if _is_quarantined(action, outcome):
        observed = {"error": outcome.error, "events": [e.get("type") for e in outcome.events]}
        return Check("quarantined", predicted, observed, "step disturbed (quarantined)", "quarantined")

    if not outcome.ok:
        return Check("failed", predicted, {"error": outcome.error, **outcome.detail}, outcome.error or "", kind)
    if rule.kind == "mine" and "tool_used" in outcome.detail and outcome.detail["tool_used"] != action.tool:
        observed = {"error": "wrong_tool", **outcome.detail}
        return Check("failed", predicted, observed, "wrong tool held", kind)

    detail = ""
    if rule.kind == "mine":
        observed = {"block_removed": bool(outcome.block_removed), "drops": counts(outcome.drops)}
        raw = outcome.drops | {outcome.detail["block"]: -1} if "block" in outcome.detail else {}
        if outcome.picked_up != outcome.drops:
            detail = "pickup incomplete (tolerant)"
    else:
        raw = dict(outcome.inventory_delta)
        station = rule.preconditions.get("station")
        if station and outcome.detail.get(_STATION_RETRIEVED[rule.kind]) is False:
            # Picking the placed station back up is pickup, which is tolerant.
            raw[station] = raw.get(station, 0) + 1
            detail = f"{station} not picked back up (tolerant)"
        for tool in outcome.detail.get("tools_broken", []):
            # A tool that broke on the station dig is a loss, not an effect of the rule.
            raw[tool] = raw.get(tool, 0) + 1
            detail = "; ".join(filter(None, [detail, f"{tool} wore out (tolerant)"]))
        if rule.kind == "smelt" and outcome.detail.get("fuel") and outcome.detail.get("fuel_used"):
            raw[outcome.detail["fuel"]] = raw.get(outcome.detail["fuel"], 0) + outcome.detail["fuel_used"]
        observed = {"inventory_delta": counts(raw)}

    if kind == "stochastic":
        in_range = _in_range(predicted["allowed"], observed)
        return Check("stochastic", predicted, observed, "drops in range" if in_range else "drops out of range (neutral)", kind)

    mismatch = species_mismatch(raw)
    if mismatch:
        observed["species_mismatch"] = mismatch
    if observed != predicted:
        return Check("mismatch", predicted, observed, _describe_mismatch(predicted, observed), kind)
    return Check("match", predicted, observed, detail, kind)


def _is_quarantined(action: Action, outcome: Outcome) -> bool:
    if outcome.error == "quarantined":
        return True
    for event in outcome.events:
        event_type = event.get("type")
        if event_type in ("death", "respawn"):
            return True
        if event.get("phase", "step") != "step":
            continue  # travel-phase events: the step's own window was clean
        if event_type in QUARANTINE_EVENTS:
            return True
        if event_type in _INVENTORY_STEP_EVENTS and action.kind in ("craft", "smelt"):
            return True
    return False


def _allowed(rule: Rule) -> dict[str, list[int]]:
    """Item -> [min, max] drop count of a stochastic mine rule, by family."""
    declared = rule.effects.get("allowed") or {item: [0, n] for item, n in rule.effects.get("drops", {}).items()}
    allowed: dict[str, list[int]] = {}
    for item, (low, high) in declared.items():
        merged = allowed.setdefault(family(item), [0, 0])
        merged[0] += low
        merged[1] += high
    return dict(sorted(allowed.items()))


def _in_range(allowed: dict[str, list[int]], observed: dict[str, Any]) -> bool:
    drops = observed.get("drops", {})
    return bool(observed.get("block_removed")) and set(drops) <= set(allowed) and all(
        low <= drops.get(item, 0) <= high for item, (low, high) in allowed.items()
    )


def _describe_mismatch(predicted: dict[str, Any], observed: dict[str, Any]) -> str:
    parts = []
    for key in predicted:
        if predicted[key] != observed.get(key):
            parts.append(f"{key}: expected {_fmt(predicted[key])}, saw {_fmt(observed.get(key))}")
    if "species_mismatch" in observed:
        parts.append(f"species changed: {observed['species_mismatch']}")
    return "; ".join(parts)


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in value.items()) + "}" if value else "nothing"
    return str(value)
