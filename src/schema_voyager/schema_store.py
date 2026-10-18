from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import Action, Rule, Skill
from .normalize import family
from .verifier import CheckKind, evaluate

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
    return f"{world_id}/{kind}:{family(subject)}"


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
        opened = self.epochs.get(key, [])
        return opened[-1].number if opened else 0

    def epoch_of(self, transition: Transition) -> int:
        key = epoch_key(transition.world_id, transition.action.kind, transition.action.target or "")
        started = [e.number for e in self.epochs.get(key, []) if e.start_seq <= transition.seq]
        return max(started, default=0)

    def add_rule(self, rule: Rule) -> None:
        if rule.source == "revision":
            rule.epoch = self.current_epoch(epoch_key(rule.scope, rule.kind, rule.subject))
        self.rules[rule.id] = rule

    def usable_rules(self, world_id: str) -> list[Rule]:
        demoted_here = self.demoted_in.get(world_id, {})
        return [
            rule
            for rule in self.rules.values()
            if rule.scope in (TRAIN_WORLD, world_id) and rule.status != "demoted" and rule.id not in demoted_here
        ]

    def record_transition(self, transition: Transition) -> None:
        transition.seq = len(self.transitions)
        self.transitions.append(transition)

    def demote(self, rule_id: str, transition_id: str, world_id: str) -> list[str]:
        """Demote a rule in one world and flag every skill that cites it."""
        rule = self.rules[rule_id]
        if rule.status == "certified" or self.replay(rule)[0]:
            self._open_epoch(rule, transition_id, world_id)
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

    def retest(self, rule_id: str, world_id: str) -> Rule:
        """Add a copy of a demoted rule whose evidence starts now, so the contradiction that
        demoted the original cannot count against it. A retest never opens an epoch."""
        rule = self.rules[rule_id]
        transition_id = self.demoted_in.get(world_id, {}).get(rule_id)
        if transition_id is None:
            raise ValueError(f"cannot retest {rule_id}: it is not demoted in {world_id}")
        if rule.source == "retest":
            raise ValueError(f"cannot retest {rule_id}: it is already a retest")
        if any(r.source == "retest" and r.revision_of == rule_id for r in self.rules.values()):
            raise ValueError(f"cannot retest {rule_id}: it already has a retest")
        retest_id = f"{rule_id}.retest@{world_id}"
        if retest_id in self.rules:
            raise ValueError(f"cannot retest {rule_id}: a rule with id {retest_id} already exists")

        retest = Rule(
            id=retest_id,
            action=copy.deepcopy(rule.action),
            preconditions=copy.deepcopy(rule.preconditions),
            effects=copy.deepcopy(rule.effects),
            status="hypothesis",
            source="retest",
            scope=world_id,
            rank=0,
            rationale=f"retest of {rule_id}, demoted by {transition_id}",
            revision_of=rule_id,
            check=copy.deepcopy(rule.check),
            epoch=self.current_epoch(epoch_key(world_id, rule.kind, rule.subject)),
            since_seq=len(self.transitions),
        )
        self.add_rule(retest)
        return retest

    def _open_epoch(self, rule: Rule, transition_id: str, world_id: str) -> None:
        """A contradicted rule that had earned trust means the world changed: evidence
        gathered before the contradicting transition no longer speaks for new rules."""
        key = epoch_key(world_id, rule.kind, rule.subject)
        transition = next((t for t in self.transitions if t.id == transition_id), None)
        start_seq = transition.seq if transition is not None else len(self.transitions)
        opened = self.epochs.setdefault(key, [])
        if opened and opened[-1].start_seq == start_seq:
            return  # the same transition already opened this epoch
        opened.append(Epoch(key, self.current_epoch(key) + 1, start_seq, transition_id, rule.id))

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
            if transition.seq < rule.since_seq or self.epoch_of(transition) < rule.epoch:
                continue
            verdict = evaluate(rule, transition.action, transition.observed)
            if verdict == "confirm":
                confirmed.append(transition)
            elif verdict == "contradict":
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
            "epochs": {key: [asdict(e) for e in opened] for key, opened in self.epochs.items()},
            "transitions": [
                {
                    "id": t.id,
                    "episode_id": t.episode_id,
                    "world_id": t.world_id,
                    "action": t.action.to_dict(),
                    "observed": t.observed,
                    "kind": t.kind,
                    "seq": t.seq,
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
                Transition(
                    t["id"], t["episode_id"], t["world_id"], Action(**t["action"]), t["observed"],
                    kind=t.get("kind", "exact"), seq=t.get("seq", position),
                )
                for position, t in enumerate(payload["transitions"])
            ],
            demoted_in=payload["demoted_in"],
            epochs={key: [Epoch(**e) for e in opened] for key, opened in payload.get("epochs", {}).items()},
        )


def applies(rule: Rule, action: Action) -> bool:
    """Whether a rule makes a prediction for this action: same action and
    subject, and for mining and killing, a tool the rule's preconditions allow."""
    if action.kind != rule.kind or action.target is None or family(action.target) != family(rule.subject):
        return False
    if rule.kind in ("mine", "kill"):
        tools = rule.preconditions.get("tool_any", [])
        return not tools or action.tool in tools
    return True
