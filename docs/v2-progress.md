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

## Commands that work

- Tests: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q` from sv2.

## Milestones

| Milestone | Status |
|---|---|
| Wave 0 (bootstrap, contracts) | done: f7e048c (Opus review: 10 fixes applied before commit) |
| Wave 1 lanes | launched ~16:10 PDT. rules merged 6d9dfce (16:20). Pending: infra (live), bot, backends, life |
| M0 open world | not started |
| M1 natural-terrain actions | not started |
| M2 life loop | not started |
| M3 mid-run rule change | not started |
| M4 generalization | not started |
