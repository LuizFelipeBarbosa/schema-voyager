from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Episode, Skill, WorldRule


class SchemaStore:
    """Persistent, inspectable workspace for rules, skills, and evidence.

    Passing ``root=None`` keeps the store in memory, which is useful in tests.
    A filesystem-backed store writes small JSON files so a demo can show the
    agent's learned state without introducing a database dependency.
    """

    def __init__(self, root: Path | None = None):
        self.root = root
        self.rules: dict[str, WorldRule] = {}
        self._skills: dict[str, Skill] = {}
        self.episodes: list[dict[str, Any]] = []
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    def has_skill(self, goal_item: str) -> bool:
        return any(skill.goal_item == goal_item for skill in self._skills.values())

    def get_skill_for_goal(self, goal_item: str) -> Skill | None:
        return next(
            (skill for skill in self._skills.values() if skill.goal_item == goal_item),
            None,
        )

    def upsert_rule(self, rule: WorldRule) -> None:
        existing = self.rules.get(rule.rule_id)
        if existing is not None:
            existing.confidence = min(5, max(existing.confidence, rule.confidence) + 1)
            for evidence in rule.evidence:
                if evidence not in existing.evidence:
                    existing.evidence.append(evidence)
            self._write_json("world_model.json", [asdict(item) for item in self.rules.values()])
            return
        self.rules[rule.rule_id] = rule
        self._write_json("world_model.json", [asdict(item) for item in self.rules.values()])

    def upsert_skill(self, skill: Skill) -> None:
        previous = self._skills.get(skill.skill_id)
        if previous is not None:
            skill.version = previous.version + 1
            skill.evidence = list(dict.fromkeys(previous.evidence + skill.evidence))
        self._skills[skill.skill_id] = skill
        self._write_json("skills.json", [asdict(item) for item in self._skills.values()])

    def record_episode(self, episode: Episode) -> None:
        payload = asdict(episode)
        payload["final_observation"]["position"] = asdict(episode.final_observation.position)
        for serialized_step, step in zip(payload["steps"], episode.steps):
            serialized_step["action"] = asdict(step.action)
        self.episodes.append(payload)
        self._write_json("episodes.json", self.episodes)

    def _write_json(self, filename: str, payload: Any) -> None:
        if self.root is None:
            return
        (self.root / filename).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

