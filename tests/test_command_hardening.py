"""
Unit tests for Telegram & CLI command hardening, safe markdown replies, and collision resilience.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os

from telegram_brain import safe_reply
from mtf_matrix import render_telegram_heatmap, render_telegram_multi_matrix
from ops_dashboard import start_dashboard_background, stop_dashboard_background
from execution_engine import ExecutionEngine


class TestCommandHardening(unittest.TestCase):

    def test_safe_reply_handles_parse_error(self):
        """Verify safe_reply falls back to plain text when Telegram raises entity parse errors."""
        import asyncio

        async def run_test():
            msg_mock = AsyncMock()
            # First reply_text call raises parse error, second succeeds
            msg_mock.reply_text.side_effect = [
                Exception("Can't parse entities: character '[' is reserved"),
                AsyncMock(text="plain reply ok"),
            ]
            update_mock = MagicMock()
            update_mock.message = msg_mock

            res = await safe_reply(update_mock, "Test message with broken [bracket markdown", parse_mode="Markdown")
            self.assertIsNotNone(res)
            # Verify reply_text was called twice: first with Markdown, second with parse_mode=None
            self.assertEqual(msg_mock.reply_text.call_count, 2)
            self.assertIsNone(msg_mock.reply_text.call_args_list[1].kwargs.get("parse_mode"))

        asyncio.run(run_test())

    def test_safe_reply_with_reply_markup(self):
        """Verify safe_reply forwards reply_markup properly."""
        import asyncio

        async def run_test():
            msg_mock = AsyncMock()
            msg_mock.reply_text.return_value = AsyncMock()
            update_mock = MagicMock()
            update_mock.message = msg_mock
            markup_mock = MagicMock()

            await safe_reply(update_mock, "Message with buttons", reply_markup=markup_mock)
            self.assertEqual(msg_mock.reply_text.call_args.kwargs.get("reply_markup"), markup_mock)

        asyncio.run(run_test())

    def test_render_telegram_heatmap_brackets(self):
        """Verify rendered heatmap output does not contain bare unescaped square brackets."""
        # Test single heatmap
        hm = render_telegram_heatmap("XAUUSD")
        self.assertIsInstance(hm, str)
        # Ensure there is no bare "[🟥" or "[score_bar]" outside code blocks
        for line in hm.splitlines():
            if "🟥" in line or "🟩" in line or "⬜" in line:
                # The score bar should be wrapped in backticks: `...`
                self.assertTrue(line.startswith("`") and line.endswith("`"), f"Score bar line not wrapped in code backticks: {line}")

        # Test multi heatmap
        mm = render_telegram_multi_matrix(["XAUUSD", "BTCUSDT"])
        self.assertIsInstance(mm, str)
        for line in mm.splitlines():
            if "Score:" in line:
                # Must not contain unescaped "[score_bar]"
                self.assertNotIn("[🟥", line)
                self.assertNotIn("[🟩", line)
                self.assertNotIn("[⬜", line)

    def test_format_positions_detail_brackets(self):
        """Verify execution engine format_positions_detail uses parentheses instead of brackets for IDs."""
        from risk_manager import RiskManager
        rm = RiskManager(equity=10000.0)
        engine = ExecutionEngine(risk_manager=rm)
        # Add a position and pending setup
        engine.active_positions["TEST"] = MagicMock(
            symbol="TEST",
            executed_price=100.0,
            remaining_size=1.0,
            direction="LONG",
            unrealized_pnl=5.0,
            stop_loss=90.0,
            tp1=110.0,
            tp2=120.0,
            lots=0.1,
            leverage=10,
            margin_required=10.0,
            id="pos123",
            ticket=555666,
            trailing_sl_active=False,
        )
        engine.pending_setups["PEND"] = MagicMock(
            symbol="PEND",
            entry_level=200.0,
            stop_loss=190.0,
            lots=0.1,
            direction="BUY",
            id="ord789",
        )

        detail = engine.format_positions_detail()
        self.assertIn("(#pos123)", detail)
        self.assertIn("(#ord789)", detail)
        self.assertNotIn("[#pos123]", detail)
        self.assertNotIn("[#ord789]", detail)

    def test_dashboard_port_collision_resilience(self):
        """Verify start_dashboard_background gracefully handles port collision."""
        stop_dashboard_background()
        with patch("ops_dashboard.create_server") as mock_create:
            mock_create.side_effect = OSError(98, "Address already in use")
            srv, url = start_dashboard_background(host="127.0.0.1", port=8765)
            self.assertIsNone(srv)
            self.assertEqual(url, "http://127.0.0.1:8765")

    def test_telegram_command_handlers_safe(self):
        """Verify command handlers in main.py execute cleanly with mock Update/Context."""
        import asyncio
        from main import (
            slots_command,
            matrix_command,
            dashboard_command,
            positions_command,
            unknown_command,
            cancel_command,
        )

        async def run_handlers():
            mock_msg = AsyncMock()
            mock_msg.text = "/test"
            mock_update = MagicMock()
            mock_update.message = mock_msg
            mock_ctx = MagicMock()
            mock_ctx.args = []

            # /slots
            await slots_command(mock_update, mock_ctx)
            mock_ctx.args = ["auto"]
            await slots_command(mock_update, mock_ctx)

            # /matrix
            mock_ctx.args = ["gold"]
            await matrix_command(mock_update, mock_ctx)

            # /dashboard
            mock_ctx.args = ["status"]
            await dashboard_command(mock_update, mock_ctx)

            # /positions
            mock_ctx.args = []
            await positions_command(mock_update, mock_ctx)

            # /unknown_command
            mock_msg.text = "/foobar"
            await unknown_command(mock_update, mock_ctx)

            # /cancel
            mock_ctx.args = ["XAUUSD"]
            await cancel_command(mock_update, mock_ctx)

        asyncio.run(run_handlers())


if __name__ == "__main__":
    unittest.main()
