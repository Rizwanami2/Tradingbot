"""Regression tests for the isolated historical backtesting engine."""
import unittest
import numpy as np
import pandas as pd
from backtest_engine import BacktestConfig, run_backtest, _validated_frame

def candles(closes):
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) + 0.6
    lows = np.minimum(opens, closes) - 0.6
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1}, index=idx)

class TestBacktestEngine(unittest.TestCase):
    def test_empty_and_malformed_data_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            run_backtest(pd.DataFrame())
        bad = candles(list(range(10, 40)))
        bad.iloc[5, bad.columns.get_loc("high")] = 1
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            _validated_frame(bad)

    def test_duplicate_timestamps_rejected(self):
        data = candles(list(range(10, 50)))
        data.index = [data.index[0]] * len(data)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _validated_frame(data)

    def test_non_finite_config_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            BacktestConfig(initial_equity=float("nan")).validate()
        with self.assertRaisesRegex(ValueError, "risk_per_trade"):
            BacktestConfig(risk_per_trade=0.10).validate()

    def test_metrics_and_equity_are_consistent(self):
        data = candles([10,9,8,7,8,9,10,11,12,13,12,11,10,9,8,7,8,9,10,11,12,13,14,15,14,13,12,11,10,9,8,9,10,11,12,13,14,15,16,17,16,15])
        cfg = BacktestConfig(initial_equity=10000, risk_per_trade=0.01, max_leverage=3, fee_rate=0.0004, slippage_bps=2, fast_ema=3, slow_ema=6, atr_period=3, stop_atr=1.5, reward_risk=1.5)
        result = run_backtest(data, cfg, symbol="TEST", timeframe="1h")
        self.assertGreater(result["candles"], 0)
        self.assertAlmostEqual(result["final_equity"], result["initial_equity"] + result["net_pnl"])
        self.assertGreaterEqual(result["max_drawdown_pct"], 0)
        self.assertLessEqual(result["max_drawdown_pct"], 100)
        self.assertEqual(len(result["trades_detail"]), result["trades"])
        self.assertEqual(len(result["equity_curve"]), len(data) - 1)
        for trade in result["trades_detail"]:
            self.assertGreater(trade["quantity"], 0)
            self.assertGreater(trade["fees"], 0)
            self.assertIn(trade["exit_reason"], {"stop", "target", "end_of_data"})

    def test_fees_and_slippage_do_not_improve_results(self):
        data = candles([10,9,8,7,8,9,10,11,12,13,12,11,10,9,8,7,8,9,10,11,12,13,14,15,14,13,12,11,10,9,8,9,10,11,12,13,14,15,16,17,16,15])
        base = dict(initial_equity=10000, risk_per_trade=0.01, max_leverage=3, fast_ema=3, slow_ema=6, atr_period=3, stop_atr=1.5, reward_risk=1.5)
        free = run_backtest(data, BacktestConfig(**base, fee_rate=0, slippage_bps=0))
        costly = run_backtest(data, BacktestConfig(**base, fee_rate=0.001, slippage_bps=10))
        self.assertLessEqual(costly["final_equity"], free["final_equity"])

    def test_stop_wins_when_stop_and_target_touch_same_candle(self):
        from backtest_engine import _indicators
        data = candles([10,9,8,7,8,9,10,11,12,13,12,11,10,9,8,7,8,9,10,11,12,13,14,15,14,13,12,11,10,9,8,9,10,11,12,13,14,15,16,17,16,15])
        cfg = BacktestConfig(initial_equity=10000, risk_per_trade=0.01, max_leverage=3,
                             fee_rate=0, slippage_bps=0, fast_ema=3, slow_ema=6,
                             atr_period=3, stop_atr=1.0, reward_risk=1.0, allow_short=False)
        prepared = _indicators(_validated_frame(data), cfg)
        signals = np.flatnonzero(prepared["signal"].to_numpy() == 1)
        self.assertGreater(len(signals), 0)
        entry_i = int(signals[0]) + 1
        data.iloc[entry_i, data.columns.get_loc("high")] = data.iloc[entry_i]["close"] + 100
        data.iloc[entry_i, data.columns.get_loc("low")] = max(0.1, data.iloc[entry_i]["close"] - 100)
        result = run_backtest(data, cfg, symbol="TEST", timeframe="1h")
        entry_time = str(data.index[entry_i])
        trade = next((t for t in result["trades_detail"] if t["entry_time"] == entry_time), None)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "stop")

    def test_insufficient_history_rejected(self):
        with self.assertRaisesRegex(ValueError, "Not enough candles"):
            run_backtest(candles([10,11,12,13,14]), BacktestConfig(fast_ema=2, slow_ema=4, atr_period=3))

if __name__ == "__main__":
    unittest.main()
