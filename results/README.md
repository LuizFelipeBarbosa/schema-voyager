# Results

Copied from `runs/mc-demo-2` (code commit `e55be7f`) by `schema-voyager export` on 2026-10-17 14:19.

Note: Layout 8 rerun pending: a bot glitch hit D's episode there (repeated craft_failed, then lost inventory).

- `results.md`: the results table; `results.json`: one record per episode
- `episodes/*.jsonl`: every plan, step, prediction, observation, contradiction and revision; replay them with `schema-voyager panel`
- `store_D_*.json`: D's schema store before training, after one episode, after training, and after the holdout
- `skills_*.json`: the baselines' skill libraries
