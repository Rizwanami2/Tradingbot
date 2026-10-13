"""Unit tests for 2-year candle analysis and caching."""
from __future__ import annotations

import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from historical_2y import (
    fetch_daily_2y,
    analyze_2y,
    format_history_2y,
    history_levels_for_planner,
    clear_history_cache,
    History2Y,
)


def _make_daily_df(n: int = 100, trend: str = "bull") -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="1D")
    if trend == "bull":
        close = np.linspace(50000.0, 70000.0, n)
    elif trend == "bear":
        close = np.linspace(70000.0, 50000.0, n)
    else:
        close = np.full(n, 60000.0) + np.sin(np.linspace(0, 10, n)) * 100.0

    high = close + 200.0
    low = close - 200.0
    open_ = close - 50.0
    vol = np.full(n, 1000.0)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    }, index=dates)


class TestHistorical2Y(unittest.TestCase):
    def setUp(self):
        clear_history_cache()

    def tearDown(self):
        clear_history_cache()

    def test_fetch_daily_2y_caching(self):
        sample_df = _make_daily_df(100)
        with patch("realtime_prices.fetch_ohlcv", return_value=sample_df) as mock_fetch:
            df1 = fetch_daily_2y("BTCUSDT")
            self.assertEqual(len(df1), 100)
            self.assertEqual(mock_fetch.call_count, 1)

            # Second call should use in-memory cache without hitting network
            df2 = fetch_daily_2y("BTCUSDT")
            self.assertEqual(len(df2), 100)
            self.assertEqual(mock_fetch.call_count, 1)

    def test_fetch_daily_2y_stale_fallback(self):
        sample_df = _make_daily_df(100)
        with patch("realtime_prices.fetch_ohlcv", return_value=sample_df):
            df_init = fetch_daily_2y("XAUUSD")
            self.assertEqual(len(df_init), 100)

        # Force network error across all providers
        with patch("realtime_prices.fetch_ohlcv", side_effect=RuntimeError("Network offline")):
            with patch("data_feed.DataFeed.fetch", side_effect=RuntimeError("DataFeed offline")):
                df_cached = fetch_daily_2y("XAUUSD")
                self.assertEqual(len(df_cached), 100)
                self.assertFalse(df_cached.empty)

    def test_clear_history_cache(self):
        sample_df = _make_daily_df(100)
        with patch("realtime_prices.fetch_ohlcv", return_value=sample_df) as mock_fetch:
            fetch_daily_2y("BTCUSDT")
            self.assertEqual(mock_fetch.call_count, 1)

            clear_history_cache()

            fetch_daily_2y("BTCUSDT")
            self.assertEqual(mock_fetch.call_count, 2)

    def test_analyze_2y_bullish(self):
        df_bull = _make_daily_df(100, trend="bull")
        h = analyze_2y("BTCUSDT", df=df_bull)
        self.assertEqual(h.symbol, "BTCUSDT")
        self.assertEqual(h.bars, 100)
        self.assertEqual(h.trend, "BULL")
        self.assertGreater(h.bias_score, 0.25)
        self.assertGreater(len(h.support), 0)
        self.assertGreater(len(h.resistance), 0)

        d = h.to_dict()
        self.assertEqual(d["trend"], "BULL")

    def test_analyze_2y_bearish(self):
        df_bear = _make_daily_df(100, trend="bear")
        h = analyze_2y("BTCUSDT", df=df_bear)
        self.assertEqual(h.symbol, "BTCUSDT")
        self.assertEqual(h.bars, 100)
        self.assertEqual(h.trend, "BEAR")
        self.assertLess(h.bias_score, -0.25)

    def test_analyze_2y_degraded_and_empty(self):
        # Empty df
        h_empty = analyze_2y("BTCUSDT", df=pd.DataFrame())
        # With patch ensuring fetch returns empty
        with patch("historical_2y.fetch_daily_2y", return_value=pd.DataFrame()):
            h_none = analyze_2y("BTCUSDT", df=None)
            self.assertEqual(h_none.bars, 0)
            self.assertEqual(h_none.trend, "RANGE")
            self.assertEqual(h_none.summary, "Insufficient 2y history")

        # Missing required OHLC columns
        bad_df = pd.DataFrame({"random_col": range(50)})
        h_bad = analyze_2y("BTCUSDT", df=bad_df)
        self.assertEqual(h_bad.bars, 0)
        self.assertEqual(h_bad.summary, "Missing OHLC columns in 2y history")

    def test_format_history_2y(self):
        empty_h = History2Y(
            symbol="BTCUSDT", bars=0, years_span=0.0, mark=0.0,
            high_2y=0.0, low_2y=0.0, high_52w=0.0, low_52w=0.0,
            ema50=0.0, ema200=0.0, trend="RANGE",
            summary="Insufficient 2y history",
        )
        msg_empty = format_history_2y(empty_h)
        self.assertIn("Insufficient 2y history", msg_empty)

        valid_h = History2Y(
            symbol="BTCUSDT", bars=100, years_span=0.27, mark=65000.0,
            high_2y=70000.0, low_2y=50000.0, high_52w=70000.0, low_52w=50000.0,
            ema50=62000.0, ema200=58000.0, trend="BULL",
            support=[50000.0, 55000.0], resistance=[68000.0, 70000.0],
            bias_score=0.45, summary="2Y Bull",
        )
        msg_valid = format_history_2y(valid_h)
        self.assertIn("2Y Candle Analysis", msg_valid)
        self.assertIn("BULL", msg_valid)

    def test_history_levels_for_planner(self):
        h = History2Y(
            symbol="BTCUSDT", bars=100, years_span=0.27, mark=65000.0,
            high_2y=70000.0, low_2y=50000.0, high_52w=70000.0, low_52w=50000.0,
            ema50=62000.0, ema200=58000.0, trend="BULL",
            support=[50000.0, 55000.0], resistance=[68000.0, 70000.0],
            bias_score=0.45, summary="2Y Bull",
        )
        levels = history_levels_for_planner(h)
        self.assertEqual(levels["high_2y"], 70000.0)
        self.assertEqual(levels["low_2y"], 50000.0)
        self.assertIn("sup2y_0", levels)
        self.assertIn("res2y_0", levels)


if __name__ == "__main__":
    unittest.main()
