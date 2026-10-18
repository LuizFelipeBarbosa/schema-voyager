"""SchemaVoyager (system D): plan over rules, check every step's exact
prediction, and on the first mismatch demote the rule, ask the LLM for ranked
alternatives, and replan from the current state.

The LLM is called only to propose prior rules (once, before training) and to
revise a contradicted rule. While predictions hold, execution makes no calls.
"""

from __future__ import annotations

from typing import Callable

from . import normalize
from .episode import Episode
from .llm import LLM, LLMError
from .models import Action, EpisodeResult, Outcome, Rule, Skill
from .planner import PlanningError, PlanStep, make_plan
from .schema_store import MIN_CONFIRMATIONS, SchemaStore, Transition, applies
from .tasks import CRAFTABLE_ITEMS, MINEABLE_BLOCKS
from .verifier import Check, check, predict


def seed_prior_rules(store: SchemaStore, llm: LLM) -> list[Rule]:
    """Everything the model believes starts as a hypothesis."""
    rules = llm.propose_prior_rules(MINEABLE_BLOCKS, CRAFTABLE_ITEMS)
    for rule in rules:
        store.add_rule(rule)
    return rules


MAX_CHAT_MESSAGES = 3  # per burst
MAX_CHAT_CHARS = 240
MAX_PREDICTS_CHARS = 120

BeforeStep = Callable[[Episode, PlanStep, list[PlanStep]], None]


def run_schemavoyager(episode: Episode, store: SchemaStore, before_step: BeforeStep | None = None) -> EpisodeResult:
    """Run one episode, replanning from the current state after any step that fails or surprises.

    `before_step(episode, step, plan)` is called before each step runs. What a
    step's check says decides what happens next:

    | status      | recorded?               | then                                   |
    | match       | yes, kind "exact"       | next step                              |
    | stochastic  | yes, kind "stochastic"  | next step (replan if items deviate)    |
    | mismatch    | yes                     | demote, revise (one LLM call), replan  |
    | quarantined | never                   | log it, replan, no revision            |
    | failed      | no                      | replan (tolerant)                      |
    """
    task = episode.task
    episode.log.event(
        "episode_start", system=episode.system, task=task.task_id, description=task.description,
        world_id=episode.world_id, budget=task.budget, layout=episode.layout_seed,
    )
    completed: list[tuple[Action, str]] = []
    first_plan = True  # v1 narrates every plan; a natural goal is announced once
    awaiting_recovery = False  # natural: a revision was proposed, no step has run under it yet

    while not episode.goal_met and episode.can_act:
        try:
            visible = normalize.counts(episode.backend.observe().nearby) if episode.natural else None
            plan = make_plan(
                task.goal_item, task.goal_count, episode.counts, store.usable_rules(episode.world_id), store.skill_for,
                visible=visible,
            )
        except PlanningError as error:
            episode.log.event("plan_failed", reason=str(error))
            break
        if not plan:
            episode.log.event("plan_failed", reason=f"empty plan, but {task.goal_item} is not held")
            break
        _log_plan(episode, store, plan, narrate=first_plan or not episode.natural)
        first_plan = False

        for position, step in enumerate(plan):
            if not episode.can_act:
                break
            if before_step is not None:
                before_step(episode, step, plan)
            if episode.natural:
                episode.log.event(
                    "step_start", index=episode.actions + 1, action=step.action.describe(), rule=step.rule.id,
                    experimental=step.experimental, **_rule_story(store, step),
                )
            verdict: dict[str, Check] = {}
            outcome = episode.act(step.action, annotate=lambda o, s=step: _annotate(s, o, verdict))
            result = verdict["check"]
            if result.status == "failed":
                break  # tolerant: replan from the current state
            if result.status == "quarantined":
                _log_quarantine(episode, outcome, result)
                break  # not evidence: nothing recorded, nothing revised

            transition_id = f"{episode.episode_id}/{episode.actions}"
            store.record_transition(
                Transition(
                    transition_id, episode.episode_id, episode.world_id, step.action, result.observed,
                    kind="stochastic" if result.status == "stochastic" else "exact",
                )
            )
            if result.status == "mismatch":
                awaiting_recovery = _revise(episode, store, step, result, transition_id)
                break
            if awaiting_recovery and result.status == "match" and step.rule.source == "revision" and episode.natural:
                _say(episode, [_recovery_line(step.action, result)])
                awaiting_recovery = False
            completed.append((step.action, step.rule.id))
            if _deviates(episode, outcome, step, plan[position + 1:]):
                break  # a drop rolled out of reach; replan without revising anything

    result = episode.finish()
    certified = store.certify()
    episode.log.event("certified", rules=certified)
    changed_skill: Skill | None = None
    if result.success and task.split == "train":
        existing = store.skills.get(f"skill_{task.goal_item}")
        previous_rule_ids = list(existing.rule_ids) if existing is not None else None
        skill = store.compile_skill(task.goal_item, task.goal_count, completed, episode.episode_id)
        episode.log.event("skill", skill=skill.id, rule_ids=skill.rule_ids)
        if previous_rule_ids is None or previous_rule_ids != skill.rule_ids:
            changed_skill = skill
    _narrate_learning(episode, store, certified, changed_skill)
    return result


