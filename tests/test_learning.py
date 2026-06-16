"""Learning subsystem tests."""

import unittest

from learning.reinforcement_engine import ReinforcementEngine


class LearningTests(unittest.TestCase):
    """Validate lightweight reinforcement model."""

    def test_learning_orders_strategies(self) -> None:
        engine = ReinforcementEngine()
        engine.collect_metrics([
            {"action": "patch-first", "reward": 1.0},
            {"action": "observe", "reward": 0.2},
        ])
        self.assertEqual(engine.update_strategies()[0], "patch-first")
