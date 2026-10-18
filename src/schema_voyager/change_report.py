"""The M3 acceptance, read from a finished (or running) life's trace:

    python -m schema_voyager.change_report runs/<run>

One block per line of changes.jsonl, with one line per criterion, PASS or FAIL
with the evidence, scoped to that change's rule; then `changes: <passed>/<fired>
PASS`. A change's marker in the episode trace is the `world_change` event the
trigger wrote. Exits 1 when any criterion fails or nothing fired.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import normalize
from .schema_store import SchemaStore

Event = dict[str, Any]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.exit("usage: python -m schema_voyager.change_report runs/<run>")
    run_dir = Path(argv[0])
    changes = _read_jsonl(run_dir / "changes.jsonl")
    if not changes:
        print("FAIL no change was switched on (changes.jsonl is empty or missing)")
        return 1
    store = SchemaStore.load(run_dir / "store.json")
    life = {record["index"]: record for record in _read_jsonl(run_dir / "life.jsonl")}

    passed = 0
    for change in changes:
        events = _events_from_change(run_dir, change)
        print(f"change {change['change']}: {change['action']} {change['subject']}, switched on before goal {change['goal']}")
        checks = [
            _caught_at_exact_step(events, store, change),
            _one_revision_call(events, store, change),
            _revision_certified_in_epoch(store, change),
            _next_use_costs_no_calls(events, store, change, life),
            _no_mistake_repeated(events, store, change),
        ]
        for ok, text in checks:
            print(f"{'PASS' if ok else 'FAIL'} {text}")
        passed += all(ok for ok, _ in checks)
    print(f"changes: {passed}/{len(changes)} PASS")
    return 0 if passed == len(changes) else 1


def _read_jsonl(path: Path) -> list[Event]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events_from_change(run_dir: Path, change: Event) -> list[Event]:
    """Every episode event from this change's trigger on, in order, each tagged with its goal name."""
    events: list[Event] = []
    for path in sorted((run_dir / "episodes").glob("D_life_g*.jsonl")):
        goal = path.name.split("_")[2]
        if goal >= change["goal"]:
            events += [{**event, "goal": goal} for event in _read_jsonl(path)]
    marker = next(
        (i for i, e in enumerate(events) if e["kind"] == "world_change" and e.get("change") == change["change"]), len(events)
    )
    return events[marker:]


def _is_changed_action(action: dict[str, Any], change: Event) -> bool:
    return action["kind"] == change["action"] and normalize.family(action.get("target") or "") == change["subject"]


def _first_contradiction(events: list[Event], store: SchemaStore, change: Event) -> int | None:
    """The first contradiction of a rule for this change's (action, subject); other changes' are not ours."""
    return next(
        (i for i, e in enumerate(events) if e["kind"] == "contradiction" and _is_changed_rule(store.rules.get(e["rule"]), change)),
        None,
    )


def _is_changed_rule(rule: Any, change: Event) -> bool:
    return rule is not None and (rule.kind, rule.subject) == (change["action"], change["subject"])


def _caught_at_exact_step(events: list[Event], store: SchemaStore, change: Event) -> tuple[bool, str]:
    """The first changed-action step that ran (ok) after the change must be the contradiction.
    Attempts that failed before acting (pathing: unreachable, not_found) observed nothing."""
    first_step = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "step" and e["ok"] and _is_changed_action(e["action_obj"], change)),
        None,
    )
    failed_before = sum(
        1 for e in events[:first_step]
        if e["kind"] == "step" and not e["ok"] and _is_changed_action(e["action_obj"], change)
    )
    index = _first_contradiction(events, store, change)
    if index is None:
        return False, "(a) caught at the exact step: no contradiction after the change"
    contradiction = events[index]
    transition = next(t for t in store.transitions if t.id == contradiction["transition"])
    if first_step is None or not _is_changed_action(transition.action.to_dict(), change):
        return False, f"(a) caught at the exact step: first contradiction is {contradiction['rule']} on {transition.action.describe()}"
    steps_between = [e for e in events[first_step + 1:index] if e["kind"] == "step"]
    ok = not steps_between and events[first_step]["goal"] == contradiction["goal"]
    earlier = f"; {failed_before} earlier attempt(s) failed before acting" if failed_before else ""
    return ok, (f"(a) caught at the exact step: {transition.action.describe()} contradicted {contradiction['rule']} "
                f"({contradiction['transition']}){earlier}")


