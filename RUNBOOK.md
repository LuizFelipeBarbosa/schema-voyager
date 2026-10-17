# Demo runbook

The 5-minute talk as recorded segments: for each, the exact command and what
to point at. Run everything from the repository root. Only one thing may drive
the server at a time: the bot takes one action at a time, and every episode
rebuilds the shared arena.

The numbers quoted here are in `results/`: the holdout from `runs/mc-demo-3`
(code `b73b04c`; B's layout 6 rerun at `33d588c`), trained in `runs/mc-demo-2`
(code `e55be7f`).

## Before recording

1. Server and bot are up, and the bot runs the current code (restart it after
   any `git pull`):

   ```bash
   docker ps --filter name=schema-voyager-mc --format '{{.Status}}'   # Up ...
   curl -s localhost:3000/health                                      # "connected":true
   ```

   If not: `scripts/up.sh`, then `npm --prefix mc/bot start` in its own terminal.

2. Trace panel server, in its own terminal: `.venv/bin/schema-voyager panel`

3. Pick a camera, and keep the trace panel or the terminal next to it:

   | Camera | How |
   | --- | --- |
   | Browser viewer and trace panel in one window | http://127.0.0.1:8765/site/dist/trace.html?viewer=1 follows whichever episode starts next. If the viewer shows empty sky, hard-reload with Cmd+Shift+R. |
   | Browser viewer alone | http://localhost:3007 (third person, follows the bot) |
   | Minecraft 1.21.1 client | Multiplayer, `localhost` (offline-mode server), then `.venv/bin/schema-voyager spectate <your-name>` puts you in spectator mode above the arena |

   The bot says every trace line in chat: each plan, each step and its
   result, the mismatch, every ranked hypothesis, and what it learned. Only
   the Minecraft client shows the chat; press T to keep it open while
   recording. With the browser viewer, the same lines are in the terminal, and
   the panel's store card shows the learning lines.

   Other tabs: the field guide at http://127.0.0.1:8765/site/dist/index.html,
   and the recorded B+ episode (segment 5).

4. Offline check (10 s, no server, no API calls):

   ```bash
   .venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
     --store results/store_D_2_after_training.json --backend sim --llm replay --run-dir runs/check-sim
   ```

   It should end with `SUCCESS actions=17` after one recorded revision call.

## The segments

| Time | Segment | Camera |
| --- | --- | --- |
| 0:00–0:30 | 0. Why: skills don't say which facts they rely on | Field guide, top |
| 0:30–1:00 | 1. Empty store, then prior rules | Terminal |
| 1:00–1:40 | 2. D on T1, and the store after one episode | Viewer or client, and terminal |
| 1:40–2:00 | 3. The store after training | Terminal |
| 2:00–3:15 | 4. D on H3: mismatch, revision, recovery (the headline) | Viewer or client, and trace panel |
| 3:15–3:50 | 5. B+ on the same H3 | Trace panel (recorded) |
| 3:50–4:10 | 6. D's second visit | Client chat, or viewer and terminal |
| 4:10–5:00 | 7. The results table, and the limits | Field guide or terminal |

Episodes run longer than their slot on the real server (T1 about 20 s, D's
H3 about 50 s, the second visit about 40 s, B+ about 2 minutes). Record them
whole and speed them up in the edit.

### 0. Why (0:00–0:30)

Field guide, top. Say: Voyager-style agents keep skills, action sequences
that worked. A skill does not record which facts about the world it relied
on, so when the world changes quietly it keeps replaying.

### 1. Empty store, then prior rules (0:30–1:00)

```bash
.venv/bin/schema-voyager episode --system D --task t1 --layout 1 --llm replay --run-dir runs/rec-t1
```

`--llm replay` reuses Claude's recorded prior-rule response from the training
run, so the rules match `results/store_D_0_prior.json`; drop it for a fresh
call (about 2k tokens). Point at, before the episode starts:

- `Store: empty`, then the 9 rules Claude proposed, one per action, each a
  `hypothesis` with 0 confirmations.
- `mine_coal_ore`: vanilla Minecraft says any wooden or better pickaxe drops
  coal. That is the promise H3 will break.

### 2. D on T1, and the store after one episode (1:00–1:40)

Same command, keep recording. T1 is a wooden pickaxe from an empty inventory.
Point at:

- Each step's prediction matching what the server did (`ok` in the terminal
  and in chat).
