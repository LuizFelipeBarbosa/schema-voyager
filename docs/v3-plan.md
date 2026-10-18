# SchemaVoyager v3 plan: goals you give it, survival and combat, building

v2 is a bot that picks its own goals to learn how the world works. v3 keeps
that and adds a bot you can direct: type a goal ("get me 20 torches", "build a
small house", "guard me"), and it plans, gathers, fights off mobs and builds.

How this plan is kept:

- `docs/v3-plan.md` (this file): the map. Milestones, steps, order.
- `docs/v3-interfaces.md`: the contract for each milestone, written before its
  code (as `docs/v2-interfaces.md` was). New bot endpoints go in
  `contracts/bot_api.md` as v3.x sections.
- `docs/v3-progress.md`: the log. What merged, what ran live, what broke.
- The work is split into 12 Claude Code sessions (`prompts/v3/README.md`, which
  is git-ignored). Each session sends its steps to subagent lanes on `v3/<lane>`
  branches and merges them into `main` once the offline tests pass. Live checks
  run at the end of each session, on the instance its prompt names.

## Decisions (defaults: change any of them before the step that needs it)

| # | Decision | Default |
|---|---|---|
| D1 | Do combat and building go through learned rules? | No. Rules stay how the bot gets resources. Combat and building are built-in behaviors. A placed block is checked against the blueprint but never counts as evidence for a rule. Mob drops (M3) do become rules, because they are just like block drops. |
| D2 | Where you type goals | Web panel first, in-game chat second. Both feed the same queue. |
| D3 | What "PvP" means | Mobs first (survival needs it anyway), then bot-vs-bot and player duels in M3. |
| D4 | What Claude does with your text | Maps it onto a fixed set of goal types (JSON schema). It never writes bot code. |
| D5 | "get me 20 torches" when holding 8 | 20 more: the goal is to hold 28. |
| D6 | Offline first | Every step works in the simulator with `--llm fake` before it goes live, as in v2. |

## Does the structure change?

No rewrite. Four seams get wider, nothing else moves:

1. **A goal is only an item and a count** (`Task.goal_item`, `curriculum.Goal`,
   `Episode.goal_met`). It becomes a `GoalSpec` with a kind (obtain, build, kill,
   guard), and each kind has its own success check.
2. **Goals only come from the curriculum** (`_Life.run` in `life.py`). It
   becomes a queue: your goals first, then the curriculum when in learn mode.
3. **The bot runs one action at a time behind the `busy` lock** (`server.js`).
   Combat has to cut in, so a reflex layer in the bot can abort an action, the
   same way a death already does (`rejectOnDeath` in `lib/events.js`).
4. **Four actions** (`ActionKind` in `models.py`: mine, craft, smelt, explore).
   Add `place`, `clear` and `kill`.

## Order

```text
M0 foundations ──┬── M1 goals you give it ──┬── M4 building ── M5 home and composite goals
                 └── M2 survival ── M3 combat and hunting ──┘
```

M1 (Python and panel) and M2 (mostly the JS bot) can run in parallel. M4
needs M1's goal types and should come after M2, or a night-time build gets
eaten by zombies. Sizes: S is a small change with tests, M is a few files, L is
a new subsystem.

---

## M0: foundations

Fix what the hackathon runs showed, and add a mode where the bot stops
teaching itself.

### S0.1 Baseline and housekeeping (S)

- Run both suites on `main` and note the counts in `docs/v3-progress.md`:
  `.venv/bin/pytest` and `node --test mc/bot/test/unit/`.
- Decide on the unmerged `v2/explorefast` (c25e121, `/explore` fails fast):
  test it live on instance b, then merge it or drop it.
- Push `main` to `origin` (`origin/main` is 106 commits behind) and remove the leftover
  `sv2-wt/` worktrees.
- Settle how live tests get run (during the hackathon the auto-mode check
  blocked them): you run them, or allow-list `scripts/v2-*.sh` and
  `node mc/bot/test/*.js`.

### S0.2 Never retry an identical failed step (S)

The problem: in `runs/m4-2024` the bot tried `craft bucket` 28–30 times per goal.
A `failed` step breaks the step loop in `run_schemavoyager` (`agent.py`),
which plans again, gets the same plan, and fails the same way.

- In `run_schemavoyager`, remember `(rule id, error, inventory)` for each failed
  step. A second identical failure blocks that rule for the rest of the goal:
  plan with `[r for r in store.usable_rules(...) if r.id not in blocked]`.
  If nothing can be planned, the goal fails now.
- Log a `rule_blocked` event (rule, error, the bot's `missing` detail).
- Keep the blocked rules in `life_state.json`. A rule blocked in two goals is
  left out of planning for the rest of the life.
- Tests (`tests/test_v2_life.py`): with a fake rule `craft bucket` that consumes
  `raw_iron`, the goal ends in 3 actions or fewer, not 30.

### S0.3 Stop the repair loop (S)

The problem: `_repair_dead_ingredients` (`life.py`) asked Claude for an
alternative to `craft_iron_sword` 7 times. Claude kept answering with the same
recipe, which still needed the ingredient nobody could produce, so each answer
was saved under a new id and repaired again. In the end Claude made up
recipes using `raw_iron` and then `raw_copper`.

- Add every proposed repair id to `self.repaired` as soon as it is added, so a
  repair is never repaired.
- Skip a proposal whose kind, subject, preconditions and effects match a rule
  the store already has.
- Skip a proposal that still consumes one of the items nothing produces.
- Tests (`tests/test_v2_repair.py`): a fake LLM that returns the same rule
  leads to one call and no new rule.

### S0.4 Let a demoted rule be tested again (M, design first)

The problem: `smelt_iron_ingot` was demoted by a false contradiction (since
fixed), and a demotion lasts for the whole world, so iron was lost for the
rest of the life.

- Design note in `docs/v3-interfaces.md` first, because demotion opens an
  epoch (`schema_store.py`) and the hidden-change detection depends on it.
- Proposal: when an item has no producer left and a demoted rule produces it,
  run that rule once as an experiment (once per rule per life). If it matches,
  open a new epoch with the rule as a hypothesis again.
- Tests: a false demotion followed by a goal that needs the item. The rule
  comes back after one matching step.

### S0.5 Learn mode and play mode (S)

The problem: the curriculum makes a crafting table twice and a furnace three times
(`runs/preview-2005/life.jsonl`, goals 2/4 and 15/18/20). It does this on
purpose: a rule is certified only after 2 confirmations from 2 goals, and every
goal is "hold one more".

- Play mode (`LifeConfig.mode: "learn" | "play"`, CLI `--mode`), in which the
  curriculum never picks goals, lands in M1 together with the goal queue
  (session S06). Without a queue, play mode has nothing to do.
- In learn mode, `Curriculum.next_goal` skips items to keep, such as stations
  (crafting_table, furnace, chest) and tools, once one is held. Their rules
  stay hypotheses longer. That's fine: the hidden changes target planks,
  sticks and coal.
- Tests: a 15-goal simulated life crafts at most one crafting table and one
  furnace.

### S0.6 A full inventory (M)

Longer lives and building will fill the 36 slots, and today nothing handles it
(`lib/world.js` just leaves the item in the bot's hand).

- `/observe` reports `free_slots`.
- New bot action `POST /toss {"items": {"dirt": 40}}`, which reports what left
  the inventory.
- Python side: when `free_slots < 4` between steps, toss junk. Junk is anything
  that no usable rule consumes, no queued goal needs, and isn't a tool, minus a
  reserve of 64 scaffold blocks (`SCAFFOLD_BLOCKS`).
- Simulator: `NaturalWorld` gets a 36-slot limit so the tests exercise this.

### S0.7 Live baseline (S)

A 30-minute life on instance b on the new `main`. Done when: no goal has more
than 3 identical failed steps, there are 0 false contradictions, and at most
one crafting table is crafted. Save it as `runs/v3-baseline`.

### S0.8 Reaching ore (S, added by the orchestrator after S01/S02; session S13)

`mine iron_ore` failed `unreachable` in every life: walks cut off while still moving (three 11 s candidates per
call), cut-off blocks remembered as unreachable, deep ore reached only by staircase, and underground retries that
explore blind and never go up. Contract v3.0g. Done when: in two lives no goal has more than 2 `unreachable` ore
mines in a row, iron goals succeed in at least one, and `node mc/bot/test/v2_iron.js` passes.

### S0.9 Polish (S, added by the orchestrator after S06, S07 and S10; session S14)

Make what's built robust enough to leave running. Musts: a lost bot (a restart, a reset connection, a long `409`)
interrupts the goal and requeues it instead of ending the life, and the life waits up to 120 s for the bot; the panel
sends goals to the life that is running (`runs/<life>/live.json`), not to the newest `goals.jsonl`. Then the review
backlog: no frontier in play mode, "Cancelled" after a late cancel, `!status` reports the queue, `INTERRUPTED` in the
terminal trace, build `Movements` follow `sprintAllowed`, quiet auto-eat, a flat arena for the guard live test,
scaffold cleared beside a finished house, door planks of one species; and the README. Contract v3.0h. Done when:
a play-mode life survives its bot being stopped and restarted mid-goal (the goal `interrupted` with `bot`, requeued,
then `done`), and a built house leaves no scaffold outside its box (`runs/v3-polish-live`).

---

## M1: goals you give it

Done when: typing "get me 10 torches" in the panel, or `!goal 10 torches` in
chat, makes the bot do it while the panel shows the status. "Build a castle"
gets a polite reply that it can't build yet.

### S1.1 Contract (S)

In `docs/v3-interfaces.md`:

```python
GoalKind = Literal["obtain", "build", "kill", "guard"]

@dataclass(frozen=True)
class GoalSpec:
    id: str                   # "u003" (user) or "g012" (curriculum)
    kind: GoalKind
    source: Literal["user", "curriculum", "system"]   # system: hunger, threats
    text: str = ""            # what the user typed
    item: str | None = None   # obtain: normalized name ("#planks", "torch")
    count: int = 1            # obtain: how many more; kill: how many
    target: str | None = None # kill: mob type; guard: player name
    blueprint: dict | None = None  # build (M4)
```

Goal queue: `runs/<life>/goals.jsonl`, append-only. Each line is a submission
or a status change: `{"id", "t", "status": "submitted|queued|running|done|failed|cancelled|unsupported", ...}`.
A file, because the panel, the life and the chat bridge are separate processes,
and v2 already uses jsonl files to connect them. It also survives `--resume`.

### S1.2 `goals.py`: the queue (S)

- `GoalQueue(path)` with `submit(text, source)`, `pending()`, `next()`,
  `mark(id, status, **fields)`, `cancel(id)`. State is rebuilt by reading the
  file (latest line per id wins).
- Tests: submit, run, mark done and resume all round-trip. A corrupt last line
  (a crash mid-write) is skipped.

### S1.3 The life loop takes goals from the queue (M)

- In `_Life.run`: (1) parse any `submitted` goals (S1.4); (2) run the oldest
  `queued` user goal; (3) otherwise, in learn mode, the curriculum as now; in play
  mode, idle (poll the queue every 2 s, and guard from M2 on).
- `_run_goal` takes a `GoalSpec`. For `obtain` it builds the same `Task` as now,
  with `goal_count = held + count`.
- Items you ask for join `_unasked_families` (next to the curriculum frontier),
  so the bot asks Claude for prior rules about items it has never seen.
- Cancel and urgent goals: the existing `before_step` hook checks the queue and
  raises `GoalInterrupted`. The goal ends `interrupted`, which the life already
  records without counting it as a failure.
- A user goal that fails is not skipped for the rest of the life the way a
  curriculum goal is. It is marked `failed` with the reason.
- Tests: in the simulator, a queued `obtain torch 4` runs before the curriculum.
  A cancel mid-goal stops it at the next step.

### S1.4 Claude parses the goal (M)

- `LLM.parse_goal(text, inventory, known_items) -> GoalSpec | Unsupported`. A JSON
  schema with `kind` as an enum, the item as a vanilla id, and
  `{"kind": "unsupported", "reply": "..."}` for anything else. It goes through
  `_json_call`, so replay and the spend cap work as for every other call.
- `src/schema_voyager/data/items_1_21_1.json`: every vanilla item id, exported
  once from minecraft-data by a script, so a misspelled or invented item is
  rejected before planning.
- Fake mode: a regex (`(\d+)?\s*(\w+)`) for offline tests.
- Kinds the running code can't do yet (build before M4, kill before M3) are
  marked `unsupported` with a reply, never queued.

### S1.5 A prompt box in the panel (M)

- `panel.py`: add `do_POST` for `/goals` (appends `submitted` to the live run's
  `goals.jsonl`) and `GET /goals.json` (the queue with statuses). The panel
  never calls Claude, so it needs no API key. The life parses.
- The panel only binds 127.0.0.1. Keep it that way, since anyone who can reach it
  can steer the bot.
- `site/dist/trace.html` and `trace.js`: an input and a Send button above the life
  view, and a list of goals with status chips (queued, running, done, failed),
  plus a Cancel button on queued and running goals.
- Tests: `tests/test_panel.py` for the POST route. `trace.test.mjs` for
  rendering the list.

### S1.6 In-game chat commands (S)

- Bot: `bot.on('chat', (username, message))`. Messages starting with `!` from a
  player in `BOT_OWNERS` (env, comma-separated) become a `chat_command` event
  in the event log. Python already reads that log through `/status?since=`.
- The life polls `/status` between steps and while idle, and turns
  `!goal <text>`, `!stop`, `!cancel` and `!status` into queue operations.
- The bot replies in chat through the existing `/chat`, which keeps its anti-spam
  limit.

### S1.7 Narration and trace (S)

- The `goal` event gains `source` and `text`. The panel's goal card shows
  "Your goal: get me 10 torches" for user goals.
- Chat: "On it: 10 torches (7 steps)", then "Done: 10 torches" or "Couldn't get
  torches: <reason>".

---

## M2: survival

Done when: a 30-minute life on easy difficulty with the day/night cycle on has
no death spirals and no false contradictions from fights, and at least half its
goals succeed.

### S2.1 Difficulty from the environment (S)

- `scripts/v2-env.sh` writes `difficulty=${SV2_DIFFICULTY:-peaceful}`. The
  learning demo stays on peaceful, and play runs use easy.
- Optional `SV2_DAYLIGHT=off`, which sets `gamerule doDaylightCycle false` over
  RCON: always day, so only mobs in caves. A middle step while combat matures.

### S2.2 The bot sees creatures (S)

- `/observe` and `/status` add `nearby_entities`: type to `{count, nearest}` within
  16 blocks, from `bot.entities`. The kind comes from minecraft-data (`hostile`,
  `animal`, `player`, ...).
- `Observation.entities` in `models.py`, so the planner and the trace can see them.

### S2.3 Reflex layer: `lib/guard.js` (L)

The fast loop. Python over HTTP is too slow for a fight.

- Dependencies (exact versions, after checking they work with mineflayer
  4.39): `mineflayer-pvp`, `mineflayer-armor-manager`, `mineflayer-auto-eat`.
- What counts as a threat (checked every 4 ticks): a `hostile` entity within 8
  blocks that can see the bot, or whatever damaged the bot in the last 3 s.
- Modes: `off`, `passive` (run away), `defend` (fight back, the default). In
  every mode: back away from a creeper closer than 5 blocks, and run and eat
  below 6 health.
- Cutting in. Guard aborts the running action the same way a death does:
  a `rejectOnThreat()` promise next to `rejectOnDeath()` in `lib/events.js`. Then
  the `action()` wrapper in `server.js` stops the bot, awaits `guard.fight()`
  (its own 30 s limit), and answers
  `{"ok": false, "error": "interrupted", "detail": {"threat": "zombie", "outcome": "killed"}}`.
- While idle (no action running), guard fights directly and holds the `busy`
  lock, so Python gets `409 busy` for that time.
- Events: `threat`, `fight_start`, `fight_end` (`target`, `outcome`,
  `damage_taken`), `fled`, `ate`.
- Later, maybe: pause and resume the action's pathfinder goal instead of aborting.
  Not needed at first.
- Tests: unit tests with a fake bot (threat picking, the creeper rule, the abort
  path). Live: `mc/bot/test/v3_guard_live.js` summons a zombie next to the bot
  over RCON during a `/mine`.

### S2.4 Python handles fights and deaths (M)

- `interrupted` joins `RETRIED_ERRORS` (`episode.py`), so the step is retried
  after the fight. The verifier already quarantines a step with damage or a
  death in its window, so a fight is never evidence.
- `MineflayerBackend._post`: on `409 busy`, wait and retry for up to 60 s.
- Death: the goal ends `interrupted`, not `failed`. The life logs what was
  lost (`inventory_lost`) and replans from the empty inventory where the bot
  respawns. Three deaths in 10 minutes stop the life with `stop_reason="deaths"`.

### S2.5 Armor and a shield (M)

- Add `leather_*`, `iron_helmet`, `iron_chestplate`, `iron_leggings`,
  `iron_boots` and `shield` to `WHITELIST` (`curriculum.py`) and to the prior-rule
  vocabulary.
- `mineflayer-armor-manager` puts armor on when it's picked up. Guard holds the
  shield in the offhand.

### S2.6 Eating (S)

- `mineflayer-auto-eat` eats held food when food is 14 or less, but never
  during a craft or smelt, when a window is open.
- `/status` already reports food. When food drops below 8 and the bot holds no
  food, the life queues a `system` goal for food (filled in by M3's hunting).

### S2.7 Threats in the simulator (M)

- `NaturalWorld` already rolls seeded damage (`TRAVEL_DAMAGE_CHANCE`). Add seeded
  threats that end an action with `interrupted` and a `fight_end` event, plus a
  food level that drops over time.
- Tests: a simulated life on "easy" finishes its goals, with 0 rules certified or
  demoted by interrupted steps.

### S2.8 Live soak on easy (S)

Two 30-minute lives, day/night cycle on. Log deaths, fights and interrupted
steps in `docs/v3-progress.md`.

---

## M3: combat and hunting

Done when: the bot feeds itself by hunting, `kill 3 zombies` and `guard me`
work as goals, and two bots can duel.

### S3.1 Bot action `/attack` (M)

- `POST /attack {"mob": "cow", "weapon": "stone_sword", "max_distance": 32}`.
  Find the nearest one, walk to it, fight with `mineflayer-pvp` until it dies,
  and read its drops with the existing `watchDrops` and `pickUpEntities`.
- The response has the same shape as `/mine`: `killed`, `drops`, `picked_up`,
  `travel`, `events`. Errors: `not_found`, `escaped`, `unreachable`, `timeout`.
- `POST /look` accepts mobs as well as blocks, so `_explore_toward` (`episode.py`)
  can search for animals the way it searches for ore.

### S3.2 Kill rules (L)

- `Action("kill", target="cow", tool="stone_sword")` and a rule kind `kill`, with
  the subject a mob and drops like a mine rule, for example `{"beef": [1, 3]}`.
- Drops are random, so these rules use the existing stochastic check (the
  ranges in `_allowed` in `verifier.py`).
- The planner treats `kill` like `mine` (`_Planner._mine`, generalized). A weapon
  is optional, like a tool.
- Prior rules: mobs the bot sees join `_unasked_families`.
- The simulator spawns seeded animals.
- Bonus, in the spirit of v2: a datapack that changes a mob's loot table is a
  new hidden change the bot should notice (`CHANGE_STEPS` in `life.py`).

### S3.3 Food from hunting (M)

- Add `cooked_beef`, `cooked_porkchop`, `cooked_chicken` and `bread` to
  `WHITELIST`. Smelt rules for meat come from the priors the same way iron does.
- S2.6's hunger goal becomes `obtain cooked_beef 4`, planned as kill cow, then
  smelt beef.

### S3.4 Goals: kill and guard (M)

- `kill <mob> <n>`: repeat `/attack` until `n` kills are counted in `fight_end`
  events.
- `guard <player>`: the bot follows the player (`GoalFollow`) with guard set to
  `defend` for them as well. It runs until `!stop` or a time limit
  (`goal_wall_clock_s`). Success: the player stayed alive.
- `parse_goal` (S1.4) stops marking these kinds `unsupported`.

### S3.5 Duels (L, open-ended)

- Two bots on one server: a second bot process with its own `BOT_USERNAME`,
  `BOT_API_PORT` and the same `MC_PORT`.
- `POST /duel {"player": "VoyagerBot2"}`. Tactics to add one at a time:
  attack-cooldown timing, critical hits (strike while falling), sprint reset,
  strafing, raising the shield when the other bot winds up.
- `scripts/v3-duel.sh` runs N duels between two tactic sets and prints a win
  rate. This is the open-ended part. Measure each tactic before keeping it.

---

## M4: building

Done when: "build a small wooden house with a door" shows a preview in the panel.
After you approve it, the bot gathers the materials and builds it, and a final
scan matches the blueprint cell for cell.

### S4.1 Blueprints (M)

The contract in `docs/v3-interfaces.md`, with layers bottom-up, rows north to
south, characters west to east:

```json
{
  "name": "small house",
  "size": [7, 5, 7],
  "palette": {"L": "#log", "P": "#planks", "G": "glass", "D": "oak_door", "T": "torch", ".": "air", " ": "any"},
  "layers": [
    ["LPPPPPL", "P.....P", "P.....P", "P.....P", "P.....P", "P.....P", "LPPDPPL"],
    ["..."]
  ]
}
```

- `blueprint.py`: `validate(bp)` checks the size (at most 16 x 12 x 16), rows of
  equal length, and that palette entries are placeable blocks. `materials(bp)`
  gives the bill of materials, with a door counted once even though it is two
  blocks tall. `render(bp)` gives the layer slices for the panel.
- First version palette: full blocks, glass and panes, doors, torches. Stairs,
  slabs and anything with a facing come later.
- Tests: validation errors, materials for the example, door handling.

### S4.2 Bot actions `/place` and `/clear` (M)

- `POST /place {"block": "oak_planks", "x": 10, "y": 64, "z": -3}`. Walk within
  reach (pathing may pillar with `SCAFFOLD_BLOCKS`), find a face to place
  against, equip, place, and read the cell back. Response: `placed`, `found`,
  `travel`, `events`. Errors: `no_support`, `occupied`, `missing_item`,
  `unreachable`.
- `POST /clear {"x", "y", "z"}`: dig one cell with the best tool, for site prep.
- `POST /scan {"origin": [x, y, z], "size": [w, h, d]}`: block names in a box,
  for checking the build and resuming it.
- Unit tests with a fake world. Live: place a 3 x 3 floor and read it back.

### S4.3 Build order: `builder.py` (L)

- Input: the blueprint, an origin and a scan of the site. Output: ordered steps.
- First clear the cells that must be air, top-down. Then place bottom-up. Within
  a layer, a block goes down only once something next to it is already there
  (BFS from the ground and what's already placed).
- Cells that already match are skipped, so resuming after an interruption or a
  death just continues.
- Doors and torches go last, since they need a block to stand on or against.
- Tests: the example house orders correctly, an overhang is placed after its
  support, and a half-built site resumes where it stopped.

### S4.4 Where to build (M)

- `POST /survey {"size": [7, 7], "radius": 24}`: candidate origins near the bot,
  ranked by how many blocks must be cut or filled, with water, lava and trees
  ruled out.
- The life picks the best one, saves it as a `place` (M5), and clears or fills it
  as part of the build steps.

### S4.5 Materials through the planner (S)

- For each material: `make_plan(item, held + needed)`, the existing planner,
  which is exactly what it's good at.
- New items (glass means sand, smelted) join `_unasked_families`, so prior
  rules for them come the usual way.
- With more than one wood type, the bill uses `#planks` and `#log`, and the bot
  places whichever species it holds.

### S4.6 Running the build (M)

- `Action("place", ...)` and `Action("clear", ...)` in `models.py`. Each step's
  check is exact (is the block there afterwards?), but these steps are never
  recorded as rule evidence (D1).
- The goal succeeds when a final `/scan` matches the blueprint everywhere it
  isn't `any`.
- Progress events for the panel: `build_progress {placed, total}`.

### S4.7 Claude designs the blueprint, and you approve it (M)

- `LLM.design_blueprint(text, limits) -> blueprint`, with the JSON schema above.
  If `validate` fails, the errors go back to Claude, up to 2 retries.
- The panel shows the layer slices and the materials list, with Approve and
  Discard buttons. Chat gets `!yes` and `!no`. The goal stays
  `awaiting_approval` until then.
- Fake mode: a fixed 5 x 4 x 5 hut, for offline tests.

### S4.8 Simulator (M)

`NaturalWorld` supports place, clear, scan and survey, so the whole build path
runs offline.

### S4.9 Live (S)

Small wooden house end to end on instance b, recorded for the README.

---

## M5: a home, storage and composite goals

### S5.1 Places (M)

`life_state.json` gains `places`: home (the build origin), furnaces and chests
the bot placed, with positions. The planner prefers a known furnace within
range over crafting a new one.

### S5.2 Chest storage (M)

`POST /deposit` and `POST /withdraw` at a known chest. This replaces S0.6's tossing
once there is a home: junk and spare materials go into the home chest, and the
planner counts chest contents as items it can fetch, with a walk.

### S5.3 Composite goals (M)

`parse_goal` may return a list. For example, "get ready for night" becomes
shelter (build), 8 torches (obtain), 4 cooked beef (obtain) and an iron sword
(obtain). They are queued in order, each one checked on its own. If one fails,
Claude is asked once to replace the rest of the list.

---

## Where to start

1. S0.2, S0.3, S0.5: small, and they fix the two bugs you've seen.
2. S0.1 and S0.7: a clean baseline to compare against.
3. S1.1 (the contract), then M1 and M2 in parallel lanes.

## Open questions

- Live tests: who runs them, and should they be allow-listed (S0.1)?
- Push to `origin` now (S0.1)?
- Any change to the defaults D1–D6?
