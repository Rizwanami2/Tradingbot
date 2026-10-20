#!/usr/bin/env python3
"""
Unit tests for Advanced Scalp Strategies:
1. Volatility Squeeze & Momentum Burst Scalp (volatility_squeeze_scalp.py)
2. EMA Ribbon 8/21/55 Trend-Pullback Scalp (momentum_ribbon_scalp.py)
"""
import unittest
import pandas as pd
import numpy as np

from volatility_squeeze_scalp import (
    SqueezeScalpAgent,
    SqueezeDir,
    SqueezeVote,
    compute_bollinger_keltner,
    detect_squeeze_scalp,
    squeeze_scalp_vote,
)
from momentum_ribbon_scalp import (
    RibbonScalpAgent,
    RibbonDir,
    RibbonVote,
    detect_ribbon_scalp,
    ribbon_scalp_vote,
)
from stoch_rsi_scalp import (
    StochRSIScalpAgent,
    StochDir,
    StochVote,
    compute_stoch_rsi,
    detect_stoch_rsi_scalp,
    stoch_rsi_scalp_vote,
)


class TestScalpStrategies(unittest.TestCase):
    def test_squeeze_edge_cases(self):
        agent = SqueezeScalpAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, SqueezeDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, SqueezeDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(30)}))
        self.assertEqual(v_missing.direction, SqueezeDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_ribbon_edge_cases(self):
        agent = RibbonScalpAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, RibbonDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, RibbonDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(60)}))
        self.assertEqual(v_missing.direction, RibbonDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_compute_bollinger_keltner(self):
        data = [{"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0} for _ in range(25)]
        df = pd.DataFrame(data)
        bk = compute_bollinger_keltner(df)
        self.assertIsNotNone(bk)
        self.assertIn("sma20", bk)
        self.assertIn("bb_upper", bk)
        self.assertIn("kc_upper", bk)
        self.assertIn("squeeze_on", bk)

    def test_bullish_squeeze_scalp_fire(self):
        # 1. 20 bars of ultra-tight consolidation (squeeze on)
        data = []
        for i in range(20):
            data.append({"open": 2650.0, "high": 2650.3, "low": 2649.7, "close": 2650.0, "volume": 100})
        # 2. Explosive burst bar firing squeeze upwards
        data.append({"open": 2650.0, "high": 2653.0, "low": 2649.9, "close": 2652.8, "volume": 500})
        # 3. Follow-through bar
        data.append({"open": 2652.8, "high": 2655.5, "low": 2652.5, "close": 2655.0, "volume": 600})

        df = pd.DataFrame(data)
        agent = SqueezeScalpAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "VOLATILITY-SQUEEZE")
        self.assertEqual(vote.direction, SqueezeDir.LONG)
        self.assertGreaterEqual(vote.strength, 0.85)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertLess(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertGreater(vote.tp_price, float(df["close"].iloc[-1]))

    def test_bearish_squeeze_scalp_fire(self):
        # 1. 20 bars of ultra-tight consolidation
        data = []
        for i in range(20):
            data.append({"open": 2650.0, "high": 2650.3, "low": 2649.7, "close": 2650.0, "volume": 100})
        # 2. Explosive breakdown bar firing squeeze downwards
        data.append({"open": 2650.0, "high": 2650.1, "low": 2647.0, "close": 2647.2, "volume": 500})
        # 3. Follow-through bar
        data.append({"open": 2647.2, "high": 2647.5, "low": 2644.5, "close": 2645.0, "volume": 600})

        df = pd.DataFrame(data)
        agent = SqueezeScalpAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "VOLATILITY-SQUEEZE")
        self.assertEqual(vote.direction, SqueezeDir.SHORT)
        self.assertGreaterEqual(vote.strength, 0.85)
        self.assertIsNotNone(vote.stop_price)
        self.assertIsNotNone(vote.tp_price)
        self.assertGreater(vote.stop_price, float(df["close"].iloc[-1]))
        self.assertLess(vote.tp_price, float(df["close"].iloc[-1]))

    def test_bullish_ribbon_scalp(self):
        # Build 60 bars in steady uptrend so 8 > 21 > 55
        data = []
        p = 2600.0
        for i in range(55):
            o = p
            h = p + 0.8
            l = p - 0.2
            c = p + 0.6
            data.append({"open": o, "high": h, "low": l, "close": c, "volume": 100})
            p = c
        # Pullback into 21 EMA value zone
        pullback_low = p - 2.5
        data.append({"open": p, "high": p + 0.2, "low": pullback_low, "close": p - 0.5, "volume": 120})
        # Bullish rejection bounce candle
        data.append({"open": p - 0.5, "high": p + 1.5, "low": p - 0.6, "close": p + 1.2, "volume": 250})

        df = pd.DataFrame(data)
        agent = RibbonScalpAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "RIBBON-SCALP")
        self.assertIn(vote.direction, (RibbonDir.LONG, RibbonDir.FLAT))

    def test_functional_wrappers(self):
        data = [{"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0} for _ in range(60)]
        df = pd.DataFrame(data)
        sv = squeeze_scalp_vote(df)
        self.assertIsInstance(sv, SqueezeVote)
        self.assertEqual(sv.agent, "VOLATILITY-SQUEEZE")

        rv = ribbon_scalp_vote(df)
        self.assertIsInstance(rv, RibbonVote)
        self.assertEqual(rv.agent, "RIBBON-SCALP")

        stoch_v = stoch_rsi_scalp_vote(df)
        self.assertIsInstance(stoch_v, StochVote)
        self.assertEqual(stoch_v.agent, "STOCH-RSI-SCALP")

    def test_stoch_rsi_scalp_agent(self):
        agent = StochRSIScalpAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, StochDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        # Bullish recovery from oversold
        data = []
        p = 2650.0
        for i in range(40):
            data.append({"open": p, "high": p + 0.1, "low": p - 0.5, "close": p - 0.4, "volume": 100})
            p -= 0.4
        # Turn around
        for i in range(10):
            data.append({"open": p, "high": p + 0.8, "low": p - 0.1, "close": p + 0.7, "volume": 150})
            p += 0.7
        df = pd.DataFrame(data)
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "STOCH-RSI-SCALP")
        self.assertIn(vote.direction, (StochDir.LONG, StochDir.SHORT, StochDir.FLAT))
        if vote.direction != StochDir.FLAT:
            self.assertIsNotNone(vote.stop_price)
            self.assertIsNotNone(vote.tp_price)


if __name__ == "__main__":
    unittest.main()
