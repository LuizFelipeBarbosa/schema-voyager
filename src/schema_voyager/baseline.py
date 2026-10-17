"""Skill-only baseline (system B), Voyager-style.

Skills are successful action sequences with a description. The top 3 are
retrieved by description, the LLM writes a plan from them and the current
observation, and after a failed attempt it gets the execution errors and the
final inventory back, with up to 3 replans. There are no per-step
predictions: it checks the goal only when a plan ends or an action errors.

B+ is the same baseline with richer feedback: each executed action is
reported with its inventory change, so a mine that yielded nothing is visible.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .episode import Episode
from .llm import LLMError
from .models import Action, EpisodeResult, Outcome

MAX_REPLANS = 3


@dataclass
class BaselineSkill:
    name: str
    description: str
    goal_item: str
    actions: list[dict] = field(default_factory=list)


@dataclass
class SkillLibrary:
    skills: list[BaselineSkill] = field(default_factory=list)

    def retrieve(self, query: str, k: int = 3) -> list[BaselineSkill]:
        words = set(_words(query))
        scored = sorted(self.skills, key=lambda s: -len(words & set(_words(s.description + " " + s.goal_item))))
        return scored[:k]

    def add(self, skill: BaselineSkill) -> None:
        self.skills = [s for s in self.skills if s.name != skill.name] + [skill]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([asdict(s) for s in self.skills], indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SkillLibrary":
        return cls([BaselineSkill(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))])


def run_skill_only(
    episode: Episode, library: SkillLibrary, fake_planner=None, report_effects: bool = False
) -> EpisodeResult:
    """`fake_planner(inventory) -> list[Action]` stands in for the LLM in fake mode.
    `report_effects` turns B into B+."""
    task = episode.task
    episode.log.event(
        "episode_start", system=episode.system, task=task.task_id, description=task.description,
        world_id=episode.world_id, budget=task.budget, layout=episode.layout_seed,
    )
    retrieved = library.retrieve(f"{task.description} {task.goal_item}")
    episode.used_skills = [s.name for s in retrieved]
    executed: list[Action] = []
    feedback: str | None = None

    for attempt in range(1 + MAX_REPLANS):
        if episode.goal_met or episode.budget_left <= 0:
            break
        observation = episode.backend.observe()
        episode.inventory = observation.inventory
        prompt = _render_prompt(episode, observation.nearby, retrieved, feedback)
        tokens_before = episode.llm.total_tokens
        fake_plan = fake_planner(observation.inventory) if fake_planner else None
        try:
            summary, actions = episode.llm.baseline_plan(prompt, fake_plan)
        except LLMError as error:
            summary, actions = f"LLM call failed: {error}", []
        episode.log.event(
            "llm_plan", attempt=attempt + 1, summary=summary, tokens=episode.llm.total_tokens - tokens_before,
            actions=[a.describe() for a in actions],
        )
        episode.backend.say(f"Plan attempt {attempt + 1}: {len(actions)} actions.")

        errors, ran = [], []
        for action in actions:
            if episode.budget_left <= 0:
                errors.append("action budget exhausted")
                break
            outcome = episode.act(action)
            ran.append(_describe_effect(action, outcome) if report_effects and outcome.ok else action.describe())
            if not outcome.ok:
                missing = outcome.detail.get("missing")
                errors.append(f"{action.describe()} failed: {outcome.error}" + (f" (missing {missing})" if missing else ""))
                break
            executed.append(action)

        if episode.goal_met:
            break
        executed_label = "Actions executed, with each one's inventory change" if report_effects else "Actions executed"
        feedback = (
            f"{executed_label}: {'; '.join(ran) or 'none'}.\n"
            f"Execution errors: {'; '.join(errors) or 'none, but the goal was not reached'}.\n"
            f"Inventory now: {json.dumps(episode.inventory)}."
        )
        episode.log.event("feedback", summary=feedback.replace("\n", " "))

    result = episode.finish()
    if result.success and task.split == "train":
        library.add(BaselineSkill(
            name=f"obtain_{task.goal_item}",
            description=task.description,
            goal_item=task.goal_item,
            actions=[a.to_dict() for a in executed],
        ))
    return result


def _describe_effect(action: Action, outcome: Outcome) -> str:
    change = ", ".join(f"{item} {count:+d}" for item, count in sorted(outcome.inventory_delta.items()) if count)
    return f"{action.describe()} (inventory change: {change or 'none'})"


def _render_prompt(episode: Episode, nearby: dict[str, int], skills: list[BaselineSkill], feedback: str | None) -> str:
    task = episode.task
    lines = [
        f"Goal: have {task.goal_count} {task.goal_item} in the inventory. {task.description}",
        f"Inventory: {json.dumps(episode.inventory) if episode.inventory else 'empty'}",
        f"Blocks visible within 16 blocks: {json.dumps(nearby)}",
        f"Actions left in the budget: {episode.budget_left}",
    ]
    if skills:
        lines.append("\nRelevant skills from your library (action sequences that worked before):")
        for skill in skills:
            steps = "; ".join(Action(**a).describe() for a in skill.actions)
            lines.append(f"- {skill.name}: {skill.description}\n  {steps}")
    if feedback:
        lines.append(f"\nYour previous plan did not reach the goal.\n{feedback}\nWrite the actions to run next, from the current state.")
    return "\n".join(lines)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower().replace("_", " "))
