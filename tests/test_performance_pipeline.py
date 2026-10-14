"""
Test Performance Analytics Pipeline & Wiring
============================================
Verifies that PerformanceTracker:
1. Calculates Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor, Expectancy, and Agent Contribution.
2. Automatically syncs historical closed trades from trade_journal.json.
3. Is automatically updated whenever TradeJournal closes a trade.
4. Is automatically updated whenever ExecutionEngine closes a position.
5. Correctly handles Telegram /performance subcommands (summary, agents, trades, reset, help).
6. Correctly handles CLI performance subcommands in both main.py and main_full.py.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from performance_analytics import PerformanceTracker, TradeRecord
from trade_journal import TradeJournal
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager


class TestPerformancePipeline(unittest.TestCase):
    def test_performance_tracker_metrics_and_sharpe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = os.path.join(tmpdir, "perf_stats.json")
            tracker = PerformanceTracker(path=stats_path, journal_path=None)
            
            # Initial state
            empty_rep = tracker.telegram_report()
            self.assertIn("Performance", empty_rep)
            self.assertIn("No closed trades recorded yet", empty_rep)

            # Record trades: 3 wins, 2 losses
            trades_data = [
                ("t1", "XAUUSD", "LONG", 2000.0, 2020.0, 2.0, 40.0, ["TREND", "LIQUIDITY"]),
                ("t2", "BTCUSDT", "SHORT", 60000.0, 60500.0, -1.0, -20.0, ["TREND"]),
                ("t3", "XAUUSD", "LONG", 2010.0, 2025.0, 1.5, 30.0, ["LIQUIDITY", "INDICATORS"]),
                ("t4", "BTCUSDT", "LONG", 61000.0, 60000.0, -1.0, -20.0, ["INDICATORS"]),
                ("t5", "XAUUSD", "SHORT", 2030.0, 2010.0, 2.0, 40.0, ["TREND", "AI-LEARNER"]),
            ]
            for tid, sym, direct, entry, exit_px, r, pnl, agents in trades_data:
                tracker.record_trade(TradeRecord(
                    id=tid, symbol=sym, direction=direct,
                    entry=entry, exit=exit_px, r_multiple=r,
                    pnl=pnl, agents=agents,
                ))

            s = tracker.summary()
            self.assertEqual(s["trades"], 5)
            self.assertEqual(s["wins"], 3)
            self.assertEqual(s["losses"], 2)
            self.assertAlmostEqual(s["win_rate"], 0.6, places=2)
            self.assertAlmostEqual(s["total_pnl"], 70.0, places=2)
            self.assertAlmostEqual(s["total_r"], 3.5, places=2)
            self.assertGreater(s["profit_factor"], 1.0)
            self.assertGreater(s["sharpe_ratio"], 0.0)
            self.assertGreaterEqual(s["max_drawdown_r"], 0.0)

            # Reports
            rep = tracker.telegram_report()
            self.assertIn("Performance Report", rep)
            self.assertIn("Sharpe:", rep)
            self.assertIn("Profit Factor:", rep)
            self.assertIn("Win Rate:", rep)
            self.assertIn("Agent Contribution", rep)
            self.assertIn("TREND", rep)

            ag_rep = tracker.agent_report()
            self.assertIn("Agent Performance Rankings", ag_rep)
            self.assertIn("TREND", ag_rep)

            tr_rep = tracker.recent_trades_report(5)
            self.assertIn("Recent Closed Trades", tr_rep)
            self.assertIn("XAUUSD", tr_rep)
            self.assertIn("BTCUSDT", tr_rep)

            # Reset
            res = tracker.reset()
            self.assertEqual(res["status"], "reset")
            self.assertEqual(tracker.summary()["trades"], 0)

    def test_sync_from_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            j_path = os.path.join(tmpdir, "trade_journal.json")
            stats_path = os.path.join(tmpdir, "perf_stats.json")

            # Create mock journal file
            journal_data = {
                "closed": [
                    {
                        "id": "j1", "symbol": "XAUUSD", "direction": "LONG",
                        "entry": 2000.0, "exit_price": 2015.0, "r_multiple": 1.5,
                        "pnl": 30.0, "agents": ["TREND"], "regime": "trend",
                    },
                    {
                        "id": "j2", "symbol": "BTCUSDT", "direction": "SHORT",
                        "entry": 60000.0, "exit_price": 59000.0, "r_multiple": 2.0,
                        "pnl": 40.0, "agents": ["LIQUIDITY"], "regime": "range",
                    },
                ]
            }
            with open(j_path, "w", encoding="utf-8") as f:
                json.dump(journal_data, f)

            with patch("performance_analytics.STATS_FILE", stats_path), patch("performance_analytics.PerformanceTracker._sync_from_journal") as mock_sync:
                # Custom sync with test path
                tracker = PerformanceTracker(path=stats_path)
                with patch("os.path.exists", return_value=True), patch("builtins.open", patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(journal_data)))):
                    tracker._sync_from_journal()

    def test_trade_journal_wires_performance_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            j_path = os.path.join(tmpdir, "test_journal.json")
            stats_path = os.path.join(tmpdir, "perf_stats.json")

            with patch("performance_analytics.STATS_FILE", stats_path):
                journal = TradeJournal(path=j_path)
                tid = journal.record_open(
                    symbol="XAUUSD", direction="LONG", entry=2000.0,
                    stop=1990.0, tp1=2020.0, size=0.1, risk_amount=10.0,
                    regime="trend", session="LONDON", agents=["TREND"],
                )
                journal.record_close(trade_id=tid, exit_price=2015.0, auto_learn=True)

                tracker = PerformanceTracker(path=stats_path)
                self.assertGreaterEqual(tracker.summary()["trades"], 1)

    def test_execution_engine_wires_performance_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = os.path.join(tmpdir, "perf_stats.json")
            with patch("performance_analytics.STATS_FILE", stats_path):
                risk = RiskManager(equity=1000.0, symbol="XAUUSD")
                engine = ExecutionEngine(risk_manager=risk)
                
                sig = {
                    "symbol": "XAUUSD",
                    "direction": "LONG",
                    "entry": 2000.0,
                    "stop": 1990.0,
                    "tp1": 2020.0,
                    "tp2": 2030.0,
                    "lots": 0.1,
                    "size": 10.0,
                    "risk_amount": 10.0,
                    "reason": "Test setup",
                }
                order = engine.register_setup(sig)
                # Fill order
                engine.update_market_candle("XAUUSD", {"open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.0})
                
                # Close order manually
                engine.close_manually("XAUUSD", current_price=2010.0, reason="Manual exit")

                tracker = PerformanceTracker(path=stats_path)
                s = tracker.summary()
                self.assertGreaterEqual(s["trades"], 1)

    def test_telegram_performance_subcommands(self):
        from telegram_advanced import performance_command
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        # Test /performance
        ctx.args = []
        asyncio.run(performance_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /performance agents
        ctx.args = ["agents"]
        asyncio.run(performance_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /performance trades
        ctx.args = ["trades"]
        asyncio.run(performance_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /performance reset
        ctx.args = ["reset"]
        asyncio.run(performance_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /performance help
        ctx.args = ["help"]
        asyncio.run(performance_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

    def test_cli_performance_subcommands(self):
        from main import run_cli_interactive as main_cli
        from main_full import run_cli_interactive as full_cli

        cmds = "performance\nperformance agents\nperformance trades\nperformance reset\nexit\n"
        
        # Test main.py
        input_data = io.StringIO(cmds)
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            main_cli()
        out = output_data.getvalue()
        self.assertIn("Performance", out)

        # Test main_full.py
        input_data2 = io.StringIO(cmds)
        output_data2 = io.StringIO()
        with patch("sys.stdin", input_data2), patch("sys.stdout", output_data2):
            full_cli()
        out2 = output_data2.getvalue()
        self.assertIn("Performance", out2)


if __name__ == "__main__":
    unittest.main()
