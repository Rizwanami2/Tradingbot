#!/usr/bin/env python3
"""
Unit tests verifying that:
1. Scalp profile generates current market position signals with immediate fast market execution.
2. Balanced profile generates perfect entry level signals (best_entry from institutional / structural planning),
   NOT filling at current market price, but registering as pending limit setups until price touches the level.
"""
import unittest
import pandas as pd
import numpy as np

from entry_level_planner import EntryPlan, plan_best_entry, attach_plan_to_signal
from execution_engine import ExecutionEngine
from risk_manager import RiskManager
from strategy_profiles import StrategyProfile, apply_profile, get_current_profile
from signal_pipeline import format_signal_message, try_paper_execute
from anti_spam import get_anti_spam_limiter


def _make_dummy_df(mark: float = 65000.0, n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="1min")
    closes = np.linspace(mark - 200, mark, n)
    highs = closes + 10.0
    lows = closes - 10.0
    opens = closes - 2.0
    volumes = np.full(n, 100.0)
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=dates)


class TestPerfectEntryScalpVsBalanced(unittest.TestCase):
    def setUp(self):
        get_anti_spam_limiter().reset()
        self.risk = RiskManager(equity=10000.0)
        self.engine = ExecutionEngine(risk_manager=self.risk)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def tearDown(self):
        get_anti_spam_limiter().reset()
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def test_scalp_profile_uses_current_market_position(self):
        """Scalp signals must execute at current market price with fast_market_fill=True."""
        mark = 65000.0
        plan = EntryPlan(
            direction="LONG",
            mark=mark,
            best_entry=64600.0,
            entry_low=64500.0,
            entry_high=64700.0,
            stop=64400.0,
            tp1=65300.0,
            tp2=65600.0,
            confidence=0.85,
        )
        scalp_sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": mark,
            "stop": mark - 200.0,
            "tp1": mark + 300.0,
            "tp2": mark + 600.0,
            "is_scalp": True,
            "fast_market_fill": True,
            "lots": 0.05,
            "size": 0.05,
            "leverage": 20.0,
        }

        attached = attach_plan_to_signal(scalp_sig, plan)

        # In scalp, entry must remain at current market mark price
        self.assertTrue(attached["is_scalp"])
        self.assertTrue(attached["fast_market_fill"])
        self.assertFalse(attached.get("is_perfect_entry", False))
        self.assertEqual(attached["entry"], mark)
        self.assertEqual(attached["market_price"], mark)

        # Paper execute under scalp fills immediately at market
        ok, note = try_paper_execute(self.engine, attached, candle_price=mark)
        self.assertTrue(ok)
        self.assertIn("BTCUSDT", self.engine.active_positions)
        self.assertEqual(len(self.engine.pending_setups), 0)

    def test_balanced_profile_uses_perfect_entry_level_not_current_market(self):
        """Balanced signals must give perfect entry level signal and register as pending limit."""
        mark = 65000.0
        perfect_level = 64650.0  # Discount pullback level below current market
        plan = EntryPlan(
            direction="LONG",
            mark=mark,
            best_entry=perfect_level,
            entry_low=64600.0,
            entry_high=64700.0,
            stop=64350.0,
            tp1=65500.0,
            tp2=65800.0,
            confidence=0.90,
        )
        balanced_sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": mark,
            "stop": mark - 400.0,
            "tp1": mark + 600.0,
            "tp2": mark + 1000.0,
            "is_scalp": False,
            "fast_market_fill": False,
            "lots": 0.05,
            "size": 0.05,
            "leverage": 20.0,
        }

        attached = attach_plan_to_signal(balanced_sig, plan)

        # In balanced, entry MUST be the calculated perfect entry level, NOT current market!
        self.assertFalse(attached["is_scalp"])
        self.assertFalse(attached["fast_market_fill"])
        self.assertTrue(attached["is_perfect_entry"])
        self.assertEqual(attached["entry"], perfect_level)
        self.assertNotEqual(attached["entry"], mark)
        self.assertEqual(attached["market_price"], mark)
        self.assertEqual(attached["stop"], 64350.0)

        # When executed at current market price (65000), it must NOT fill immediately.
        # It must stay PENDING waiting for pullback to perfect_level!
        ok, note = try_paper_execute(self.engine, attached, candle_price=mark)
        self.assertTrue(ok)
        self.assertIn("Setup pending", note)
        self.assertEqual(len(self.engine.active_positions), 0)
        self.assertEqual(len(self.engine.pending_setups), 1)

        # Now simulate market price pulling back to touch the perfect entry level
        events = self.engine.update_market_candle("BTCUSDT", {
            "open": 64700.0,
            "high": 64750.0,
            "low": 64600.0,  # Touches 64650
            "close": 64620.0,
        })
        self.assertTrue(any(ev.get("event") == "TRADE_EXECUTED" for ev in events))
        self.assertIn("BTCUSDT", self.engine.active_positions)
        self.assertEqual(len(self.engine.pending_setups), 0)

    def test_format_signal_message_distinguishes_scalp_and_balanced(self):
        """Format signal message clearly labels SCALP (Market) vs BALANCED (Perfect Entry)."""
        scalp_res = {
            "symbol": "BTCUSDT",
            "price": 65000.0,
            "signal": {
                "direction": "LONG",
                "entry": 65000.0,
                "stop": 64800.0,
                "tp1": 65300.0,
                "tp2": 65600.0,
                "is_scalp": True,
                "fast_market_fill": True,
            },
            "votes": [],
        }
        scalp_msg = format_signal_message(scalp_res)
        self.assertIn("SCALP — CURRENT MARKET", scalp_msg)
        self.assertIn("Market Order", scalp_msg)

        balanced_res = {
            "symbol": "BTCUSDT",
            "price": 65000.0,
            "signal": {
                "direction": "LONG",
                "entry": 64650.0,
                "stop": 64350.0,
                "tp1": 65500.0,
                "tp2": 65800.0,
                "is_scalp": False,
                "fast_market_fill": False,
                "is_perfect_entry": True,
                "market_price": 65000.0,
            },
            "votes": [],
        }
        balanced_msg = format_signal_message(balanced_res)
        self.assertIn("BALANCED — PERFECT ENTRY LEVEL", balanced_msg)
        self.assertIn("Pending Limit Setup", balanced_msg)
        self.assertIn("Current Market", balanced_msg)

    def test_arbiter_decide_scalp_vs_balanced_consensus(self):
        """Arbiter.decide in scalp generates market entry, while balanced generates perfect entry."""
        from multi_agent_trading_bot import Arbiter, Dir, Vote
        df1 = _make_dummy_df(65000.0)

        class MockAgent:
            def __init__(self, name, dir_):
                self.name = name
                self.dir_ = dir_
            def vote(self, *a, **k):
                return Vote(self.name, self.dir_, 0.9, "bullish")

        # 1. Scalp Arbiter
        arb_scalp = Arbiter()
        arb_scalp.is_scalp = True
        arb_scalp.fast_market_fill = True
        arb_scalp.agents = [MockAgent("TREND", Dir.LONG), MockAgent("INDICATORS", Dir.LONG), MockAgent("GAUTAM-JHA", Dir.LONG)]
        for attr in ["mr", "fb", "mtf", "asian", "mt5", "orr", "sd", "ote", "sqz", "ribbon", "stoch", "auto_trend"]:
            setattr(arb_scalp, attr, None)

        sig_scalp, why_scalp, _ = arb_scalp.decide(df1, None, None, None, None, self.risk, symbol="BTCUSDT", on_demand=True)
        self.assertIsNotNone(sig_scalp)
        self.assertTrue(sig_scalp["is_scalp"])
        self.assertTrue(sig_scalp["fast_market_fill"])
        self.assertFalse(sig_scalp.get("is_perfect_entry", False))
        self.assertEqual(sig_scalp["entry"], 65000.0)

        # 2. Balanced Arbiter
        arb_bal = Arbiter()
        arb_bal.is_scalp = False
        arb_bal.fast_market_fill = False
        arb_bal.agents = [MockAgent("TREND", Dir.LONG), MockAgent("INDICATORS", Dir.LONG), MockAgent("GAUTAM-JHA", Dir.LONG)]
        for attr in ["mr", "fb", "mtf", "asian", "mt5", "orr", "sd", "ote", "sqz", "ribbon", "stoch", "auto_trend"]:
            setattr(arb_bal, attr, None)

        sig_bal, why_bal, _ = arb_bal.decide(df1, None, None, None, None, self.risk, symbol="BTCUSDT", on_demand=True)
        self.assertIsNotNone(sig_bal)
        self.assertFalse(sig_bal["is_scalp"])
        self.assertFalse(sig_bal["fast_market_fill"])
        self.assertTrue(sig_bal["is_perfect_entry"])
        # Entry level must be a calculated structural level, not raw current market close
        self.assertNotEqual(sig_bal["entry"], 65000.0)
        self.assertIn("BALANCED", why_bal)
        self.assertIn("Perfect Entry", why_bal)


if __name__ == "__main__":
    unittest.main()
