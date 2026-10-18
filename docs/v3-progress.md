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
| S07 survival | running | b | Lanes: entities-bot, guard-core, survival-plugins, handler-abort, then guard-wire, smelt-abort, python-survival, sim-threats, then difficulty-env, whitelist, death-handling (after S06 life-queue), live |
| S08 combat and hunting | not started | b | |
| S09 building foundations | running | a | Contract v3.4a and bot API v3.4 (0d14636); lanes blueprint, build-bot, build-sim, build-backend, then place-live |
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
