#!/usr/bin/env python3
"""
Unit tests for Heavy Bot AI & Autonomous Training Engine (heavy_trainer.py)
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from heavy_trainer import HeavyTrainer


class TestHeavyTrainer(unittest.TestCase):
    def setUp(self):
        self.trainer = HeavyTrainer(symbols=["BTCUSDT", "XAUUSD"], years=1.0, timeframes=["1d"])

    def test_trainer_initialization(self):
        self.assertEqual(self.trainer.symbols, ["BTCUSDT", "XAUUSD"])
        self.assertEqual(self.trainer.years, 1.0)
        self.assertIsNotNone(self.trainer.learner)
        self.assertIsNotNone(self.trainer.brain)
        self.assertIsNotNone(self.trainer.edge)
        self.assertIsNotNone(self.trainer.ai_suite)

    def test_train_ai_suite(self):
        res = self.trainer.train_ai_advanced_suite()
        self.assertTrue(res.get("enabled"))
        self.assertIn("TREND", res.get("agent_trust", {}))
        self.assertEqual(res.get("risk_persona"), "med")

    def test_format_markdown_report(self):
        mock_summary = {
            "total_elapsed_sec": 42.5,
            "years": 5.0,
            "symbols": ["BTCUSDT", "XAUUSD"],
            "autonomous_learner": {
                "trades": 350,
                "expectancy": 0.125,
                "flaws": 12,
                "banned_setups": 8,
                "banned_hours": [1, 5, 14],
                "banned_weekdays": [3],
                "min_score": 2.1,
                "min_rr": 2.0,
                "conviction_floor": 0.62,
            },
            "ai_learner": {
                "samples_trained": 1200,
                "win_rate": 55.2,
                "min_conf": 0.58,
                "calibration_bins": 10,
                "feature_importance": {"slope_25": 0.75, "ret_5": 0.55},
            },
            "learning_edge": {
                "active_time_slots": 115,
                "setups_mapped": 28,
            },
            "ai_advanced_suite": {
                "min_scorecard": 55.0,
                "risk_persona": "med",
            },
        }
        report = self.trainer.format_markdown_report(mock_summary)
        self.assertIn("Heavy Bot AI & Autonomous Training Report", report)
        self.assertIn("350", report)
        self.assertIn("+0.125R", report)
        self.assertIn("01:00", report)
        self.assertIn("Thu", report)
        self.assertIn("55.2%", report)
        self.assertIn("slope_25", report)

    def test_train_ai_model_synthetic_data(self):
        # Generate 150 bars of synthetic data
        n = 150
        dates = pd.date_range("2026-01-01", periods=n, freq="1D")
        p = 2000.0 + np.cumsum(np.random.normal(0, 5, n))
        synthetic_df = pd.DataFrame({
            "open": p,
            "high": p + 10.0,
            "low": p - 10.0,
            "close": p + 1.0,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        with patch.object(self.trainer.brain, "_load_history", return_value=synthetic_df):
            res = self.trainer.train_ai_model(step=10, forward_bars=5)
            self.assertIn("samples_trained", res)
            self.assertGreater(res["samples_trained"], 0)
            self.assertIn("model_weights", res)
            self.assertEqual(len(res["model_weights"]), 10)


if __name__ == "__main__":
    unittest.main()
