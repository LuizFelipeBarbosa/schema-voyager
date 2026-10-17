# SchemaVoyager

A Minecraft agent that discovers rules it doesn't already know, stores them as
verified schemas, and uses them to reuse skills safely in changed worlds.

The demo runs on a real vanilla 1.21.1 server with a Mineflayer bot. The agent
trains on three tasks, certifies the rules it relied on, and is then dropped
into a world where **coal ore only drops coal when mined with a stone-tier
pickaxe** (a loot-table datapack the client can never see). SchemaVoyager (D)
catches the missing drop at the mine step, demotes the rule, asks Claude for
ranked alternatives, tests "needs a stone pickaxe", and finishes the task. A
Voyager-style skill-only baseline (B) with the same model and actions replays
its torch skill and keeps mining coal with a wooden pickaxe.

## Quick start

Prerequisites: Docker (Colima on macOS), Node 20+, Python 3.11+ with `uv`.

```bash
# 1. Minecraft server (vanilla 1.21.1, superflat, peaceful, RCON on)
scripts/up.sh

# 2. Bot server: HTTP API on :3000, 3D viewer on http://localhost:3007
npm --prefix mc/bot install      # first time only
npm --prefix mc/bot start        # leave running in its own terminal

# 3. Python agent
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
cp .env.example .env             # then put your ANTHROPIC_API_KEY in .env

# 4. The demo: train D and B on T1-T3, then H3 in the changed world
.venv/bin/schema-voyager demo --backend mc
```

Watch it in the browser viewer at http://localhost:3007, or join `localhost`
from a Minecraft **1.21.1** client (offline-mode server) and run
`.venv/bin/schema-voyager spectate <your-name>`. The bot narrates its plan,
mismatches and hypotheses in chat.

No server? `schema-voyager demo --backend sim` runs the same code against the
simulator (label it as such). `--llm fake` needs no API key; `--llm replay`
reuses recorded Claude responses from `runs/llm_cache.jsonl`.

## Commands

| Command | What it does |
| --- | --- |
| `schema-voyager demo [--backend mc\|sim] [--llm live\|replay\|fake] [--systems D,B]` | Full sweep: prior rules, training, H3 holdouts, results table |
| `schema-voyager episode --system D --task h3 --layout 6 --store runs/<run>/store_D_2_after_training.json` | One episode from a saved store, e.g. to record footage |
| `schema-voyager episode --system B --task h3 --layout 6 --skills runs/<run>/skills_B_after_training.json` | Same for the baseline |
| `schema-voyager arena --layout 6 --task h3` | Build a layout on the server and switch its world rules |
| `schema-voyager spectate <player>` | Spectator mode above the arena |
| `schema-voyager report runs/<run>` | Print a run's results table |
| `python scripts/eval_revision.py --n 5` | Live check that Claude ranks the stone-tier rule in its top 3 |

Each run writes to `runs/<run>/`: `episodes/*.jsonl` (every plan, step,
prediction, observation, contradiction and revision), store snapshots
(`store_D_0_prior.json`, `_1_after_first_episode`, `_2_after_training`,
`_3_after_holdout`), the baseline's skill library, and `results.md`.

## How it works

```text
            prior rules (Claude)            ranked revisions (Claude)
                    |                                 ^
                    v                                 | counterexample
 task --> planner over rules --> step --> bot --> verifier --exact match--> next step
               ^                                      |
               |                     mismatch: demote rule in this world,
          schema store <---------------------- flag skills that cite it
   rules + evidence + skills
```

- **Rules** are STRIPS-style operators (`mine coal_ore` with `tool_any`, drops
  `{coal: 1}`), each with a status: hypothesis, certified or demoted.
- **Certification** replays the logged transitions in the rule's scope: zero
  contradictions, at least 2 confirmations, from at least 2 episodes. After one
  episode nothing is certified; after training on two layouts everything the
  holdout relies on is.
- **Verification** is per step and only on effects the server decides: a mined
  block was removed, which item entities it dropped (read from entity metadata,
  separately from pickup), and a craft's inventory change (read after the
  server resyncs). Pickup and pathing are tolerant and never revise a rule.
- **World scoping**: each holdout episode gets a fresh, opaque world id. A
  contradiction there demotes the rule only in that world; holdout transitions
  never count as evidence for training rules.
- **Skills** are compiled plans tagged with the rule ids they depend on;
  demoting a rule flags them.
- **LLM use**: D calls Claude only to propose prior rules (once) and to revise
  a contradicted rule. B calls it to write every plan and replan.
- **Same hint for every system**: D's revision prompt and B's replan prompt
  both say "This world's rules may differ from vanilla Minecraft" (one shared
  constant in `llm.py`), so B is not missing a clue D gets.

Model: `claude-sonnet-5-5` for every system (override with
`SCHEMA_VOYAGER_MODEL`). Token counts come from API usage.

## Layout

```text
mc/docker-compose.yml            pinned server (itzg/minecraft-server, 1.21.1)
mc/datapacks/r2_coal_tool/       R2: coal ore loot table needs a stone-tier pickaxe
mc/bot/                          Mineflayer bot server (contract: contracts/bot_api.md)
src/schema_voyager/
  layouts.py        seeded walled-arena layouts (sim and real server)
  arena.py          RCON builder, reset, datapack switch
  mineflayer_backend.py / environment.py   real and simulated backends
  planner.py        backward chaining over usable rules
  verifier.py       exact vs tolerant step checks
  schema_store.py   rules, evidence replay, certification, demotion, skills
  agent.py          SchemaVoyager (D)
  baseline.py       skill-only Voyager-style baseline (B)
  llm.py            Claude calls, token metering, record/replay
  evaluator.py      metrics and results table
  demo.py           CLI
tests/              offline tests (pytest); live bot tests: node mc/bot/test/*.js
```
