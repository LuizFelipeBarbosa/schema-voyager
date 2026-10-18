# SchemaVoyager v2 progress

Re-read this first after a restart or context compaction. The session prompt is
`/Users/luiz/Developer/crusoe-hackathon/schema-voyager/prompts/v2-session.md`.

## Bootstrap (2026-10-17 15:16 PDT)

- Orchestrator model: Opus 5.5 (`claude-opus-5-5`).
- Demo tip: `demo/v3-lite` at b73b04c (matches the prompt; includes 4eb144e, 466df86, ff974c1, b73b04c).
  The main repo has uncommitted demo edits (README.md, demo.py, tests/test_demo.py): not ours, not in sv2.
- Worktree: `/Users/luiz/Developer/crusoe-hackathon/sv2` on `v2/open-world`, branched from b73b04c.
- Python: `sv2/.venv` (3.12), `-e '.[dev]'`; baseline suite 38 passed.
- Node: v26.5.0; `npm --prefix mc/bot ci` done in sv2 (expected `canvas` allow-scripts warning).
- Key: `ANTHROPIC_API_KEY` copied into `sv2/.env` (git-ignored by `.gitignore:6:.env`), plus the v2 settings.
- Ports 25566, 25576, 3200, 3207 (instance a) and 25567, 25577, 3210, 3217 (instance b): all free.
- Java: system 1.8; openjdk 21.0.12.1 already installed at `/opt/homebrew/opt/openjdk@21/bin/java`
  (`JAVA_BIN` in `.env`). Nothing to install.
- `screen` at /usr/bin/screen. Node has no RCON package: v2 Node tests need a small RCON client over `net`
  (the v1 test helpers use `docker exec schema-voyager-mc`, which is the demo's container: never in v2).

## Facts verified in the v1 code

- Env names the v1 code reads: `MC_HOST` (also RCON host), `RCON_PORT`, `RCON_PASSWORD`, `BOT_USERNAME`
  (arena.py); `BOT_API_URL` (mineflayer_backend.py); bot: `MC_HOST`, `MC_PORT`, `BOT_USERNAME`,
  `BOT_API_PORT`, `VIEWER_PORT`. `sv2/.env` sets all of them to the v2 values, plus `SV2_*`.
- `MineflayerBackend.__init__` builds an `Arena()` eagerly (no connection until used).
- Pathing: `canDig=false`, `allow1by1towers=false`, no scaffolding (server.js). Mining needs an air
  neighbour within 16 blocks (world.js). Explore is a 10-block spiral stopping at barriers.
- Oak hard-codes: `agent.py` MINEABLE_BLOCKS/CRAFTABLE_ITEMS, `llm.py` prompts, `data/prior_rules.json`.
- r2 datapack overrides only `blocks/coal_ore` (pack_format 48).

## Decisions

- User (15:20): cut-off 23:00 PDT. EULA accepted (`server-a/eula.txt`, `server-b/eula.txt` written).
  Protect neither M3 nor M4: "go the full way".
- User approved: additive v2 changes to /mine, /craft, /explore (v1 requests unchanged).
- User approved: copper, redstone and lapis ores (and deepslate variants) are stochastic, not exact
  (random drop counts in 1.21.1). Correction to design decision 3.
- User approved: a new epoch also opens when a contradicted hypothesis had 1+ confirmations in its
  epoch (extension of design decision 2).
- Wave 0 moved v1's MINEABLE_BLOCKS / CRAFTABLE_ITEMS from agent.py to tasks.py (so the rules lane's
  architecture test passes in its worktree). `planner.py` (smelt support) goes to the life lane.
  The bot lane also owns `mc/bot/test/v2_helpers.js` (RCON client over a socket).
- Stubs: normalize.py, verifier.py (kind_of, evaluate, whitelists), schema_store.py (Epoch,
  epoch_key, current_epoch, epoch_of, Transition.kind/seq) carry Wave 0 signatures; other lanes
  monkeypatch them in their own tests until the merge.

- 16:00: demo tip moved to 4f1c29e (adds Crusoe Managed Inference as a second LLM provider); not needed by v2.

- Interface added by the orchestrator for M3/M4 wiring: `runs/<run>/changes.jsonl` lines
  `{t, change, action, subject, goal}`; `SV2_M3_CHANGE=r2` env (or `life --change r2`) arms the trigger.

- User (18:12): approved a fourth LLM call type, `repair` (re-propose a rule whose ingredient nothing
  produces). Cause: Claude's smelt prior named `iron_ore`, so iron was unplannable, untestable, never revised.
- User (17:40): keeps instances a and b for M4 (another session's demo-rec script had used instance a;
  its `v2-change.sh install` leaked the M3 datapacks into M4's world-2024; that M4 attempt is in
  runs/invalid/). Otopo (human) set to spectator during runs, with approval.
- Demo run at the user's request (17:44, runs/demo-v2): M3 5/5 again; stopped at empty_frontier after 12 min.
  Live run with keep-going (18:03-, runs/demo-v2-live): first segment hit empty_frontier at 18:15; resumed
  with --keep-going at 138d574 (18:16); the repair call fixed smelt_iron_ingot live (iron_ore -> raw_iron).

- 18:12-18:40: lanes `keepgoing` (--keep-going explores on an empty frontier), `repair` (v2.2 repair call;
  fixed smelt_iron_ingot live), `panel` (trace panel reads v2 traces; `?run=` filter) merged.
