import tempfile
import unittest
from pathlib import Path

from schema_voyager.evaluator import evaluate
from schema_voyager.schema_store import SchemaStore
from schema_voyager.tasks import holdout_tasks, training_tasks


class LoopTests(unittest.TestCase):
    def test_training_compiles_skills_and_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SchemaStore(Path(directory))
            report = evaluate(training_tasks(), store, "hybrid", learn=True)
            self.assertEqual(report.success_rate, 1.0)
            self.assertEqual(len(store.skills), 3)
            self.assertGreaterEqual(len(store.rules), 3)
            self.assertTrue((Path(directory) / "skills.json").exists())

    def test_hybrid_reuses_skills_and_reduces_probe_actions(self):
        trained = SchemaStore()
        evaluate(training_tasks(), trained, "hybrid", learn=True)
        baseline = evaluate(holdout_tasks(), SchemaStore(), "baseline")
        hybrid = evaluate(holdout_tasks(), trained, "hybrid")
        self.assertEqual(baseline.success_rate, 1.0)
        self.assertEqual(hybrid.success_rate, 1.0)
        self.assertEqual(hybrid.skill_reuse_rate, 1.0)
        self.assertLess(hybrid.average_actions, baseline.average_actions)
        self.assertLess(hybrid.average_tokens, baseline.average_tokens)


if __name__ == "__main__":
    unittest.main()