def _one_revision_call(events: list[Event], store: SchemaStore, change: Event) -> tuple[bool, str]:
    index = _first_contradiction(events, store, change)
    revisions = [e for e in events[index:] if e["kind"] == "revision" and e["rule"] == events[index]["rule"]] if index is not None else []
    # One revision event is one LLM call; later ones are other rules' contradictions.
    first = revisions[:1]
    calls = len(first)
    text = f"(b) LLM calls for the revision: {calls}" + (f" ({first[0]['tokens']} tokens)" if first else "")
    if index is not None:
        # The revise calls a retest of the contradicted rule caused are reported, not failed on.
        after_retest = sum(
            1 for e in events[index:]
            if e["kind"] == "revision" and _is_retest_of(store.rules.get(e["rule"]), events[index]["rule"])
        )
        text += f"; {after_retest} more after its retest" if after_retest else ""
    return calls == 1, text


def _is_retest_of(rule: Any, rule_id: str) -> bool:
    return rule is not None and rule.source == "retest" and rule.revision_of == rule_id


def _revisions_of(store: SchemaStore, change: Event):
    return [
        rule for rule in store.rules.values()
        if rule.source == "revision" and rule.kind == change["action"] and rule.subject == change["subject"]
    ]


def _revision_certified_in_epoch(store: SchemaStore, change: Event) -> tuple[bool, str]:
    for rule in _revisions_of(store, change):
        confirmed, _ = store.replay(rule)
        in_epoch = all(store.epoch_of(t) == rule.epoch for t in confirmed)
        if rule.status == "certified" and rule.epoch >= 1 and confirmed and in_epoch:
            return True, f"(c) {rule.id} certified in epoch {rule.epoch} with {len(confirmed)} confirmations, all in that epoch"
    found = ", ".join(f"{r.id} {r.status} epoch {r.epoch}" for r in _revisions_of(store, change)) or "no revision"
    return False, f"(c) no revision certified within its new epoch ({found})"


def _next_use_costs_no_calls(events: list[Event], store: SchemaStore, change: Event, life: dict[int, Event]) -> tuple[bool, str]:
    revised = {rule.id for rule in _revisions_of(store, change)}
    index = _first_contradiction(events, store, change)
    contradiction_goal = events[index]["goal"] if index is not None else change["goal"]
    use = next(
        (e for e in events if e["kind"] == "step" and e["goal"] > contradiction_goal and e.get("rule") in revised), None
    )
    if use is None:
        return False, "(d) no later goal used the revised rule"
    # The episode's own calls: life.jsonl also counts the prior rules asked for between goals.
    end = next(e for e in events if e["kind"] == "episode_end" and e["goal"] == use["goal"])
    calls, with_priors = end["llm_calls"], life[int(use["goal"][1:])]["llm_calls"]
    return calls == 0, f"(d) next goal using {use['rule']} ({use['goal']}) made {calls} LLM calls ({with_priors} with prior-rule calls between goals)"


def _no_mistake_repeated(events: list[Event], store: SchemaStore, change: Event) -> tuple[bool, str]:
    index = _first_contradiction(events, store, change)
    if index is None:
        return False, "(e) no contradiction to check"
    contradiction = events[index]
    mistake = next(t for t in store.transitions if t.id == contradiction["transition"]).action
    repeats = [
        e for e in events[index + 1:]
        if e["kind"] == "step"
        and (e.get("rule") == contradiction["rule"] or _is_retest_of(store.rules.get(e.get("rule")), contradiction["rule"]))
        and (e["action_obj"]["kind"], e["action_obj"].get("tool")) == (mistake.kind, mistake.tool)
        and _is_changed_action(e["action_obj"], change)
    ]
    by_retest = sum(1 for e in repeats if e["rule"] != contradiction["rule"])
    text = f"(e) {mistake.describe()} attempted again after the contradiction: {len(repeats)} times"
    return not repeats, text + (f" ({by_retest} by a retest)" if by_retest else "")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
