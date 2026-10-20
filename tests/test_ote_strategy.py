#!/usr/bin/env python3
"""
Unit tests for Institutional Optimal Trade Entry (OTE) Strategy.
"""
import unittest
import pandas as pd
import numpy as np

from ote_strategy import (
    OTEAgent,
    OTEDir,
    OTEVote,
    detect_ote_setup,
    ote_vote,
)


class TestOTEStrategy(unittest.TestCase):
    def test_edge_cases(self):
        agent = OTEAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, OTEDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, OTEDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(30)}))
        self.assertEqual(v_missing.direction, OTEDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_bullish_ote_golden_pocket(self):
        # 1. Swing low at 2640.0
        # 2. Impulsive displacement rally up to 2660.0 (dist = 20.0 pts)
        #    - Fib 50.0% = 2650.0
        #    - Fib 61.8% = 2647.64
        #    - Fib 70.5% = 2645.90
        #    - Fib 78.6% = 2644.28
        # 3. Pullback bar with low at 2646.0 (deep in OTE 70.5%), lower wick rejection, closing at 2648.5
        # 4. Confirmation bounce bar closing up at 2651.0
        data = []
        # Base origin
        data.append({"open": 2641.0, "high": 2642.0, "low": 2640.0, "close": 2641.5, "volume": 100})
        # Rally leg
        for p in [2645.0, 2650.0, 2655.0, 2660.0]:
            data.append({"open": p - 3.0, "high": p + 0.5, "low": p - 3.5, "close": p, "volume": 300})
        # Top extreme: 2660.0
        # Pullback into OTE
        data.append({"open": 2659.0, "high": 2659.5, "low": 2653.0, "close": 2654.0, "volume": 150})
        # Tap 70.5% OTE (2645.90) with lower wick down to 2646.0, closing at 2648.5
        data.append({"open": 2652.0, "high": 2653.0, "low": 2646.0, "close": 2648.5, "volume": 350})
        # Current bounce confirmation candle
        data.append({"open": 2648.5, "high": 2652.0, "low": 2648.0, "close": 2651.5, "volume": 280})

        # Add preceding baseline bars so lookback > 20
        prefix = []
        for i in range(15):
            prefix.append({"open": 2642.0, "high": 2643.0, "low": 2641.0, "close": 2642.0, "volume": 80})

        df = pd.DataFrame(prefix + data)
        agent = OTEAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "OTE-STRATEGY")
        self.assertEqual(vote.direction, OTEDir.LONG)
        self.assertGreaterEqual(vote.strength, 0.85)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertLess(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertGreater(vote.tp_price, float(df["close"].iloc[-1]))

    def test_bearish_ote_golden_pocket(self):
        # 1. Swing high at 2660.0
        # 2. Impulsive displacement drop down to 2640.0 (dist = 20.0 pts)
        #    - Fib 50.0% = 2650.0
        #    - Fib 61.8% = 2652.36
        #    - Fib 70.5% = 2654.10
        #    - Fib 78.6% = 2655.72
        # 3. Pullback bar with high at 2654.0 (deep in OTE 70.5%), upper wick rejection, closing at 2651.5
        # 4. Confirmation drop candle closing down at 2648.0
        data = []
        # Base origin high
        data.append({"open": 2658.0, "high": 2660.0, "low": 2657.0, "close": 2658.5, "volume": 100})
        # Drop leg
        for p in [2655.0, 2650.0, 2645.0, 2640.0]:
            data.append({"open": p + 3.0, "high": p + 3.5, "low": p - 0.5, "close": p, "volume": 300})
        # Bottom extreme: 2640.0
        # Retrace into OTE
        data.append({"open": 2641.0, "high": 2647.0, "low": 2640.5, "close": 2646.0, "volume": 150})
        # Tap 70.5% OTE (2654.10) with upper wick up to 2654.0, closing at 2651.5
        data.append({"open": 2647.0, "high": 2654.0, "low": 2646.5, "close": 2651.5, "volume": 350})
        # Current drop confirmation candle
        data.append({"open": 2651.5, "high": 2652.0, "low": 2647.5, "close": 2648.0, "volume": 280})

        prefix = []
        for i in range(15):
            prefix.append({"open": 2658.0, "high": 2659.0, "low": 2657.0, "close": 2658.0, "volume": 80})

        df = pd.DataFrame(prefix + data)
        agent = OTEAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "OTE-STRATEGY")
        self.assertEqual(vote.direction, OTEDir.SHORT)
        self.assertGreaterEqual(vote.strength, 0.85)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertGreater(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertLess(vote.tp_price, float(df["close"].iloc[-1]))

    def test_functional_wrapper(self):
        df = pd.DataFrame({
            "open": [2650.0 + i * 0.1 for i in range(25)],
            "high": [2650.5 + i * 0.1 for i in range(25)],
            "low": [2649.5 + i * 0.1 for i in range(25)],
            "close": [2650.2 + i * 0.1 for i in range(25)],
        })
        v = ote_vote(df)
        self.assertIsInstance(v, OTEVote)
        self.assertEqual(v.agent, "OTE-STRATEGY")


if __name__ == "__main__":
    unittest.main()
