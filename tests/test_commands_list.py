import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

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
    delta_command,
    broker_command,
    oanda_command,
    alpaca_command,
    ccxt_command,
    set_paper_wallet_balance,
    bridge_command,
    mt5_command,
    settings_command,
    handle_command_menu_callback,
    build_settings_dashboard,
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
        self._orig_broker = engine.broker
        self._orig_positions = dict(engine.active_positions)
        self._orig_setups = dict(engine.pending_setups)
        self._orig_mt5_backend = getattr(config, "MT5_BACKEND", "auto")
        self._orig_broker_backend = getattr(engine.broker, "backend", "auto")
        self._orig_broker_eff = getattr(engine.broker, "effective_backend", "simulated")

    def setUp(self):
        self._orig_optin = config.AUTOTRADE_LIVE_OPT_IN
        self._orig_broker = engine.broker
        self._orig_positions = dict(engine.active_positions)
        self._orig_setups = dict(engine.pending_setups)
        self._orig_mt5_backend = getattr(config, "MT5_BACKEND", "auto")
        self._orig_broker_backend = getattr(engine.broker, "backend", "auto")
        self._orig_broker_eff = getattr(engine.broker, "effective_backend", "simulated")

    def tearDown(self):
        import main
        main.stop_background_autotrade_worker()
        config.AUTOTRADE_LIVE_OPT_IN = self._orig_optin
        engine.broker = self._orig_broker
        engine.active_positions = self._orig_positions
        engine.pending_setups = self._orig_setups
        config.MT5_BACKEND = self._orig_mt5_backend
        if hasattr(engine.broker, "backend"):
            engine.broker.backend = self._orig_broker_backend
        if hasattr(engine.broker, "effective_backend"):
            engine.broker.effective_backend = self._orig_broker_eff

    async def asyncTearDown(self):
        import main
        main.stop_background_autotrade_worker()
        config.AUTOTRADE_LIVE_OPT_IN = self._orig_optin
        engine.broker = self._orig_broker
        engine.active_positions = self._orig_positions
        engine.pending_setups = self._orig_setups
        config.MT5_BACKEND = self._orig_mt5_backend
        if hasattr(engine.broker, "backend"):
            engine.broker.backend = self._orig_broker_backend
        if hasattr(engine.broker, "effective_backend"):
            engine.broker.effective_backend = self._orig_broker_eff

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
        self.assertIn("/delta", text)
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
        self.assertIn("/brain", text)
        self.assertIn("/relearn", text)
        self.assertIn("/learner", text)
        self.assertIn("/train", text)
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

        # MT5 / Broker category
        self.ctx.args = ["mt5"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 & Broker Accounts", text)
        self.assertIn("/mt5", text)
        self.assertIn("/delta", text)

        # Delta category filter
        self.ctx.args = ["delta"]
        await help_command(self.update, self.ctx)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 & Broker Accounts", text)
        self.assertIn("/delta", text)

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
        self.assertIn("Autonomous Brain & Multi-Agent Learning", text)
        self.assertIn("/brain", text)
        self.assertIn("/learner", text)
        self.assertIn("/train", text)
        self.assertIn("/relearn", text)

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

    async def test_unknown_command_removed_features(self):
        for cmd in ["/ai", "/veto", "/featured", "/feature"]:
            self.message.text = cmd
            self.message.reply_text.reset_mock()
            await unknown_command(self.update, self.ctx)
            self.message.reply_text.assert_called_once()
            text = self.message.reply_text.call_args[0][0]
            self.assertIn("has been completely removed", text)

    async def test_ask_command(self):
        self.ctx.args = []
        await ask_command(self.update, self.ctx)
        self.message.reply_text.assert_called_with("Usage: `/ask <your question>` or `/chat <your question>`\nExample: `/chat btc chart` or `/chat gold analysis`", parse_mode="Markdown")

        self.ctx.args = ["What", "is", "BTC", "trend?"]
        with patch("main.gemini.ask", return_value="Market is bullish."):
            await ask_command(self.update, self.ctx)
            self.message.reply_text.assert_called_with("Market is bullish.", parse_mode="Markdown")

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

    async def test_close_command(self):
        engine.active_positions.clear()
        if hasattr(engine.broker, "positions"):
            engine.broker.positions.clear()
        self.ctx.args = ["all"]
        await close_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("No open positions to close", text)

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
        self.assertIn("removed", self.message.reply_text.call_args[0][0])

        await export_command(self.update, self.ctx)
        self.assertTrue(len(self.message.reply_text.call_args[0][0]) > 0)

        await setups_command(self.update, self.ctx)
        self.assertIn("removed", self.message.reply_text.call_args[0][0])

        await abooks_command(self.update, self.ctx)
        self.assertIn("removed", self.message.reply_text.call_args[0][0])

        await heal_command(self.update, self.ctx)
        self.assertTrue(len(self.message.reply_text.call_args[0][0]) > 0)

    async def test_balance_command_view_and_set(self):
        # 1. View balance returns live MT5 balance card
        self.ctx.args = []
        await balance_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        view_text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 Live Account Balance", view_text)
        self.assertIn("Live Balance:", view_text)

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

    async def test_delta_command_flow(self):
        from delta_broker import MockDeltaBroker
        orig_broker = engine.broker
        engine.broker = None
        self.ctx.args = []
        with patch.object(config, "DELTA_EXCHANGE_API_KEY", ""), patch.object(config, "DELTA_EXCHANGE_API_SECRET", ""):
            await delta_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("Delta Exchange India Live Broker", msg)
            self.assertIn("/delta live <API_KEY> <API_SECRET>", msg)

        mock_delta = MockDeltaBroker(balance=12500.0, leverage=25.0)
        engine.broker = mock_delta
        self.ctx.args = []
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Delta Exchange India Broker Telemetry", msg)
        self.assertIn("12,500.00", msg)

        self.ctx.args = ["status"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Delta Exchange India Telemetry", msg)
        self.assertIn("12,500.00", msg)

        self.ctx.args = ["leverage", "75"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Delta Exchange Leverage Updated", msg)
        self.assertIn("75x", msg)

        self.ctx.args = ["auto_leverage", "on"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Delta Exchange Auto-Leverage", msg)
        self.assertIn("ENABLED", msg)

        self.ctx.args = ["positions"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("No active open positions on Delta Exchange India", msg)

        mock_delta.place_order(symbol="BTCUSD", side="buy", qty=0.1, price=65000.0)
        self.ctx.args = ["positions"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Delta Exchange Active Positions", msg)
        self.assertIn("BTCUSD", msg)

        self.ctx.args = ["close", "BTCUSD"]
        await delta_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("closed successfully", msg)

        engine.broker = orig_broker

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

    def test_cli_delta_interactive(self):
        input_data = io.StringIO("help\ndelta\ndelta status\nswitch delta\nbroker delta\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("delta [CMD]", out)
        self.assertIn("Delta Exchange", out)

    async def test_telegram_bridge_command_status(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 2, "volume": 0.15, "login": 889900})
        with patch.object(ea_client, "status", mock_status):
            self.ctx.args = []
            await bridge_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("MetaTrader 5 EA Bridge Dashboard", msg)
            self.assertIn("889900", msg)
            self.assertIn("0.15", msg)

    async def test_telegram_bridge_command_ping_success(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 0, "volume": 0.0, "login": 123456})
        with patch.object(ea_client, "status", mock_status):
            self.ctx.args = ["ping"]
            await bridge_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("EA Bridge Ping: PONG", msg)
            self.assertIn("Responsive", msg)

    async def test_telegram_bridge_command_ping_fail(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": False, "error": "timeout", "hint": "Check EA"})
        with patch.object(ea_client, "status", mock_status):
            self.ctx.args = ["ping"]
            await bridge_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("EA Bridge Ping Failed", msg)
            self.assertIn("Check EA", msg)

    async def test_telegram_bridge_command_dir(self):
        self.ctx.args = ["dir", "/custom/ea/bridge/dir"]
        await bridge_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("EA Bridge Directory Updated", msg)
        self.assertIn("/custom/ea/bridge/dir", msg)

    async def test_telegram_bridge_command_on_and_off(self):
        self.ctx.args = ["on"]
        await bridge_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Switched to EA Bridge Backend", msg)
        self.assertEqual(config.MT5_BACKEND, "ea_bridge")

        self.ctx.args = ["off"]
        await bridge_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Switched to Auto MT5 Backend", msg)
        self.assertEqual(config.MT5_BACKEND, "auto")

    async def test_telegram_mt5_bridge_and_backend_subcommands(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 0, "volume": 0.0, "login": 99999})
        with patch.object(ea_client, "status", mock_status):
            self.ctx.args = ["bridge"]
            await mt5_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("MetaTrader 5 EA Bridge Dashboard", msg)

            self.ctx.args = ["backend"]
            await mt5_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("MT5 Backend Status", msg)

            self.ctx.args = ["backend", "ea_bridge"]
            await mt5_command(self.update, self.ctx)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("MT5 Backend Updated", msg)
            self.assertIn("ea_bridge", msg)
            self.assertEqual(config.MT5_BACKEND, "ea_bridge")

    async def test_settings_command(self):
        await settings_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        msg = self.message.reply_text.call_args[0][0]
        markup = self.message.reply_text.call_args[1].get("reply_markup")
        self.assertIn("TradingBot System Configuration & Settings", msg)
        self.assertIn("Execution Broker", msg)
        self.assertIn("Strategy Profile", msg)
        self.assertIsNotNone(markup)

    async def test_command_menu_callbacks(self):
        query_mock = MagicMock()
        query_mock.answer = AsyncMock()
        query_mock.edit_message_text = AsyncMock()
        update_mock = MagicMock(spec=Update)
        update_mock.callback_query = query_mock

        # Test category navigation
        query_mock.data = "cmd:cat:signals"
        handled = await handle_command_menu_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        query_mock.edit_message_text.assert_called()
        self.assertIn("Signals & Strategy Confluence", query_mock.edit_message_text.call_args[0][0])

        # Test settings refresh callback
        query_mock.data = "settings:refresh"
        handled = await handle_command_menu_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertIn("TradingBot System Configuration & Settings", query_mock.edit_message_text.call_args[0][0])

    async def test_unknown_command_with_smart_suggestions(self):
        self.message.text = "/signals"
        await unknown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        markup = self.message.reply_text.call_args[1].get("reply_markup")
        self.assertIn("Did you mean", msg)
        self.assertIn("/signal", msg)
        self.assertIsNotNone(markup)


if __name__ == "__main__":
    unittest.main()