def _narrate_learning(
    episode: Episode, store: SchemaStore, certified: list[str], changed_skill: Skill | None
) -> None:
    lines = _learning_lines(episode, store, certified, changed_skill)
    if not lines:
        return
    messages = _learning_messages(lines)
    episode.log.event("learned", lines=messages)
    _say(episode, messages)


def _learning_lines(
    episode: Episode, store: SchemaStore, certified: list[str], changed_skill: Skill | None
) -> list[str]:
    lines: list[str] = []
    if episode.natural:
        if certified:
            rules = (store.rules[rule_id] for rule_id in certified)
            lines.append("Certified: " + ", ".join(f"{rule.kind} {_plain(rule.subject)}" for rule in rules))
        return lines
    if certified:
        details = []
        for rule_id in certified:
            rule = store.rules[rule_id]
            confirmed, _ = store.replay(rule)
            episodes = len({transition.episode_id for transition in confirmed})
            details.append(f"{_display_rule_id(rule_id)} ({episodes} episodes)")
        lines.append(f"Certified: {', '.join(details)}")

    if changed_skill is not None:
        lines.append(_skill_learning_line(store, changed_skill))

    if episode.task.split == "holdout":
        kept = []
        for rule in store.rules.values():
            if rule.scope != episode.world_id or rule.source != "revision" or rule.status != "hypothesis":
                continue
            confirmed, contradicted = store.replay(rule)
            if not confirmed or contradicted:
                continue
            count = len(confirmed)
            confirmation = "once" if count == 1 else f"{count} times"
            kept.append(
                f"{_display_rule_id(rule.id)} ({_revision_description(rule)}), "
                f"confirmed {confirmation}"
            )
        if kept:
            lines.append(f"Kept for this world: {'; '.join(kept)}")
    return lines


def _skill_learning_line(store: SchemaStore, skill: Skill) -> str:
    candidates = [
        other
        for other in store.skills.values()
        if other.id != skill.id and other.status == "ok" and set(other.rule_ids) <= set(skill.rule_ids)
    ]
    base = max(candidates, key=lambda item: (len(item.rule_ids), item.id), default=None)
    covered = set(base.rule_ids) if base is not None else set()
    relies = [_display_rule_id(rule_id) for rule_id in skill.rule_ids if rule_id not in covered]
    description = f"builds on {base.id}, " if base is not None else ""
    return f"Learned {skill.id}: {description}relies on {', '.join(relies)}"


def _revision_description(rule: Rule) -> str:
    tools = rule.preconditions.get("tool_any", [])
    if rule.kind == "mine":
        if not tools:
            tool_text = "no special tool"
        elif len(tools) <= 2:
            tool_text = ", ".join(tools)
        else:
            tool_text = f"{tools[0]} (+{len(tools) - 1} more)"
        return f"{rule.subject} needs {tool_text}"
    return f"craft {rule.subject}"


def _display_rule_id(rule_id: str) -> str:
    return rule_id.split("@", 1)[0]


