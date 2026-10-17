| System | Task | Success | LLM tokens | LLM calls | Actions | Blocks walked | Repeated failures | Actions after contradiction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B | h3 | 0/3 | 6536 ± 14 | 4.0 ± 0.0 | 19.3 ± 5.8 | 39.1 ± 35.5 | 6.3 ± 2.3 | 1.0 ± 0.0 |
| B+ | h3 | 0/3 | 7034 ± 45 | 4.0 ± 0.0 | 23.0 ± 5.3 | 59.4 ± 41.1 | 7.7 ± 1.5 | 2.0 ± 0.0 |
| D | h3 | 3/3 | 1794 ± 115 | 1.0 ± 0.0 | 17.0 ± 0.0 | 16.5 ± 0.5 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| D | h3 visit2 | 1/1 | 0.0 | 0.0 | 16.0 | 14.8 | 0.0 | - |
| B | t1 | 2/2 | 1658 ± 721 | 1.5 ± 0.7 | 9.0 ± 2.8 | 7.4 ± 0.5 | 0.0 ± 0.0 | - |
| B+ | t1 | 2/2 | 2486 ± 1852 | 2.0 ± 1.4 | 10.5 ± 3.5 | 9.6 ± 2.5 | 0.5 ± 0.7 | - |
| D | t1 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 9.0 ± 0.0 | 5.7 ± 1.3 | 0.0 ± 0.0 | - |
| B | t2 | 2/2 | 4007 ± 3216 | 2.5 ± 2.1 | 14.5 ± 3.5 | 30.3 ± 10.2 | 0.5 ± 0.7 | 3.0 |
| B+ | t2 | 2/2 | 3365 ± 2223 | 2.0 ± 1.4 | 13.0 ± 1.4 | 23.2 ± 6.5 | 0.0 ± 0.0 | - |
| D | t2 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 15.5 ± 2.1 | 36.5 ± 29.0 | 0.0 ± 0.0 | - |
| B | t3 | 2/2 | 1772 ± 13 | 1.0 ± 0.0 | 9.0 ± 0.0 | 16.5 ± 0.9 | 0.0 ± 0.0 | - |
| B+ | t3 | 2/2 | 3802 ± 223 | 2.0 ± 0.0 | 11.5 ± 0.7 | 19.4 ± 2.4 | 0.0 ± 0.0 | - |
| D | t3 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 11.0 ± 0.0 | 14.8 ± 0.6 | 0.0 ± 0.0 | - |

Mean ± sd over layouts. LLM tokens are input plus output from API usage, per episode. D's one-time prior-rule call before training is not charged to any episode. After a failed plan, B and B+ get the same "rules may differ from vanilla" sentence as D's revision prompt. Repeated failures count any identical action that failed or mined a block that dropped nothing more than once, including the shared policy's retries after an explore (`not_found`, `unreachable`, `craft_failed`, `dig_failed`, `timeout`); every attempt and explore counts as an action.

A `visit2` row is D back in a holdout world it has already seen, with the store it left there.