- Chat: `Learned skill_wooden_pickaxe: relies on mine_oak_log, …`, the skill
  and the rules it cites.
- `Store after the episode`: every rule it used has confirmations from 1
  episode and is still a `hypothesis`. Certification needs at least 2
  confirmations from at least 2 episodes, with none contradicting it.

### 3. The store after training (1:40–2:00)

```bash
.venv/bin/schema-voyager store results/store_D_2_after_training.json
```

Point at: after T1–T3 on layouts 1 and 2, all 9 rules are `certified`
(`mine_coal_ore`: 2 confirmations from 2 episodes), and `skill_torch` cites
`mine_coal_ore`.

### 4. D on H3: the headline (2:00–3:15)

```bash
.venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
  --store results/store_D_2_after_training.json --run-dir runs/rec-h3
```

About 50 s, one Claude call. Say first: H3 is 4 torches again, in a world
where a datapack makes coal ore drop coal only for a stone-tier pickaxe.
Nobody tells the agent, and the client can't see the loot table. Point at, in
order:

1. Plan 1 reuses `skill_wooden_pickaxe` and `skill_torch`; every step is on a
   certified rule.
2. Step 10, `mine coal_ore with wooden_pickaxe`: predicted `coal ×1`, the
   server dropped nothing. MISMATCH, at the exact step.
3. Store: `mine_coal_ore` demoted in this world only; `skill_torch` flagged
   because it cites it.
4. One Claude call returns ranked revisions; stone pickaxe comes first. Any
   revision that would still predict the drop that just failed is rejected.
   Claude returned one revision on layout 6 in the results run and three on
   layout 8, two of them rejected.
5. Plan 2 tests the hypothesis (`HYPOTHESIS` in the panel, a yellow `?` in
   the terminal). Step 15, `mine coal_ore with stone_pickaxe`, drops
   `coal ×1`; then 4 torches, at step 17.
6. Chat: `Kept for this world: mine_coal_ore.rev1 (coal_ore needs
   stone_pickaxe), confirmed once`.

The episode's first line shows its world, `world=w01d3c` for `runs/rec-h3`.
Segment 6 needs it.

### 5. B+ on the same H3 (3:15–3:50)

The recorded real-server episode, at 4×:
http://127.0.0.1:8765/site/dist/trace.html?file=results/episodes/B%2B_h3_L6.jsonl&autoplay=1&speed=4
(`%2B` is the `+`).

Point at:

- Same model, same actions, same 5 coal ores. After a failed plan it gets the
  same "rules may differ from vanilla" sentence as D's revision prompt.
- Its feedback reads `mine coal_ore with wooden_pickaxe (inventory change:
  none)` after each coal mine. Claude's plan 3: "Mining coal_ore with the
  wooden pickaxe gave no coal, so I'll try mining it again".
- It mines all 5 coal ores with the wooden pickaxe, then plan 4 explores for
  more coal "to mine it with the wooden pickaxe". It fails after 4 Claude
  calls (7,065 tokens).

Live instead (about 2 minutes and 4 Claude calls; the outcome varies):

```bash
.venv/bin/schema-voyager episode --system B+ --task h3 --layout 6 \
  --skills results/skills_B+_after_training.json --run-dir runs/rec-bplus
```

### 6. D's second visit (3:50–4:10)

D back in the world from segment 4, with the store it left there:

```bash
.venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
  --store runs/rec-h3/store_D_after_episode.json --world w01d3c --run-dir runs/rec-h3
```

About 40 s; play it at 2×. Point at:

- No mismatch: plan 1 goes straight to the stone pickaxe.
- `CLAUDE CALLS 0` in the panel, `llm_tokens=0` in the terminal.
- Chat: `Certified: mine_coal_ore.rev1 (2 episodes)`. The revision is now
  certified for this world.

If segment 4 used another run directory, take the world id from its first
line (`world=w…`) and use that directory here too.

### 7. The results table, and the limits (4:10–5:00)

Field guide, "The scoreboard", or in the terminal:

```bash
.venv/bin/schema-voyager report results
```

