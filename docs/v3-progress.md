# SchemaVoyager v3 progress

## Read this first

This is the shared log for the v3 sessions that build `docs/v3-plan.md`; the contracts are in `docs/v3-interfaces.md`.
Every session marks itself in "Sessions" and appends one line to "Log" per merge or live run, then a section at its end.
Re-read it before every merge: other sessions share `main` and the two live instances (a and b).

Live tests (settled in S01, user's choice): each session runs its live commands itself, in auto mode, with
the live command forms pre-approved by the orchestrator (`scripts/v2-*`, `scripts/world-*`, `node mc/bot/test/*`,
`PYTHONPATH=src .venv/bin/python -m schema_voyager.demo *`, `screen -dmS sv2-*`, `screen -S sv2-* -X quit`,
`screen -ls*`, with instance b's settings as a prefix). No Claude Code settings files are edited. `main` is not
pushed to `origin` until the user says so.

## Routing

- Sessions run on Opus 5.5 as the architect, with one Opus advisor per session (a `Plan` subagent, `model: "opus"`,
  re-used through `SendMessage`). Implementers are `general-purpose` subagents with `model: "sonnet"` (Sonnet 5.5).
- Check (S01, 2026-10-17): a Sonnet lane created `SMOKE.txt` in worktree `sv2-wt/smoke` on `v3/smoke` and its
  verification (`test "$(cat SMOKE.txt)" = ok`) exited 0, re-run by the architect. The Opus `Plan` advisor answered
  and was re-used through `SendMessage`. A live Sonnet lane ran a life on instance b (`runs/s01-explorefast`).
- An Agent call without `run_in_background` starts in the background. Headless sessions pass
  `run_in_background: false` so the lane finishes before the turn ends.
- Live runs from a worktree: the worktree has no `.env`, venv or `server-*/`. When only the bot differs, run
  `scripts/v2-up.sh --no-bot` from `sv2`, start the worktree's bot in screen `sv2-<inst>-bot` writing `sv2/run/bot-<inst>.pid`
  and `sv2/logs/bot-<inst>.log` (the `v2-up.sh` command line with `cd <worktree>/mc/bot`), then run the life from `sv2`.

## Baseline (S01)

- `main` at 819b175: pytest 341 passed, 1 failed; bot unit 103 passed; the panel test crashed. Both failures came from
  `git show v2/open-world:...`, a branch deleted after its merge.
- Fixed in cb00942 (the tests compare against `main`): **pytest 342 passed, bot unit 103 passed, panel all passed.**

## Sessions

| Session | Status | Instance | Owner notes |
|---|---|---|---|
| S01 setup and baseline | done | b | Baseline 342/103/panel; explorefast merged (bot 108); live tests via pre-approved forms; not pushed |
| S02 fix the stuck loops | in progress | b | v3.0a/b/d contract 7524cfc; tag `v2-final` (local) at 661422f |
| S03 retest demoted rules | not started | none | |
| S04 full inventory | running | a | Contract v3.0c and `/toss`; lanes toss-bot, toss-backend, junk-policy, then toss-sim, then junk-life |
| S05 contracts for M1 and M2 | done | none | Contracts v3.1/v3.2 and stubs merged 1b80640; plugin pins in v3.2g; prompt changes for S06/S07 in v3.2h |
| S06 goals you give it | not started | a | |
| S07 survival | not started | b | |
| S08 combat and hunting | not started | b | |
| S09 building foundations | not started | a | |
| S10 build goals | not started | a | |
| S11 duels | not started | b | |
| S12 home, storage, composite goals | not started | a | |

## Log

- 22:46 S01 cb00942 merge `v3/baseline-ref`: two tests read the deleted `v2/open-world`, now `main`. pytest 342, bot 103, panel pass.
- 22:50-23:11 S01 live `runs/s01-explorefast` (instance b, seed 1001 fresh, `--change scramble --keep-going --cap-usd 1`,
  10 min then 10 min `--resume`). The bot ran from the `sv2-wt/explorefast` worktree at 2cc9da6. The run's `COMMIT`
  file says cb00942, the `sv2` checkout the Python ran from, which did not have explorefast. 26 goals, 1 failed
  (bucket), 0 Tracebacks, $0.14. 14 explores, all blind: four 0.1 s no-path failures and one fail-fast at 4.0 s
  (g016). Inconclusive as a live test of the fix: there was no directional explore, so the hop, the neighbour
  directions and the hole exit didn't run. No regression: a blind explore that stops moving now ends after about
  4 s, where the old code waited 30 s (preview-2005 g021 step 8).
- 23:15 S01 81a9fc1 merge `v3/explorefast` (rebased c25e121), on the offline tests: pytest 342, bot 108 (5 new),
  panel pass. The live run shows no regression. The directional fallbacks are not tested live.
- 23:23 S02 7524cfc contract v3.0a/b/d (blocked rules, repairs, kept items, explore `took`); local tag `v2-final` at 661422f.
- 23:26 S02 74732e8 merge `v3/keep-items`: `next_goal` skips a held station or tool; the v1-constants test reads `v2-final`. A 15-goal fake-LLM sim life crafted 2 tables before, 1 after. pytest 360, bot 108, panel pass.
- 23:28 S02 7e1b7b2 merge `v3/repair-dedupe`: a repair equal to a known rule or still consuming a dead item is skipped (`skipped` in the record), repairs go into `repaired`. Two `test_v2_repair.py` assertions updated for that. pytest 364, bot 108, panel pass.
- 23:29 S02 ae754d2 merge `v3/retry-block`: the second identical failure blocks the rule for the goal (`rule_blocked`), `run_schemavoyager(excluded=...)`. pytest 369, bot 108, panel pass.
- 23:33 S02 7c28fa9 merge `v3/blocked-memory`: a rule blocked with `missing_ingredients` in 2 goals is excluded from planning and `next_goal` for the rest of the life (`"blocked"` in `life_state.json`). The `test_v2_nav.py` stub takes `excluded`. pytest 374, bot 108, panel pass.
- 23:35 S02 31218b2 merge `v3/explore-took`: a directional `/explore` answers `took`, the attempt that moved; `_remember_explore` kills a requested direction a neighbour covered. pytest 380, bot 110, panel pass.
- 23:30 S04 7ebbd68 contract v3.0c (inventory space) and `bot_api.md` v3.0 `/toss`/`free_slots`; stubs `Observation.free_slots`, `LifeBackend.toss`.
- 23:37 S04 959c7b1 merge `v3/junk-policy`: `inventory.py` (`stack_size`, `slots_used`, `junk_to_toss`: all junk once triggered, a 64-block scaffold reserve, rule items keep one stack). pytest 422, bot 110, panel pass.
- 23:41 S04 c2479be merge `v3/toss-backend`: `MineflayerBackend.toss`, `free_slots` parsed into `Observation`. pytest 429.
- 23:41 S04 1569837 merge `v3/toss-bot`: `POST /toss` (smallest stacks first, never the held tool, walk 4.5 blocks away, not forward), `free_slots` on `/observe`. pytest 429, bot 120, panel pass.
- 23:42 S04 a967275 merge `v3/toss-sim`: `NaturalWorld` 36 slots, gains stop at what fits, a craft that can't fit fails `craft_failed`; `SCAFFOLD_BLOCKS` moved to `inventory.py` (re-exported by `mineflayer_backend`). pytest 442, bot 120, panel pass.
- 23:45 S04 515456b merge `v3/junk-life`: `_run_goal` drops junk first when `free_slots < 4` (toss, observe again; `tossed` event after `goal`, `tossed` in the goal record, "Dropping ... to make room."). pytest 445, bot 120, panel pass.
- 23:54 S05 1b80640 merge `v3/s05-contracts`: contracts v3.1 (goals) and v3.2 (survival) in both documents; stubs `GoalSpec`, `Unsupported`, `Observation.entities`, `goals.py`, `interrupted` in `RETRIED_ERRORS` and `_NEVER_BLOCKED_ERRORS`. pytest 445, bot 120, panel pass.
- 23:49 S04 live `node mc/bot/test/v3_toss_live.js` (instance a, `fresh` snapshot; bot and Python from `sv2` at f820661): ALL PASS.
  30 stacks of dirt: `free_slots` 6. `/toss {dirt: 100, poppy: 3}`: `tossed {dirt: 100}`, `free_slots` 7, `walked_away`,
  5 blocks, no events, nothing picked up again after 3 s. Left the bot 2112 dirt + 5 poppy (`free_slots` 2). Held-tool
  check skipped (the API can't select a slot; unit-tested).
- 23:49-23:59 S04 live `runs/s04-junk` (instance a, 10 min, `--cap-usd 1`, $0.07, same commit): 15 goals, 1 failed
  (iron_ingot, `unreachable`), 0 Tracebacks. g001 starts with `tossed {poppy: 5, dirt: 2048}`, free slots 2 to 35, walk
  5.7 blocks, `travel.picked_up {}`; in-game "Dropping 5 poppy and 2048 dirt to make room." No tossed stack came back
  (largest later dirt pickup 7). 5 step-phase `stray_pickup` (1 item each), all on mine steps; the dirt ones are the
  travel's own digs (g007: dug 6 dirt-dropping blocks, picked up 5 in travel + 1 in the step). Quarantines: g007 #1
  `interference, stray_pickup` (a mine: interference quarantines it), g015 #3 `damage`. No craft or smelt quarantined.
- 23:50 S04 a225fbc merge `v3/toss-live`: `mc/bot/test/v3_toss_live.js`. pytest 445, bot 120.

## S01 setup and baseline (done 2026-10-17)

- Commits on `main`: 6e28c6a plan; 819b175 progress and interfaces skeleton; cb00942 merge `v3/baseline-ref`;
  81a9fc1 merge `v3/explorefast`. Suite on 81a9fc1: pytest 342, bot unit 108, panel all passed.
- Instance b is down, with no lock. Instance a was not touched. Leftovers: the branch `v2/explorefast` (c25e121, superseded by
  81a9fc1) is kept because S01 didn't create it. No `sv2-wt/*` worktrees are left. `origin/main` is 115 commits behind (at b6503c6 plus this fix)
  and not pushed (user: not yet).
- Open, for the first S02/S04 life that has 3 or more directional explores that don't move on the first try: no
  zero-move explore over 25 s; at least 20% of directional explores move 2 blocks or more; no goal asks for the same
  direction 4 or more times (`/tmp/sv2-s01-explore.py <run-dir>` measures these; it is not in the repo). Any explore
  over 35 s means the hole exit (`getOutOfEnclosure` calling `exploreUp`, up to 100 s) needs a look.
  How to measure, from `<run-dir>/episodes/*.jsonl` in goal order:
  - an explore is a `kind: "step"` record whose `action` starts with "explore" (directional ones read "explore NW");
  - skip `explore up` steps with `detail.sky: true`;
  - duration is the step's `t` minus the `t` of the previous record that isn't `travel`;
  - moved is the step's `travel.distance` (else the change in `detail.position` from the previous step);
  - the fair baseline is preview-2005 from g022 (after the unstick merge).
- Open, separate from explore: in `runs/s01-explorefast` g025 (bucket), all 8 failures were `mine iron_ore`
  returning `unreachable` after walking 35 to 95 blocks, a `/mine` reach problem like preview-2005 g027.

## S05 contracts for M1 and M2 (done 2026-10-17)

- Contracts: `docs/v3-interfaces.md` v3.1 (goals) and v3.2 (survival); `contracts/bot_api.md` v3.1 (chat commands)
  and v3.2 (entities, guard, `/guard`, `interrupted`, events, armor and food). Stubs on `main`: `GoalSpec`,
  `Unsupported`, the `GoalKind`/`GoalSource`/`GoalStatus` literals and `Observation.entities` in `models.py`;
  `goals.py` (`GoalQueue` raising `NotImplementedError`, `GoalEntry`, `GoalInterrupted`); `interrupted` in
  `RETRIED_ERRORS` (`episode.py`) and in `_NEVER_BLOCKED_ERRORS` (`agent.py`, so fights never block a rule).
- The user's answers (through the orchestrator): `BOT_OWNERS` is `Otopo` (live tests add `Tester`); D1–D6 approved
  as written, so D2 (panel first, then chat) and D5 ("get me 20" means 20 more) stand; the play-mode idle question
  wasn't asked separately, so the default holds: stand still, and the guard defends from M2 on.
- Plugin versions (v3.2g, checked against mineflayer 4.39.0 and pathfinder 2.4.5, Node 26.5.0): `mineflayer-pvp`
  1.3.2, `mineflayer-armor-manager` 2.0.1, `mineflayer-auto-eat` 3.3.6 (the last CommonJS release; 4.x and later are
  ESM-only). pvp must be given the bot's `worldPreservingMovements` on every spawn, or a fight paths with digging on.
- Found while writing them, and settled in the contracts: `keepInventory` is on in every world
  (`sv2_apply_gamerules`), so a death loses no items and a re-queued goal keeps its `target_count`; daylight is off
  on every up today, so `SV2_DAYLIGHT` defaults to `off`; an aborted handler (timeout or death, and now a guard
  abort) kept running after `busy` was released, so `busy` now waits for the handler (409 `detail: "orphan"`).
- The advisor reviewed the contracts three times. Fixed from its reviews: the orphan hole, an eat already in
  progress at lock time, which endpoints the guard cuts into, furnace leftovers, the 409 backoff, the HTTP timeout,
  `GoalInterrupted` skipping `finish`/`certify`, `LLMError` around `parse_goal`, and `life_events.jsonl` on resume.
- For the orchestrator, prompt changes the contracts need (v3.2h): S06 `chat-bot` gains `scripts/v2-up.sh` (the bot
  line's `BOT_OWNERS`); S07 `guard-wire` gains `mc/bot/lib/smelt.js`; S07 `python-survival` gains `episode.py`
  (retry `interrupted` in place) and `REQUEST_TIMEOUT_S = 180`; S07 `survival-plugins` follows the lock rule instead
  of "window or dig"; S07 `difficulty-env` works in `sv2_apply_gamerules` only, `SV2_DAYLIGHT` default `off`; S07
  `death-handling` cancels a system goal on a death, keeps a user goal's `target_count`, and adds edible foods to
  the junk toss's `wanted`; S07 `guard-wire` adds a 45 s reconnect backstop for a handler that never settles.
