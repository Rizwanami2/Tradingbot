"""
Unit tests for IndicatorEngine and AIPredictor.
"""

import unittest
import numpy as np
import pandas as pd
from trading_strategy_indicators_pro import IndicatorEngine, AIPredictor, Direction


class TestIndicators(unittest.TestCase):
    def setUp(self):
        self.engine = IndicatorEngine()
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1min")
        p = 2650 + np.cumsum(np.random.randn(n) * 0.3)
        self.df = pd.DataFrame(
            {
                "open": p,
                "high": p + np.random.uniform(0.1, 1.5, n),
                "low": p - np.random.uniform(0.1, 1.5, n),
                "close": p + np.random.randn(n) * 0.1,
                "volume": np.random.randint(100, 1000, n),
            },
            index=dates,
        )

    def test_indicator_snapshot(self):
        snap = self.engine.compute(self.df)
        self.assertIsNotNone(snap)
        self.assertGreaterEqual(snap.rsi, 0.0)
        self.assertLessEqual(snap.rsi, 100.0)
        self.assertGreater(snap.bb_upper, snap.bb_lower)
        self.assertIn(snap.supertrend_dir, (-1, 1))
        self.assertGreaterEqual(snap.score, -5.0)
        self.assertLessEqual(snap.score, 5.0)

    def test_ai_predictor(self):
        predictor = AIPredictor()
        pred = predictor.predict(self.df)
        self.assertIn(pred.direction, (Direction.LONG, Direction.SHORT, Direction.FLAT))
        self.assertGreaterEqual(pred.confidence, 0.0)
        self.assertLessEqual(pred.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
