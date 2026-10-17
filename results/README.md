# Results

Copied from `runs/mc-demo-3` (code commit `b73b04c`) by `schema-voyager export` on 2026-10-17 15:28.
Its training episodes, the store snapshots up to `store_D_2_after_training.json` and the `*_after_training` skill libraries come from `runs/mc-demo-2` (code commit `e55be7f`); the holdout started from that trained state.

Note: H3 rerun after the layout-8 bot fixes, from mc-demo-2's training. B's layout-6 episode was rerun in the same world at commit 33d588c: in the first attempt a wedged bot's skip list hid the remaining coal ore, and the superseded trace is kept in runs/mc-demo-3/superseded/. In mc-demo-2 the baselines each recovered on 1 of 3 layouts; here on none.

- `results.md`: the results table; `results.json`: one record per episode
- `episodes/*.jsonl`: every plan, step, prediction, observation, contradiction and revision; replay them with `schema-voyager panel`
- `store_D_*.json`: D's schema store before training, after one episode, after training, after the holdout, and after the second visit when there was one
- `skills_*.json`: the baselines' skill libraries