def _learning_messages(lines: list[str], limit: int = 240) -> list[str]:
    messages: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current} | {line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        while len(line) > limit:
            messages.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        messages.append(current)
    return messages


def _say(episode: Episode, messages: list[str]) -> None:
    """One burst of chat: at most 3 messages of 240 characters."""
    for message in messages[:MAX_CHAT_MESSAGES]:
        episode.backend.say(message[:MAX_CHAT_CHARS])


def _deviates(episode: Episode, outcome: Outcome, step: PlanStep, remaining: list[PlanStep]) -> bool:
    """Whether the inventory differs from what the plan expected. In a natural world,
    only the items the remaining plan uses count, so pickups on the way (dirt,
    cobblestone) don't force a replan after every mine."""
    if not episode.natural:
        return outcome.inventory != step.expected_inventory
    used = _items_used(remaining)
    actual = normalize.counts(outcome.inventory)
    return any(actual.get(item, 0) != step.expected_inventory.get(item, 0) for item in used)


def _items_used(steps: list[PlanStep]) -> set[str]:
    used: set[str] = set()
    for step in steps:
        rule = step.rule
        used.update(rule.preconditions.get("consumes", {}))
        used.update(rule.yields())
        if rule.preconditions.get("station"):
            used.add(rule.preconditions["station"])
        used.update(item for item in (step.action.tool, step.action.fuel) if item)
    return used


def _log_quarantine(episode: Episode, outcome: Outcome, result: Check) -> None:
    reason = ", ".join(sorted({event["type"] for event in outcome.events})) or result.detail or "interference"
    episode.log.event("quarantined", index=episode.actions, reason=reason)


def _annotate(step: PlanStep, outcome: Outcome, verdict: dict[str, Check]) -> dict:
    result = check(step.rule, step.action, outcome)
    verdict["check"] = result
    return {
        "rule": step.rule.id, "experimental": step.experimental, "predicted": result.predicted,
        "check": result.status, "observed": result.observed, "check_detail": result.detail,
    }


def _log_plan(episode: Episode, store: SchemaStore, plan: list[PlanStep], narrate: bool = True) -> None:
    plan_rules = {step.rule.id for step in plan}
    skills = [
        skill.id
        for skill in store.skills.values()
        if skill.status == "ok" and set(skill.rule_ids) <= plan_rules
    ]
    episode.used_skills = list(dict.fromkeys(episode.used_skills + skills))
    episode.log.event(
        "plan",
        steps=[
            {"action": s.action.describe(), "rule": s.rule.id, "experimental": s.experimental}
            | (_rule_story(store, s) if episode.natural else {})
            for s in plan
        ],
        skills=skills,
    )
    if not narrate:
        return
    if episode.natural:
        _say(episode, [_goal_line(episode.task, plan)])
    else:
        _say(episode, [f"Plan: {len(plan)} steps, starting with {plan[0].action.describe()}."])


def _goal_line(task: Task, plan: list[PlanStep]) -> str:
    goal = f"Goal: {task.goal_count} {_plain(task.goal_item)}"
    untested = next((step for step in plan if step.experimental), None)
    if untested is None:
        return f"{goal} (all rules known)"
    action = untested.action
    tested = f"{_plain(action.target or '')} with {action.tool}" if action.kind == "mine" and action.tool else action.describe()
    return f"{goal}, to test {tested}"


def _rule_story(store: SchemaStore, step: PlanStep) -> dict:
    """What the stage shows about the rule a step tests."""
    return {
        "status": step.rule.status, "confirmed": len(store.replay(step.rule)[0]), "needed": MIN_CONFIRMATIONS,
        "predicts": _predicts(step.rule, step.action)[:MAX_PREDICTS_CHARS],
    }


def _predicts(rule: Rule, action: Action | None = None) -> str:
    """One line for what a rule predicts: `2 planks -> 4 stick`, `coal_ore with wooden_pickaxe -> 1 coal`."""
    gains = _counts_text(rule.yields())
    if rule.kind == "mine":
        tools = [action.tool] if action is not None and action.tool else rule.preconditions.get("tool_any", [])
        return f"{_plain(rule.subject)} with {' / '.join(tools)} -> {gains}" if tools else f"{_plain(rule.subject)} -> {gains}"
    inputs = [f"{_plain(item)}" if count == 1 else f"{count} {_plain(item)}" for item, count in rule.preconditions.get("consumes", {}).items()]
    if rule.kind == "smelt":
        inputs.append("fuel")
    station = rule.preconditions.get("station")
    where = {"furnace": " in a furnace", "crafting_table": " at a crafting_table"}.get(station, f" at a {station}" if station else "")
    return f"{' + '.join(inputs)}{where} -> {gains}"


def _plain(item: str) -> str:
    return item.lstrip("#")


def _counts_text(counts: dict[str, int]) -> str:
    return ", ".join(f"{count} {_plain(item)}" for item, count in counts.items() if count > 0) or "nothing"


def _gained(side: dict) -> str:
    """The items a check's predicted or observed side says were gained."""
    return _counts_text(side["drops"] if "drops" in side else side.get("inventory_delta", {}))


def _recovery_line(action: Action, result: Check) -> str:
    with_tool = f" with {action.tool}" if action.tool else ""
    return f"Recovered: got {_gained(result.observed)}{with_tool}"


def _revise(episode: Episode, store: SchemaStore, step: PlanStep, result: Check, transition_id: str) -> bool:
    """Demote the contradicted rule and ask for revisions. Returns whether one was kept."""
    rule = step.rule
    flagged = store.demote(rule.id, transition_id, episode.world_id)
    episode.log.event(
        "contradiction", rule=rule.id, transition=transition_id, detail=result.detail,
        predicted=result.predicted, observed=result.observed, flagged=flagged,
    )
    if episode.natural:
        _say(episode, [f"MISMATCH: expected {_gained(result.predicted)}, got {_gained(result.observed)}"])
    else:
        _say(episode, [f"Unexpected: {result.detail}. Rule {rule.id} no longer trusted here."])

    counterexample = {"action": step.action.to_dict(), "predicted": result.predicted, "observed": result.observed}
    known_items = sorted({item for r in store.usable_rules(episode.world_id) for item in r.yields()})
    tokens_before = episode.llm.total_tokens
    try:
        revise = episode.llm.revise_rule_v2 if episode.natural else episode.llm.revise_rule
        hypotheses = revise(rule, store.evidence_summary(rule), counterexample, known_items, episode.world_id)
    except LLMError as error:
        episode.log.event("plan_failed", reason=f"revision call failed: {error}")
        hypotheses = []
    episode.revisions += 1

    summaries = []
    top_kept: Rule | None = None
    for hypothesis in hypotheses:
        # A hypothesis must explain the counterexample it was proposed for.
        kept = not applies(hypothesis, step.action) or predict(hypothesis, step.action) == result.observed
        if kept:
            store.add_rule(hypothesis)
            top_kept = top_kept or hypothesis
        summaries.append({"rank": hypothesis.rank + 1, "id": hypothesis.id, "kept": kept,
                          "summary": _summarize(hypothesis), "rule": hypothesis.to_dict()})
    episode.log.event("revision", rule=rule.id, hypotheses=summaries, tokens=episode.llm.total_tokens - tokens_before)
    if not episode.natural:
        if summaries:
            _say(episode, [f"Hypothesis 1: {summaries[0]['summary']}"])
        return top_kept is not None
    if top_kept is not None:
        _say(episode, [f"Hypothesis: {_hypothesis_text(top_kept)}"])
    return top_kept is not None


def _hypothesis_text(rule: Rule) -> str:
    return _revision_description(rule) if rule.kind == "mine" else _predicts(rule)


def _summarize(rule: Rule) -> str:
    if rule.kind == "mine":
        tools = rule.preconditions.get("tool_any", [])
        drops = rule.yields()
        what = ", ".join(f"{n} {item}" for item, n in drops.items()) or "nothing"
        how = f"with {' / '.join(tools)}" if tools else "with any tool or bare hand"
        return f"mine {rule.subject} {how} -> drops {what}. {rule.rationale}"
    gains = ", ".join(f"{n} {item}" for item, n in rule.yields().items())
    return f"craft {rule.subject} -> {gains}. {rule.rationale}"
