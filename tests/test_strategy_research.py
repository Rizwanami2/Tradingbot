"""Tests for research-only signal analysis and journaling utilities."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_research import (
    analyze_timeframes,
    append_trade_journal,
    compare_backtest_results,
    summarize_trade_journal,
)


def candles(trend="up", n=80):
    x = np.arange(n, dtype=float)
    close = 100 + (x * 0.3 if trend == "up" else -x * 0.3)
    close = np.maximum(close, 10)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


class TestStrategyResearch(unittest.TestCase):
    def test_multi_timeframe_alignment_is_explainable_and_bounded(self):
        result = analyze_timeframes(
            {"5m": candles("up"), "15m": candles("up"), "1h": candles("up")},
            fast_ema=4, slow_ema=10, atr_period=5,
        )
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["timeframes_used"], 3)
        self.assertGreater(result["quality_score"], 0)
        self.assertLessEqual(result["quality_score"], 100)
        self.assertEqual(result["score_kind"], "heuristic_alignment_not_probability")
        self.assertIn("ema_fast", result["timeframe_details"]["1h"])

    def test_conflicting_timeframes_are_reported(self):
        result = analyze_timeframes(
            {"5m": candles("up"), "15m": candles("down"), "1h": candles("up")},
            fast_ema=4, slow_ema=10, atr_period=5,
        )
        self.assertEqual(result["direction"], "LONG")
        self.assertGreaterEqual(result["conflicting_timeframes"], 1)

    def test_invalid_windows_and_insufficient_history_rejected(self):
        with self.assertRaises(ValueError):
            analyze_timeframes({"1h": candles()}, fast_ema=12, slow_ema=10)
        with self.assertRaisesRegex(ValueError, "enough valid candles"):
            analyze_timeframes({"1m": candles(n=5)}, fast_ema=3, slow_ema=6, atr_period=3)

    def test_journal_aggregates_by_strategy_symbol_timeframe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            common = {"strategy": "ema", "symbol": "BTC/USDT", "timeframe": "1h",
                      "entry_time": "2025-01-01T00:00:00Z", "exit_time": "2025-01-01T01:00:00Z"}
            append_trade_journal(path, {**common, "net_pnl": 10})
            append_trade_journal(path, {**common, "net_pnl": -4})
            summary = summarize_trade_journal(path)
            self.assertEqual(summary["trades"], 2)
            group = summary["groups"][0]
            self.assertEqual(group["trades"], 2)
            self.assertAlmostEqual(group["net_pnl"], 6)
            self.assertEqual(group["win_rate_pct"], 50)

    def test_journal_rejects_missing_fields_and_corrupt_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            with self.assertRaisesRegex(ValueError, "missing"):
                append_trade_journal(path, {"strategy": "ema"})
            path.write_text("{bad-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid trade journal row"):
                summarize_trade_journal(path)

    def test_backtest_comparison_ranks_return_relative_to_drawdown(self):
        result = compare_backtest_results([
            {"strategy": "A", "total_return_pct": 12, "max_drawdown_pct": 8,
             "trades": 20, "win_rate_pct": 55, "profit_factor": 1.4},
            {"strategy": "B", "total_return_pct": 10, "max_drawdown_pct": 2,
             "trades": 20, "win_rate_pct": 52, "profit_factor": 1.3},
        ])
        self.assertEqual(result[0]["strategy"], "B")
        with self.assertRaisesRegex(ValueError, "Out-of-range"):
            compare_backtest_results([
                {"strategy": "bad", "total_return_pct": 2, "max_drawdown_pct": -1,
                 "trades": 10, "win_rate_pct": 50, "profit_factor": 1.1}
            ])


if __name__ == "__main__":
    unittest.main()