| System | H3 success | LLM tokens / episode | Claude calls | Repeated failures | Actions after the surprise |
| --- | --- | --- | --- | --- | --- |
| D | 3/3 | 1,794 ± 115 | 1 | 0 | 0 |
| B | 0/3 | 6,536 ± 14 | 4 | 6.3 ± 2.3 | 1 |
| B+ | 0/3 | 7,034 ± 45 | 4 | 7.7 ± 1.5 | 2 |
| D, second visit | 1/1 | 0 | 0 | 0 | – |

Point at: D recovers every time, with one Claude call and without repeating
the failed action; the baselines spend nearly 4× the tokens. In the earlier
sweep (`runs/mc-demo-2`), B and B+ each recovered on 1 of 3 layouts, so say
"the baselines sometimes work it out", not "never". Then the limits, out
loud (see Q&A).

## Fallback: the simulator

If the server or the bot is down, segments 1, 2, 3, 4 and 6 run the same
code against the simulator. Add `--backend sim --llm replay` to each episode
command, and say on screen that it is the simulator. D's revision replays
the recorded Claude response with its original token count, and the second
visit makes no call:

```bash
.venv/bin/schema-voyager episode --system D --task t1 --layout 1 --backend sim --llm replay --run-dir runs/sim-t1
.venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
  --store results/store_D_2_after_training.json --backend sim --llm replay --run-dir runs/sim-h3
.venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
  --store runs/sim-h3/store_D_after_episode.json --world waf52c --backend sim --llm replay --run-dir runs/sim-h3
```

There are no recorded baseline responses for the simulator, so segment 5
always uses the recorded real-server trace.

| Problem | Do this |
| --- | --- |
| Server or bot down | Recorded real-server runs in the panel, said to be recordings: http://127.0.0.1:8765/site/dist/trace.html?file=results/episodes/D_h3_L6.jsonl&autoplay=1 and `…D_h3_L6_visit2.jsonl…`; or the simulator commands above. |
| Anthropic API slow or down | Add `--llm replay` to the H3 command. The revision is recorded, so it replays a real response and its token count. |
| Panel shows an older episode | Pick "Follow the newest episode" in its menu; it switches when the next trace starts. |
| Viewer empty | Cmd+Shift+R. |
| Chat lags behind the bot | Expected in bursts: the bot paces chat to about one line a second after 8 at once, so vanilla does not kick it for spam. |

## Q&A

- **The claim.** It notices at the exact step, revises with one LLM call,
  and recovers in the same episode. Not "it constantly learns". The second
  visit shows it doesn't repeat the mistake in a world it has seen.
- **Limits, stated up front.** 3 holdout layouts in a controlled arena,
  n=3 per system. D's one-time prior-rule call (about 2k tokens) isn't
  charged to any episode.
- **Does it generalize?** The checking mechanism doesn't depend on the
  world: the loot rule and the drop reading work anywhere. Natural terrain
  needs better navigation, which is future work and a natural fit with
  Voyager's skill library; the v2 track is exploring it.
- **Is the baseline fair?** B and B+ use the same model, the same actions,
  the same coal and the same "rules may differ" sentence. B+ even saw
  "inventory change: none" and repeated the mine.
- **Did you try Crusoe's models?** Yes: `SCHEMA_VOYAGER_PROVIDER=crusoe`
  runs every system on Crusoe Managed Inference (Nemotron 3 Super). Its
  revisions matched Claude's: the stone-pickaxe rule ranked first in 5 of 5
  trials. Its prior rules, written in one call before training, were usable
  in 1 of 4 samples. One left the crafting table out of the pickaxe recipes,
  one had the stone pickaxe consume stone instead of cobblestone, and one
  returned a single rule. A wrong precondition makes the action fail
  outright, and D treats a failure as tolerant, so it replans the same plan:
  today D revises wrong effects, not wrong preconditions. That is the next
  feature. On the one usable sample, D trained exactly as it does with
  Claude. Logs: `runs/nemotron-eval/`.

## After a final sweep

```bash
.venv/bin/schema-voyager export runs/<run> --note "anything a reader must know, e.g. a rerun"
.venv/bin/python -m pytest -q
git add results site/dist/results.js && git commit -m "Record final results from runs/<run>"
```

`export` copies the table, traces and store snapshots to `results/` and
updates the numbers on the field guide. `demo --from runs/<run>
--second-visit` reruns only the holdout from a trained run.
