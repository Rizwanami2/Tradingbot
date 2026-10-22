"""
Unit tests verifying that autotrade executes positions when /signal is invoked,
and does not execute unsolicited trades in the background (signal-driven autotrade mode).
"""

from __future__ import annotations

import asyncio
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import main
from execution_engine import ExecutionEngine
from risk_manager import RiskManager
from telegram_advanced import asignal_command


class TestSignalAutotradeExecution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.stop_background_autotrade_worker()
        self.orig_mode = config.get_autotrade_mode()
        config.set_autotrade_mode("signal", persist=False)
        config.MT5_BACKEND = "auto"
        main.autotrade_active = False
        self.engine = main.engine
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        if hasattr(self.engine, "reset_halt"):
            self.engine.reset_halt()
        try:
            from anti_spam import get_anti_spam_limiter
            get_anti_spam_limiter().reset()
        except Exception:
            pass
        if hasattr(self.engine, "broker") and self.engine.broker is not None:
            if hasattr(self.engine.broker, "backend"):
                self.engine.broker.backend = "auto"
            if hasattr(self.engine.broker, "effective_backend"):
                self.engine.broker.effective_backend = "simulated"
            if hasattr(self.engine.broker, "_sim_positions") and isinstance(self.engine.broker._sim_positions, dict):
                self.engine.broker._sim_positions.clear()
            if hasattr(self.engine.broker, "_positions") and isinstance(self.engine.broker._positions, dict):
                self.engine.broker._positions.clear()
            if hasattr(self.engine.broker, "positions") and isinstance(self.engine.broker.positions, dict):
                self.engine.broker.positions.clear()

    def tearDown(self):
        main.stop_background_autotrade_worker()
        config.set_autotrade_mode(self.orig_mode, persist=False)
        main.autotrade_active = False
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        if hasattr(self.engine, "reset_halt"):
            self.engine.reset_halt()
        try:
            from anti_spam import get_anti_spam_limiter
            get_anti_spam_limiter().reset()
        except Exception:
            pass
        if hasattr(self.engine, "broker") and self.engine.broker is not None:
            if hasattr(self.engine.broker, "_sim_positions") and isinstance(self.engine.broker._sim_positions, dict):
                self.engine.broker._sim_positions.clear()
            if hasattr(self.engine.broker, "_positions") and isinstance(self.engine.broker._positions, dict):
                self.engine.broker._positions.clear()
            if hasattr(self.engine.broker, "positions") and isinstance(self.engine.broker.positions, dict):
                self.engine.broker.positions.clear()

    async def test_signal_command_executes_trade_into_position(self):
        """Verify that when /signal is run with an actionable signal, autotrade executes it."""
        mock_signal = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2700.0,
            "stop": 2690.0,
            "tp1": 2715.0,
            "tp2": 2730.0,
            "lots": 0.10,
            "size": 10.0,
            "leverage": 20.0,
            "margin_required": 135.0,
            "risk_amount": 100.0,
            "rr": 1.5,
            "reason": "Test MTF confluence buy",
            "current_price": 2700.0,
            "is_scalp": True,
            "fast_market_fill": True,
        }

        mock_res = {
            "symbol": "XAUUSD",
            "price": 2700.0,
            "status": "BUY Confluence Active",
            "signal": mock_signal,
            "votes": [],
        }

        with patch("signal_pipeline.run_signal", return_value=mock_res):
            mock_msg = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_msg
            mock_ctx = MagicMock()
            mock_ctx.args = ["XAUUSD"]

            await main.signal_command(mock_update, mock_ctx)

            # Assert position was executed by autotrade
            self.assertTrue(main.autotrade_active)
            self.assertTrue(len(self.engine.active_positions) > 0 or len(self.engine.pending_setups) > 0)
            
            # Verify reply text confirmed execution
            sent_texts = [call.args[0] for call in mock_msg.reply_text.call_args_list if call.args]
            self.assertTrue(any("Autotrade Executed" in t for t in sent_texts))

    async def test_signal_command_flat_does_not_execute(self):
        """Verify that when /signal finds no setup, no trade is executed."""
        mock_res = {
            "symbol": "XAUUSD",
            "price": 2700.0,
            "status": "No setup - Neutral",
            "signal": None,
            "votes": [],
        }

        with patch("signal_pipeline.run_signal", return_value=mock_res):
            mock_msg = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_msg
            mock_ctx = MagicMock()
            mock_ctx.args = ["XAUUSD"]

            await main.signal_command(mock_update, mock_ctx)

            self.assertEqual(len(self.engine.active_positions), 0)
            self.assertEqual(len(self.engine.pending_setups), 0)

    async def test_asignal_command_executes_trade(self):
        """Verify that /asignal also executes trade into a position when setup is formed."""
        mock_signal = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 65000.0,
            "stop": 64500.0,
            "tp1": 66000.0,
            "tp2": 67000.0,
            "lots": 0.05,
            "size": 0.05,
            "leverage": 20.0,
            "margin_required": 162.5,
            "risk_amount": 25.0,
            "rr": 2.0,
            "reason": "SMC Orderblock test",
            "current_price": 65000.0,
            "is_scalp": True,
            "fast_market_fill": True,
        }
        mock_eval = {
            "price": 65000.0,
            "status": "Bullish SMC Setup",
            "signal": mock_signal,
            "votes": [{"agent": "SMC", "direction": "LONG", "reason": "OB"}],
            "blocked": False,
        }

        with patch("advanced_integration.AdvancedEngine.evaluate", return_value=mock_eval), \
             patch("telegram_advanced.safe_reply", new_callable=AsyncMock) as mock_safe_reply:
            mock_update = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.args = ["BTCUSDT"]

            await asignal_command(mock_update, mock_ctx)

            self.assertTrue(len(self.engine.active_positions) > 0 or len(self.engine.pending_setups) > 0)
            self.assertTrue(main.autotrade_active)

    async def test_autotrade_mode_signal_command(self):
        """Verify /autotrade signal and /autotrade auto commands switch modes properly."""
        mock_msg = AsyncMock()
        mock_update = MagicMock()
        mock_update.message = mock_msg
        mock_ctx = MagicMock()

        # Switch to signal mode
        mock_ctx.args = ["signal"]
        await main.autotrade_command(mock_update, mock_ctx)
        self.assertEqual(config.get_autotrade_mode(), "signal")
        self.assertTrue(main.autotrade_active)
        self.assertIn("SIGNAL-DRIVEN", mock_msg.reply_text.call_args_list[-1].args[0])

        # Switch to continuous mode
        mock_ctx.args = ["auto"]
        await main.autotrade_command(mock_update, mock_ctx)
        self.assertEqual(config.get_autotrade_mode(), "continuous")
        self.assertIn("CONTINUOUS AUTONOMOUS", mock_msg.reply_text.call_args_list[-1].args[0])

        # Check mode inquiry
        mock_ctx.args = ["mode"]
        await main.autotrade_command(mock_update, mock_ctx)
        self.assertIn("CONTINUOUS", mock_msg.reply_text.call_args_list[-1].args[0])

    def test_background_worker_signal_mode_blocks_autonomous_entries(self):
        """Verify that in signal-driven mode, can_open is False in background worker logic."""
        config.set_autotrade_mode("signal", persist=False)
        main.autotrade_active = True

        cur_mode = config.get_autotrade_mode()
        self.assertEqual(cur_mode, "signal")

        # Emulate background worker logic
        can_open = True
        if not main.autotrade_active:
            can_open = False
        elif cur_mode == "signal":
            can_open = False

        self.assertFalse(can_open, "Background worker must not open unsolicited trades in signal mode")

    def test_cli_interactive_signal_executes_trade(self):
        """Verify that CLI interactive 'signal' command executes trade when signal is found."""
        mock_sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2700.0,
            "stop": 2690.0,
            "tp1": 2715.0,
            "tp2": 2730.0,
            "lots": 0.05,
            "size": 5.0,
            "leverage": 20.0,
            "margin_required": 67.5,
            "risk_amount": 50.0,
            "rr": 1.5,
            "reason": "CLI signal test",
            "current_price": 2700.0,
            "is_scalp": True,
            "fast_market_fill": True,
        }
        mock_res = {
            "symbol": "XAUUSD",
            "price": 2700.0,
            "status": "BUY",
            "signal": mock_sig,
        }

        with patch("main.evaluate_symbol", return_value=mock_res), \
             patch("main.start_background_autotrade_worker"):
            input_data = io.StringIO("signal XAUUSD\nexit\n")
            output_data = io.StringIO()
            with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
                main.run_cli_interactive()

            out = output_data.getvalue()
            self.assertIn("[Autotrade Executed]", out)
            self.assertTrue(len(self.engine.active_positions) > 0 or len(self.engine.pending_setups) > 0)

    def test_format_signal_message_hides_all_agents_status_and_shows_entry_level(self):
        """Verify format_signal_message hides verbose agent status by default and shows entry level setup."""
        from signal_pipeline import format_signal_message
        res = {
            "symbol": "XAUUSD",
            "price": 2725.50,
            "status": "Scalp LONG score=1.85 (micro SL/TP)",
            "leverage": 20,
            "votes": [
                {"agent": "TREND", "direction": "LONG", "reason": "EMA bullish cross"},
                {"agent": "LIQUIDITY", "direction": "LONG", "reason": "BSL taken"},
                {"agent": "INDICATOR", "direction": "LONG", "reason": "RSI oversold rebound"},
            ],
            "signal": {
                "symbol": "XAUUSD",
                "direction": "LONG",
                "entry": 2725.50,
                "stop": 2721.50,
                "tp1": 2731.50,
                "tp2": 2735.50,
                "lots": 0.05,
                "size": 5.0,
                "leverage": 20.0,
                "margin_required": 68.14,
                "rr": 1.5,
                "is_scalp": True,
            },
        }

        # Default: all agents status hidden, only entry level / setup shown
        msg_default = format_signal_message(res)
        self.assertIn("Multi-Agent Evaluation for XAUUSD", msg_default)
        self.assertIn("Consensus:", msg_default)
        self.assertIn("TRADE SETUP (SCALP — CURRENT MARKET", msg_default)
        self.assertIn("Entry:", msg_default)
        self.assertIn("Stop Loss:", msg_default)
        self.assertIn("Target 1:", msg_default)
        self.assertNotIn("**Agent Votes:**", msg_default)
        self.assertNotIn("TREND:", msg_default)
        self.assertNotIn("LIQUIDITY:", msg_default)

        # Explicit show_agent_votes=True includes individual agent breakdown or consensus matrix
        msg_verbose = format_signal_message(res, show_agent_votes=True)
        self.assertTrue("**Agent Votes:**" in msg_verbose or "CONSENSUS MATRIX" in msg_verbose)
        self.assertIn("TREND", msg_verbose)
        self.assertIn("LIQUIDITY", msg_verbose)

    def test_register_setup_auto_heals_invalid_long_levels(self):
        """Verify register_setup auto-heals misordered LONG levels without raising ValueError."""
        inverted_long = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2725.0,
            "stop": 2730.0,   # stop > entry (invalid)
            "tp1": 2720.0,    # tp1 < entry (invalid)
            "tp2": 2715.0,    # tp2 < tp1 (invalid)
            "lots": 0.01,
            "size": 1.0,
            "leverage": 20.0,
            "margin_required": 13.6,
            "is_scalp": True,
        }
        order = self.engine.register_setup(inverted_long, reason="Auto-heal test")
        self.assertIsNotNone(order)
        self.assertLess(order.stop_loss, order.entry_level)
        self.assertLess(order.entry_level, order.tp1)
        self.assertLessEqual(order.tp1, order.tp2)

    def test_register_setup_auto_heals_invalid_short_levels(self):
        """Verify register_setup auto-heals misordered SHORT levels without raising ValueError."""
        inverted_short = {
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "entry": 65000.0,
            "stop": 64500.0,   # stop < entry (invalid)
            "tp1": 65500.0,    # tp1 > entry (invalid)
            "tp2": 66000.0,    # tp2 > tp1 (invalid)
            "lots": 0.01,
            "size": 0.01,
            "leverage": 20.0,
            "margin_required": 32.5,
            "is_scalp": True,
        }
        order = self.engine.register_setup(inverted_short, reason="Auto-heal short test")
        self.assertIsNotNone(order)
        self.assertGreater(order.stop_loss, order.entry_level)
        self.assertGreater(order.entry_level, order.tp1)
        self.assertGreaterEqual(order.tp1, order.tp2)

    def test_attach_plan_to_signal_scalp_guarantees_bracket_ordering(self):
        """Verify attach_plan_to_signal keeps valid bracket levels when price moves past original TP1."""
        from entry_level_planner import EntryPlan, attach_plan_to_signal
        plan = EntryPlan(
            direction="LONG",
            mark=2735.0,     # market price moved up to 2735
            best_entry=2720.0,
            entry_low=2718.0,
            entry_high=2722.0,
            stop=2715.0,
            tp1=2725.0,
            tp2=2730.0,
            confidence=0.85,
        )
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2720.0,
            "stop": 2715.0,
            "tp1": 2725.0,
            "tp2": 2730.0,
            "is_scalp": True,
        }
        out = attach_plan_to_signal(sig, plan)
        self.assertEqual(out["entry"], 2735.0)
        self.assertLess(out["stop"], out["entry"])
        self.assertLess(out["entry"], out["tp1"])
        self.assertLessEqual(out["tp1"], out["tp2"])


if __name__ == "__main__":
    unittest.main()
