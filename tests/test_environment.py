import unittest

from schema_voyager.environment import MinecraftLikeEnv
from schema_voyager.models import Action
from schema_voyager.tasks import training_tasks


class EnvironmentTests(unittest.TestCase):
    def test_mining_requires_a_pickaxe(self):
        task = training_tasks()[1]
        task.start_inventory = {}
        env = MinecraftLikeEnv(task)
        env.step(Action("move", "east"))
        env.step(Action("move", "east"))
        result = env.step(Action("mine"))
        self.assertFalse(result.ok)
        self.assertIn("requires a pickaxe", result.message)

    def test_crafting_updates_inventory_and_goal(self):
        env = MinecraftLikeEnv(training_tasks()[0])
        env.step(Action("move", "east"))
        env.step(Action("mine"))
        env.step(Action("move", "south"))
        env.step(Action("mine"))
        env.step(Action("craft", "planks"))
        env.step(Action("craft", "planks"))
        env.step(Action("craft", "sticks"))
        result = env.step(Action("craft", "wooden_pickaxe"))
        self.assertTrue(result.ok)
        self.assertTrue(result.observation.success)


if __name__ == "__main__":
    unittest.main()
