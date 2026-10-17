"""Live check of the LLM's world-model calls, run before any real-server episode.

1. Prior rules: Claude proposes one rule per action; we diff them against the
   hand-written vanilla rules.
2. H3 revision: given the coal-ore counterexample (wooden pickaxe, block
   removed, nothing dropped), Claude must rank a stone-tier pickaxe rule in its
   top 3 that the agent keeps (it explains the counterexample). Passes at
   >= 4 of N.

    python scripts/eval_revision.py --n 5
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from schema_voyager.agent import CRAFTABLE_ITEMS, MINEABLE_BLOCKS
from schema_voyager.llm import LLM, fixture_prior_rules
from schema_voyager.models import Action
from schema_voyager.schema_store import applies
from schema_voyager.verifier import predict

STONE_TIER = {"stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    llm = LLM(mode="live")
    print(f"provider={llm.provider} model={llm.model} effort={llm.effort}")

    priors = {rule.id: rule for rule in llm.propose_prior_rules(MINEABLE_BLOCKS, CRAFTABLE_ITEMS)}
    print(f"\nPrior rules ({llm.calls[-1].tokens} tokens):")
    differences = 0
    for expected in fixture_prior_rules():
        got = priors.get(expected.id)
        same = got is not None and got.preconditions == expected.preconditions and got.effects == expected.effects
        differences += not same
        detail = "" if same else f"   expected {expected.preconditions} {expected.effects}, got " + (
            f"{got.preconditions} {got.effects}" if got else "nothing")
        print(f"  {'same' if same else 'DIFF'}  {expected.id}{detail}")

    coal_rule = priors.get("mine_coal_ore") or next(r for r in fixture_prior_rules() if r.id == "mine_coal_ore")
    action = Action("mine", "coal_ore", tool="wooden_pickaxe")
    observed = {"block_removed": True, "drops": {}}
    counterexample = {"action": action.to_dict(), "predicted": predict(coal_rule, action), "observed": observed}
    evidence = ("Confirmed 4 times across 2 training episodes, e.g.:\n"
                '- mine coal_ore with wooden_pickaxe -> {"block_removed": true, "drops": {"coal": 1}}')
    known = sorted({item for rule in priors.values() for item in rule.yields()})

    passes = 0
    for trial in range(args.n):
        hypotheses = llm.revise_rule(coal_rule, evidence, counterexample, known, world_id="weval")
        kept = [h for h in hypotheses if not applies(h, action) or predict(h, action) == observed]
        stone_rank = next(
            (i + 1 for i, h in enumerate(kept)
             if h.yields().get("coal") and set(h.preconditions.get("tool_any", [])) & STONE_TIER
             and "stone_pickaxe" in h.preconditions.get("tool_any", [])),
            None,
        )
        passes += stone_rank is not None
        print(f"\nTrial {trial + 1} ({llm.calls[-1].tokens} tokens): stone-tier rule at kept rank {stone_rank}")
        for h in hypotheses:
            mark = "kept" if h in kept else "rejected"
            print(f"  {h.rank + 1}. [{mark}] tool_any={h.preconditions.get('tool_any')} drops={h.yields()} "
                  f"- {h.rationale}")

    print(f"\nPrior rules differing from vanilla: {differences}")
    print(f"Revision: stone-tier pickaxe rule in kept top 3 in {passes}/{args.n} trials")
    print(f"Total tokens: {llm.total_tokens} over {len(llm.calls)} calls")
    sys.exit(0 if passes >= max(1, round(0.8 * args.n)) else 1)


if __name__ == "__main__":
    main()
