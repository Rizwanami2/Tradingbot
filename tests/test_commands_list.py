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
    status_command,
    health_command,
    leverage_command,
    cancel_command,
    positions_command,
    autotrade_command,
    balance_command,
    broker_command,
    oanda_command,
    alpaca_command,
    ccxt_command,
    set_paper_wallet_balance,
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
        self.user = MagicMock()
        self.user.id = 999
        self.update.effective_user = self.user
        self._orig_optin = config.AUTOTRADE_LIVE_OPT_IN

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def asyncTearDown(self):
        config.AUTOTRADE_LIVE_OPT_IN = self._orig_optin

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

    def test_cli_brain_subcommands(self):
        input_data = io.StringIO("brain\nbrain rules\nbrain flaws\nbrain on\nbrain off\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Autonomous Learner", out)
        self.assertIn("Rules & Active Filters", out)
        self.assertIn("Flaw Mining", out)
        self.assertIn("ENABLED", out)
        self.assertIn("PAUSED", out)

        from main_full import run_cli_interactive as full_cli
        input_data_full = io.StringIO("brain\nbrain rules\nbrain flaws\nbrain on\nbrain off\nexit\n")
        output_data_full = io.StringIO()
        with patch("sys.stdin", input_data_full), patch("sys.stdout", output_data_full):
            full_cli()
        out_full = output_data_full.getvalue()
        self.assertIn("Autonomous Learner", out_full)
        self.assertIn("Rules & Active Filters", out_full)
        self.assertIn("Flaw Mining", out_full)
        self.assertIn("ENABLED", out_full)
        self.assertIn("PAUSED", out_full)


    async def test_status_command(self):
        self.ctx.args = []
        await status_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("System & Portfolio Status", text)

    async def test_health_command(self):
        self.ctx.args = []
        await health_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Health Telemetry", text)

    async def test_leverage_command(self):
        self.ctx.args = []
        await leverage_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Current leverage:", text)

        self.ctx.args = ["15"]
        await leverage_command(self.update, self.ctx)
        text2 = self.message.reply_text.call_args[0][0]
        self.assertIn("Leverage set to `15x`", text2)

    async def test_cancel_command(self):
        self.ctx.args = ["BTCUSDT"]
        await cancel_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("No pending setup for `BTCUSDT`", text)

    async def test_positions_command(self):
        self.ctx.args = []
        await positions_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertTrue("Positions" in text or "Active" in text or "Open" in text)

    async def test_autotrade_command(self):
        # 1. In LIVE mode without opt-in, autotrade on is blocked with safety warning
        self.ctx.args = ["on"]
        config.AUTOTRADE_LIVE_OPT_IN = False
        await autotrade_command(self.update, self.ctx)
        text_warn = self.message.reply_text.call_args[0][0]
        self.assertIn("Live Trading Protection", text_warn)

        # 2. In LIVE mode with confirm, autotrade activates
        self.ctx.args = ["on", "confirm"]
        await autotrade_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade ACTIVATED", text)

        # 3. Autotrade off pauses
        self.ctx.args = ["off"]
        await autotrade_command(self.update, self.ctx)
        text_off = self.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade PAUSED", text_off)

        # 4. With config.AUTOTRADE_LIVE_OPT_IN = True, activates without confirm
        config.AUTOTRADE_LIVE_OPT_IN = True
        self.ctx.args = ["on"]
        await autotrade_command(self.update, self.ctx)
        text_opt = self.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade ACTIVATED", text_opt)
        config.AUTOTRADE_LIVE_OPT_IN = False

    async def test_fp4_commands(self):
        from telegram_feature_pack_v4 import digest_command, export_command, setups_command, abooks_command, heal_command
        self.ctx.args = []
        await digest_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        self.assertIn("Daily Digest", self.message.reply_text.call_args[0][0])

        await export_command(self.update, self.ctx)
        self.assertTrue(len(self.message.reply_text.call_args[0][0]) > 0)

        await setups_command(self.update, self.ctx)
        self.assertIn("Setups", self.message.reply_text.call_args[0][0])

        await abooks_command(self.update, self.ctx)
        self.assertIn("A/B", self.message.reply_text.call_args[0][0])

        await heal_command(self.update, self.ctx)
        self.assertTrue(len(self.message.reply_text.call_args[0][0]) > 0)

    async def test_balance_command_view_and_set(self):
        # 1. View balance informs user paper trading wallet has been removed
        self.ctx.args = []
        await balance_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        view_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Paper Trading Wallet Removed", view_text)
        self.assertIn("MetaTrader 5 Live is the sole exclusive broker", view_text)

        # 2. set_paper_wallet_balance fails-closed
        ok, msg, _ = set_paper_wallet_balance(25000)
        self.assertFalse(ok)
        self.assertIn("Paper trading wallet has been removed", msg)

    async def test_broker_command_and_native_removals(self):
        # 1. View broker info
        self.ctx.args = []
        await broker_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        broker_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Execution Broker Environment", broker_text)
        self.assertIn("MetaTrader 5 (Exclusive Live Execution Engine)", broker_text)

        # 2. Switch to paper mode informs paper mode removed
        self.ctx.args = ["paper"]
        await broker_command(self.update, self.ctx)
        paper_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Paper Trading Mode Removed", paper_text)

        # 3. Attempting native brokers via broker command warns user
        for b in ("oanda", "alpaca", "ccxt"):
            self.ctx.args = [b]
            await broker_command(self.update, self.ctx)
            warn_text = self.message.reply_text.call_args[0][0]
            self.assertIn(f"Native Live Broker `{b.upper()}` Removed", warn_text)
            self.assertIn("MetaTrader 5 (`/mt5`) is now the exclusive live execution broker", warn_text)

    async def test_native_broker_commands_direct(self):
        # Test direct /oanda, /alpaca, /ccxt commands
        for cmd_func, name in [(oanda_command, "OANDA"), (alpaca_command, "Alpaca"), (ccxt_command, "CCXT Live")]:
            await cmd_func(self.update, self.ctx)
            rep = self.message.reply_text.call_args[0][0]
            self.assertIn(f"{name} Broker Removed", rep)
            self.assertIn("MetaTrader 5 (`/mt5`) is the sole live broker", rep)

    def test_cli_balance_and_native_broker_removal(self):
        input_data = io.StringIO("balance\nbalance 75000\noanda status\nalpaca status\nccxt status\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Paper trading and paper wallet balance have been removed", out)
        self.assertIn("Native live broker 'OANDA' has been removed", out)
        self.assertIn("Native live broker 'ALPACA' has been removed", out)
        self.assertIn("Native live broker 'CCXT' has been removed", out)


if __name__ == "__main__":
    unittest.main()
