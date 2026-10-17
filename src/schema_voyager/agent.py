"""SchemaVoyager (system D): plan over rules, check every step's exact
prediction, and on the first mismatch demote the rule, ask the LLM for ranked
alternatives, and replan from the current state.

The LLM is called only to propose prior rules (once, before training) and to
revise a contradicted rule. While predictions hold, execution makes no calls.
"""

from __future__ import annotations

from .episode import Episode
from .llm import LLM, LLMError
from .models import Action, EpisodeResult, Outcome, Rule
from .planner import PlanningError, PlanStep, make_plan
from .schema_store import SchemaStore, Transition, applies
from .verifier import Check, check, predict

MINEABLE_BLOCKS = ["oak_log", "stone", "coal_ore"]
CRAFTABLE_ITEMS = ["oak_planks", "stick", "crafting_table", "wooden_pickaxe", "stone_pickaxe", "torch"]


def seed_prior_rules(store: SchemaStore, llm: LLM) -> list[Rule]:
    """Everything the model believes starts as a hypothesis."""
    rules = llm.propose_prior_rules(MINEABLE_BLOCKS, CRAFTABLE_ITEMS)
    for rule in rules:
        store.add_rule(rule)
    return rules


def run_schemavoyager(episode: Episode, store: SchemaStore) -> EpisodeResult:
    """Run one episode, replanning from the current state after any step that fails or surprises."""
    task = episode.task
    episode.log.event(
        "episode_start", system=episode.system, task=task.task_id, description=task.description,
        world_id=episode.world_id, budget=task.budget, layout=episode.layout_seed,
    )
    completed: list[tuple[Action, str]] = []

    while not episode.goal_met and episode.budget_left > 0:
        try:
            plan = make_plan(
                task.goal_item, task.goal_count, episode.inventory, store.usable_rules(episode.world_id), store.skill_for
            )
        except PlanningError as error:
            episode.log.event("plan_failed", reason=str(error))
            break
        _log_plan(episode, store, plan)

        for step in plan:
            if episode.budget_left <= 0:
                break
            verdict: dict[str, Check] = {}
            outcome = episode.act(step.action, annotate=lambda o, s=step: _annotate(s, o, verdict))
            result = verdict["check"]
            if result.status == "failed":
                break  # tolerant: replan from the current state

            transition_id = f"{episode.episode_id}/{episode.actions}"
            store.record_transition(
                Transition(transition_id, episode.episode_id, episode.world_id, step.action, result.observed)
            )
            if result.status == "mismatch":
                _revise(episode, store, step, result, transition_id)
                break
            completed.append((step.action, step.rule.id))
            if outcome.inventory != step.expected_inventory:
                break  # a drop rolled out of reach; replan without revising anything

    result = episode.finish()
    certified = store.certify()
    episode.log.event("certified", rules=certified)
    if result.success and task.split == "train":
        skill = store.compile_skill(task.goal_item, task.goal_count, completed, episode.episode_id)
        episode.log.event("skill", skill=skill.id, rule_ids=skill.rule_ids)
    return result


def _annotate(step: PlanStep, outcome: Outcome, verdict: dict[str, Check]) -> dict:
    result = check(step.rule, step.action, outcome)
    verdict["check"] = result
    return {
        "rule": step.rule.id, "experimental": step.experimental, "predicted": result.predicted,
        "check": result.status, "observed": result.observed, "check_detail": result.detail,
    }


def _log_plan(episode: Episode, store: SchemaStore, plan: list[PlanStep]) -> None:
    plan_rules = {step.rule.id for step in plan}
    skills = [
        skill.id
        for skill in store.skills.values()
        if skill.status == "ok" and set(skill.rule_ids) <= plan_rules
    ]
    episode.used_skills = list(dict.fromkeys(episode.used_skills + skills))
    episode.log.event(
        "plan",
        steps=[{"action": s.action.describe(), "rule": s.rule.id, "experimental": s.experimental} for s in plan],
        skills=skills,
    )
    first = plan[0].action.describe() if plan else "nothing"
    episode.backend.say(f"Plan: {len(plan)} steps, starting with {first}.")


def _revise(episode: Episode, store: SchemaStore, step: PlanStep, result: Check, transition_id: str) -> None:
    rule = step.rule
    flagged = store.demote(rule.id, transition_id, episode.world_id)
    episode.log.event(
        "contradiction", rule=rule.id, transition=transition_id, detail=result.detail,
        predicted=result.predicted, observed=result.observed, flagged=flagged,
    )
    episode.backend.say(f"Unexpected: {result.detail}. Rule {rule.id} no longer trusted here.")

    counterexample = {"action": step.action.to_dict(), "predicted": result.predicted, "observed": result.observed}
    known_items = sorted({item for r in store.usable_rules(episode.world_id) for item in r.yields()})
    tokens_before = episode.llm.total_tokens
    try:
        hypotheses = episode.llm.revise_rule(
            rule, store.evidence_summary(rule), counterexample, known_items, episode.world_id
        )
    except LLMError as error:
        episode.log.event("plan_failed", reason=f"revision call failed: {error}")
        hypotheses = []
    episode.revisions += 1

    summaries = []
    for hypothesis in hypotheses:
        # A hypothesis must explain the counterexample it was proposed for.
        kept = not applies(hypothesis, step.action) or predict(hypothesis, step.action) == result.observed
        if kept:
            store.add_rule(hypothesis)
        summaries.append({"rank": hypothesis.rank + 1, "id": hypothesis.id, "kept": kept,
                          "summary": _summarize(hypothesis), "rule": hypothesis.to_dict()})
    episode.log.event("revision", rule=rule.id, hypotheses=summaries, tokens=episode.llm.total_tokens - tokens_before)
    if summaries:
        episode.backend.say(f"Hypothesis 1: {summaries[0]['summary']}")


def _summarize(rule: Rule) -> str:
    if rule.kind == "mine":
        tools = rule.preconditions.get("tool_any", [])
        drops = rule.yields()
        what = ", ".join(f"{n} {item}" for item, n in drops.items()) or "nothing"
        how = f"with {' / '.join(tools)}" if tools else "with any tool or bare hand"
        return f"mine {rule.subject} {how} -> drops {what}. {rule.rationale}"
    gains = ", ".join(f"{n} {item}" for item, n in rule.yields().items())
    return f"craft {rule.subject} -> {gains}. {rule.rationale}"
