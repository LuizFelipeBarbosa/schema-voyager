from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Action, Rule, Skill
from .verifier import CheckKind, predict

TRAIN_WORLD = "train"
MIN_CONFIRMATIONS = 2
MIN_EPISODES = 2


@dataclass
class Transition:
    """One exact-checkable step, kept for replay."""

    id: str
    episode_id: str
    world_id: str
    action: Action
    observed: dict[str, Any]
    kind: CheckKind = "exact"  # "exact" or "stochastic"; quarantined steps are never recorded
    seq: int = -1  # assigned by record_transition: its position in the log


@dataclass
class Epoch:
    """From `start_seq` on, a rule key's earlier evidence can neither confirm nor
    contradict rules proposed in this epoch. See docs/v2-interfaces.md."""

    key: str  # epoch_key(world_id, action type, subject)
    number: int  # 1, 2, ...; epoch 0 is implicit, from the start of the log
    start_seq: int  # seq of the transition that opened it, which belongs to it
    opened_by: str  # that transition's id
    rule_id: str  # the rule whose contradiction opened it


def epoch_key(world_id: str, kind: str, subject: str) -> str:
    raise NotImplementedError("schema_store.epoch_key: rules lane")


@dataclass
class SchemaStore:
    """Rules, skills and the transitions that are evidence for them.

    Rules are usable in their own scope (the training world, or the holdout
    world whose revision proposed them) unless they were demoted there.
    Certification only gates what becomes a reusable skill and what is saved.
    """

    rules: dict[str, Rule] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    demoted_in: dict[str, dict[str, str]] = field(default_factory=dict)  # world -> rule -> transition
    epochs: dict[str, list[Epoch]] = field(default_factory=dict)  # epoch key -> opened epochs, in order

    def current_epoch(self, key: str) -> int:
        raise NotImplementedError("SchemaStore.current_epoch: rules lane")

    def epoch_of(self, transition: Transition) -> int:
        raise NotImplementedError("SchemaStore.epoch_of: rules lane")

    def add_rule(self, rule: Rule) -> None:
        self.rules[rule.id] = rule

    def usable_rules(self, world_id: str) -> list[Rule]:
        demoted_here = self.demoted_in.get(world_id, {})
        return [
            rule
            for rule in self.rules.values()
            if rule.scope in (TRAIN_WORLD, world_id) and rule.status != "demoted" and rule.id not in demoted_here
        ]

    def record_transition(self, transition: Transition) -> None:
        self.transitions.append(transition)

    def demote(self, rule_id: str, transition_id: str, world_id: str) -> list[str]:
        """Demote a rule in one world and flag every skill that cites it."""
        rule = self.rules[rule_id]
        self.demoted_in.setdefault(world_id, {})[rule_id] = transition_id
        if rule.scope == world_id:
            rule.status = "demoted"
        else:
            rule.evidence.setdefault("counterexamples", []).append(transition_id)

        flagged = []
        for skill in self.skills.values():
            if rule_id in skill.rule_ids and skill.status == "ok":
                skill.status = "flagged"
                skill.flagged_by = rule_id
                flagged.append(skill.id)
        return flagged

    def certify(self) -> list[str]:
        """Replay in scope and promote hypotheses that pass all three tests:
        zero contradictions, enough confirmations, and confirmations from
        more than one episode."""
        newly_certified = []
        for rule in self.rules.values():
            confirmed, contradicted = self.replay(rule)
            rule.evidence["confirmed"] = [t.id for t in confirmed]
            rule.evidence["contradicted"] = [t.id for t in contradicted]
            if rule.status != "hypothesis" or contradicted:
                continue
            episodes = {t.episode_id for t in confirmed}
            if len(confirmed) >= MIN_CONFIRMATIONS and len(episodes) >= MIN_EPISODES:
                rule.status = "certified"
                newly_certified.append(rule.id)
        return newly_certified

    def replay(self, rule: Rule) -> tuple[list[Transition], list[Transition]]:
        confirmed, contradicted = [], []
        for transition in self.transitions:
            if transition.world_id != rule.scope or not applies(rule, transition.action):
                continue
            if transition.observed == predict(rule, transition.action):
                confirmed.append(transition)
            else:
                contradicted.append(transition)
        return confirmed, contradicted

    def evidence_summary(self, rule: Rule) -> str:
        confirmed, _ = self.replay(rule)
        if not confirmed:
            return "No confirming observations yet; this rule comes from prior knowledge."
        episodes = sorted({t.episode_id for t in confirmed})
        lines = [f"Confirmed {len(confirmed)} times across {len(episodes)} training episodes, e.g.:"]
        for transition in confirmed[:3]:
            lines.append(f"- {transition.action.describe()} -> {json.dumps(transition.observed)}")
        return "\n".join(lines)

    def compile_skill(self, goal_item: str, goal_count: int, steps: list[tuple[Action, str]], episode_id: str) -> Skill:
        rule_ids = list(dict.fromkeys(rule_id for _, rule_id in steps))
        skill_id = f"skill_{goal_item}"
        existing = self.skills.get(skill_id)
        if existing is not None and existing.rule_ids == rule_ids:
            existing.evidence.append(episode_id)
            return existing
        skill = Skill(
            id=skill_id,
            goal_item=goal_item,
            goal_count=goal_count,
            rule_ids=rule_ids,
            steps=[action.to_dict() for action, _ in steps],
            evidence=[episode_id],
        )
        self.skills[skill_id] = skill
        return skill

    def skill_for(self, item: str) -> Skill | None:
        skill = self.skills.get(f"skill_{item}")
        if skill is None or skill.status != "ok":
            return None
        return skill

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [rule.to_dict() for rule in self.rules.values()],
            "skills": [skill.to_dict() for skill in self.skills.values()],
            "demoted_in": self.demoted_in,
            "transitions": [
                {
                    "id": t.id,
                    "episode_id": t.episode_id,
                    "world_id": t.world_id,
                    "action": t.action.to_dict(),
                    "observed": t.observed,
                }
                for t in self.transitions
            ],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SchemaStore":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            rules={r["id"]: Rule.from_dict(r) for r in payload["rules"]},
            skills={s["id"]: Skill.from_dict(s) for s in payload["skills"]},
            transitions=[
                Transition(t["id"], t["episode_id"], t["world_id"], Action(**t["action"]), t["observed"])
                for t in payload["transitions"]
            ],
            demoted_in=payload["demoted_in"],
        )


def applies(rule: Rule, action: Action) -> bool:
    """Whether a rule makes a prediction for this action: same action and
    subject, and for mining, a tool the rule's preconditions allow."""
    if action.kind != rule.kind or action.target != rule.subject:
        return False
    if rule.kind == "mine":
        tools = rule.preconditions.get("tool_any", [])
        return not tools or action.tool in tools
    return True
