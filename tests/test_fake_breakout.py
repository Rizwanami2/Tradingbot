#!/usr/bin/env python3
"""
Unit Tests for Fake Breakout & Liquidity Sweep Strategy.
=======================================================
Verifies all 6 institutional fake breakout patterns:
1. Swing Failure Pattern (SFP)
2. Turtle Soup (Linda Raschke 20-bar reversal)
3. Wyckoff Spring & UTAD
4. Judas Swing (Session Open liquidity hunt)
5. Equal Highs / Lows (EQH/EQL) Sweep
6. Failed Break-and-Retest Trap
Plus FakeBreakoutAgent and Arbiter integration.
"""
from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from fake_breakout_strategy import (
    FBDir,
    FBVote,
    FakeBreakoutAgent,
    detect_all_fake_breakouts,
    detect_equal_highs_lows_sweep,
    detect_failed_break_and_retest,
    detect_judas_swing,
    detect_swing_failure_pattern,
    detect_turtle_soup,
    detect_wyckoff_spring_utad,
)
from multi_agent_trading_bot import Arbiter, Dir
from risk_manager import RiskManager


def _make_base_df(n: int = 50, base_price: float = 2650.0) -> pd.DataFrame:
    """Helper generating a clean linear non-cyclic baseline DataFrame."""
    prices = [base_price + i * 0.05 for i in range(n)]
    dates = pd.date_range("2026-10-17 08:00:00", periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame({
        "open": prices,
        "high": [p + 0.2 for p in prices],
        "low": [p - 0.2 for p in prices],
        "close": [p + 0.02 for p in prices],
        "volume": [1000.0] * n,
    }, index=dates)
    return df


class TestFakeBreakoutPatterns(unittest.TestCase):

    def test_sfp_bullish(self):
        """Bullish SFP: Price wicks below swing low and closes back inside with strong lower wick."""
        df = _make_base_df(30, 2650.0)
        # Establish a clear swing low at bar 10
        df.iloc[10, df.columns.get_loc("low")] = 2640.0
        # Normal intermediate bars staying above 2645.0
        for i in range(11, 29):
            df.iloc[i, df.columns.get_loc("low")] = 2645.0
            df.iloc[i, df.columns.get_loc("high")] = 2652.0
            df.iloc[i, df.columns.get_loc("close")] = 2648.0

        # Bar 29 (current): Sweeps below 2640.0 down to 2638.0, but closes at 2643.0 (rejection wick)
        df.iloc[-1, df.columns.get_loc("open")] = 2642.0
        df.iloc[-1, df.columns.get_loc("high")] = 2644.0
        df.iloc[-1, df.columns.get_loc("low")] = 2638.0   # Swept swing low of 2640.0
        df.iloc[-1, df.columns.get_loc("close")] = 2643.0 # Closed back above 2640.0

        sfp = detect_swing_failure_pattern(df)
        self.assertIsNotNone(sfp)
        self.assertEqual(sfp["pattern"], "SFP_BULLISH")
        self.assertEqual(sfp["direction"], FBDir.LONG)
        self.assertGreaterEqual(sfp["strength"], 0.75)
        self.assertIn("Bullish SFP", sfp["reason"])

    def test_sfp_bearish(self):
        """Bearish SFP: Price wicks above swing high and closes back inside with strong upper wick."""
        df = _make_base_df(30, 2650.0)
        # Establish swing high at bar 10
        df.iloc[10, df.columns.get_loc("high")] = 2660.0
        for i in range(11, 29):
            df.iloc[i, df.columns.get_loc("high")] = 2655.0
            df.iloc[i, df.columns.get_loc("close")] = 2652.0

        # Bar 29 (current): Sweeps above 2660.0 up to 2663.0, closes at 2657.0 (upper wick rejection)
        df.iloc[-1, df.columns.get_loc("open")] = 2658.0
        df.iloc[-1, df.columns.get_loc("high")] = 2663.0   # Swept swing high of 2660.0
        df.iloc[-1, df.columns.get_loc("low")] = 2656.0
        df.iloc[-1, df.columns.get_loc("close")] = 2657.0 # Closed back below 2660.0

        sfp = detect_swing_failure_pattern(df)
        self.assertIsNotNone(sfp)
        self.assertEqual(sfp["pattern"], "SFP_BEARISH")
        self.assertEqual(sfp["direction"], FBDir.SHORT)
        self.assertGreaterEqual(sfp["strength"], 0.75)
        self.assertIn("Bearish SFP", sfp["reason"])

    def test_turtle_soup_bullish(self):
        """Turtle Soup Bullish: 20-bar low swept and reversed."""
        df = _make_base_df(35, 2650.0)
        # Reference window for 35 bars with lookback=20 is bars 13 to 32 (-22 to -2)
        # Set 20-bar low at bar 18
        df.iloc[18, df.columns.get_loc("low")] = 2640.0
        for i in range(19, 33):
            df.iloc[i, df.columns.get_loc("low")] = 2645.0

        # Previous bar pierces low to 2638.0
        df.iloc[-2, df.columns.get_loc("open")] = 2642.0
        df.iloc[-2, df.columns.get_loc("low")] = 2638.0
        # Current bar reverses back inside with strong close above prior 20-bar low and prev open
        df.iloc[-1, df.columns.get_loc("open")] = 2639.0
        df.iloc[-1, df.columns.get_loc("low")] = 2638.5
        df.iloc[-1, df.columns.get_loc("high")] = 2644.0
        df.iloc[-1, df.columns.get_loc("close")] = 2643.0

        ts = detect_turtle_soup(df)
        self.assertIsNotNone(ts)
        self.assertEqual(ts["pattern"], "TURTLE_SOUP_BUY")
        self.assertEqual(ts["direction"], FBDir.LONG)
        self.assertGreaterEqual(ts["strength"], 0.75)

    def test_wyckoff_spring(self):
        """Wyckoff Spring: Pierces trading range support on elevated volume and snaps back."""
        df = _make_base_df(45, 2650.0)
        # Range between 2645.0 and 2655.0
        for i in range(40):
            df.iloc[i, df.columns.get_loc("high")] = 2655.0
            df.iloc[i, df.columns.get_loc("low")] = 2645.0
            df.iloc[i, df.columns.get_loc("close")] = 2650.0
            df.iloc[i, df.columns.get_loc("volume")] = 1000.0

        # Spring bar (last bar): dips to 2642.0, volume 2200 (2.2x), closes at 2647.0
        df.iloc[-1, df.columns.get_loc("open")] = 2646.0
        df.iloc[-1, df.columns.get_loc("low")] = 2642.0
        df.iloc[-1, df.columns.get_loc("high")] = 2648.0
        df.iloc[-1, df.columns.get_loc("close")] = 2647.0
        df.iloc[-1, df.columns.get_loc("volume")] = 2200.0

        spring = detect_wyckoff_spring_utad(df)
        self.assertIsNotNone(spring)
        self.assertEqual(spring["pattern"], "WYCKOFF_SPRING")
        self.assertEqual(spring["direction"], FBDir.LONG)
        self.assertGreaterEqual(spring["strength"], 0.80)

    def test_wyckoff_utad(self):
        """Wyckoff UTAD: Pierces trading range resistance on elevated volume and closes back inside."""
        df = _make_base_df(45, 2650.0)
        for i in range(40):
            df.iloc[i, df.columns.get_loc("high")] = 2655.0
            df.iloc[i, df.columns.get_loc("low")] = 2645.0
            df.iloc[i, df.columns.get_loc("close")] = 2650.0
            df.iloc[i, df.columns.get_loc("volume")] = 1000.0

        # UTAD bar: spikes to 2659.0, volume 2400 (2.4x), closes back at 2653.0
        df.iloc[-1, df.columns.get_loc("open")] = 2654.0
        df.iloc[-1, df.columns.get_loc("high")] = 2659.0
        df.iloc[-1, df.columns.get_loc("low")] = 2652.0
        df.iloc[-1, df.columns.get_loc("close")] = 2653.0
        df.iloc[-1, df.columns.get_loc("volume")] = 2400.0

        utad = detect_wyckoff_spring_utad(df)
        self.assertIsNotNone(utad)
        self.assertEqual(utad["pattern"], "WYCKOFF_UTAD")
        self.assertEqual(utad["direction"], FBDir.SHORT)
        self.assertGreaterEqual(utad["strength"], 0.80)

    def test_equal_highs_sweep(self):
        """Equal Highs (EQH) Sweep: Two nearly identical swing highs swept then reversed."""
        df = _make_base_df(40, 2650.0)
        # Create first high at bar 15: 2660.0
        df.iloc[15, df.columns.get_loc("high")] = 2660.0
        # Create second high at bar 25: 2660.02 (within 0.08% tolerance)
        df.iloc[25, df.columns.get_loc("high")] = 2660.02

        # Current bar sweeps above EQH to 2661.5, closes at 2658.0 with upper rejection wick
        df.iloc[-1, df.columns.get_loc("open")] = 2659.0
        df.iloc[-1, df.columns.get_loc("high")] = 2661.5
        df.iloc[-1, df.columns.get_loc("low")] = 2657.0
        df.iloc[-1, df.columns.get_loc("close")] = 2658.0

        sweep = detect_equal_highs_lows_sweep(df)
        self.assertIsNotNone(sweep)
        self.assertEqual(sweep["pattern"], "EQH_LIQUIDITY_SWEEP")
        self.assertEqual(sweep["direction"], FBDir.SHORT)
        self.assertGreaterEqual(sweep["strength"], 0.75)

    def test_failed_break_and_retest(self):
        """Failed Breakout & Retest Trap: Breakout fails to hold and collapses through level."""
        df = _make_base_df(40, 2650.0)
        # Establish resistance level in base window (bars -40 to -10, i.e. 0 to 29)
        df.iloc[15, df.columns.get_loc("high")] = 2655.0

        # Breakout occurs at bar 32: spikes above 2655.0 to 2660.0, closes at 2658.0
        df.iloc[32, df.columns.get_loc("high")] = 2660.0
        df.iloc[32, df.columns.get_loc("close")] = 2658.0

        # Current bar retests and collapses back under 2655.0 to 2650.0
        df.iloc[-1, df.columns.get_loc("open")] = 2656.0
        df.iloc[-1, df.columns.get_loc("high")] = 2657.0
        df.iloc[-1, df.columns.get_loc("low")] = 2649.0
        df.iloc[-1, df.columns.get_loc("close")] = 2650.0

        trap = detect_failed_break_and_retest(df)
        self.assertIsNotNone(trap)
        self.assertEqual(trap["pattern"], "FAILED_RETEST_BULL_TRAP")
        self.assertEqual(trap["direction"], FBDir.SHORT)
        self.assertGreaterEqual(trap["strength"], 0.75)


class TestFakeBreakoutAgentAndArbiter(unittest.TestCase):

    def test_agent_flat_on_empty_or_small_data(self):
        agent = FakeBreakoutAgent()
        vote = agent.vote(pd.DataFrame())
        self.assertEqual(vote.direction, FBDir.FLAT)
        self.assertEqual(vote.strength, 0.0)

    def test_agent_flat_when_no_pattern(self):
        agent = FakeBreakoutAgent()
        df = _make_base_df(40, 2650.0)
        # Ensure last bar is an inside bar that swept neither the high nor low
        prev_h = float(df.iloc[-2]["high"])
        prev_l = float(df.iloc[-2]["low"])
        df.iloc[-1, df.columns.get_loc("high")] = prev_h - 0.05
        df.iloc[-1, df.columns.get_loc("low")] = prev_l + 0.05
        df.iloc[-1, df.columns.get_loc("open")] = prev_l + 0.10
        df.iloc[-1, df.columns.get_loc("close")] = prev_h - 0.10
        vote = agent.vote(df)
        self.assertEqual(vote.direction, FBDir.FLAT)
        self.assertIn("No fake breakout active", vote.reason)

    def test_agent_detects_sfp_and_votes(self):
        agent = FakeBreakoutAgent()
        df = _make_base_df(30, 2650.0)
        df.iloc[10, df.columns.get_loc("low")] = 2640.0
        for i in range(11, 29):
            df.iloc[i, df.columns.get_loc("low")] = 2645.0
        df.iloc[-1, df.columns.get_loc("open")] = 2642.0
        df.iloc[-1, df.columns.get_loc("high")] = 2644.0
        df.iloc[-1, df.columns.get_loc("low")] = 2638.0
        df.iloc[-1, df.columns.get_loc("close")] = 2643.0

        vote = agent.vote(df)
        self.assertEqual(vote.agent, "FAKE-BREAKOUT")
        self.assertEqual(vote.direction, FBDir.LONG)
        self.assertGreaterEqual(vote.strength, 0.75)
        self.assertIn("SFP_BULLISH", vote.pattern)

    def test_arbiter_includes_fake_breakout(self):
        arb = Arbiter()
        self.assertIsNotNone(arb.fb)
        self.assertIn("FAKE-BREAKOUT", arb.WEIGHTS)
        self.assertGreater(arb.WEIGHTS["FAKE-BREAKOUT"], 0.0)
        self.assertAlmostEqual(Arbiter.WEIGHTS["FAKE-BREAKOUT"], 1.35)

        risk_mgr = RiskManager()
        df = _make_base_df(50, 2650.0)
        sig, why, votes = arb.decide(df, None, None, None, None, risk_mgr, symbol="XAUUSD", on_demand=True)
        vote_agents = [v.agent for v in votes]
        self.assertIn("FAKE-BREAKOUT", vote_agents)


if __name__ == "__main__":
    unittest.main()