- 18:45: brief for the next session written to prompts/v2-demo-refine.md (gitignored like the original brief).
- 18:50 hand-off: everything stopped at the user's request (both instances down); only the panel (:8766) is up.

## Commands that work

- Tests: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q` from sv2.
- Instance a (bash only; from sv2 after the infra merge, else from ../sv2-wt/infra with SV2_HOME=sv2):
  `scripts/v2-up.sh [--no-bot]`, `scripts/v2-down.sh`, `scripts/world-snapshot.sh <name>`,
  `scripts/world-restore.sh <name>` (snapshot `fresh` = seed 1001 right after generation).
  Instance b: prefix `SV2_INSTANCE=b SV2_MC_PORT=25567 SV2_RCON_PORT=25577 SV2_BOT_PORT=3210 SV2_VIEWER_PORT=3217`
  (all four ports, since .env holds instance a's).
- Screens: `sv2-a-mc`, `sv2-a-bot`; logs in sv2/logs/, pids in sv2/run/.
- M1 gate: `node mc/bot/test/v2_iron.js` from sv2 (after `scripts/world-restore.sh fresh`).
- Live life: `screen -dmS sv2-a-life bash -c 'cd sv2 && PYTHONPATH=src .venv/bin/python -m schema_voyager.demo life --backend mc --llm live --minutes 35 --run-dir runs/<run> > logs/life-<run>.log 2>&1'`

## Evidence so far

- Sim M2 (fake LLM, sim seed 1, 25 goals): 70 steps, all match, 0 contradictions; certified 15 rules incl.
  mine #log, coal_ore, craft #planks, stick, crafting_table, wooden/stone pickaxe, torch; NOT mine stone
  (planner tie-break picks mine_deepslate for #cobblestone -> integration fix). `runs/sim-m2/`.
  After integ (planner prefers visible producers): sim seeds 2 and 3 certify all 9 T1-T3 rules; seed 1
  certifies mine_deepslate in place of mine_stone (that sim world shows more deepslate than stone).
- Sim M3 preview (`runs/sim-m3-r2`, `runs/sim-m3-stick2`): change caught at the exact step, 1 revision call,
  revision certified in its epoch, next goal using it 0 LLM calls.
- Live LLM check in sim (`runs/sim-live-check`): v2 prior call OK, 15 well-formed rules, $0.024.

## Milestones

| Milestone | Status |
|---|---|
| Wave 0 (bootstrap, contracts) | done: f7e048c (Opus review: 10 fixes applied before commit) |
| Wave 1 lanes | all merged: rules, backends, life, infra, integ, bot (b23cacb, ~16:12) |
| Wave 3 offline lanes | worlds merged; change merged ca84dc0 (r2 fires on the first wooden coal mine whose rule has a confirmation) |
| M0 open world | done: seed 1001 (snowy taiga, spruce), instance a up, snapshot `fresh`, restore 7-10 s |
| M1 natural-terrain actions | GATE PASS live (logs/v2_iron-1.log, ~16:13): empty inventory -> wooden -> stone pickaxe, all exact checks MATCH; continued to iron_pickaxe (17/17 MATCH, 285 s) |
| M2 life loop | live run done (runs/m2-live-1, 16:16-16:51, 35 min unattended, stop wall_clock): 29 goals, 22 success, 14 certified (all T1-T3 + furnace, iron_ore, smelt_iron_ingot, iron_pickaxe, bucket), 69 exact MATCH, 0 contradictions, 2 quarantined (interference), $0.127. Stuck: after iron the bot was ~60 blocks underground; log goals (chest, stone_sword, stone_axe) failed 2x each on not_found -> skipped. Fixes: `surface` (explore up, merged 3695a0a) and `nav` (go up when the seen resource is far above, no E<->W reversal, cheapest-tool travel digging, wall-clock interruption not a failure; in progress). Opus review: **M2 met with caveats** (all 14 certifications legitimate; stall = navigation; iron pickaxes worn by travel digs; stochastic path never exercised). Timeline: T1-T3 certified by 6.6 min, iron pickaxe 13.4 min, bucket 28.8 min; 27,109 tokens, 12 calls (9 prior, 3 expansion). Offline rerun at 2645df7 (runs/sim-m2-seed{1,2,3}): seeds 2, 3 certify all 9 T1-T3; seed 1 certifies mine_deepslate in place of mine_stone |
| M3 mid-run rule change | not started |
| M4 generalization | **stopped 18:50 at the user's request (hand-off)**: m4-2024 complete with 2 FALSE contradictions (smelt with stick fuel gives no ingot but ok:true; craft stick x2 lost planks in the grid), m4-31337 partial; to be rerun after fixes by the next session (prompts/v2-demo-refine.md). Was relaunched 18:17 at 138d574 on instance b only, sequential (2024, 31337, 22, 555; 20 min each; ETA ~19:45), while the demo life runs on instance a. Earlier attempts invalid (runs/invalid/). First launch 17:30 at d5329e6 (a: 2024, 22; b: 31337, 555; 20 min each); seeds chosen by spawn-biome survey only (logs/survey-b.log, survey-b2.log): 2024 forest (birch+oak), 31337 grove (spruce), 22 savanna_plateau, 555 snowy_plains (hard); M3 rerun on 2024. Runs wait for M2/M3 review |
