| System | Task | Success | LLM tokens | LLM calls | Actions | Blocks walked | Repeated failures | Actions after contradiction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B | h3 | 1/3 | 6635 ± 107 | 4.0 ± 0.0 | 18.0 ± 2.0 | 24.0 ± 12.3 | 5.3 ± 1.5 | 1.0 ± 0.0 |
| B+ | h3 | 1/3 | 7160 ± 359 | 4.0 ± 0.0 | 21.7 ± 4.2 | 54.6 ± 52.5 | 5.7 ± 2.9 | 2.0 ± 0.0 |
| D | h3 | 3/3 | 1852 ± 7 | 1.0 ± 0.0 | 28.7 ± 20.2 | 49.0 ± 54.2 | 5.7 ± 9.8 | 0.0 ± 0.0 |
| B | t1 | 2/2 | 1658 ± 721 | 1.5 ± 0.7 | 9.0 ± 2.8 | 7.4 ± 0.5 | 0.0 ± 0.0 | - |
| B+ | t1 | 2/2 | 2486 ± 1852 | 2.0 ± 1.4 | 10.5 ± 3.5 | 9.6 ± 2.5 | 0.5 ± 0.7 | - |
| D | t1 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 9.0 ± 0.0 | 5.7 ± 1.3 | 0.0 ± 0.0 | - |
| B | t2 | 2/2 | 4007 ± 3216 | 2.5 ± 2.1 | 14.5 ± 3.5 | 30.3 ± 10.2 | 0.5 ± 0.7 | 3.0 |
| B+ | t2 | 2/2 | 3365 ± 2223 | 2.0 ± 1.4 | 13.0 ± 1.4 | 23.2 ± 6.5 | 0.0 ± 0.0 | - |
| D | t2 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 15.5 ± 2.1 | 36.5 ± 29.0 | 0.0 ± 0.0 | - |
| B | t3 | 2/2 | 1772 ± 13 | 1.0 ± 0.0 | 9.0 ± 0.0 | 16.5 ± 0.9 | 0.0 ± 0.0 | - |
| B+ | t3 | 2/2 | 3802 ± 223 | 2.0 ± 0.0 | 11.5 ± 0.7 | 19.4 ± 2.4 | 0.0 ± 0.0 | - |
| D | t3 | 2/2 | 0.0 ± 0.0 | 0.0 ± 0.0 | 11.0 ± 0.0 | 14.8 ± 0.6 | 0.0 ± 0.0 | - |

Mean ± sd over layouts. LLM tokens are input plus output from API usage, per episode. D's one-time prior-rule call before training is not charged to any episode. After a failed plan, B and B+ get the same "rules may differ from vanilla" sentence as D's revision prompt. Repeated failures count any identical action that failed or mined a block that dropped nothing more than once, including `not_found` retries after the visible ore ran out.
