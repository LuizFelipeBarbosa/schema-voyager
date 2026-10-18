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
| S02 fix the stuck loops | done | b | v3.0a/b/d/e; 7 lanes merged (pytest 455, bot 126); `runs/v3-baseline` 3 of 4 checks, (a) fails only on exempt `unreachable`; not pushed |
| S03 retest demoted rules | done | none | v3.0f; 3 lanes merged (pytest 642, bot 191 on e76ea39); a demoted rule gets one retest as `<id>.retest@<world>`; offline only; not pushed |
| S04 full inventory | done | a | `/toss`, `free_slots`, `inventory.py`, 36-slot sim, junk tossed before a goal; live `runs/s04-junk`; pytest 445, bot 120; not pushed |
| S05 contracts for M1 and M2 | done | none | Contracts v3.1/v3.2 and stubs merged 1b80640; plugin pins in v3.2g; prompt changes for S06/S07 in v3.2h |
| S06 goals you give it | done | a | M1: 9 lanes merged (pytest 715, bot 212, panel and goals pass); live `runs/v3-m1-live` 4 of 4 checks plus the panel box; not pushed |
| S07 survival | done | b | 24 lanes merged (pytest 840, bot 266); live guard test ALL PASS; soaks `runs/v3-soak-1..3` (soak 3 on the final code: 30/32 goals, 0 deaths, 0 false contradictions); not pushed |
| S08 combat and hunting | done | b | Contract v3.3; 12 lanes merged (pytest 1173, bot 357); live `runs/v3-m3-live` ALL PASS (cooked beef by kill and smelt with stochastic in-range kills and 0 mismatches, kill 3 zombies, guard me); soak `runs/v3-m3-soak` 25/28, 0 deaths; not pushed |
| S09 building foundations | done | a | Contract v3.4a and bot API v3.4; 5 lanes merged (pytest 821, bot 258); `runs/s09-place-live` ALL PASS (16 checks) from `main`; not pushed |
| S10 build goals | done | a | Contract v3.4b and bot API v3.4b; 12 lanes merged (pytest 997, bot 312); live `runs/v3-build-live` and `-easy`: Claude's house approved in the panel, built and matched (0 failed steps); protection checked live; not pushed |
| S11 duels | running | b | Contract v3.6 (`/duel`, tactics, the duel protocol); lanes fight-core, duel-bot, duel-runner, then one per tactic |
| S12 home, storage, composite goals | running | a | Contract v3.5 and stubs (advisor: GO with changes, applied) |
| S13 mine reach (`unreachable` ore) | done | b | v3.0g; 4 lanes merged (pytest 997, bot 313); live `runs/v3-reach-2024` 1/44 ore `unreachable`, 11/12 iron goals, `runs/v3-reach-31337` 0/13, 4/4; `v2_iron.js` GATE PASS; not pushed |
| S14 polish | done | a | Contract v3.0h; 11 lanes merged (pytest 1106 at 77b4bd1, 1147 on fed3117; bot 338/356); live `runs/v3-polish-live`: bot killed mid-goal -> `interrupted` `bot`, requeued, `done`; panel by `live.json`; second house leaves no scaffold; guard live test ALL PASS; not pushed |

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
- 23:36-00:06 S02 live `runs/v3-baseline-1` (instance b, seed 2024 fresh, `--change scramble --keep-going --minutes 30 --cap-usd 2`), bot and Python from `sv2` at 7ebbd68 (S04's toss merges landed during the run, after both had loaded). 55 goals, 54 ok, $0.25, 0 Tracebacks. 1 crafting table (m4-2024: 2), 0 `.fix1.fix1`, 0 `rule_blocked`, `change_report` 3/3 pass. One false contradiction: g018 `smelt iron_ingot` saw `wooden_pickaxe: -1`, because `retrieveStation` dug the furnace with the last pickaxe on its last durability point; `smelt_iron_ingot` was demoted and g018 (iron_pickaxe) failed. Max identical failed steps 5 (g014, g018: `mine iron_ore` `unreachable`, exempt from blocking).
- 00:16 S02 b7ca710 contract v3.0e (`tools_broken`); 1cd61f1 merge `v3/wear-verify` (backend passes it, the verifier adds it back), e34d65d merge `v3/wear-bot` (`retrieveStation` digs with the cheapest durable tool and measures a break). pytest 455, bot 126, panel pass.
- 00:19-00:49 S02 live `runs/v3-baseline` (instance b, seed 2024 fresh, same flags), bot and Python from `sv2` at 8f9fc0b. 33 goals, 32 ok (g019 iron_pickaxe failed), $0.19, 0 Tracebacks. Contradictions 3, all at fired changes; `change_report` 3/3 pass. 1 crafting table; 2 furnaces, because g017's furnace drop was missed (`furnace_retrieved: false`, no dig warning) and a g019 walk picked it up later. 0 `.fix1.fix1`, 0 repairs, 0 `rule_blocked`, 0 `tools_broken` (the bot sends `tools_broken: []`). Failed steps outside the exempt search/travel errors: 0 (m4-2024: 30, 30, 30). Literal max 12: g020 `mine #log` `unreachable` with the bot stuck at y=50 under the surface, plus 8 blind explores that moved 0 (no `explore up` asked).
- 01:12 S06 3649653 contract v3.1 extended (one goal ending `_end_goal`, a user goal's budget and clock, `wanted` keeps done user goals, `goal_label`, failure reasons, idle never observes); stubs `goal_label`, `run_schemavoyager(goal=)`, `Episode.finish(announce=)`. pytest 455, bot 126, panel pass.
- 01:18 S03 e5602f7 contract v3.0f (retesting a demoted rule); ae4cf43 merge `v3/retest-store`: `SchemaStore.retest` adds `<base>.retest@<world>` (source `retest`, `revision_of` the original, empty evidence, current epoch) whose replay starts at `Rule.since_seq`; `store.epochs` untouched. pytest 568, bot 126, panel pass.
- 01:24 S03 e66b334 merge `v3/retest-life`: `_repair_dead_ingredients` first retests the demoted producer (most confirmations) of an item nothing usable produces, in passes, once per rule; `retest` events (`started`/`match`/`mismatch`) and `"retests"` in the next goal's record via `_log_unreported`/`_attach_unreported`; `retests` in `life_state.json`; `change_report` (e) counts a retest's repeat, (b) reports its revise calls. pytest 611, bot 137, panel and goals pass.
- 01:25 S07 036a002 plugins (`mineflayer-pvp` 1.3.2, `mineflayer-armor-manager` 2.0.1, `mineflayer-auto-eat` 3.3.6); 3ccd26a contract changes (guard aborts leave windows to the handler, handlers stop on `session.signal`, counted `hold()`, `MAX_INTERRUPTS`, v3.2i simulator); f21cfbf merge `v3/entities-bot`: `nearby_entities` on `/observe` and `/status`. pytest 611, bot 144, panel pass.
- 01:31 S03 379bba2 contract fix v3.0f (retest ids); f3e73dd merge `v3/retest-ids`: the advisor's final check found that `<base>.retest@` ids collided for nested revisions (`x.rev1@w`, `x.rev1@w.rev1@w`), the second copy overwrote the first and `_retest_demoted`'s passes never ended. Now the full id, an existing id raises, and a pass counts a new retest's yields as produced. pytest 624, bot 189, panel and goals pass. Offline check on copies of `runs/v3-baseline-1` and `runs/m4-2024`: the trigger retests only `smelt_iron_ingot`, 0 repair calls, epochs unchanged.
- 01:21 S06 8f479eb merge `v3/goal-queue` (`GoalQueue` over `goals.jsonl`, flock, transition table, ids, `next()` system first, `goal_label`); a78281d merge `v3/goal-parse` (`LLM.parse_goal` live/replay/fake, `vanilla_items`/`vanilla_foods`, `data/items_1_21_1.json`: 1333 items, 41 foods); b8cac25 merge `v3/chat-bot` (`lib/chat_commands.js`, `BOT_OWNERS` on the bot line); 2337d21 merge `v3/panel-ui` (`site/dist/goals.js`, test `node site/dist/goals.test.mjs`). pytest 597, bot 137, panel and goals pass.
- 01:29 S06 0297fc3 merge `v3/panel-routes`: `POST /goals`, `GET /goals.json`, `POST /goals/<id>/cancel` (run by name or newest `goals.jsonl`; 415/403/400/404/409). pytest 620, bot 180.
- 01:31 S06 dae44b8 merge `v3/life-queue` (rebased over S03's retest-life): the life parses typed and `!goal` goals, runs queued goals first through one ending (`_end_goal`), cancels and preempts between steps (`_before_step`, a marked place for S07's death check), `--mode play` idles. **S07 `death-handling` can start.** pytest 642, bot 189, panel and goals pass.
- 01:32 S07 2f4d8fc merge `v3/survival-plugins` (`attachSurvival(bot, eventLog)` → `{hold, onIdleTick}`, armor pickup listener removed, auto-eat at 14 or 17 while hurt, no off-hand, `ate`); 00839d4 merge `v3/guard-core` (`lib/guard.js`: threats at 5 blocks or 8 and closing, attackers for 3 s, creeper back-off, fight/flee under one 30 s deadline); f35e754 merge `v3/handler-abort` (`session.signal` through walks, pickups and stations; an aborted walk marks no target; `/craft` remembers a table left standing); 83e8045 merge `v3/guard-fixes` (orchestrator review: owners' hits ignored, no eat during a hold). pytest 642, bot 191, panel pass.
- 01:44 S07 2208b23 merge `v3/python-survival` (`Observation.entities`, 409 busy retried 0.25 s then 0.5 s up to 60 s, `say` never, `interrupted` detail flat, in-place retry `MAX_INTERRUPTS` 4, `REQUEST_TIMEOUT_S` 180); 23c1d72 merge `v3/sim-threats` (`NaturalWorld(difficulty=)`: seeded threats answer `interrupted` with fight events, food and sim auto-eat, entities, `die()`; `_candidates` KeyError fixed; seed 1 on easy 15/15 goals with 4 interrupted steps, 0 contradictions); 2366eef merge `v3/smelt-abort` (furnace drained first with `interference`, abort takes items back, `reachStation`); 28af805 merge `v3/guard-wire` (`lib/action_lock.js`, cut-in → `interrupted`, busy until settled and through the fight, 409 `orphan`/`guard`, 45 s backstop, `POST /guard`). pytest 680, bot 212, panel pass.
- 01:44 S06 ad4d699 merge `v3/goal-narration`: "On it: 10 torches (7 steps)" for a user goal's first plan, `finish(announce=False)` for life goals, trace lines for `goal` and `goal_interrupted`, "Your goal: ..." on the goal card (the card is in `site/dist/life.js`, not `trace.js`; the lane took `life.js` and `trace.test.mjs`). 62fc748 merge `v3/chat-live` (`mc/bot/test/v3_chat_live.js`). pytest 680, bot 196.
- 01:38-01:53 S06 live `runs/v3-m1-live` (instance a, `fresh-2024`, `BOT_OWNERS=Otopo,Tester`, `--mode play --minutes 15 --cap-usd 2`; bot, panel (port 8767) and Python from `sv2` at ad4d699, the run's `COMMIT`). (1) `curl -X POST` "get me 10 torches" -> u001 done in 117 s from an empty inventory (16 actions, 12 torches), panel `done`; chat "Queued u001: 10 torches", "On it: 10 torches (14 steps)", "Done: 10 torches", no "Goal reached". (2)-(4) `node mc/bot/test/v3_chat_live.js --checks 2,3,4`: ALL PASS. `!goal 4 sticks` -> u002 done (target_count 7, held 3); `!goal build a castle` -> "I can't build yet. ...", u003 unsupported; `!goal 20 iron ingots` then `!cancel` after 2 steps -> `goal_interrupted` cancelled 6 s later, 1 in-flight step after the cancel, "Cancelled u004: 20 iron ingots" once. Stopped on `wall_clock`.
- 01:54-02:00 S06 live `--resume --minutes 6` of `runs/v3-m1-live`: the panel's own `site/dist/goals.js` (driven by node with real fetch and the page's Origin against the live panel; no browser in this headless session; same src as ad4d699) sent "get me 10 torches" -> u005 queued, running, done in 30 s. Total spend $0.076.
- 01:56 S06 dcba09f merge `v3/goal-endings` (advisor): sim tests for the wall-clock requeue, the per-goal clock and the failure-reason order; no code change needed. pytest 715, bot 212, panel and goals pass.
- 02:04 S07 30c20c4 merge `v3/difficulty-env`; eceb359 merge `v3/whitelist` (iron armor + shield; leather armor kept but whitelisted only in M3: nothing makes leather yet, so its rules drew repair calls); 45b83ee merge `v3/death-handling` (deaths, `stop_reason` deaths, hunger goal, idle passes never observe); 0b290c8 merge `v3/guard-live`; fb068c2 merge `v3/guard-dead` (live finding: the guard re-fought a husk's body for 0.9 s after killing it, and fought a creeper in melee at 6.6 blocks; now dead mobs are skipped and any creeper means back_off). pytest 731, bot 215, panel pass. Live `node mc/bot/test/v3_guard_live.js` on instance b (bot and Python from `sv2` at fb068c2, `fresh-2024`, easy): ALL PASS twice (cut-in during `/mine` → `interrupted` husk `killed`, the retry digs `oak_log: 1`; creeper → `fled` creeper, moved 4.1/3.1 blocks, no fight; idle fight → 409 `guard` then `/observe` ok; `/guard` modes; a dead husk not re-fought).
- 02:26 S09 0d14636 contract v3.4a (blueprint format, `place`/`clear`, `scan`, `set_keep_out`, simulator) and bot API v3.4 (`/place`, `/clear`, `/scan`, `keep_out`), after the advisor's review; stubs: `place`/`clear` in `ActionKind`, `Action.pos` (in `_V2_ACTION_FIELDS`), `LifeBackend.scan`/`set_keep_out`, `blueprint.py` constants and signatures. pytest 731, bot 215, panel and goals pass.
- 02:37 S09 4f036f4 merge `v3/blueprint`: `blueprint.py` `validate` (shape, palette, unknown characters, doors, torches, connection to the ground, 20-message cap), `materials` (the example house: 74 `#planks`, 12 `#log`, 3 glass, 1 torch, 1 door), `cells`, `door_tops`, `matches`, `render`, `is_full`, `carries`. pytest 765, bot 215, panel and goals pass.
- 02:38 S09 bee5841 merge `v3/build-sim`: `NaturalWorld` `place` (already there, `missing_item`, `occupied`, `no_support`; torch or `wall_torch`; doors fill two cells), `clear` (air, `unbreakable`, both door halves, no drops), a cell with no open neighbour is `unreachable` (architect's merge fix: the walks never dig), `scan`, `set_keep_out`; `place`/`clear` in `THREATENED_ACTIONS`. pytest 789, bot 215, panel and goals pass.
- 02:05-02:35 S07 live `runs/v3-soak-1` (instance b, seed 2024 `fresh-2024`, `SV2_DIFFICULTY=easy SV2_DAYLIGHT=on`, `--keep-going --change scramble --minutes 30 --cap-usd 2`), bot and Python from `sv2` at 2f1559f. `wall_clock`, 38 goals, 22 ok (58%; curriculum 21/22), $0.12. 0 deaths; 2 fights, both in `mine` steps, `killed`, retried in place and then fine; 0 idle fights, 0 orphaned handlers; 0 contradictions outside fired changes (3 at fired changes); the 2 interrupted steps were `failed` checks, no transition. Found: 15 `hunger: 4 apple` system goals in a row (14 failed, about 60 s each: the planner reached apple through a leaves rule); fixed in ac07dd5 (contract) and 99a44ad (`HUNGER_RETRY_S` 900). pytest 793, bot 215.
- 02:39 S09 a685e52 merge `v3/build-backend`: `MineflayerBackend` `place`/`clear` through `/place`/`/clear` in both modes (`scaffold`, `keep_out` while set, the step's own `inventory_delta`, `detail` with `target`), `scan` through `/scan`, `set_keep_out`. pytest 821, bot 215, panel and goals pass.
- 02:41 S09 4e68d4f merge `v3/build-bot`: `lib/place.js` `/place` (checks before and after the walk, fresh non-digging movements with `scaffold` minus the block and a `keep_out` place exclusion of cost 1000, `GoalPlaceBlock` wrapped by `StanceGoal` so the walk never ends in the cell, on a cleared cell or, for a door, in the box; faces below, N, S, E, W, above; 3 tries, `place_failed`) and `/clear` (`unbreakable`, `hazard` via `hazardNear`, fastest held tool, `missing_tool` over 15 s, confirmed dig); `lib/scan.js` `/scan`; `station.js` exports `overlapsBot`. pytest 821, bot 252, panel and goals pass.
- 02:40-03:10 S07 live `runs/v3-soak-2` (same instance, world and flags as soak 1), bot from fb068c2, Python from `sv2` at c1f2f37 (with 99a44ad). `wall_clock`, 29 goals, 23 ok (79%; 85% without the 2 death interrupts), $0.15. 2 deaths (g018, g019; both goals ended `interrupted` `death`, `inventory_lost {}`, no third death so no `deaths` stop); 4 interrupted steps (`fled` 3, `timeout` 1), all retried in place; 1 creeper back-off; 0 idle fights, 0 orphaned handlers, 0 `409`; 0 contradictions outside fired changes (3 at fired changes). Hunger: 2 apple goals, then 3 `hungry` (the 900 s skip worked). Both deaths came from fights with bare hands: a witch at 10 health (starving: easy floors health at 10 at food 0), and a skeleton fought for 30 s, then the in-place retries walked back into its arrows twice.
- 02:43-03:16 S09 live `runs/s09-place-live` (instance a, `fresh-2024`, peaceful; `node mc/bot/test/v3_place_live.js`: RCON-levelled site, 3 x 3 floor and a 3-high pillar by `/place`, `/scan` match, torch, `wall_torch`, door, `/clear` all, `/scan` air, plus idempotent/occupied/missing_item/bad_request). Runs 1-3 found three bot bugs: the `overlapsBot` margin refused an off-centre bot (pathfinder's `fullStop` snaps it within 0.2 of the centre, so the real hitbox never crossed); a door's upper half reaches the bot ~0.5 s after `/place` answered (RCON showed both halves on the server); `GoalLookAtBlock` can never be met for a torch or grass (no collision shape), 5 s then `unreachable`. Fixed (advisor on the last two). Runs 4-5 from the worktree and run 6 from `main` 6350e2d: ALL PASS, 16 checks.
- 03:16 S09 6350e2d merge `v3/place-live`: `mc/bot/test/v3_place_live.js`; exact-hitbox backstop plus a controls re-centre, doors wait for the upper half, a reach-only goal for shapeless `/clear` targets; bot API v3.4 notes updated. pytest 821, bot 258, panel and goals pass. Ops: `screen -S sv2-a-bot -X quit` leaves the node bot running (orphaned under `login`); stop it by its pid in `run/bot-a.pid`.
- 03:28 S07 after the soak-2 deaths (advisor: fix, then soak 3): ddcab3a contract (rest after a low-health flight, arming goal); cd87652 merge `v3/no-sprint` then c70712a merge `v3/sprint-peaceful` (orchestrator review: pathing sprints only on peaceful; off peaceful sprinting drained food 20→2 in about 10 min, and health only regenerates at food 18+); 92c5331 merge `v3/guard-armed` (unarmed flees, out of reach for 5 s ends a fight `lost`, a mob that beat the bot is only fled from, `respond()` waits for the fight's cleanup); daae52e merge `v3/rest-retry` (rest until health 14 if food ≥ 18, else stop retrying); 86ee140 merge `v3/arm-goal` (`arm: 1 <sword>` system goal on a threat while unarmed). pytest 840, bot 266, panel pass. Live guard test on c70712a: ALL PASS (a first run failed check 1 `unreachable` in 129 ms: the bot started on a tree from the earlier life, 2 blocks above the placed log).
- 03:27-03:57 S07 live `runs/v3-soak-3` (same instance, world and flags), bot and Python from `sv2` at c70712a. `wall_clock`, 33 goals, 31 ok (94%; 30/32 by goal id), $0.25. 0 deaths; 2 fights (both `killed`, in `mine` steps), 1 creeper back-off; 3 interrupted steps (`killed` 2, `fled` 1), all retried in place, all `failed` checks with no transition; 0 idle fights, 0 orphaned handlers, 0 `409`; 0 contradictions outside fired changes (3 at fired changes); 0 hunger and 0 arming goals; food 14-20 all life (soak 2 reached 0). Only 3 threats in 30 min: the rest, arming, out-of-reach and beaten-mob paths didn't run live (unit-tested).
- 03:45 S10 f3e73d9 contract v3.4b (protected boxes sent per request, `/survey`, `/goto`, the approval flow, panel approve/discard and preview, `design_blueprint`, doors as `#door`, build order, the build runner) and bot API v3.4b, after the advisor's review (GO with changes, then a design check: GO with five fixes); stubs `Site`, `goto` in `ActionKind`, `survey`/`set_protected`, `Box`/`site_box`, `builder.py`. pytest 840, bot 266.
- 04:06 S10 025286d merge `v3/build-order` (`builder.py`: clear top-down, fill under layer 0, place by a `(y, z, x)` heap of supported cells, torches, doors last; `progress`, `mismatches`, `needed`); 38284c1 merge `v3/survey-bot` (`POST /survey`, `POST /goto`); fb752e6 merge `v3/survey-backend` (`survey()` and `goto` in both backends); fc9c725 merge `v3/blueprint-llm` (`design_blueprint`, limits 7 x 4 x 7 and 120 cells, 2 retries, `FAKE_HUT`; architect's fix: a worked example in the prompt). pytest 880, bot 283, panel and goals pass.
- 04:29 S10 6d9a5a6 merge `v3/protect-backend` (`set_protected`: `protect` on `/mine`, `/explore`, `/look`, `/place`, `/clear`, `/goto`, `/survey`; the simulator skips protected targets, dig paths, look hits and climbs); a6b473e merge `v3/protect-bot` (`lib/protect.js`, target and look filters, `DiggingMovements` break/place exclusions, build walks' place exclusion, no stance on top of the `keep_out` box); 491124f merge `v3/panel-preview` (`/goals/<id>/approve` and `/discard`, `preview` on `/goals.json`, `site/dist/build_preview.js`, build lines in the trace; test `node site/dist/build_preview.test.mjs` joins the offline suite); 54126d8 merge `v3/build-run` (`build_runner.py`, `agent.pursue`, `planner.make_plan_for`, `Episode.act` build-kind policy); ae6ba95 merge `v3/doors` (`#door`, `matches` takes any wooden door, simulator recipe, fake prior). pytest 958, bot 297, panel, goals and preview pass.
- 04:33 S13 d617135 contract v3.0g (reaching a `/mine` target; plan S0.8, after the advisor's review: `reach` top-level, `no_path` from the last `path_update` status); 0853c32 merge `v3/mine-approach` (`lib/approach.js`: 60 s call, attempts end `reached`/`no_path`/`path_timeout`/`ended`/`stalled`/`no_approach` (10 s)/`approaching`, only `no_path` in the 10-min memory, `approaching` marks nothing, `gotoGoalDetailed`, top-level `reach`); ee4f6bb merge `v3/dig-down` (stock downward dig kept in `DiggingMovements`); d93456a merge `v3/unreachable-retry` (`approaching` retried at once, other `unreachable` mines `_explore_toward`, `reach` in the mine detail). pytest 971 (1 skipped), bot 312, panel and goals pass.
- 04:54 S10 3496bb1 merge `v3/build-approval` (`parse_goal` supports `build` via `design_blueprint`, `accept` sends a build to `awaiting_approval`, the plan line, `!yes`/`!no`, `!status`; three tests that asserted "can't build" updated, as the contract changes it); 704fa0e merge `v3/build-life` (build goals in the M1 loop through one ending, protected boxes in `life_state.json`, `set_protected` at start, on `--resume` and on change, build materials in priors and the junk toss); d13950c merge `v3/door-order` (found by `build-life`: mixed log species left no 6 planks of one kind and `craft #door` was blocked on 5 of 8 simulator seeds; doors are now gathered last, and the reason names every need not held; the fake hut builds on seeds 1-8). pytest 997, bot 312, panel, goals and preview pass.
- 04:55-05:12 S10 live `runs/v3-build-live` (instance a, `fresh-2024`, peaceful, `BOT_OWNERS=Otopo,Tester`, `--mode play --minutes 60 --cap-usd 3`; bot, panel (port 8768) and Python from `sv2` at 8118c7e, the run's `COMMIT`). Panel `POST /goals` "build a small wooden house with a door" -> u001 `awaiting_approval` in 7 s with Claude's "small house" 5 x 4 x 5 (12 `#log`, 59 `#planks`, 1 `oak_door`; preview saved, `trace.html` and `build_preview.js` served 200), `POST /goals/u001/approve`, then running -> `done` in 304 s: gathering 232 s, building 52 s (72 place steps, 0 failed, 1 pass), site [11, 67, 8] cut 0 fill 0. An independent `/scan` of the site box vs the blueprint: `mismatches` `[]`. $0.039. Clip: `runs/v3-build-live/build.mp4` and `build.gif` (`scripts/v3-clip.js`, headless Chrome screencast of the viewer on 3207). Then `runs/v3-build-live-easy` (same world restored, easy): the same blueprint and site, `done` in 368 s, 72 steps, 0 failed, no fights or deaths, `mismatches` `[]`, $0.040.
- 05:13-05:16 S10 live protection check: `--resume` of `runs/v3-build-live-easy` (no world restore, the house standing), panel "get me 10 logs" -> u002 `done` in 70 s, 10 oak logs from trees 4+ blocks outside the house box, while an unprotected `/look` lists the house's own logs; a `/scan` of the site box before and after: every house cell identical, `mismatches` `[]`; six grass cells under the walls turned to dirt (vanilla grass decay under solid blocks). Instance a down, lock released.
- 05:36 S14 0ede41b contract v3.0h after the advisor's review (GO with changes: every socket error is a `BackendError`, `Episode.finish` without the bot, requeue after a pass-level wait, scaffold cells from every attempt); stubs `LifeBackend.connected`, `Action.scaffold`, interrupt `bot`, `LifeConfig.bot_wait_s`/`bot_poll_s`. pytest 997 (1 skipped), bot 312, panel, goals and preview pass.
- 04:35-05:29 S13 live on instance b (peaceful, `--change scramble --keep-going --cap-usd 2`, bot and Python from `main` with the fix, run `COMMIT` 3496bb1/8118c7e): `runs/v3-reach-2024` (fresh-2024, 30 min) 32 goals, 31 ok (iron_boots cut by the clock), $0.25, ore `unreachable` 1/44 (baseline-1 12/23), 11/12 iron goals; `runs/v3-reach-31337` (fresh-31337, 20 min) 45/45 ok, $0.23, ore `unreachable` 0/13, 4/4 iron goals. 0 Tracebacks. A first `v2_iron.js` ran on the stale `world-1001` (restore without `SV2_SEED`) and failed at planks.
- 05:31-05:36 S13 live `node mc/bot/test/v2_iron.js` on a fresh `world-2024` (instance b, `main` 9e775d4's bot): GATE PASS, iron_pickaxe in 238 s, every check MATCH. 05:38 c03fce7 merge `v3/think-ms` (live finding: `think_ms` counted each A* search's cumulative time on every partial update). pytest 997 (1 skipped), bot 313, panel and goals pass.
- 05:50 S14 77ab8ee merge `v3/backend-health` (`_post`: every socket error, bad JSON and a `not_connected` answer are `BackendError`; `connected()` reads `GET /health`); **e9b379f merge `v3/life-robust` (S08: this is S14's `life.py` change)**: `_Life.run`'s pass body moved into `_run_pass()`, a `BackendError` there or mid-goal waits for the bot (`bot_down`/`bot_back`), ends the goal `interrupted` with `bot` and requeues it (3 losses: `failed`), `stop_reason` `bot_down` after 120 s; `_say` best-effort; `Episode.finish` without the bot; `run_life` writes `live.json`. pytest 1026 (1 skipped), bot 313, panel, goals and preview pass.
- 05:52 S14 2b3fd71 merge `v3/panel-live` (no `run`: the one run whose `live.json` pid is alive, else 409 `ambiguous_run` with `runs`, else the newest `goals.jsonl`; the goals box shows `Run: <name>` and honors `?run=`); 899f7e3 merge `v3/door-species` (`ensure_door_planks` before a build's door: craft `<species>_planks` from the best species, or gather 2 more logs first). pytest 1033 (1 skipped), bot 313, panel, goals and preview pass.
- 06:03 S14 b9ff10b merge `v3/trace-interrupted` (`INTERRUPTED (<reason>)` in the terminal trace); **8cf585a merge `v3/life-polish` (S08: S14's second `life.py` change)**: play mode's `_needed_items` has no frontier, a goal cancelled after its last step says "Cancelled: ...", `!status` between goals says "Next: u004: 10 torches. Queued: 2.". pytest 1044 (1 skipped).
- 06:07 S14 52d3d42 merge `v3/bot-polish` (build `Movements` follow `sprintAllowed`, `autoeat_error` skips "No food found.", `v3_guard_live.js` builds its arena at y 200 over the bot and teleports it there; architect's fix: the cleanup box covers the glass ceiling); 32091e2 merge `v3/build-scaffold` (`travel.placed_cells`, `Episode.placed_cells`, `clear_scaffold` after a matched build, `scaffold_cleared`/`scaffold_left` in `build_done`; architect's fix: four bot unit tests expected the old empty travel). pytest 1051 (1 skipped), bot 316, panel, goals and preview pass.
- 06:10 S08 contract v3.3 (kill rules, `/attack`, mobs in `/look`, `/follow`, kill and guard goals) in both documents after the advisor's review (GO with changes: `escaped` never blocks, quarry set at the candidate, kills by `fight_end` id, baby animals only, sprint copy restored, seen mobs and `cooked_x` in priors, runners in `goal_runners.py`); stubs `kill` in `ActionKind`, `Action.describe`, `Rule.subject`/`yields` for kill, `Status.follow`, `LifeBackend.look(mobs=)`/`follow`/`stop_follow`. pytest 1051 (1 skipped).
- 06:28 S08 group 1: 94c0a1e merge `v3/kill-rules` (kill rules always stochastic: `killed` plus drops in range, never a mismatch; `_harvest` plans mine and kill on drop low ends; mobs in view rank kill rules; `escaped` never blocks); 2e15be9 merge `v3/kill-llm` (`propose_prior_rules_v2(mobs=)`, request byte-identical without mobs; fake kill and meat smelt priors; `vanilla_mobs`); 76692c9 merge `v3/kill-sim` (simulator kills on a `kills` stream, escapes, hostile damage, meat smelting, mobs in `look`, `follow`); a5f7966 merge `v3/attack-bot` (`POST /attack`, mobs in `/look`, `setQuarry`, `fight()` `flee`/`sprint`/`weapon`, `id` on `fight_end`). pytest 1100 (1 skipped), bot 333, panel pass.
- 06:10-06:20 S14 live `runs/v3-polish-live` (instance a, `fresh-2024`, peaceful, daylight off, `BOT_OWNERS=Otopo,Tester`, `--mode play --minutes 20 --cap-usd 2`; bot, panel (8768) and Python from `sv2` at 73474f9). The panel resolved the run from `live.json` (no `run` in the requests). u001 "get me 10 torches": the bot killed by pid mid-`/mine` -> `RemoteDisconnected` -> `BackendError` -> `bot_down`, the bot restarted with `v2-up.sh` (lock hand-over `SV2_LOCK_SESSION`), `bot_back` after 22.1 s, u001 `interrupted` with `bot`, requeued (`target_count` 10 kept), rerun `done` (12 torches). u002 "build a small wooden house with a door": `awaiting_approval` in 8 s, approved in the panel, `done` in 6 min, 72/72 placed, an independent scan `mismatches` `[]`; `build_done` `scaffold_cleared` 1, `scaffold_left` 0, but a 2-block dirt tower stayed at (15, 67..68, 13): mineflayer's `blockPlaced` came after the walk resolved and was never recorded.
- 06:30 S14 after the advisor (do both): 8d39016 merge `v3/scaffold-record` (`recordTravel` wraps `bot.placeBlock` and reads the cells back after placements settle; place and goto walks always settle); 77b4bd1 merge `v3/scaffold-ring` (ring scan before and after the build, `scaffold_from`). pytest 1106 (1 skipped), bot 338, panel, goals and preview pass.
- 06:49 S08 group 2: eb518fb merge `v3/parse-kill-guard` (`parse_goal` kill and guard; two tests that asserted the old can't-hunt replies updated); d671c09 merge `v3/kill-backend` (`/attack` in the backend, `look(mobs=)`, `follow`/`stop_follow`, `Episode.act` explores toward a mob sighting, retries `escaped`/`timeout` in place); c159ded merge `v3/hunt-food` (`observed_mobs`/`asked_mobs`, `cooked_x` asked, hunger ranking; three LLM test doubles take `mobs`); fed3117 merge `v3/follow-bot` (`/follow`, `/follow/stop`, `/status.follow`, `player_attacked`/`player_near`, fights sprint, ranged mobs 8 s). pytest 1147 (1 skipped), bot 356, panel pass.
- 06:32-06:48 S14 live `runs/v3-polish-live` session 2 (`--resume --minutes 15`, the same world with u002's house, bot restarted on main at 77b4bd1, Python and panel from `sv2` at e2a734a). u003 "build a small wooden house with a door": approved in the panel, `done` in 5 min at [4, 76, 31], 72/72 placed, an independent scan `mismatches` `[]`; `build_done` `scaffold_cleared` 2, `scaffold_left` 0, `scaffold_from {walks: 2, ring: 2}` (the new recorder caught the tower by itself); both cells air on a rescan. **The live scaffold check passes on u003.** u002's 2-block tower at (15, 67..68, 13) is still in instance a's world: it was built before `scaffold-record` and `scaffold-ring` (the next `world-restore.sh fresh-2024` removes it). Ended `wall_clock`, `live.json` removed, lock released; $0.076.
- 06:49 S14 live `node mc/bot/test/v3_guard_live.js` (instance a, under the lock, main at fed3117's bot code): ALL PASS (5 checks) in the new arena at y 200 over the bot; afterwards the bot was back on the ground and a scan of the arena's 6727 cells found no block left. Instance a down, panel stopped, lock released.
- 07:07 S08 796807f merge `v3/goal-runners` (`goal_runners.py`: arm first, kill until n `fight_end` kills by id with a 3-failure streak and a clock, guard until the clock or the player dies or is lost, `stop_follow` always; the life dispatches them, resolves "me" to the `!goal` speaker, buffers fight and follow events, stops following at start). pytest 1170 (1 skipped), bot 356, panel pass.
- 07:08-07:38 S08 live on instance b (`fresh-2024`, easy, `BOT_OWNERS=Otopo,Tester`, play mode, live LLM): `runs/v3-m3-live-try1` (arena outside the world border: 3 border deaths, the life's `deaths` stop; checks 1-2 passed on natural mobs); `runs/v3-m3-live`: checks 1-2 pass, check 3 failed twice (criterion during fights; then the follower rested 5.5 blocks away), a261840 merge `v3/follow-tight`, `--resume`, then `node mc/bot/test/v3_hunt_live.js` ALL PASS in one invocation on a261840 (`logs/s08-hunt-live-final2.log`). 4d332d2 merge `v3/hunt-live`. 0 deaths, 0 mismatches.
- 07:37-07:57 S08 live `runs/v3-m3-soak` (learn mode, easy, daylight on, scramble, 20 min): 25/28 ok, 0 deaths, mismatches only at fired changes, mines 64 reached / 2 stalled / 0 unreachable; no hunting (no cow in view at a prior call). 7d94718 merge `v3/kill-polish`. pytest 1173 (1 skipped), bot 357, panel pass.
- 08:38 S11 f7f37f2 contract v3.6 (duels: `/duel`, `/duel/stop`, `duel_end`, the tactics baseline, `fight()` `tactics`/`fleeBelow`, the duel protocol; advisor GO with changes: 40 duels per series, golden apple only, the strike gate counts its own ticks, one `attemptAttack` override, a close-range mover because pathfinder's path-end stop cancels jumps and knockback).
- 08:38 S11 1b4de81 merge `v3/fight-core` (`lib/tactics.js`: full-cooldown strike gate, pvp's shield routine, the mover within 4 blocks with safe steps; release at 5 blocks or a 2-block height gap); 9419261 merge `v3/duel-bot` (`lib/duel.js`, `POST /duel`, `/duel/stop`); a59ef9f merge `v3/duel-runner` (`scripts/v3-duel.sh`, `scripts/v3-duel.js`; fix: `--vs` keeps the bot on set A). pytest 1174 (1 skipped), bot 403, panel pass.
- 08:05 S12 dab14c0/38182b7 contract v3.5 (places, chests, home stations, composite goals; the bot never enters a house: stations and chest in the yard) after the advisor's review (GO with changes, all applied); stubs `withdraw`/`composite` kinds, `HOME_RANGE`, `HomeStock`, `ChestResult`, `Episode.home`, `LifeBackend.deposit`/`set_stations`. Instance a's world with S14's two houses saved as snapshot `s12-houses`. pytest 1173, bot 374, panel pass.
- 08:40 S12 43bc8bb merge `v3/places` (`places.py`: `Home`, `Places`, `door_cell`, `home_box`, `yard_slots`, `home_from_protected`; `blueprint.merge_boxes`; `inventory.junk_to_store`). pytest 1208.
- 08:43 S12 e68a163 merge `v3/composite-queue` (parents with ordered children in `goals.jsonl`, `accept_children`, `settle`, cancel cascade; `parse_goal` lists with `home=`, `replan_children`; architect's fix: a fake `build ...` text is never split). pytest 1247.
- 08:45 S12 f54526e merge `v3/chest-backend` (deposit, the withdraw action, `set_stations` with `station`/`protect` on `/craft` and `/smelt`; simulator chests and home stations; architect's fix: deposit `free_slots` is the inventory's in both backends). pytest 1280.
- 08:47 S12 2b1c52c merge `v3/chest-bot` (`/deposit`, `/withdraw`, `/chest`). pytest 1280 (1 skipped), bot 415 (with S11's merges), panel pass.
- S11 live `runs/v3-duel-smoke` (instance b, `fresh-2024`, 2 duels baseline vs baseline): the runner works end to end (arena, RCON reset, both `/duel`, the table). Found: double KOs (server log: both bots slain in the same second in 3 of 4 duels; each bot had ended `killed` on the other's `entityDead`), and 10 s lost per reset while the dead bot respawned. Every hit 3.12 (a full-cooldown iron sword on iron armor), 7 to kill; regen stays off (`health_end` = 20 − `damage_taken`).
- S11 91ec089 contract `both_died`; 7100330 merge `v3/double-ko` (`killed` after a 0.5 s grace, `both_died`; the runner reads `death` events, a double KO is a draw, waits for both bots alive); 8d08c2d merge `v3/crit`. pytest 1280, bot 432, panel pass.
- S11 live `runs/v3-duel-aa` (A/A, 40 duels, 0 invalid): 27 double KOs, A 10 wins to B 3 (58.8%, CI [43.4, 72.6]), A winning with either bot and on either side. The set whose request went first won: a few ms of head start decide a mirror duel. Fixed by 2f3fa99 (contract `start_at`) and 09:00 b19b7cc merge `v3/duel-sync` (one start instant 1 s ahead; `duration` ends at the deciding death). pytest 1297, bot 437, panel pass.
- 09:05 S12 6136cf6 merge `v3/home-plan` (`HomeStock.near`; `stations` in `make_plan`/`make_plan_for`; `planner.withdrawals`; `pursue` withdraws from the chest before a plan, `lost`/`skipped` home kinds; `withdraw` in `BUILD_KINDS`). pytest 1297.
- 09:07 S12 66317a1 merge `v3/station-bot` (`/craft` and `/smelt` take `station` and `protect`; held stations placed outside protected boxes; home station walked to and used in place; craft no longer reports a dug-up remembered table as a gain; architect's fix: one walking-movements helper exported from `goto.js`). bot 450.
- 09:10 S12 ff7fcb0 merge `v3/composite-life` (children, one replan, parent endings and settling in `life.py`; architect's fix: `mark` updates fields in the same status, so a replanned parent stays `running`). pytest 1307.
- 09:12 S12 b6af8a6 merge `v3/composite-panel` (children under their parent with "N of M done"; trace lines for `withdraw`, `stored`, `home_*`, `goal_children`, `goal_replanned`, `goal_parent_end`; architect's fix: home events carry `station`, not `kind`). pytest 1307 (1 skipped), bot 450, panel pass; contract v3.5 clarified from the lanes' notes.
- S11 live `runs/v3-duel-aa2` (A/A with `start_at`, 40 duels, 0 invalid): **A 51.2% [36.3, 65.9]**, 7 to 6, 27 double KOs; by bot 4/20 and 3/20, by side 4/20 and 3/20. The setup is unbiased.
- S11 live `runs/v3-duel-crit` (crit vs baseline, 40 duels, 0 invalid): **crit 93.8% [81.8, 98.0], KEPT** (37 wins, 2 losses, 1 draw; by bot 18/20 and 19/20, by side 19/20 and 18/20). Only 41 of crit's 251 hits were crits (about 1 a duel): most of the gain is the shield's. Striking on its own rhythm, the crit bot has its shield up when the baseline strikes (it blocked 5 strikes a duel, the baseline 0.6), where two baselines strike in the same tick with both shields down. Best set: `[crit]`.
- S11 a319c9c merge `v3/wtap` (release forward and sprint 3 ticks after each strike, keep pressing in otherwise; hooks run in `REGISTRY` order, crit last). pytest 1326, bot 454, panel pass.
- S11 live `runs/v3-duel-wtap` (crit,wtap vs crit, 40 duels, 0 invalid): **crit,wtap 48.8% [34.1, 63.7], DROPPED** (14 wins, 15 losses, 11 double KOs). In the crit mirror the shields block almost nothing (0.1 a duel): both bots strike on the same rhythm again. Best set: `[crit]`.
- S11 95edddd merge `v3/strafe` (within 4 blocks hold left or right, switch after 15-35 ticks or when the safety check releases the side; `afterSafety` hook). pytest 1326, bot 459, panel pass.
- S11 live `runs/v3-duel-strafe` (crit,strafe vs crit, 40 duels, 0 invalid): **crit,strafe 45.0% [30.7, 60.2], DROPPED** (8 wins, 12 losses, 20 double KOs). Best set: `[crit]`.
- 09:18 S12 472526e merge `v3/home-life` (places in `life_state.json` with the resume migration, the yard in `home_box` and `merge_boxes` to 32, furnishing, storing before tossing, one chest goal per home, `Episode.home` write-back, `home=` for parsing). Existing tests changed: two `test_v3_build_life.py` protected-box assertions (the home's box now includes the yard) and one `max_goals` (the chest goal runs first). pytest 1326 (1 skipped), bot 450, panel pass.
- 09:21-09:38 S12 live on instance a (`s12-houses`, peaceful, daylight off, `BOT_OWNERS=Otopo,Tester`, play mode, live LLM, resume of a copy of `runs/v3-polish-live`; `node mc/bot/test/v3_home_live.js`): `runs/v3-m5-live-try1` (`--south 4`): chest placed in the yard, but `/deposit`'s walk to `GoalNear(chest, 2)` stalled 45 s under a canopy (travel 1.3) and the life fell back to tossing; check 1 FAIL, reproduced 3/3 by hand. `runs/v3-m5-live` (`--south 5`, fresh restore): ALL PASS: junk + coal 64 stored (free slots 2 -> 23, nothing tossed), "8 torches" withdrew coal x2 (chest 64 -> 62), "get ready for night" -> u007 16 torches, u008 a bed, u009 a shield, in order, each `success`, parent `done`, no replan; houses `mismatches` `[]`; $0.12.
- 09:44 S12 03360b2 merge `v3/home-live` (the live script). 09:58 bd5d161 merge `v3/chest-reach` (chest and home-station walks to range 3, 8 s stall cutoff; simulator `CHEST_RANGE` 3); 2a18fc3 contract. pytest 1326 (1 skipped), bot 470 (with S11's merges), panel pass.

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

## S04 full inventory (done 2026-10-18)

- Commits on `main`: 7ebbd68 contracts and stubs; 959c7b1 `v3/junk-policy`; c2479be `v3/toss-backend`; 1569837
  `v3/toss-bot`; a967275 `v3/toss-sim`; 515456b `v3/junk-life`; a225fbc `v3/toss-live`. Suite on ee9edbc: pytest 445,
  bot unit 120, panel all passed.
- Contracts: `contracts/bot_api.md` "v3.0: `/toss` and `free_slots`"; `docs/v3-interfaces.md` v3.0c "Inventory space".
  Changes from the S04 prompt, on the advisor's verdict: once triggered, `junk_to_toss` drops all the junk (not just
  enough for 4 free slots), keeps `curriculum.is_kept_item` items, and keeps one stack per raw id of a rule item (the
  rest is dropped only while still under 4 free slots). The simulator fails a craft or smelt whose output can't fit
  (`craft_failed`, inventory unchanged), as the bot does. `SCAFFOLD_BLOCKS` moved to `inventory.py`
  (`mineflayer_backend` imports it), because importing `mineflayer_backend` pulls in `rcon`.
- Live evidence: `node mc/bot/test/v3_toss_live.js` ALL PASS, and `runs/s04-junk` (see Log, 23:49). Bot and Python both
  ran from `sv2` at f820661. Instance a is down with no lock; no S04 worktrees or branches are left; not pushed.
- Open:
  - Deferred: a goal that fills the inventory halfway through (nothing tosses between steps).
  - Only one live toss (g001). Rule-item surplus, a blocked walk away, and many tosses over a long life are unit-tested
    only. A later goal walking back over a pile would pick it up again in travel; not seen in `runs/s04-junk`. Watch for
    it in S0.7.
  - `lib/toss.js` drops a partial stack with `bot.toss`, which takes the first matching stack rather than the
    smallest. Only the leftover count is affected.
  - Step-phase `stray_pickup` on mine steps from the travel's own digs exists without any toss (about the same
    rate in `runs/s01-explorefast`). It quarantines nothing by itself on a mine.
  - The held-tool check is unit-tested only: the API can't select a hotbar slot.
  - Seen by the `toss-sim` lane, not fixed: in the simulator, `_candidates` raises `KeyError` when mining `grass_block`
    or `dirt` in a column without that span (seeds 0-7).

## S02 fix the stuck loops (done 2026-10-18)

- Contract: 7524cfc v3.0a (blocked rules), v3.0b (repairs, kept items), v3.0d (explore `took`); b7ca710 v3.0e (`tools_broken`).
  Local tag `v2-final` at 661422f (not pushed); `test_the_v1_llm_constants_are_unchanged` reads it.
- Merges: 74732e8 `keep-items`, 7e1b7b2 `repair-dedupe`, ae754d2 `retry-block`, 7c28fa9 `blocked-memory`, 31218b2 `explore-took`,
  1cd61f1 `wear-verify`, e34d65d `wear-bot`. Existing tests changed: two `test_v2_repair.py` assertions (`skipped`, and the repair id
  now in `repaired`) and the `test_v2_nav.py` stub signature (`excluded`). Suite: pytest 342 → 455 (S04/S05 merges included), bot 108 → 126, panel pass.
- `tools_broken` came out of the first baseline life (`runs/v3-baseline-1`). The bot broke its last pickaxe while digging the furnace back up,
  and `smelt_iron_ingot` was falsely demoted. Advisor: fix it in S02, by measuring the break (not inferring it) and adding it back in the verifier.
- Live, seed 2024, 30 min (`runs/v3-baseline-1` at 7ebbd68, `runs/v3-baseline` at 8f9fc0b; m4-2024 for comparison):

  | check | v3-baseline | v3-baseline-1 | m4-2024 |
  |---|---|---|---|
  | (a) max identical failed steps per goal, literal | 12 (exempt `unreachable`) | 5 (exempt) | 30 |
  | (a) the same, errors that block (not `not_found`/`unreachable`/`hazard`/`timeout`/`not_connected`) | 0 | 0 | 30 |
  | (b) contradictions outside fired changes; `change_report` | 0; 3/3 pass | 1 (worn pickaxe); 3/3 pass | 2 (no changes file) |
  | (c) crafting tables crafted | 1 | 1 | 2 |
  | (d) `.fix1.fix1` chains | 0 | 0 | 0 (27 repairs) |
  | goals ok / total, spend | 32/33, $0.19 | 54/55, $0.25 | 28/35, $0.35 |

- **(a) fails as written.** It is met for every error that blocks: no craft or smelt failed at all in either run. The 12 are g020
  (stick): the bot stood at y=50 under the surface (logs at y=66-76). Each of 12 `mine #log` walks went about 16 blocks and ended where
  it started, with the inventory unchanged. 8 blind explores in between failed in 0.1 s and moved 0, and Python never asked for
  `explore up`. That cost about 4 min, which is part of why this run did 33 goals where run 1 did 55. Not caused by S02's changes
  (travel, `/mine` and `agent.py` are untouched by the wear lanes). **For the orchestrator:** reword S0.7's done-when to "for errors
  that block", or accept the gap explicitly.
- Open, for a navigation lane (the `/mine` reach item): underground with no sky, ask for `explore up` before blind explores; blind
  explores that can't move should fail fast or stop; no cap on `unreachable` retries other than the goal budget. `mine` steps ending
  `unreachable`: 25 (9 after walking 30 or more blocks) in v3-baseline, 13 (5) in v3-baseline-1, 19 (17) in s01-explorefast.
- Open, pickup timing: in v3-baseline g017 the furnace dig worked (no warning), but the drop wasn't collected (`furnace_retrieved: false`).
  g018 crafted a second furnace, and a g019 dig-through walk picked the old one up.
- Explore recheck (S01 method): v3-baseline-1 had 77 directional explores, all moved on the first try (`took` = the request), 0 zero-move
  over 25 s, 77/77 moved 2 or more blocks, at most 1 ask for the same direction per goal, none over 35 s. v3-baseline had only 1 directional
  explore. The precondition (3 or more directional explores that don't move on the first try) is still unmet, so the hop, the neighbour
  directions and the hole exit haven't run live.
- Untested live: `rule_blocked` (no goal hit the craft loop, so units only); a `tools_broken` that isn't empty (the bot sends `[]`); the
  explorefast fallbacks.
- Instance b is down, with no lock. No `sv2-wt/*` worktrees and no `v3/*` branches are left. Not pushed.

## S03 retest demoted rules (done 2026-10-18)

- Contract `docs/v3-interfaces.md` v3.0f (e5602f7); the replay bullet of `docs/v2-interfaces.md` "Epochs" notes the window.
  Merges: ae4cf43 `v3/retest-store`, e66b334 `v3/retest-life`, f3e73dd `v3/retest-ids` (contract fix 379bba2). Suite on
  e76ea39 (after S06's `life-queue`, dae44b8, which kept the retest calls): pytest 642, bot unit 191, panel and goals
  tests pass. Offline only; no instance touched. Not pushed.
- Design: a demoted rule is retested at most once per life, as a new rule `<rule id>.retest@<world>` (`source: "retest"`,
  `revision_of` the original, empty evidence) whose replay starts at `Rule.since_seq`, the store's transition count at
  the retest. The original stays demoted. A retest never opens or touches an epoch; once it runs it is an ordinary
  hypothesis (a contradiction after a confirmation in its window opens one, as for any rule). Rejected: B (mark the
  contradiction suspect: erases a real `r2` contradiction when the retest uses a stone pickaxe) and a store epoch at
  the retest (it would stamp later revisions and move a certified revision's confirmations out of its epoch, failing
  `change_report` (c)).
- Trigger: `_repair_dead_ingredients` first runs `_retest_demoted`: report outcomes, then in passes retest the demoted
  producer (most confirmations) of an asked item nothing usable produces and a usable rule consumes, uses as a station
  or first tool, or the frontier names. Then repairs as before. `runs/v3-baseline-1` g018 and `runs/m4-2024` g014
  would each have retested `smelt_iron_ingot` instead of the iron-consumer repairs (m4-2024: 27 repair calls).
- Hidden-change guard: `_open_change` and `change_report` (c)/(d) read only `source == "revision"`, so a retest never
  closes a change or counts as the revision. (e) now counts a retest's repeat of the mistaken action and tool
  ("(M by a retest)"); (b) reports a retest's revise calls without failing on them.
- `_run_goal` calls `self._log_unreported(log)` (repair, then retest events) and `self._attach_unreported(record)`
  (`"repairs"`, `"retests"`). S06's `life-queue` (dae44b8) kept both; later rewrites of `_run_goal` must too.
- Advisor (one Opus `Plan`, three consultations): design GO with changes (fixed-point passes, empty evidence, (e) and (b),
  extra tests); final check NOT DONE on a retest-id collision (`x.rev1@w` and `x.rev1@w.rev1@w` both gave
  `x.rev1.retest@w`, so `_retest_demoted` never ended), fixed in f3e73dd; re-check DONE.
- Open: a retest's outcome is reported at the next `_repair_dead_ingredients`, so one still pending when the life ends is
  only in the store. Not yet seen live: the first live life with a false demotion should show `retest started` then
  `match` in the next goals' traces, and `change_report` unchanged.

## S06 goals you give it (done 2026-10-18)

- Contract: 3649653 extends v3.1 (on the advisor's first verdict and the orchestrator's notes): `count` means the
  target for curriculum goals and "how many more" for user/system goals; `goal_label`; the pass never observes when
  idle; one `mark(running, target_count=)`; a user goal's plan, budget and clock (30 s per planned step,
  `life_clock_limits_goal` compares with it), planned with `_excluded_rules()`; the failure-reason order; one ending
  (`_end_goal`); `wanted` keeps queued and done user goals' items; `seq <= bot_seq` skipped; the narration split
  (`run_schemavoyager(goal=)` says "On it", the life says "Done"/"Couldn't get"/"Cancelled", `Episode.finish(announce=)`).
- Merges: 8f479eb `goal-queue`, a78281d `goal-parse`, b8cac25 `chat-bot`, 2337d21 `panel-ui`, 0297fc3 `panel-routes`,
  dae44b8 `life-queue` (rebased over S03's retest-life), ad4d699 `goal-narration`, 62fc748 `chat-live`, dcba09f
  `goal-endings`. Suite on dcba09f: pytest 715, bot unit 212, `node site/dist/trace.test.mjs` and
  `node site/dist/goals.test.mjs` pass (the full offline suite now includes `goals.test.mjs`).
- Re-split: the goal card lives in `site/dist/life.js` (`goalCardHtml`), so `goal-narration` owned `life.js` and
  `trace.test.mjs` instead of `trace.js`. No existing test was edited.
- Live (`runs/v3-m1-live`, instance a): all four checks pass, plus the panel box itself driving a goal to `done`
  (see Log, 01:38 and 01:54). Instance a is down with no lock; the panel on 8767 is stopped; no S06 worktrees or
  `v3/*` branches are left. Not pushed.
- Open (advisor's final review and the run):
  - Without `run`, the panel posts to whichever `goals.jsonl` changed last; a sim or soak life started in the same
    `runs/` (learn mode too) takes the panel's goals.
  - Play mode: S03's `_needed_items` still adds the curriculum frontier (unlike `_unasked_families`), so a retest can
    start, and be announced, for an item nobody asked for.
  - A panel cancel after the last step: `mark(done)` is refused (the queue stays `cancelled`) but chat says "Done: ...".
  - `!status` says "Idle." between goals even when goals are queued.
  - A `BackendError` mid-goal still crashes the life and leaves the goal `running` until `--resume` (as in v2).
  - A 20-iron-ingot goal gets a 13-minute clock (26 steps x 30 s), most of a 15-minute life.
  - The terminal trace prints `FAIL actions=3` for an interrupted episode (`episode_end`).
  - The fake parser needs the number before the item ("4 torches", not "torch 4").
  - Not clicked in a real browser (headless session): `goals.js` was driven by node with real fetch and the page's
    Origin against the live panel. Preemption by a system goal is sim-tested only (no system goal before S07's hunger).
  - The Tester client logs a mineflayer `PartialReadError` (armor-trim registry) on join; harmless.

## S09 building foundations (done 2026-10-18)

- Contract: 0d14636 v3.4a in `docs/v3-interfaces.md` (blueprint format, `place`/`clear`, `scan`, `set_keep_out`, the
  simulator, ownership) and v3.4 in `contracts/bot_api.md` (`/place`, `/clear`, `/scan`, `keep_out`), after the
  advisor's review (GO with changes: an asymmetric example, no `dirt` or scaffold blocks in `FULL_BLOCKS`, a
  `keep_out` box so scaffold never lands in the build, doors last and placed from outside the box since pathfinder
  never opens doors, `REPLACEABLE`/`is_full`/`carries` exported, one message per unconnected group, Claude's palette as
  a list of `{key, block}` for structured output). Kept the plan's space for "any" (the advisor suggested `-`).
- Stubs on `main` (0d14636): `place`/`clear` in `ActionKind`; `Action.pos` (coerced to a tuple of ints, in
  `_V2_ACTION_FIELDS` so recorded LLM keys are unchanged); `LifeBackend.scan`/`set_keep_out`; `blueprint.py`
  constants and signatures.
- Merges: 4f036f4 `blueprint`, bee5841 `build-sim` (architect's merge fix: a cell with no open neighbour is
  `unreachable`, the walks never dig), a685e52 `build-backend`, 4e68d4f `build-bot`, 6350e2d `place-live` (the live
  test and three bot fixes it found). Suite on 6350e2d: pytest 821, bot unit 258, panel and goals pass.
- Live (`runs/s09-place-live`, instance a, `fresh-2024`, peaceful): `node mc/bot/test/v3_place_live.js` ALL PASS
  (16 checks) in runs 4-5 (worktree) and run 6 (`main` 6350e2d, `main`'s bot). Runs 1-3 found: the `overlapsBot`
  margin refusing an off-centre bot; a door's upper half arriving ~0.5 s after `/place` answered; `GoalLookAtBlock`
  unmeetable for blocks with no collision shape (torch, grass). Contract notes updated in bot API v3.4.
- Ops: `screen -S sv2-<inst>-bot -X quit` leaves the node bot alive (orphaned under `login`); stop it by its pid in
  `run/bot-<inst>.pid`. A bot run from a worktree used the `v2-up.sh` command line with `cd <worktree>/mc/bot`.
- c0c4daa (advisor's final check): `Action.pos` must be three integers (`ValueError`), instead of `int()` truncating.
- Instance a is down with no lock; no S09 worktrees or `v3/*` branches are left. Not pushed.
- Advisor (one Opus `Plan`, three consultations): contract GO with changes (above); the live stance bugs diagnosed (the
  `overlapsBot` margin, the unmeetable raycast goal for shapeless blocks); final check DONE.
- Open, for S10 (build goals):
  - A placed log can be chosen as a `#log` mine target (simulator `_candidates`, the bot's `findBlocks`), and a later
    `dig_through` walk can tunnel through the build: M5 needs an `exclusionAreasBreak` around the home box.
  - `set_keep_out` is backend state: set it when a build starts, clear it when the goal ends, and set it again from the
    saved origin after `--resume` or a bot reconnect.
  - Scan the site from one layer below the origin (`oy - 1`, height + 1): layer 0 assumes solid ground, and only a scan
    shows the holes to fill under walls, the door and layer-0 torches.
  - Order inside the house: torches before doors, doors last and placed from outside (`keep_out`); pathfinder can't
    open doors.
  - Count `build_progress` from scans or cell state, not `placed: true` (a retry answers `placed: false`).
  - An `unreachable` costs up to about 5 s; retry it once at most. A roof 5 or more blocks up: with `canDig` off and
    `maxDropDown` 4, the bot gets down only by its own scaffold; watch the descent live. `travel.placed` has scaffold
    names, not positions.
  - `/clear`'s `hazard` also fires next to flowing water (`hazardNear`). Planks dug by hand take ~3.6 s per cell.
  - The plan's space for "any" is kept: `design_blueprint`'s prompt should say `.` for interior air. Claude's palette
    goes over the wire as a list of `{key, block}` (v3.4a, "Claude's form").

## S07 survival (done 2026-10-18)

- Commits on `main`: 036a002 plugins (`mineflayer-pvp` 1.3.2, `mineflayer-armor-manager` 2.0.1, `mineflayer-auto-eat` 3.3.6);
  contract 3ccd26a, 3fda6c0, ac07dd5, ddcab3a; merges f21cfbf `entities-bot`, 2f4d8fc `survival-plugins`, 00839d4 `guard-core`,
  f35e754 `handler-abort`, 83e8045 `guard-fixes`, 2208b23 `python-survival`, 23c1d72 `sim-threats`, 2366eef `smelt-abort`,
  28af805 `guard-wire`, 30c20c4 `difficulty-env`, eceb359 `whitelist`, 45b83ee `death-handling`, 0b290c8 `guard-live`,
  fb068c2 `guard-dead`, 99a44ad `hunger-retry`, cd87652 `no-sprint`, 92c5331 `guard-armed`, daae52e `rest-retry`, 86ee140
  `arm-goal`, c70712a `sprint-peaceful`. Suite on 492176a: pytest 840, bot unit 266, panel all passed.
- Re-splits from the prompt: no handler stopped after `interruptBot()` (a cancelled walk looked `unreachable`, so `/mine`
  walked to the next candidate and remembered a good block as unreachable), so `handler-abort` threads `session.signal` through
  the walk, dig, pickup and station helpers, and `smelt-abort` took `lib/smelt.js` out of `guard-wire`.
- Contract changes (S07's own sections), from the advisor's three reviews and the live runs: a guard abort leaves an open window
  to its handler (mineflayer clicks a closed window's slot numbers in the player inventory); `attachSurvival(bot, eventLog)`
  returns a counted `{hold, onIdleTick}`, auto-eat `offhand: false`, `startAt` 17 while hurt, `eat()` after a hold; a threat is
  a hostile within 5 blocks, or 8 and closing; the guard never fights an owner (orchestrator review), never re-fights a dead
  mob's body, never fights a creeper (any creeper threat backs off), flees when unarmed, ends a fight with a target out of
  reach for 5 s, and only flees from a mob that beat it; `MAX_INTERRUPTS` 4 per step; Python rests after a low-health flight;
  hunger and arming system goals skip a failed item for 900 s; pathing sprints only on peaceful (orchestrator review); stations
  left standing by an abort are remembered and walked back to. Leather armor is a kept item but joins `WHITELIST` only in M3
  (nothing makes leather yet, and its rules drew repair calls).
- Orchestrator notes done: the simulator's `_candidates` KeyError (fixed in `sim-threats`, test first); `wanted` keeps S06's done
  user goals and adds edible foods; owners' hits ignored; no eat during a hold; sprinting kept on peaceful.
- Live, instance b, seed 2024 `fresh-2024`, easy (see Log): `node mc/bot/test/v3_guard_live.js` ALL PASS on fb068c2 (twice) and
  on c70712a. Soaks with day/night on, 30 min, `--keep-going --change scramble --cap-usd 2`:

  | run | code | goals ok (by goal id) | deaths | fights / interrupted steps | false contradictions | spend |
  |---|---|---|---|---|---|---|
  | `runs/v3-soak-1` | 2f1559f | 21/37 (curriculum 21/22; 15 hunger goals for apple, 14 failed) | 0 | 2 / 2 | 0 | $0.12 |
  | `runs/v3-soak-2` | c1f2f37 (bot fb068c2) | 22/29 | 2 (bare-handed fights) | 3 / 4 | 0 | $0.15 |
  | `runs/v3-soak-3` | c70712a | 30/32 | 0 | 2 / 3 | 0 | $0.25 |

  The advisor reviewed the evidence: M2's done-when is met by soak 3.
- Open, for S08 and later:
  - Little combat live: 3 threats in soak 3, all in one goal. The rest, arming, out-of-reach and beaten-mob paths and the
    "three deaths in 10 minutes" stop never ran live (unit-tested only). One seed; the plan's two lives on the final code are
    soak 1 (before the hunger fix) plus soak 3.
  - Hunger is held off, not solved: food stayed 14-20 without sprinting, but nothing edible is obtainable until M3, and a longer
    life reaches the 10-health starvation floor on easy. M3's hunting should feed `vanilla_foods` into the hunger goal.
  - No sprinting off peaceful: travel is about 25% slower and parkour gaps are 2 blocks, not 4, so travel times on easy aren't
    comparable with peaceful runs. S09's `lib/place.js` builds its own `Movements`, which still sprints.
  - The soak-3 fights used a `stone_axe` (an axe counts as armed, so no arming goal ran).
  - `v3_guard_live.js` depends on where the previous life left the bot (one run started on a tree and failed `unreachable`); it
    should teleport the bot to a known spot first.
  - auto-eat's own `autoeat_error` event still logs "No food found." (30 lines in a session); only the `eat()` catch is quiet.
  - In the simulator, a fight's `damage` events are phase `step`, so most interrupted steps are judged `quarantined`, the bot's
    `failed`; neither records a transition.
- Instance b is down, with no lock. No S07 worktrees or branches are left. Not pushed.

## S10 build goals (done 2026-10-18)

- Contract: f3e73d9 v3.4b in `docs/v3-interfaces.md` and bot API v3.4b, after the advisor's review (GO with changes) and
  the build runner's design check (GO with five fixes); 8118c7e adds "doors gathered last". Beyond the S10 prompt:
  protected boxes (sent per request as `protect`, so a reconnect or restart loses nothing; `building`/`built`/`partial`
  in `life_state.json`), `goto` (back to the site), `#door` (any wooden door; crafted from 6 planks of one species),
  `accept` sending a build straight to `awaiting_approval` (new transition `submitted -> awaiting_approval`),
  `agent.pursue`/`planner.make_plan_for` (several needs in one episode), `Episode.act`'s build-kind policy (no explores,
  one in-place retry of `unreachable`/`timeout`), limits 7 x 4 x 7 and 120 cells.
- Merges: 025286d `build-order`, 38284c1 `survey-bot`, fb752e6 `survey-backend`, fc9c725 `blueprint-llm` (architect's
  fix: a worked example in the prompt), 6d9a5a6 `protect-backend`, a6b473e `protect-bot`, 491124f `panel-preview`,
  54126d8 `build-run`, ae6ba95 `doors`, 3496bb1 `build-approval`, 704fa0e `build-life`, d13950c `door-order`;
  4eb011b `scripts/v3-clip.js`. Suite on 1d296c3: pytest 997 (1 skipped), bot unit 312, `trace.test.mjs`,
  `goals.test.mjs` and `build_preview.test.mjs` pass (the last joins the offline suite).
- Re-splits: `build-life` became `build-approval` (parse, approval, `!yes`/`!no`; ran alongside `doors`) and
  `build-life` (running builds, protected state); `build-run` ran alongside wave B, since it needed nothing from it.
  Existing tests edited only where the contract changed them: "can't build" replies (`test_v3_parse_goal.py`,
  `test_v3_life_queue.py`), the transition table copy (`test_v3_goals.py`), the panel's build-goal helper, and one
  reason string (`test_v3_build_run.py`).
- Found by the lanes: `craft #door` blocked on 5 of 8 simulator seeds (planks from mixed log species): fixed by
  `door-order`; the fake hut now builds on seeds 1-8.
- Live (instance a, see Log 04:55-05:16): `runs/v3-build-live` (peaceful) and `runs/v3-build-live-easy` (easy) pass:
  approve in the panel, gather, 72 place steps with 0 failures, `mismatches` `[]` on an independent scan, about 5-6
  minutes and $0.04 each. Clip `runs/v3-build-live/build.mp4` / `build.gif` (`runs/` is git-ignored: not in the
  README yet). Protection: a resumed life's 10-log goal left every house cell unchanged; the bot log shows `protect`
  on its `/mine` requests. Instance a is down with no lock; no S10 worktrees or `v3/*` branches are left. Not pushed.
- Advisor (one Opus `Plan`, four consultations): contract GO with changes; design check GO with five fixes; final
  review: nothing blocks done.
- Open, for S12 and the orchestrator:
  - Must (S12): the bot can't enter a finished house (walks never open doors), so nothing inside is reachable; and
    crafting tables/furnaces aren't kept out of protected boxes. Needs a door-open step or a doorless doorway.
  - Must: the protected list has no cap in Python; the bot refuses more than 32 boxes (400 on every `/mine`), and
    `partial` entries pile up over a long life. Cap or merge in `life.py`.
  - A resumed build that needs only a door, with mixed planks held, can still be blocked on species.
  - Not tried live: chat `!yes`/`!no` (the panel route was used), a real browser click on the preview (curl and
    served 200 only), glass or torch designs (Claude chose wood and a door only).
  - Dirt scaffold pillars stay beside the house (`travel.placed` has names, not positions).
  - Grass under the walls decays to dirt: house checks must use `builder.mismatches`, not identical scans.
  - `!yes` answers "Approved" even if `mark` loses a race with a panel discard.

## S13 mine reach (done 2026-10-18)

- Diagnosis (five Explore reads of the code and eight lives; advisor reviewed): `unreachable` ore mines were 17/27 (s01-explorefast),
  8/34 (preview-2005), 12/23 (v3-baseline-1), 12/37 (v3-baseline), 7/21, 21/41, 16/41 (soaks 1-3, after S07). About 75% were walks cut
  off while still moving or digging: v2 `/mine` gave each of up to 4 candidates 11 s within 36 s (calls of 33.1 s and 36.1 s = three
  or four cut attempts), toward candidates in different directions ("81 blocks walked, ~5 net"), and every cut-off block went into the
  10-minute unreachable memory (12 of 38 baseline targets were mined within 2 blocks later). Contributing: deep ore (dy median -9 to
  -17) reached only by a 3-5 dig staircase per level (`DiggingMovements` refused downward digs). Not the cause: `_explore_toward`
  (no `look` preceded any ore failure), lava (none). Cancelled walks: 0 of the pre-S07 unreachables (no busy/interrupted, `events: []`
  in all 38 baseline ones); after S07 guard cut-ins answer `interrupted`, deaths `quarantined`. Python: `unreachable` retried after
  blind `/explore {}`, which fails in 55-100 ms underground (24 of 31 after a failed mine in the baselines), never `explore up`
  (v3-baseline g020: 12 `mine #log` at y=50, ~4 min).
- Contract: d617135 v3.0g in `contracts/bot_api.md` and `docs/v3-interfaces.md` (plan S0.8), after the advisor's review (`reach`
  top-level; `no_path` read from the last `path_update` status since `goto` resolves on an empty path).
- Merges: 0853c32 `mine-approach` (`lib/approach.js`: 60 s call; attempts end `reached`, `no_path`, `path_timeout`, `ended`, `stalled`,
  `no_approach` after 10 s without getting 1 block closer, or `approaching` when the call's time runs out; only `no_path` in the
  10-minute memory, `approaching` marks nothing; `gotoGoalDetailed`; top-level `reach` with per-attempt `target`, `end`, `ms`,
  `start`, `closest`, `resets`, `think_ms`); ee4f6bb `dig-down` (stock downward dig kept, under the liquid, landing and protect
  rules); d93456a `unreachable-retry` (an `approaching` mine retried at once, once per act; any other `unreachable` mine explores
  toward the target, so `up` from underground, never blind); c03fce7 `think-ms` (live finding: each A* search's time counted once).
  Existing tests changed: `mine_budget.test.js` (old 11 s/36 s and remember-everything expectations), the `movements.test.js`
  downward-dig test (rule inverted). Suite on c03fce7: pytest 997 (1 skipped), bot unit 313, panel and goals pass.
- Live, instance b, peaceful (as v3-baseline-1), `--change scramble --keep-going --cap-usd 2`, bot and Python from `main` with the fix:

  | measure | `runs/v3-reach-2024` (30 min) | `runs/v3-reach-31337` (20 min) | `runs/v3-baseline-1` (30 min, seed 2024) |
  |---|---|---|---|
  | ore mines `unreachable` (iron) | 1/44 (1/33) | 0/13 (0/7) | 12/23 (10/15) |
  | longest walk before an `unreachable` | 41.4 | none | 90.8 |
  | most `unreachable` ore mines in a row in a goal | 1 | 0 | 5 (g014, g018) |
  | iron goals | 11/12 ok: iron_ingot x2, bucket, iron_pickaxe, sword, axe, shovel, chestplate, helmet, shield, shears; iron_boots cut by the clock | 4/4: iron_ingot x2, iron_boots, iron_leggings | 4/5 (iron_pickaxe g018 failed at the 300 s clock) |
  | goals ok, spend | 31/32, $0.25 | 45/45, $0.23 | 54/55, $0.25 |

  `reach` ends over every `/mine` attempt: 2024 reached 77, stalled 14 (10 on `#log`), no_approach 2, path_timeout 1, approaching 1
  (retried at once, ok); 31337 reached 21, no_approach 1; no `no_path` or `ended`. Ok ore mines take a median 12.9 s (baseline 10.2).
  After an `unreachable`: 1 immediate retry, 2 `explore up` (g017 failed and the retry then worked; g028 climbed y 66 to 85 in about
  100 s), 0 blind explores. No damage, no pit-like drops, no goal with 6 failed steps in a row.
  `node mc/bot/test/v2_iron.js` on a fresh `world-2024`: GATE PASS, iron_pickaxe in 238 s, every check MATCH (`logs/s13-v2-iron-2.log`).
- Ops: `world-restore.sh` needs `SV2_SEED` too. Without it the env falls back to `sv2/.env`'s seed and restarts on that level:
  the first iron run (`logs/s13-v2-iron.log`) ran on the stale `world-1001` (bot stocked, `planks2` on) and failed at planks. Both
  lives ran on the fresh snapshots copied into the `world-1001` folder (server-b's `world-1001` now holds fresh-31337's content
  after a life).
- Advisor (one Opus `Plan`, four consultations): diagnosis GO with changes (log why each attempt ended, only `no_path` remembered,
  no resume target, `_explore_toward` instead of blind-then-up, `lib/approach.js` and `gotoGoalDetailed` for containment); contract
  GO with two changes (above); final check DONE (re-parsed the runs: longest run of `unreachable` ore mines 1, 0, 5 in baseline-1).
- Instance b is down with no lock; no S13 worktrees or `v3/*` branches are left. Not pushed.
- Open:
  - Peaceful only: dig-down shafts and the 60 s calls haven't met hostile mobs. Run one easy life (S08's first live run will).
  - The remaining reach failures moved to `#log`: 10 of the 14 `stalled` attempts in `v3-reach-2024` (start 18.6, closest 16.6),
    one `unreachable` each in g017 and g028. Surface pathing, a separate item from ore depth.
  - `explore up` took about 100 s once (g028, y 66 to 85), a third of a curriculum goal's clock; consider a cap.
  - Ore mines are slower: median 12.9 s against 10.2 s (the trade for 1 `unreachable` in 44 against 12 in 23).
  - `v3-reach-31337` is thin (13 ore mines; the curriculum ran out after g018), and `v2_iron.js` passed once (one run, not a rate).
  - `no_path` and `ended` never happened live, so the 10-minute memory is now nearly unused; `path_timeout` once.
  - Report only, not investigated: v3-baseline g017's missed furnace drop (`furnace_retrieved: false`, no dig warning).

## S14 polish (done 2026-10-18)

- Contract v3.0h (`docs/v3-interfaces.md`, `contracts/bot_api.md`), plan S0.9; advisor's review: GO with changes.
- Merges: 77ab8ee `backend-health` (every lost-bot condition is a `BackendError`, `connected()`), e9b379f `life-robust`
  (wait up to 120 s, `interrupted` `bot`, requeue, `bot_down`, `live.json`), 2b3fd71 `panel-live` (the live run,
  `ambiguous_run`, the run line, `?run=`), 899f7e3 `door-species`, b9ff10b `trace-interrupted`, 8cf585a `life-polish`
  (play-mode needed items, late cancel, `!status`), 52d3d42 `bot-polish` (sprintAllowed, quiet auto-eat, guard arena),
  32091e2 `build-scaffold`, 73474f9 `readme`, then after the first live build 8d39016 `scaffold-record` and 77b4bd1
  `scaffold-ring`. Architect's merge fixes: the path in the `URLError` message (a flaky test), the guard arena's
  cleanup height, four bot unit tests' empty travel. `life.py` changed in e9b379f and 8cf585a (logged for S08).
- Suite on fed3117 (with S08's merges): pytest 1147 (1 skipped), bot unit 356, trace, goals and preview pass.
- Live (instance a, `runs/v3-polish-live`, `NOTES.md` there): item 1 and item 2 checked live (bot killed mid-`/mine`,
  `RemoteDisconnected` handled, `bot_back` 22 s, u001 requeued and `done`; panel requests without `run` went to the
  live life); item 10 failed on u002 (tower recorded only partly), fixed, passed on u003; item 9's guard test ALL PASS.
  $0.14 in all. README updated; the build gif isn't committed (no user approval), the README points to `scripts/v3-clip.js`.
- Advisor (one Opus `Plan`, three consultations): design GO with changes; scaffold miss: do both (recorder and ring);
  final review: GO.
- Open, for S12 and the orchestrator:
  - A wedged bot loops: if every pass-level call gets a `409` past 60 s while `connected()` is True, the life waits,
    requeues and retries until a stop check (pass-level losses don't count toward the 3). Suggested: count consecutive
    near-0 s losses and stop with `bot_stuck`.
  - `_post`'s `HTTPError` branch reads the body with `error.read()`, which can itself raise `ConnectionResetError`
    unwrapped. One-line fix, unlikely to fire.
  - u002's dirt tower stays in instance a's world until the next `world-restore.sh fresh-2024`.
  - Not tried live (unit-tested): door planks of mixed species, the chat late-cancel and `!status` lines,
    `ambiguous_run` and `?run=` in a real browser.
  - A stale `live.json` after a SIGKILL looks alive if its pid is reused (at worst an `ambiguous_run`; `--resume`
    rewrites it).
  - v3.0h gaps: pillars from an earlier, requeued attempt and scaffold placed while gathering aren't cleared.
  - The README gif still waits for the user's approval.

## S08 combat and hunting (done 2026-10-18)

- Contract: de8c337 v3.3 in `docs/v3-interfaces.md` and `contracts/bot_api.md` (kill rules, `/attack`, mobs in `/look`, `/follow`, kill and
  guard goals), after the advisor's review (GO with changes, all applied: `escaped` never blocks a rule, the quarry set when a candidate is
  picked, kills counted by `fight_end` `id`, only baby animals skipped, the sprint copy restored, seen mobs and `cooked_x` sent to the priors
  because the real schema only returns kill rules for mobs it is asked about, runners in `goal_runners.py`, `bread` dropped). Stubs: `kill` in
  `ActionKind`, `Action.describe`, `Rule.subject`/`yields` for kill, `Status.follow`, `LifeBackend.look(mobs=)`/`follow`/`stop_follow`.
- Deviation from the prompt: kill rules keep integer `drops` (the planner expects each range's low end; items whose low end is 0 aren't
  planned) plus `allowed` ranges, the shape stochastic mine rules already had, instead of ranges in `drops`. `kind_of` makes every kill rule
  stochastic, so a kill step is `stochastic`, `failed` or `quarantined`, never `mismatch`.
- Merges: 94c0a1e `kill-rules`, 2e15be9 `kill-llm`, 76692c9 `kill-sim`, a5f7966 `attack-bot`; eb518fb `parse-kill-guard`, d671c09
  `kill-backend`, c159ded `hunt-food`, fed3117 `follow-bot` (with v3.3j: fights sprint, ranged mobs 8 s); 796807f `goal-runners`; live fixes
  a261840 `follow-tight` and 4d332d2 `hunt-live`. Re-splits from the prompt: `hunt-goals` became `hunt-food` (group 2), `parse-kill-guard`
  (group 2) and `goal-runners` (group 3, new `goal_runners.py`); the armed guard tactics went into `follow-bot`. Existing tests changed: two
  that asserted the old "can't hunt" replies (`test_v3_parse_goal.py`, `test_v3_life_queue.py`), three LLM test doubles gained `mobs=()`
  (`test_v2_life.py`, `test_v2_repair.py`), one follow test (moving during its no-re-set tick). Suite on 4d332d2: pytest 1170 (1 skipped),
  bot unit 357, panel pass.
- Live, instance b, `fresh-2024`, easy, `BOT_OWNERS=Otopo,Tester`, play-mode life with the live LLM, `runs/v3-m3-live`
  (`node mc/bot/test/v3_hunt_live.js`, `logs/s08-hunt-live-final2.log`: one invocation, ALL PASS, bot a261840, Python 7c415fb..a261840):
  1. "get me 2 cooked beef" with 4 summoned cows: u009 done; two kills `stochastic` "drops in range" (beef 1 + leather 1, beef 1 +
     leather 2), smelt `match`, 0 mismatches.
  2. "!goal kill 3 zombies": u010 done, `kills` 3, three distinct `fight_end` killed ids.
  3. "!goal guard me": follows (distance 2.9 after a 10-block walk), defends (`player_near` and `player_attacked` threats, 3 zombies killed,
     Tester at 20 health), rejoins (3.3), `!stop` cancels u011 and `follow` goes null.
  Whole run: 11 user goals, 0 deaths, 0 mismatches, 4 kill checks in range, 3 smelt matches. Claude's priors: `kill_cow` beef 1-3, leather
  0-2; `kill_zombie` rotten_flesh 0-2 plus carrot, potato, iron_ingot 0-1 (plans nothing).
  `runs/v3-m3-live-try1`: the script's first arena (x ≈ 1000) was outside the 512-block world border; the border killed the bot three times
  and the life stopped on `deaths`. Checks 1-2 still passed there against natural mobs near spawn.
- Learn-mode easy life `runs/v3-m3-soak` (fresh `fresh-2024`, easy, daylight on, `--keep-going --change scramble --minutes 20 --cap-usd 2`,
  code 4d332d2): `wall_clock`, 28 goals, 25 ok, $0.25, 0 deaths, 1 guard cut-in (a `mine`, retried), 3 mismatches, all at the fired
  scramble changes (planks2, coal_tool, stick2). S13's open item: `/mine` reach on easy ended `reached` 64 times and `stalled` twice, with
  no `unreachable` and no hazard death. No hunting: the only mob in view at a prior call was a skeleton (`kill_skeleton`, bone and arrow
  0-2), so no `kill_cow` was learned. Food stayed at 8 or more, so no hunger goal ran. Two `iron_ingot` goals failed on smelt
  `craft_failed` (`used_furnace: false`, at y 57); a later `bucket` smelted fine (not investigated).
- After the live runs: 7d94718 `kill-polish` (advisor follow-ups: kill targets are living kinds only, so "kill 3 boats" is no creature;
  the panels label a kill rule "kill cow → beef 1-3, leather 0-2" instead of "craft undefined"). Suite on 7d94718: pytest 1173
  (1 skipped), bot unit 357, panel pass.
- Advisor (one Opus `Plan`, two consultations): contract GO with changes (above); final check DONE.
- Instance b is down, with no lock. No S08 worktrees or `v3/*` branches are left. Not pushed.
- Open:
  - Mobs join the priors only when they're in view (16 blocks) at a prior call between goals. A learn-mode life can go a whole session
    without `kill_cow` (the soak did), and then neither the hunger goal nor the curriculum's cooked meats can be planned. Fix: record
    `observation.entities` on every observation, not only at prior time. Also, a drop asked before its mob is seen goes out without
    `kill` in the schema.
  - Hunting in a curriculum or hunger goal is only proven by user goals so far (live check 1); hunger itself never fired live.
  - Kill-goal steps aren't rule evidence (only planned kill steps are). Leather and leather armor stay unplannable (low end 0).
  - `kill <mob>` hunts beyond view by exploring blind and then toward `look` sightings. In try1 a daytime zombie hunt walked about 260
    blocks, and the guard's kills finished the goal.
  - A guard kill polled just after a kill goal starts counts toward it (minor).
  - Deferred: a datapack changing a mob's loot table (a new `CHANGE_STEPS` hidden change; with kill rules always stochastic, noticing it
    needs out-of-range counts M3 doesn't keep), breaking line of sight against skeletons, `/attack` ignores `protect` (its walks never dig),
    the simulator's creeper kill answers `not_found`.
