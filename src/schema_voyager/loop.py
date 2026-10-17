from __future__ import annotations

from dataclasses import dataclass

from .adapter import MinecraftAdapter
from .environment import MINING_RULES, RECIPES, MinecraftLikeEnv
from .models import Action, Episode, Observation, Skill, StepRecord, TaskSpec, WorldRule
from .schema_store import SchemaStore


@dataclass
class _RunState:
    env: MinecraftAdapter
    mode: str
    store: SchemaStore
    steps: list[StepRecord]
    used_skills: list[str]
    estimated_tokens: int = 0


def _predictions(action: Action) -> list[str]:
    if action.name == "move":
        return [f"position_moves:{action.target}"]
    if action.name == "mine":
        return ["inventory_changes"]
    if action.name == "craft":
        return [f"inventory_gains:{action.target}"]
    return ["observation_refreshed"]


def _verified_effects(before: Observation, after: Observation, action: Action, ok: bool) -> list[str]:
    if not ok:
        return []
    effects: list[str] = []
    if action.name == "move" and before.position != after.position:
        effects.append("position_changed")
    if action.name == "mine" and before.inventory != after.inventory:
        effects.append("inventory_changed")
    if action.name == "craft" and action.target is not None:
        if after.inventory.get(action.target, 0) > before.inventory.get(action.target, 0):
            effects.append(f"crafted:{action.target}")
    if action.name == "inspect":
        effects.append("observation_refreshed")
    return effects


class SkillLoop:
    """A compact Voyager-like loop: plan, execute, verify, and compile."""

    def __init__(self, env: MinecraftAdapter, store: SchemaStore, mode: str):
        self.state = _RunState(env, mode, store, [], [])

    def execute(self, action: Action) -> bool:
        before = self.state.env.observe()
        result = self.state.env.step(action)
        self.state.steps.append(
            StepRecord(
                action=action,
                ok=result.ok,
                message=result.message,
                predicted_effects=_predictions(action),
                verified_effects=_verified_effects(before, result.observation, action, result.ok),
            )
        )
        # This deliberately rough estimate lets the evaluator expose the cost
        # of exploratory chatter without pretending to measure provider billing.
        self.state.estimated_tokens += 8 if action.name == "inspect" else 3
        return result.ok

    def move_to(self, target: tuple[int, int]) -> None:
        while self.state.env.position.x < target[0]:
            self.execute(Action("move", "east"))
        while self.state.env.position.x > target[0]:
            self.execute(Action("move", "west"))
        while self.state.env.position.z < target[1]:
            self.execute(Action("move", "south"))
        while self.state.env.position.z > target[1]:
            self.execute(Action("move", "north"))

    def collect(self, item: str, amount: int) -> None:
        block = next(
            (block for block, (mined_item, _) in MINING_RULES.items() if mined_item == item),
            None,
        )
        if block is None:
            raise ValueError(f"No mining rule produces {item}.")
        while self.state.env.inventory[item] < amount and not self.state.env.is_done:
            if self.state.mode == "baseline":
                self.execute(Action("inspect"))
                self.execute(Action("inspect"))
            position = self.state.env.nearest_block(block)
            if position is None:
                raise RuntimeError(f"No {block} remains while collecting {item}.")
            self.move_to((position.x, position.z))
            if not self.execute(Action("mine")):
                raise RuntimeError(self.state.steps[-1].message)

    def ensure_item(self, item: str, amount: int) -> None:
        if self.state.env.inventory[item] >= amount:
            return
        recipe = RECIPES.get(item)
        if recipe is None:
            self.collect(item, amount)
            return
        ingredients, output_count = recipe
        while self.state.env.inventory[item] < amount and not self.state.env.is_done:
            # Build nested ingredients first. For example, sticks consume
            # planks, and a wooden pickaxe consumes both; making sticks first
            # prevents the intermediate craft from spending the last planks
            # needed by the parent recipe.
            ordered_ingredients = sorted(
                ingredients.items(),
                key=lambda pair: _recipe_depth(pair[0]),
                reverse=True,
            )
            for ingredient, ingredient_count in ordered_ingredients:
                self.ensure_item(ingredient, ingredient_count)
            if not self.execute(Action("craft", item)):
                raise RuntimeError(self.state.steps[-1].message)
            # Recipes in this environment always produce a positive amount;
            # this guard prevents a future recipe edit from creating a loop.
            if output_count <= 0:
                raise ValueError(f"Recipe for {item} has no output.")

    def run(self, task: TaskSpec, learn: bool = False) -> Episode:
        goal_skill = self.state.store.get_skill_for_goal(task.goal_item)
        if self.state.mode == "hybrid" and goal_skill is not None:
            self.state.used_skills.append(goal_skill.skill_id)
        self.ensure_item(task.goal_item, task.goal_count)
        final = self.state.env.observe()
        episode = Episode(
            task_id=task.task_id,
            mode=self.state.mode,
            success=final.success,
            steps=self.state.steps,
            final_observation=final,
            used_skills=self.state.used_skills,
            estimated_output_tokens=self.state.estimated_tokens,
        )
        self.state.store.record_episode(episode)
        if learn and episode.success:
            compile_skill(task, episode, self.state.store)
        return episode


def compile_skill(task: TaskSpec, episode: Episode, store: SchemaStore) -> None:
    skill_id = f"skill_{task.goal_item}"
    action_names = list(dict.fromkeys(step.action.name for step in episode.steps))
    store.upsert_skill(
        Skill(
            skill_id=skill_id,
            goal_item=task.goal_item,
            preconditions=["task.goal_item matches skill goal"],
            steps=action_names,
            postconditions=[f"inventory[{task.goal_item}] >= {task.goal_count}"],
            validator="replay action transitions and check postcondition",
            evidence=[task.task_id],
        )
    )
    for step in episode.steps:
        if step.action.name == "craft" and step.action.target in RECIPES:
            ingredients, output_count = RECIPES[step.action.target]
            store.upsert_rule(
                WorldRule(
                    rule_id=f"craft:{step.action.target}",
                    preconditions=[f"inventory[{item}] >= {count}" for item, count in ingredients.items()],
                    predicted_effects=[f"inventory[{step.action.target}] += {output_count}"],
                    evidence=[task.task_id],
                    confidence=2,
                )
            )
    for block in set(task.resources.values()):
        if block not in MINING_RULES:
            continue
        item, amount = MINING_RULES[block]
        preconditions = ["agent is standing on the block"]
        if block == "stone":
            preconditions.append("inventory[wooden_pickaxe] >= 1 or inventory[stone_pickaxe] >= 1")
        elif block == "coal_ore":
            preconditions.append("inventory[stone_pickaxe] >= 1")
        store.upsert_rule(
            WorldRule(
                rule_id=f"mine:{block}",
                preconditions=preconditions,
                predicted_effects=[f"inventory[{item}] += {amount}"],
                evidence=[task.task_id],
                confidence=2,
            )
        )


def run_task(task: TaskSpec, store: SchemaStore, mode: str, learn: bool = False) -> Episode:
    return SkillLoop(MinecraftLikeEnv(task), store, mode).run(task, learn=learn)


def _recipe_depth(item: str) -> int:
    recipe = RECIPES.get(item)
    if recipe is None:
        return 0
    ingredients, _ = recipe
    return 1 + max((_recipe_depth(ingredient) for ingredient in ingredients), default=0)

