#!/usr/bin/env python3
"""
Unit tests for Stochastic RSI Scalp Strategy (stoch_rsi_scalp.py).
"""
import unittest
import pandas as pd
import numpy as np

from stoch_rsi_scalp import (
    StochRSIScalpAgent,
    StochDir,
    StochVote,
    compute_stoch_rsi,
    detect_stoch_rsi_scalp,
    stoch_rsi_scalp_vote,
)


class TestStochRSIScalp(unittest.TestCase):
    def test_edge_cases(self):
        agent = StochRSIScalpAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, StochDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, StochDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(40)}))
        self.assertEqual(v_missing.direction, StochDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_compute_stoch_rsi(self):
        prices = [2650.0 + np.sin(i * 0.3) * 5.0 for i in range(50)]
        df = pd.DataFrame({
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
        })
        m = compute_stoch_rsi(df)
        self.assertIsNotNone(m)
        self.assertIn("k", m)
        self.assertIn("d", m)
        self.assertIn("ema20", m)
        self.assertGreaterEqual(m["k"], 0.0)
        self.assertLessEqual(m["k"], 100.0)

    def test_bullish_stoch_rsi_scalp(self):
        # 1. Steady baseline
        prices = [2650.0 for _ in range(30)]
        # 2. Dip down to create oversold StochRSI
        for d in [2648.0, 2646.0, 2644.0, 2642.0]:
            prices.append(d)
        # 3. Hook up sharply with green candles crossing %K above %D
        prices.extend([2643.0, 2645.0, 2647.5])

        df = pd.DataFrame({
            "open": [p - 0.2 for p in prices],
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.6 for p in prices],
            "close": prices,
        })
        agent = StochRSIScalpAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "STOCH-RSI-SCALP")
        self.assertIn(vote.direction, (StochDir.LONG, StochDir.FLAT))

    def test_bearish_stoch_rsi_scalp(self):
        # 1. Steady baseline
        prices = [2650.0 for _ in range(30)]
        # 2. Rally up to create overbought StochRSI
        for d in [2652.0, 2654.0, 2656.0, 2658.0]:
            prices.append(d)
        # 3. Hook down sharply with red candles crossing %K below %D
        prices.extend([2657.0, 2655.0, 2652.5])

        df = pd.DataFrame({
            "open": [p + 0.2 for p in prices],
            "high": [p + 0.6 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
        })
        agent = StochRSIScalpAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "STOCH-RSI-SCALP")
        self.assertIn(vote.direction, (StochDir.SHORT, StochDir.FLAT))

    def test_functional_wrapper(self):
        prices = [2650.0 + i * 0.1 for i in range(45)]
        df = pd.DataFrame({
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
        })
        v = stoch_rsi_scalp_vote(df)
        self.assertIsInstance(v, StochVote)
        self.assertEqual(v.agent, "STOCH-RSI-SCALP")


if __name__ == "__main__":
    unittest.main()
