from __future__ import annotations

from dataclasses import dataclass

from .loop import run_task
from .models import Episode, TaskSpec
from .schema_store import SchemaStore


@dataclass
class EvaluationReport:
    mode: str
    episodes: list[Episode]

    @property
    def success_rate(self) -> float:
        return sum(episode.success for episode in self.episodes) / max(1, len(self.episodes))

    @property
    def average_actions(self) -> float:
        return sum(episode.action_count for episode in self.episodes) / max(1, len(self.episodes))

    @property
    def average_tokens(self) -> float:
        return sum(episode.estimated_output_tokens for episode in self.episodes) / max(1, len(self.episodes))

    @property
    def skill_reuse_rate(self) -> float:
        return sum(bool(episode.used_skills) for episode in self.episodes) / max(1, len(self.episodes))

    @property
    def failed_actions(self) -> int:
        return sum(episode.failed_actions for episode in self.episodes)

    def summary(self) -> dict[str, float | int | str]:
        return {
            "mode": self.mode,
            "success_rate": round(self.success_rate, 3),
            "average_actions": round(self.average_actions, 2),
            "average_estimated_tokens": round(self.average_tokens, 2),
            "skill_reuse_rate": round(self.skill_reuse_rate, 3),
            "failed_actions": self.failed_actions,
        }


def evaluate(tasks: list[TaskSpec], store: SchemaStore, mode: str, learn: bool = False) -> EvaluationReport:
    return EvaluationReport(mode, [run_task(task, store, mode, learn=learn) for task in tasks])

