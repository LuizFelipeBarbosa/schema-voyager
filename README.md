# SchemaVoyager

SchemaVoyager is a one-day starter repo for the Version N+1 track: a small
Voyager-style Minecraft agent that learns executable skills and stores the
world rules that justify those skills.

The repository is deliberately runnable without a Minecraft installation. It
uses a deterministic Minecraft-like grid so the loop, schema files, evaluator,
and demo can be tested in seconds. `MinecraftLikeEnv` is the adapter boundary
for replacing the grid with MineDojo, Mineflayer, or a Voyager runtime later.

## Run the demo

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
schema-voyager-demo
```

The demo trains on three tasks, evaluates a baseline against three held-out
tasks, and evaluates the hybrid agent on the same holdout. It writes the
learned state to `.demo_artifacts/`:

```text
.demo_artifacts/
  episodes.json       # action traces and observations
  skills.json         # compiled executable skill summaries
  world_model.json    # certified recipe rules and evidence
```

The starter run should show equal success on both systems, with the hybrid
using fewer actions and estimated output tokens because it does not repeat the
baseline's exploratory `inspect` calls. The token number is an inexpensive
proxy for the demo; replace it with provider usage data for a serious result.

## The exact task set

Training tasks teach the core progression:

| ID | Goal | Starting state |
| --- | --- | --- |
| `t1_wooden_pickaxe` | craft a wooden pickaxe | empty inventory, two nearby logs |
| `t2_stone_pickaxe` | craft a stone pickaxe | wooden pickaxe and sticks, stone two blocks away |
| `t3_furnace` | craft a furnace | stone pickaxe, eight stone blocks |

Holdout tasks vary spawn location, distance, and partial inventory:

| ID | Goal | Variation |
| --- | --- | --- |
| `h1_wooden_pickaxe_new_spawn` | wooden pickaxe | new spawn coordinates |
| `h2_stone_pickaxe_hidden_resource` | stone pickaxe | distant stone, one log and one stick already held |
| `h3_starter_kit` | furnace | new spawn and a wider stone field |

The action space is intentionally small: `move`, `mine`, `craft`, and
`inspect`. Recipes and mining prerequisites are in
`src/schema_voyager/environment.py`.

## Architecture

```text
TaskSpec
   |
   v
MinecraftLikeEnv ---- observe() / step(Action) ----> SkillLoop
                                                        |
                         +------------------------------+------------------+
                         |                                                 |
                   SchemaStore                                      verifier
             rules + skills + evidence                     predicted vs observed effects
                         |
                         v
                   compiled skill
```

`SkillLoop` is the control loop:

1. Inspect the current inventory and task goal.
2. Recursively satisfy recipe ingredients, locating and mining raw resources.
3. Execute each action through the environment boundary.
4. Record predicted effects and verify them against the next observation.
5. On a successful training episode, compile a skill and promote the observed
   recipe transitions into evidence-backed rules.

`SchemaStore` keeps the world model and skill library separate. A skill says
which action families achieved a goal. A rule records a precondition, a
predicted effect, and the task IDs that supplied evidence for it.

## Schema format

The in-memory format is represented by `WorldRule` and `Skill` dataclasses and
is saved as JSON. A typical learned rule looks like:

```json
{
  "rule_id": "craft:wooden_pickaxe",
  "preconditions": ["inventory[planks] >= 3", "inventory[sticks] >= 2"],
  "predicted_effects": ["inventory[wooden_pickaxe] += 1"],
  "evidence": ["t1_wooden_pickaxe", "h1_wooden_pickaxe_new_spawn"],
  "confidence": 2
}
```

The demo only promotes rules after a successful, verified episode. A real
LLM-backed version can add a hypothesis stage before this promotion and reject
rules whose predictions fail on replay.

## Evaluation

`evaluate()` returns:

- `success_rate`: fraction of tasks reaching the requested goal item;
- `average_actions`: environment actions per episode;
- `average_estimated_tokens`: proxy for model output cost;
- `skill_reuse_rate`: holdout episodes that used a trained skill;
- `failed_actions`: actions rejected by the environment.

The intended competition comparison is:

```text
baseline: no learned store, extra inspection during resource collection
hybrid:   trained SchemaStore, reusable skills, verified transitions
```

For a stronger experiment, run each condition over multiple seeded worlds,
replace the token proxy with actual API usage, and add adversarial resource
placements that invalidate an unverified rule.

## Turning this into real Minecraft

Keep the agent and evaluator unchanged and implement the `MinecraftAdapter`
protocol in `src/schema_voyager/adapter.py`. The adapter needs these methods
and state fields:

```python
task: TaskSpec
position: Position
inventory: Counter
is_done: bool
is_success: bool
reset() -> Observation
observe() -> Observation
step(action: Action) -> ActionResult
nearest_block(block: str) -> Position | None
```

The adapter translates `move`, `mine`, and `craft` into MineDojo or
Mineflayer/Voyager actions and maps the real observation back to the compact
`Observation` model. Start with the three training goals, log every raw
observation, and only promote skills after replaying their predicted effects.

## Repository layout

```text
src/schema_voyager/
  models.py       # task, observation, action, schema, and episode dataclasses
  environment.py  # deterministic Minecraft-like environment
  tasks.py        # train/holdout task definitions
  schema_store.py # JSON-backed rules, skills, and evidence
  loop.py         # plan -> execute -> verify -> compile loop
  evaluator.py    # metrics and evaluation reports
  demo.py         # one-command end-to-end run
tests/            # fast environment and loop checks
examples/         # sample persisted schema JSON
```

