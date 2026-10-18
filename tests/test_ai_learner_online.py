"""Stable online learning tests for AILearnerAgent."""
from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from ai_learner import AILearnerAgent, Dir


def _dummy_df(n: int = 120, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 2000 + np.cumsum(rng.normal(0, 0.4, n))
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.8,
            "low": price - 0.8,
            "close": price,
            "volume": rng.integers(100, 500, n),
        }
    )


class TestAILearnerOnline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.path = self.tmp.name
        if os.path.exists(self.path):
            os.unlink(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_weight_clip_and_bounds(self):
        agent = AILearnerAgent(path=self.path)
        feats = np.ones(len(agent.feature_names)) * 4.0
        for _ in range(30):
            agent.learn(
                direction_correct=False,
                conf=0.8,
                won=False,
                pnl=-1.5,
                features=feats,
                r_multiple=-1.5,
            )
        w = np.array(agent.memory["model_weights"])
        self.assertTrue(np.all(np.abs(w) <= agent.WEIGHT_CLIP + 1e-9))
        self.assertGreaterEqual(agent.memory["min_conf"], agent.MIN_CONF_FLOOR)
        self.assertLessEqual(agent.memory["min_conf"], agent.MIN_CONF_CEIL)

    def test_min_conf_requires_sample_size(self):
        agent = AILearnerAgent(path=self.path)
        start = float(agent.memory["min_conf"])
        for i in range(agent.MIN_TRADES_FOR_CONF - 1):
            agent.learn(True, 0.75, True, 1.0, r_multiple=1.0)
        self.assertAlmostEqual(float(agent.memory["min_conf"]), start, places=3)

    def test_r_multiple_updates_sum_r(self):
        agent = AILearnerAgent(path=self.path)
        agent.learn(True, 0.7, True, 2.0, r_multiple=1.25)
        agent.learn(False, 0.7, False, -1.0, r_multiple=-0.8)
        self.assertAlmostEqual(agent.memory["sum_r"], 0.45, places=3)
        self.assertEqual(agent.memory["trades"], 2)

    def test_predict_vote_smoke(self):
        agent = AILearnerAgent(path=self.path)
        df = _dummy_df()
        direction, conf, score = agent.predict(df)
        self.assertIn(direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)
        v = agent.vote(df)
        self.assertEqual(v.agent, "AI-LEARNER")

    def test_memory_persists(self):
        agent = AILearnerAgent(path=self.path)
        agent.learn(True, 0.66, True, 1.0, r_multiple=0.9, regime="trend")
        agent2 = AILearnerAgent(path=self.path)
        self.assertEqual(agent2.memory["trades"], 1)
        self.assertEqual(agent2.memory.get("version"), 2)


if __name__ == "__main__":
    unittest.main()
