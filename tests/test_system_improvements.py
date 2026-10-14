"""Unit tests for system-wide improvements: data feeds, risk management, execution safety, and indicators."""
from __future__ import annotations

import os
import tempfile
import unittest
import numpy as np
import pandas as pd

from data_feed import DataFeed
from risk_manager import RiskManager, INSTRUMENTS
from autonomous_learner import AutonomousLearner, Knowledge
from trading_strategy_indicators_pro import IndicatorEngine
from advanced_backtest import SimConfig, AdvancedSimulator, ema_cross_signal
from main import get_price_info, format_price_string


class TestSystemImprovements(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def test_data_feed_latest_price_schema(self):
        feed = DataFeed(symbol="XAUUSD", source="demo")
        quote = feed.get_latest_price()
        self.assertIn("ok", quote)
        self.assertTrue(quote["ok"])
        self.assertIn("mark", quote)
        self.assertGreater(quote["mark"], 0.0)
        self.assertIn("symbol", quote)
        self.assertEqual(quote["symbol"], "XAUUSD")
        self.assertIn("change_pct", quote)

    def test_risk_manager_instrument_profiles(self):
        self.assertIn("XAUTUSD", INSTRUMENTS)
        self.assertIn("BTCUSDT", INSTRUMENTS)
        self.assertNotIn("SOLUSDT", INSTRUMENTS)
        self.assertNotIn("ETHUSDT", INSTRUMENTS)
        self.assertNotIn("BNBUSDT", INSTRUMENTS)
        self.assertEqual(INSTRUMENTS["XAUTUSD"]["contract_size"], 1)
        self.assertEqual(INSTRUMENTS["PAXGUSDT"]["contract_size"], 1)

    def test_risk_manager_margin_capacity_blocking(self):
        # With tiny equity, affordable lots is less than min lot
        rm = RiskManager(equity=1.0, symbol="BTCUSDT")
        plan = rm.position_size(entry=60000.0, stop=59000.0)
        self.assertEqual(plan.lots, 0.0)
        self.assertIn("blocked", plan.reason)

    def test_autonomous_learner_atomic_save(self):
        mem_path = os.path.join(self.tmp_dir, "test_mem.json")
        learner = AutonomousLearner(memory_path=mem_path, auto_bootstrap=False)
        learner.knowledge.min_score = 2.15
        learner._save()
        self.assertTrue(os.path.exists(mem_path))
        # Reload in new instance
        learner2 = AutonomousLearner(memory_path=mem_path, auto_bootstrap=False)
        self.assertAlmostEqual(learner2.knowledge.min_score, 2.15)

    def test_indicator_adx_directional_logic(self):
        dates = pd.date_range("2026-01-01", periods=60, freq="1min")
        # Strong uptrend: higher highs and higher lows
        highs = np.linspace(100, 200, 60) + np.random.uniform(0.1, 0.5, 60)
        lows = np.linspace(90, 190, 60) - np.random.uniform(0.1, 0.5, 60)
        closes = np.linspace(95, 195, 60)
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes}, index=dates)

        adx, plus_di, minus_di = IndicatorEngine.adx(df, period=14)
        self.assertFalse(adx.isna().all())
        # In a monotonic uptrend, plus_di should exceed minus_di
        last_plus = float(plus_di.dropna().iloc[-1])
        last_minus = float(minus_di.dropna().iloc[-1])
        self.assertGreater(last_plus, last_minus)

    def test_format_price_string_ok(self):
        valid_quote = {
            "ok": True,
            "symbol": "XAUUSD",
            "mark": 4200.50,
            "close": 4200.50,
            "open": 4180.00,
            "high": 4210.00,
            "low": 4175.00,
            "change_pct": 0.49,
            "recommendation": "STRONG BUY",
            "source": "CCXT/Binance",
        }
        res = format_price_string(valid_quote)
        self.assertIn("XAUUSD", res)
        self.assertIn("4,200.50", res)
        self.assertIn("+0.49%", res)

    def test_advanced_simulator_bounds(self):
        cfg = SimConfig(initial_equity=10000.0, risk_per_trade=0.0025, max_leverage=20.0)
        sim = AdvancedSimulator(cfg)
        dates = pd.date_range("2026-01-01", periods=200, freq="1min")
        p = 2650 + np.cumsum(np.random.randn(200) * 0.4)
        df = pd.DataFrame({
            "open": p, "high": p + 1.2, "low": p - 1.2,
            "close": p + 0.1, "volume": 500
        }, index=dates)

        m, trades, curve = sim.run(df, ema_cross_signal, "XAUUSD")
        self.assertIsNotNone(m)
        self.assertGreater(len(curve), 1)
        for t in trades:
            # Sizing check: notional leverage <= max_leverage
            notional = t.size * t.entry
            self.assertLessEqual(notional, 10000.0 * 20.0 * 1.5)


if __name__ == "__main__":
    unittest.main()
