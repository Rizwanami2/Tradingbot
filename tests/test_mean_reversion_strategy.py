"""
Unit tests for the Mean Reversion & VWAP Volatility Band strategy agent.
"""
from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from mean_reversion_vwap_strategy import (
    MeanReversionVWAPAgent,
    MRDir,
    MRVote,
    calc_vwap_bands,
    calc_rvol,
    calc_rsi,
    detect_pinbar,
    mean_reversion_levels,
)


def _make_df(n: int = 60, base: float = 2650.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="1min")
    close = np.linspace(base, base + 10.0, n)
    high = close + 1.5
    low = close - 1.5
    open_ = close - 0.5
    volume = np.full(n, 100.0)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


def _make_flat_df(n: int = 60, base: float = 2650.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="1min")
    close = np.full(n, base) + np.sin(np.linspace(0, 3.14 * 2, n)) * 0.5
    high = close + 0.3
    low = close - 0.3
    open_ = close - 0.1
    volume = np.full(n, 100.0)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


class TestMeanReversionVWAPStrategy(unittest.TestCase):
    def setUp(self):
        self.agent = MeanReversionVWAPAgent()

    def test_calc_vwap_bands_basic(self):
        df = _make_df(60, 2650.0)
        bands = calc_vwap_bands(df, window=50)
        self.assertGreater(bands["vwap"], 2640.0)
        self.assertGreater(bands["upper_1"], bands["vwap"])
        self.assertLess(bands["lower_1"], bands["vwap"])
        self.assertGreater(bands["upper_2"], bands["upper_1"])
        self.assertLess(bands["lower_2"], bands["lower_1"])
        self.assertGreater(bands["bandwidth"], 0.0)

    def test_calc_vwap_bands_empty_and_degraded(self):
        bands = calc_vwap_bands(pd.DataFrame())
        self.assertEqual(bands["vwap"], 0.0)

        # None df
        bands_none = calc_vwap_bands(None)
        self.assertEqual(bands_none["vwap"], 0.0)

        # Zero volume
        df = _make_df(20)
        df["volume"] = 0.0
        bands_zero_vol = calc_vwap_bands(df)
        self.assertGreater(bands_zero_vol["vwap"], 0.0)

    def test_calc_rvol(self):
        df = _make_df(30)
        # Normal volume
        rvol = calc_rvol(df, period=20)
        self.assertAlmostEqual(rvol, 1.0, places=1)

        # Volume spike on last bar
        df.iloc[-1, df.columns.get_loc("volume")] = 300.0
        rvol_spike = calc_rvol(df, period=20)
        self.assertGreaterEqual(rvol_spike, 2.5)

    def test_calc_rsi(self):
        df = _make_df(40)
        rsi = calc_rsi(df, period=14)
        self.assertGreaterEqual(rsi, 0.0)
        self.assertLessEqual(rsi, 100.0)

    def test_detect_pinbar(self):
        # Bullish hammer: open=100, high=101, low=90, close=99 (long lower wick)
        df_hammer = pd.DataFrame([{
            "open": 98.0, "high": 99.0, "low": 90.0, "close": 98.5, "volume": 100.0
        }])
        is_bull, is_bear = detect_pinbar(df_hammer)
        self.assertTrue(is_bull)
        self.assertFalse(is_bear)

        # Bearish shooting star: open=91, high=100, low=90, close=90.5 (long upper wick)
        df_star = pd.DataFrame([{
            "open": 91.5, "high": 100.0, "low": 90.0, "close": 91.0, "volume": 100.0
        }])
        is_bull, is_bear = detect_pinbar(df_star)
        self.assertFalse(is_bull)
        self.assertTrue(is_bear)

    def test_oversold_long_vote(self):
        df = _make_df(60, 2650.0)
        bands = calc_vwap_bands(df)
        # Force last bar to dip below lower_2 with hammer rejection and volume spike
        lower_2 = bands["lower_2"]
        df.iloc[-1, df.columns.get_loc("open")] = lower_2 - 5.0
        df.iloc[-1, df.columns.get_loc("high")] = lower_2 - 4.0
        df.iloc[-1, df.columns.get_loc("low")] = lower_2 - 25.0
        df.iloc[-1, df.columns.get_loc("close")] = lower_2 - 4.5
        df.iloc[-1, df.columns.get_loc("volume")] = 500.0

        vote = self.agent.vote(df)
        self.assertEqual(vote.agent, "MEAN-REVERSION")
        self.assertEqual(vote.direction, MRDir.LONG)
        self.assertGreater(vote.strength, 0.60)
        self.assertIn("oversold", vote.reason)

    def test_overbought_short_vote(self):
        df = _make_df(60, 2650.0)
        bands = calc_vwap_bands(df)
        # Force last bar to spike above upper_2 with shooting star and volume spike
        upper_2 = bands["upper_2"]
        df.iloc[-1, df.columns.get_loc("open")] = upper_2 + 5.0
        df.iloc[-1, df.columns.get_loc("high")] = upper_2 + 25.0
        df.iloc[-1, df.columns.get_loc("low")] = upper_2 + 4.0
        df.iloc[-1, df.columns.get_loc("close")] = upper_2 + 4.5
        df.iloc[-1, df.columns.get_loc("volume")] = 500.0

        vote = self.agent.vote(df)
        self.assertEqual(vote.agent, "MEAN-REVERSION")
        self.assertEqual(vote.direction, MRDir.SHORT)
        self.assertGreater(vote.strength, 0.60)
        self.assertIn("overbought", vote.reason)

    def test_equilibrium_flat_vote(self):
        df = _make_flat_df(60, 2650.0)
        vote = self.agent.vote(df)
        self.assertEqual(vote.agent, "MEAN-REVERSION")
        self.assertEqual(vote.direction, MRDir.FLAT)
        self.assertEqual(vote.strength, 0.0)
        self.assertIn("equilibrium", vote.reason)

    def test_edge_cases(self):
        # None df
        v_none = self.agent.vote(None)
        self.assertEqual(v_none.direction, MRDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        # Empty df
        v_empty = self.agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, MRDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        # Insufficient bars (<30)
        v_short = self.agent.vote(_make_df(20))
        self.assertEqual(v_short.direction, MRDir.FLAT)
        self.assertEqual(v_short.reason, "Need more bars")

        # Missing OHLC columns
        bad_df = pd.DataFrame({"foo": range(50)})
        v_bad = self.agent.vote(bad_df)
        self.assertEqual(v_bad.direction, MRDir.FLAT)
        self.assertEqual(v_bad.reason, "Missing OHLC columns")

    def test_mean_reversion_levels(self):
        df = _make_df(50)
        levels = mean_reversion_levels(df)
        self.assertIn("vwap", levels)
        self.assertIn("vwap_upper_1", levels)
        self.assertIn("vwap_lower_1", levels)
        self.assertGreater(levels["vwap"], 0.0)


if __name__ == "__main__":
    unittest.main()
