# Demo runbook

The 5-minute talk, mapped to exact commands. Run everything from the
repository root. Only one thing may drive the server at a time: the bot takes
one action at a time, and every episode rebuilds the shared arena.

## 15 minutes before

1. Server and bot are up:

   ```bash
   docker ps --filter name=schema-voyager-mc --format '{{.Status}}'   # Up ...
   curl -s localhost:3000/health                                      # "connected":true
   ```

   If not: `scripts/up.sh`, then `npm --prefix mc/bot start` in its own terminal.

2. Trace panel server, in its own terminal:

   ```bash
   .venv/bin/schema-voyager panel
   ```

3. Open three browser tabs:

   | Tab | URL | Used for |
   | --- | --- | --- |
   | Live | http://127.0.0.1:8765/site/dist/trace.html?viewer=1&follow=newest | 3D viewer and trace panel in one window; follows whatever episode starts next |
   | Guide | http://127.0.0.1:8765/site/dist/index.html | Intro, how it works, results |
   | Baseline | http://127.0.0.1:8765/site/dist/trace.html?file=results/episodes/B%2B_h3_L6.jsonl | Recorded B+ episode on the same layout (`%2B` is the `+`) |

   If the viewer shows empty sky, hard-reload with Cmd+Shift+R.

4. Check the offline path still works (10 s, no server, no API calls):

   ```bash
   .venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
     --store results/store_D_2_after_training.json --backend sim --llm replay --run-dir runs/check-sim
   ```

   It should end with `SUCCESS actions=17` and one LLM revision.

5. Optional, so the viewer shows the arena while you talk (this drives the
   server, so only when nothing else is running): `.venv/bin/schema-voyager arena --layout 6 --task h3`

## The talk

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:40 | Guide, top | Voyager-style agents keep skills: action sequences that worked. A skill does not say which facts about the world it relied on, so when the world changes quietly it keeps replaying. |
| 0:40–1:30 | Guide, "The loop": model, verify, compile | SchemaVoyager stores rules as operators with exact predicted effects. It checks each step against what the server decided, demotes a rule the moment it is contradicted, flags the skills that cite it, and asks Claude for ranked alternatives that must explain the counterexample. |
| 1:30–3:15 | Live tab, then run the live command below | Training on T1–T3 certified the rules. Now H3: craft 4 torches in a world where coal ore only drops coal for a stone pickaxe. Nobody tells the agent. |
| 3:15–4:00 | Baseline tab, play at 4× | Same model, same actions, same hint that rules may differ. B+ even sees "inventory change: none" and still retries the wooden pickaxe. |
| 4:00–5:00 | Guide, "The scoreboard" | The numbers, and the caveats below. |

Live command (about 50 s on the real server, one Claude call):

```bash
.venv/bin/schema-voyager episode --system D --task h3 --layout 6 \
  --store results/store_D_2_after_training.json --run-dir runs/live-$(date +%H%M)
```

Point at, in order:

1. Plan 1 reuses `skill_wooden_pickaxe` and `skill_torch`; every step is on a certified rule.
2. Step 10, `mine coal_ore with wooden_pickaxe`: predicted coal ×1, the server dropped nothing. Red.
3. Schema store: `mine_coal_ore` demoted in this world only, `skill_torch` flagged.
4. Claude's ranked revisions: stone pickaxe first; the one that contradicts the counterexample is rejected.
5. Plan 2 tests the hypothesis (`HYPOTHESIS` tag), step 15 confirms it, 4 torches.

Caveats to say out loud:

- 3 holdout layouts per system and one changed rule; this is a demonstration, not a benchmark.
- With the same hint, the baselines sometimes recover too (see the success column). D recovers
  every time, with one Claude call per episode instead of four, and without repeating the failed action.
- D's one-time prior-rule call before training (about 2k tokens) is not charged to any episode.

## If something breaks

| Problem | Do this |
| --- | --- |
| Server or bot down | Replay the recorded run: http://127.0.0.1:8765/site/dist/trace.html?file=results/episodes/D_h3_L6.jsonl&autoplay=1 and say it is a recorded real-server run. |
| Anthropic API slow or down | Add `--llm replay` to the live command. The layout-6 revision from the results store is recorded, so it replays the same response and token count. |
| Both down | `--backend sim --llm replay` (step 4 above), and say it is the simulator. |
| Panel shows an older episode | Pick "Follow the newest episode" in its menu; it switches when the next trace starts. |
| Viewer empty | Cmd+Shift+R in the Live tab. |

## Recording a backup video

Open the Live tab full screen, start a screen recording (Cmd+Shift+5), then run
the live command. D's episode takes about 50 s; B's or B+'s about 60–80 s:

```bash
.venv/bin/schema-voyager episode --system B+ --task h3 --layout 6 \
  --skills results/skills_B+_after_training.json --run-dir runs/live-$(date +%H%M)
```

## After a final sweep

```bash
.venv/bin/schema-voyager export runs/<run> --note "anything a reader must know, e.g. a rerun"
.venv/bin/python -m pytest -q
git add results site/dist/results.js && git commit -m "Record final results from runs/<run>"
```

`export` copies the table, traces and store snapshots to `results/` and
updates the numbers on the field guide.
