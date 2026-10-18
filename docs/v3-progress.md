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
| S02 fix the stuck loops | not started | b | |
| S03 retest demoted rules | not started | none | |
| S04 full inventory | not started | a | |
| S05 contracts for M1 and M2 | running | none | Contracts v3.1 and v3.2, stubs on main |
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
