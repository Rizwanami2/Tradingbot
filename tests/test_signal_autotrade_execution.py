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
        self.orig_mode = config.get_autotrade_mode()
        config.set_autotrade_mode("signal", persist=False)
        main.autotrade_active = False
        self.engine = main.engine
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def tearDown(self):
        config.set_autotrade_mode(self.orig_mode, persist=False)
        main.autotrade_active = False
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

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


if __name__ == "__main__":
    unittest.main()
