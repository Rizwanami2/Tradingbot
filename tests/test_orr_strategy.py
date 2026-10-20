#!/usr/bin/env python3
"""
Unit tests for Opening Range Retest & Order Flow Reversal (ORR) Strategy.
"""
import unittest
from datetime import datetime, time, timezone
import pandas as pd
import numpy as np

from orr_strategy import (
    ORRAgent,
    ORRDir,
    ORRVote,
    compute_opening_range,
    detect_opening_range_retest,
    detect_order_flow_structure_retest,
    orr_vote,
)


def _generate_synthetic_df(n: int = 50, base_price: float = 2650.0, freq: str = "1min") -> pd.DataFrame:
    dates = pd.date_range("2026-10-20 07:00:00", periods=n, freq=freq, tz="UTC")
    data = []
    p = base_price
    for _ in range(n):
        o = p
        h = p + 0.5
        l = p - 0.5
        c = p + 0.1
        data.append({"open": o, "high": h, "low": l, "close": c, "volume": 100})
        p = c
    return pd.DataFrame(data, index=dates)


class TestORRStrategy(unittest.TestCase):
    def test_edge_cases(self):
        agent = ORRAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, ORRDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, ORRDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(40)}))
        self.assertEqual(v_missing.direction, ORRDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_compute_opening_range(self):
        df = _generate_synthetic_df(n=60, base_price=2650.0)
        orang = compute_opening_range(df)
        self.assertTrue(orang.valid)
        self.assertGreater(orang.high, orang.low)
        self.assertGreater(orang.range_pts, 0.0)

    def test_bullish_orr_retest_pattern(self):
        # Build scenario:
        # 1. 20 bars range between 2650 and 2655
        # 2. Breakout candle closes at 2658 (above ORH 2655)
        # 3. Pullback bar with low touching 2655.20 with lower wick rejection, closing at 2657
        dates = pd.date_range("2026-10-20 07:00:00", periods=30, freq="1min", tz="UTC")
        data = []
        for i in range(20):
            data.append({"open": 2651.0, "high": 2654.5, "low": 2650.5, "close": 2653.0, "volume": 100})
        # Breakout bar
        data.append({"open": 2653.0, "high": 2658.5, "low": 2652.5, "close": 2658.0, "volume": 350})
        # Consolidation bar
        data.append({"open": 2658.0, "high": 2659.0, "low": 2656.5, "close": 2657.5, "volume": 150})
        # Retest bar touching broken ORH (2654.5) with lower wick rejection
        data.append({"open": 2657.0, "high": 2658.0, "low": 2654.6, "close": 2657.5, "volume": 280})
        # Current confirmation bar bouncing up
        data.append({"open": 2657.5, "high": 2661.0, "low": 2657.0, "close": 2660.5, "volume": 300})

        df = pd.DataFrame(data, index=dates[:len(data)])
        agent = ORRAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "ORR-STRATEGY")
        self.assertEqual(vote.direction, ORRDir.LONG)
        self.assertGreaterEqual(vote.strength, 0.80)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertLess(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertGreater(vote.tp_price, float(df["close"].iloc[-1]))

    def test_bearish_orr_retest_pattern(self):
        # Build scenario:
        # 1. 20 bars range between 2650 and 2655
        # 2. Breakout candle closes at 2645 (below ORL 2650.5)
        # 3. Pullback bar with high touching 2650.20 with upper wick rejection, closing down at 2647
        dates = pd.date_range("2026-10-20 07:00:00", periods=30, freq="1min", tz="UTC")
        data = []
        for i in range(20):
            data.append({"open": 2653.0, "high": 2654.5, "low": 2650.5, "close": 2652.0, "volume": 100})
        # Breakdown bar
        data.append({"open": 2651.0, "high": 2651.5, "low": 2644.0, "close": 2645.0, "volume": 350})
        # Consolidation bar
        data.append({"open": 2645.0, "high": 2648.0, "low": 2644.5, "close": 2646.5, "volume": 150})
        # Retest bar touching broken ORL (2650.5) with upper wick rejection
        data.append({"open": 2647.0, "high": 2650.4, "low": 2646.0, "close": 2646.8, "volume": 280})
        # Current confirmation bar dropping down
        data.append({"open": 2646.8, "high": 2647.2, "low": 2641.0, "close": 2641.5, "volume": 300})

        df = pd.DataFrame(data, index=dates[:len(data)])
        agent = ORRAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "ORR-STRATEGY")
        self.assertEqual(vote.direction, ORRDir.SHORT)
        self.assertGreaterEqual(vote.strength, 0.80)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertGreater(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertLess(vote.tp_price, float(df["close"].iloc[-1]))

    def test_orr_vote_functional_wrapper(self):
        df = _generate_synthetic_df(n=40)
        v = orr_vote(df)
        self.assertIsInstance(v, ORRVote)
        self.assertEqual(v.agent, "ORR-STRATEGY")
        self.assertIn(v.direction, (ORRDir.LONG, ORRDir.SHORT, ORRDir.FLAT))

    def test_swing_bos_retest_long(self):
        # 30 bars without datetime index
        data = []
        for i in range(20):
            data.append({"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0, "volume": 100})
        # Breakout above swing high (2652.0)
        data.append({"open": 2651.0, "high": 2656.0, "low": 2650.5, "close": 2655.5, "volume": 300})
        # Retest bar touching 2652.0 with lower wick
        data.append({"open": 2655.0, "high": 2655.5, "low": 2652.1, "close": 2654.5, "volume": 200})
        # Bounce bar
        data.append({"open": 2654.5, "high": 2657.0, "low": 2654.0, "close": 2656.5, "volume": 250})
        df = pd.DataFrame(data)
        agent = ORRAgent()
        v = agent.vote(df)
        self.assertEqual(v.agent, "ORR-STRATEGY")
        self.assertIn(v.direction, (ORRDir.LONG, ORRDir.FLAT))

    def test_swing_bos_retest_short(self):
        # 30 bars without datetime index
        data = []
        for i in range(20):
            data.append({"open": 2650.0, "high": 2651.0, "low": 2648.0, "close": 2649.0, "volume": 100})
        # Breakdown below swing low (2648.0)
        data.append({"open": 2649.0, "high": 2649.5, "low": 2644.0, "close": 2644.5, "volume": 300})
        # Retest bar touching 2648.0 with upper wick
        data.append({"open": 2645.0, "high": 2647.9, "low": 2644.5, "close": 2645.5, "volume": 200})
        # Continuation down bar
        data.append({"open": 2645.5, "high": 2646.0, "low": 2642.0, "close": 2642.5, "volume": 250})
        df = pd.DataFrame(data)
        agent = ORRAgent()
        v = agent.vote(df)
        self.assertEqual(v.agent, "ORR-STRATEGY")
        self.assertIn(v.direction, (ORRDir.SHORT, ORRDir.FLAT))


if __name__ == "__main__":
    unittest.main()
