"""
Unit tests for MT5 Chart Agent, Multi-Timeframe Broker Feeds, Arbiter Integration,
and Signal Pipeline Telemetry for Bitcoin and Gold.
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from multi_agent_trading_bot import (
    Arbiter,
    MT5ChartAgent,
    MarketDataFeed,
    evaluate_symbol,
    Dir,
)
from risk_manager import RiskManager
from strategy_profiles import DEFAULT_PROFILES, StrategyProfile
from regime_auto_switcher import calibrate_rolling_sharpe_weights
from signal_pipeline import run_signal, format_signal_message
from mt5_chart import (
    get_mt5_live_chart,
    build_chart_keyboard,
    normalize_mt5_symbol,
    get_display_names,
)


class TestMultiAgentMT5Chart(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        n = 100
        idx = pd.date_range("2026-01-01", periods=n, freq="1min")
        close = 2000.0 + np.cumsum(np.random.randn(n) * 0.5)
        high = close + np.random.rand(n) * 1.0
        low = close - np.random.rand(n) * 1.0
        open_ = close + np.random.randn(n) * 0.2
        volume = np.random.randint(10, 100, size=n)
        self.df1 = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
        self.daily = self.df1.copy()

    def test_mt5_chart_agent_voting(self):
        agent = MT5ChartAgent()
        vote = agent.vote(self.df1, symbol="XAUUSD")
        self.assertEqual(vote.agent, "MT5-CHART")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)
        self.assertTrue(len(vote.reason) > 0)

    def test_mt5_chart_agent_btc_voting(self):
        agent = MT5ChartAgent()
        vote = agent.vote(self.df1, symbol="BTCUSD")
        self.assertEqual(vote.agent, "MT5-CHART")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

    def test_mt5_chart_agent_fallback_on_error(self):
        agent = MT5ChartAgent()
        with patch("mt5_chart.get_mt5_live_chart", side_effect=Exception("Connection reset")):
            vote = agent.vote(self.df1, symbol="XAUUSD")
            self.assertEqual(vote.agent, "MT5-CHART")
            self.assertEqual(vote.direction, Dir.FLAT)
            self.assertEqual(vote.strength, 0.0)
            self.assertIn("Connection reset", vote.reason)

    def test_market_data_feed_multi_tf(self):
        feed = MarketDataFeed(symbol="XAUUSD", source="demo")
        data = feed.multi_tf(limit_1m=100)
        self.assertIn("1m", data)
        self.assertIn("5m", data)
        self.assertIn("15m", data)
        self.assertIn("1h", data)
        self.assertIn("daily", data)
        self.assertFalse(data["1m"].empty)

    def test_market_data_feed_with_mock_broker(self):
        feed = MarketDataFeed(symbol="XAUUSD", source="demo")
        mock_broker = MagicMock()
        mock_broker.get_candles.return_value = self.df1.copy()
        with patch("mt5_broker.get_active_broker", return_value=mock_broker):
            data = feed.multi_tf(limit_1m=100)
            self.assertIn("5m", data)
            self.assertIn("daily", data)
            self.assertEqual(len(data["5m"]), len(self.df1))

    def test_arbiter_weights_include_mt5_chart(self):
        arb = Arbiter()
        self.assertIn("MT5-CHART", arb.WEIGHTS)
        self.assertEqual(arb.WEIGHTS["MT5-CHART"], 1.35)
        self.assertIsNotNone(getattr(arb, "mt5_chart", None))

    def test_arbiter_decide_includes_mt5_chart_vote(self):
        risk = RiskManager(equity=10000.0, symbol="XAUUSD")
        arb = Arbiter()
        sig, msg, votes = arb.decide(
            self.df1, self.df1, self.df1, self.df1, self.daily, risk, symbol="XAUUSD", on_demand=True
        )
        agents_voted = [v.agent for v in votes]
        self.assertIn("MT5-CHART", agents_voted)
        if sig is not None:
            self.assertIn("mt5_chart", sig)

    def test_strategy_profiles_mt5_chart_weights(self):
        default_prof = StrategyProfile(name="test")
        self.assertIn("MT5-CHART", default_prof.weights)
        self.assertEqual(default_prof.weights["MT5-CHART"], 1.35)

        for prof_name in ("balanced", "scalp", "swing", "gold_only", "crypto"):
            self.assertIn(prof_name, DEFAULT_PROFILES)
            prof = DEFAULT_PROFILES[prof_name]
            self.assertIn("MT5-CHART", prof.weights, f"{prof_name} missing MT5-CHART weight")
            self.assertGreater(prof.weights["MT5-CHART"], 1.0)

    def test_regime_switcher_calibration_weights(self):
        w = calibrate_rolling_sharpe_weights()
        self.assertIn("MT5-CHART", w)
        self.assertEqual(w["MT5-CHART"], 1.35)

    def test_evaluate_symbol_has_mt5_chart(self):
        res = evaluate_symbol("XAUUSD", source="demo")
        self.assertIn("mt5_chart", res)
        agent_names = [v["agent"] for v in res["votes"]]
        self.assertIn("MT5-CHART", agent_names)

    def test_signal_pipeline_mt5_telemetry(self):
        res = run_signal("XAUUSD", source="demo")
        self.assertIn("mt5_chart", res)
        msg = format_signal_message(res)
        self.assertIn("MT5 Institutional Chart Telemetry", msg)
        self.assertIn("Structure Bias", msg)
        self.assertIn("Floor Pivots", msg)
        self.assertIn("Oscillators & Vol", msg)

    def test_build_chart_keyboard(self):
        kb = build_chart_keyboard(active="gold")
        self.assertIsNotNone(kb)
        # Check callback data options
        all_cb = []
        for row in kb.inline_keyboard:
            for btn in row:
                all_cb.append(btn.callback_data)
        self.assertIn("chart:gold", all_cb)
        self.assertIn("chart:btc", all_cb)
        self.assertIn("chart:both", all_cb)
        self.assertIn("chart:refresh:gold", all_cb)
        self.assertIn("sig:XAUUSD", all_cb)
        self.assertIn("sig:BTCUSDT", all_cb)

    def test_normalize_mt5_symbol(self):
        self.assertEqual(normalize_mt5_symbol("BTCUSD"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("BTCUSDT"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("btc"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("XAUUSD"), "XAUUSD")
        self.assertEqual(normalize_mt5_symbol("gold"), "XAUUSD")
        self.assertEqual(get_display_names("BTCUSD")[0], "BTCUSD")
        self.assertEqual(get_display_names("XAUUSD")[0], "XAUUSD")


if __name__ == "__main__":
    unittest.main()
