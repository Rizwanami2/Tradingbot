import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes, ApplicationBuilder

import config
from main import (
    start_command,
    help_command,
    close_command,
    closed_command,
    ask_command,
    unknown_command,
    run_cli_interactive,
    engine,
    risk_command,
    resetrisk_command,
    risk_mgr,
)
from execution_engine import TradeOrder, OrderStatus


class TestCommandsList(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.message.text = "/commands"
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def test_help_command_full_catalog(self):
        self.ctx.args = []
        await help_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("TradingBot Command Center", text)
        self.assertIn("/price", text)
        self.assertIn("/signal", text)
        self.assertIn("/asignal", text)
        self.assertIn("/quality", text)
        self.assertIn("/positions", text)
        self.assertIn("/autotrade", text)
        self.assertIn("/close", text)
        self.assertIn("/closed", text)
        self.assertIn("/cancel", text)
        self.assertIn("/mt5", text)
        self.assertIn("/broker", text)
        self.assertIn("/status", text)
        self.assertIn("/health", text)
        self.assertIn("/risk", text)
        self.assertIn("/leverage", text)
        self.assertIn("/kill", text)
        self.assertIn("/backtest", text)
        self.assertIn("/abacktest", text)
        self.assertIn("/performance", text)
        self.assertIn("/profile", text)
        self.assertIn("/board", text)
        self.assertIn("/journal", text)
        self.assertIn("/agents", text)
        self.assertIn("/tiers", text)
        self.assertIn("/digest", text)
        self.assertIn("/brain", text)
        self.assertIn("/relearn", text)
        self.assertIn("/reset", text)
        self.assertIn("/ask", text)

    async def test_help_command_categories(self):
        # Market category
        self.ctx.args = ["market"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Market Data & Live Quotes", text)
        self.assertIn("/price", text)

        # Signals category
        self.ctx.args = ["signals"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Signals & Strategy Confluence", text)
        self.assertIn("/signal", text)

        # Execution category
        self.ctx.args = ["execution"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Execution & Positions", text)
        self.assertIn("/positions", text)

        # MT5 category
        self.ctx.args = ["mt5"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 & Broker Accounts", text)
        self.assertIn("/mt5", text)

        # Risk category
        self.ctx.args = ["risk"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Risk & Safety Management", text)
        self.assertIn("/leverage", text)

        # Research category
        self.ctx.args = ["research"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Backtesting & Research", text)
        self.assertIn("/abacktest", text)

        # AI category
        self.ctx.args = ["ai"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autonomous AI & Standings", text)
        self.assertIn("/brain", text)

    async def test_start_command(self):
        await start_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("TradingBot", text)
        self.assertIn("7 autonomous strategy agents", text)
        self.assertIn("/commands", text)

    async def test_closed_command_does_not_close_open_positions(self):
        # Register a mock active position
        dummy = TradeOrder(
            id="order-closed-test",
            symbol="BTCUSDT",
            direction="LONG",
            entry_level=60000.0,
            stop_loss=59000.0,
            tp1=62000.0,
            tp2=63000.0,
            tp3=None,
            lots=0.1,
            size=1.0,
            leverage=10.0,
            margin_required=6000.0,
            liquidation_price=None,
            risk_amount=100.0,
            rr_ratio=2.0,
            setup_reason="test",
            status=OrderStatus.OPEN,
            executed_price=60000.0,
            remaining_size=1.0,
        )
        engine.active_positions["BTCUSDT"] = dummy

        # Call closed_command
        await closed_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Recently Closed Trades History", text)

        # Verify active position is NOT closed!
        self.assertIn("BTCUSDT", engine.active_positions)

        # Clean up
        engine.active_positions.clear()

    async def test_unknown_command(self):
        self.message.text = "/invalidcommand"
        await unknown_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Unknown command", text)
        self.assertIn("/commands", text)

    async def test_ask_command(self):
        self.ctx.args = []
        await ask_command(self.update, self.ctx)
        self.message.reply_text.assert_called_with("Usage: `/ask <your question>`", parse_mode="Markdown")

        self.ctx.args = ["What", "is", "BTC", "trend?"]
        with patch("main.gemini.ask", return_value="Market is bullish."):
            await ask_command(self.update, self.ctx)
            self.message.reply_text.assert_called_with("Market is bullish.")

    def test_cli_commands_and_aliases(self):
        # Test CLI handles 'commands', 'menu', 'all', 'list'
        for cmd_name in ("commands", "menu", "all", "list", "cmd"):
            input_data = io.StringIO(f"{cmd_name}\nexit\n")
            output_data = io.StringIO()
            with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
                run_cli_interactive()
            out = output_data.getvalue()
            self.assertIn("Commands: signal [SYM]", out)
            self.assertIn("📊 Market:", out)
            self.assertIn("🎯 Signals:", out)
            self.assertIn("⚡ Execution:", out)
            self.assertIn("🛡️ Risk:", out)
            self.assertIn("📈 Research:", out)
            self.assertIn("🧠 Autonomous AI:", out)

    async def test_risk_command_report(self):
        self.ctx.args = []
        await risk_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Institutional Risk Controls", text)
        self.assertIn("Risk/Trade:", text)

    async def test_risk_command_reset_daily(self):
        risk_mgr.state.daily_pnl = -400.0
        risk_mgr.state.consecutive_losses = 3
        self.ctx.args = ["reset", "daily"]
        await risk_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Risk Management Reset — DAILY", text)
        self.assertIn("Trading Status:", text)
        self.assertEqual(risk_mgr.state.daily_pnl, 0.0)
        self.assertEqual(risk_mgr.state.consecutive_losses, 0)

    async def test_risk_command_reset_weekly(self):
        risk_mgr.state.weekly_pnl = -800.0
        self.ctx.args = ["reset", "weekly"]
        await risk_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Risk Management Reset — WEEKLY", text)
        self.assertEqual(risk_mgr.state.weekly_pnl, 0.0)

    async def test_risk_command_restart(self):
        import main
        main.autotrade_active = False
        self.ctx.args = ["restart"]
        await risk_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Risk Management Reset", text)
        self.assertIn("Autotrade:** 🟢 ACTIVE", text)
        self.assertTrue(main.autotrade_active)

    async def test_resetrisk_command(self):
        risk_mgr.state.daily_pnl = -350.0
        self.ctx.args = ["daily"]
        await resetrisk_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Risk Management Reset — DAILY", text)
        self.assertEqual(risk_mgr.state.daily_pnl, 0.0)

    def test_cli_risk_reset_and_digest(self):
        risk_mgr.state.daily_pnl = -300.0
        input_data = io.StringIO("risk reset daily\nresetrisk weekly\ndigest\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Risk Management Reset — DAILY", out)
        self.assertIn("Risk Management Reset — WEEKLY", out)
        self.assertIn("Daily Digest", out)


if __name__ == "__main__":
    unittest.main()
