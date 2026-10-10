"""Regression tests for the research-only market-regime classifier."""
import unittest

import numpy as np
import pandas as pd

from market_regime import classify_market_regime


def make_candles(close, spread=0.4):
    close = np.asarray(close, dtype=float)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC"),
    )


class TestMarketRegime(unittest.TestCase):
    def test_persistent_trend_is_classified_as_trending(self):
        close = 100 + np.arange(180, dtype=float) * 0.2
        result = classify_market_regime(make_candles(close))
        self.assertEqual(result["regime"], "trending")
        self.assertEqual(result["trend_direction"], "bullish")
        self.assertGreaterEqual(result["adx"], 25)
        self.assertEqual(result["classification_kind"], "descriptive_heuristic_not_prediction")

    def test_range_bound_market_is_classified_as_ranging_or_transition(self):
        close = np.full(180, 100.0)
        result = classify_market_regime(make_candles(close, spread=0.2))
        self.assertEqual(result["regime"], "ranging")
        self.assertEqual(result["trend_direction"], "neutral")

    def test_extreme_latest_range_is_flagged_as_high_volatility(self):
        close = np.full(180, 100.0)
        data = make_candles(close, spread=0.05)
        data.iloc[-1, data.columns.get_loc("high")] = 110.0
        data.iloc[-1, data.columns.get_loc("low")] = 90.0
        result = classify_market_regime(data)
        self.assertEqual(result["regime"], "high_volatility")
        self.assertGreater(result["atr_pct"], result["historical_atr_pct_threshold"])

    def test_volatility_is_normalized_for_different_price_scales(self):
        close = 100 + np.arange(180, dtype=float) * 0.2
        low_scale = classify_market_regime(make_candles(close))
        high_scale = classify_market_regime(make_candles(close * 100))
        self.assertEqual(low_scale["regime"], high_scale["regime"])
        self.assertAlmostEqual(low_scale["atr_pct"], high_scale["atr_pct"], places=5)

    def test_rejects_insufficient_history_and_bad_parameters(self):
        with self.assertRaisesRegex(ValueError, "Not enough candles"):
            classify_market_regime(make_candles(np.arange(30, dtype=float) + 100))
        with self.assertRaisesRegex(ValueError, "high_volatility_percentile"):
            classify_market_regime(
                make_candles(100 + np.arange(180, dtype=float) * 0.2),
                high_volatility_percentile=1.0,
            )

    def test_reports_last_candle_timestamp(self):
        data = make_candles(100 + np.arange(180, dtype=float) * 0.2)
        result = classify_market_regime(data)
        self.assertEqual(result["candle_time"], data.index[-1].isoformat())


if __name__ == "__main__":
    unittest.main()
