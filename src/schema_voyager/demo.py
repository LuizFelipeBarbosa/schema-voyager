from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluator import evaluate
from .schema_store import SchemaStore
from .tasks import holdout_tasks, training_tasks


def _print_episode(episode) -> None:
    print(
        f"  {episode.task_id}: {'PASS' if episode.success else 'FAIL'} | "
        f"actions={episode.action_count} | tokens≈{episode.estimated_output_tokens} | "
        f"skills={','.join(episode.used_skills) or '-'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SchemaVoyager starter demo.")
    parser.add_argument("--artifacts", type=Path, default=Path(".demo_artifacts"))
    args = parser.parse_args()

    if args.artifacts.exists():
        for path in args.artifacts.glob("*.json"):
            path.unlink()
    store = SchemaStore(args.artifacts)

    print("SchemaVoyager: train executable skills on three Minecraft-like tasks")
    training = evaluate(training_tasks(), store, "hybrid", learn=True)
    for episode in training.episodes:
        _print_episode(episode)

    print("\nHoldout comparison")
    baseline = evaluate(holdout_tasks(), SchemaStore(), "baseline")
    hybrid = evaluate(holdout_tasks(), store, "hybrid")
    print(" baseline:", json.dumps(baseline.summary()))
    print(" hybrid:  ", json.dumps(hybrid.summary()))
    for episode in hybrid.episodes:
        _print_episode(episode)

    print(f"\nLearned {len(store.skills)} skills and {len(store.rules)} verified rules.")
    print(f"Artifacts written to {args.artifacts.resolve()}")


if __name__ == "__main__":
    main()

