"""
Unit tests for artifacts/combined_bot_agent.py
Validates diagnostic checks, single-direction protection, cooldown enforcement,
and single-cycle execution of the combined bot & agent system.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from anti_spam import get_anti_spam_limiter
from artifacts.combined_bot_agent import (
    run_diagnostic_check,
    run_single_cycle,
    setup_environment,
)
from execution_engine import ExecutionEngine
from mt5_broker import MockMT5Broker
from risk_manager import RiskManager


class TestCombinedBotAgent(unittest.TestCase):
    def setUp(self):
        self.state_file = "test_combined_bot_agent_state.json"
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except Exception:
                pass
        self.broker = MockMT5Broker(initial_balance=10000.0)
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.rm, broker=self.broker, state_file=self.state_file)
        self.limiter = get_anti_spam_limiter()
        self.limiter.reset()
        self.limiter.set_mode("on")
        self.limiter.set_entry_cooldown(45)

    def tearDown(self):
        self.limiter.reset()
        self.limiter.set_mode("auto")
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except Exception:
                pass

    def test_run_diagnostic_check(self):
        """Diagnostic check must report all operational subsystems."""
        diag = run_diagnostic_check()
        self.assertTrue(diag["all_ok"])
        details = diag["details"]
        self.assertIn("Multi-Agent Arbiter", details)
        self.assertIn("AI Advanced Suite", details)
        self.assertIn("Autonomous Learner", details)
        self.assertIn("Execution Engine", details)
        self.assertIn("Single-Direction Protection", details)
        self.assertIn("Cooldown Protection", details)
        self.assertTrue(details["Single-Direction Protection"][0])
        self.assertTrue(details["Cooldown Protection"][0])

    def test_strict_single_direction_in_combined_agent(self):
        """Active BUY strictly blocks incoming SELL on Gold in combined agent."""
        buy_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0, "stop": 2640.0,
            "tp1": 2665.0, "tp2": 2675.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 20.0, "rr": 1.5,
        }
        self.engine.register_setup(buy_sig)
        candle = {"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}
        self.engine.update_market_candle("XAUUSD", candle)

        can_enter, reason = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2651.0, engine=self.engine, direction="SHORT"
        )
        self.assertFalse(can_enter)
        self.assertIn("cannot BUY and SELL at the same time", reason)

    def test_run_single_cycle_dry_run_structure(self):
        """Single cycle dry run must produce valid evaluation report."""
        import pandas as pd
        from datetime import datetime, timezone
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=50, freq="1min")
        df1 = pd.DataFrame({
            "open": [2650.0] * 50, "high": [2652.0] * 50, "low": [2648.0] * 50,
            "close": [2651.0] * 50, "volume": [100.0] * 50
        }, index=dates)
        mock_data = {"1m": df1, "5m": df1, "15m": df1, "1h": df1, "daily": df1}

        with patch("multi_agent_trading_bot._ImprovedDataFeed", None), \
             patch("multi_agent_trading_bot.MarketDataFeed.multi_tf", return_value=mock_data), \
             patch("data_feed.DataFeed.multi_tf", return_value=mock_data):
            report = run_single_cycle(symbols=["BTCUSDT"], dry_run=True, broker=self.broker)
            self.assertIn("timestamp", report)
            self.assertIn("evaluations", report)
            self.assertIn("BTCUSDT", report["evaluations"])


if __name__ == "__main__":
    unittest.main()
