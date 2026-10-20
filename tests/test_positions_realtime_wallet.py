"""
Unit tests for real-time wallet telemetry in /positions and portfolio summary.
Verifies that /positions displays the live broker wallet balance and equity,
rather than falling back to a static 10,000 default.
"""

import io
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from execution import Position
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager
from mt5_broker import MockMT5Broker
from main import positions_command, balance_command, run_cli_interactive


class TestPositionsRealTimeWallet(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Configure risk manager with a non-default equity, e.g. $500.0
        self.rm = RiskManager(equity=500.0, symbol="XAUUSD", state_file="test_rt_wallet_risk.json")
        self.broker = MockMT5Broker(initial_balance=500.0)
        self.broker.login = 123456
        self.broker.server = "MetaQuotes-Real"
        self.engine = ExecutionEngine(risk_manager=self.rm, broker=self.broker)

    def tearDown(self):
        import os
        if os.path.exists("test_rt_wallet_risk.json"):
            try:
                os.remove("test_rt_wallet_risk.json")
            except OSError:
                pass

    def test_wallet_telemetry_reflects_live_broker_balance_not_10000(self):
        """Verify get_wallet_telemetry retrieves real broker balance and not default 10000."""
        wallet = self.engine.get_wallet_telemetry()
        self.assertEqual(wallet["balance"], 500.0)
        self.assertEqual(wallet["equity"], 500.0)
        self.assertEqual(wallet["currency"], "USD")
        self.assertEqual(wallet["account_id"], "123456")
        self.assertEqual(wallet["server"], "MetaQuotes-Real")
        self.assertTrue(wallet["connected"])

    def test_format_positions_detail_includes_realtime_wallet_section(self):
        """Verify format_positions_detail output contains the Real-Time Wallet Telemetry block."""
        detail = self.engine.format_positions_detail()
        self.assertIn("Real-Time Wallet Telemetry", detail)
        self.assertIn("Broker Account:", detail)
        self.assertIn("Wallet Balance:", detail)
        self.assertIn("$500.00 USD", detail)
        self.assertIn("Free Margin:", detail)
        self.assertIn("Floating PnL:", detail)
        self.assertNotIn("$10,000.00", detail)

    def test_format_positions_detail_reflects_broker_balance_updates(self):
        """When broker balance changes (e.g. deposit or live trade close), positions shows the new balance."""
        self.broker.balance = 1250.75
        self.broker.equity = 1250.75
        self.broker.free_margin = 1250.75

        detail = self.engine.format_positions_detail()
        self.assertIn("$1,250.75 USD", detail)
        self.assertNotIn("10000", detail)
        self.assertNotIn("10,000", detail)
        # Verify RiskManager equity synchronized
        self.assertAlmostEqual(self.engine.risk_manager.state.equity, 1250.75, places=2)
        self.assertAlmostEqual(self.engine.risk_manager.equity, 1250.75, places=2)

    def test_format_positions_detail_with_active_position_and_floating_pnl(self):
        """Verify real-time wallet displays active floating PnL and margin used."""
        pos = TradeOrder(
            id="gold-1",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2670.0,
            tp2=2680.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=26.50,
            liquidation_price=2600.0,
            risk_amount=10.0,
            rr_ratio=2.0,
            setup_reason="Breakout",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=1.0,
        )
        self.engine.active_positions[pos.id] = pos

        # Update market price to $2655 (+5.00 floating gain)
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2656.0, "low": 2650.0, "close": 2655.0})

        detail = self.engine.format_positions_detail()
        self.assertIn("Real-Time Wallet Telemetry", detail)
        self.assertIn("Used Margin:", detail)
        self.assertIn("$26.50 USD", detail)
        self.assertIn("Floating PnL:", detail)
        self.assertIn("+$5.00 USD", detail)
        self.assertIn("Equity:", detail)
        self.assertIn("$505.00 USD", detail)

    async def test_telegram_positions_command(self):
        """Verify Telegram /positions displays the real-time wallet."""
        from telegram import Chat, Message, Update
        from telegram.ext import ContextTypes

        chat = Chat(id=12345, type="private")
        message = MagicMock(spec=Message)
        message.reply_text = AsyncMock()
        update = MagicMock(spec=Update)
        update.effective_chat = chat
        update.message = message
        ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        ctx.args = []

        with patch("main.engine", self.engine):
            await positions_command(update, ctx)

        message.reply_text.assert_called_once()
        msg_text = message.reply_text.call_args[0][0]
        self.assertIn("Real-Time Wallet Telemetry", msg_text)
        self.assertIn("Wallet Balance:", msg_text)
        self.assertIn("$500.00 USD", msg_text)
        self.assertNotIn("$10,000.00", msg_text)

    async def test_telegram_positions_sync_command(self):
        """Verify Telegram /positions sync triggers reconciliation and displays updated wallet."""
        from telegram import Chat, Message, Update
        from telegram.ext import ContextTypes

        chat = Chat(id=12345, type="private")
        message = MagicMock(spec=Message)
        message.reply_text = AsyncMock()
        update = MagicMock(spec=Update)
        update.effective_chat = chat
        update.message = message
        ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        ctx.args = ["sync"]

        with patch("main.engine", self.engine):
            await positions_command(update, ctx)

        message.reply_text.assert_called_once()
        msg_text = message.reply_text.call_args[0][0]
        self.assertIn("Real-Time Wallet Telemetry", msg_text)

    def test_cli_interactive_positions_shows_wallet(self):
        """Verify interactive CLI 'positions' command displays real-time wallet."""
        input_data = io.StringIO("positions\nexit\n")
        output_data = io.StringIO()
        with patch("main.engine", self.engine), patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Real-Time Wallet Telemetry", out)
        self.assertIn("Wallet Balance:", out)
        self.assertIn("$500.00 USD", out)
        self.assertNotIn("$10,000.00", out)

    def test_bot_and_mt5_pnl_alignment_on_manual_close(self):
        """Verify that 0.01 lot XAUUSD trade buy at 2650, sell at 2660 yields exactly $10.00 realized PnL in bot and broker."""
        self.broker.balance = 1000.0
        self.broker.equity = 1000.0

        pos = TradeOrder(
            id="gold-pnl-1",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2670.0,
            tp2=2680.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=26.50,
            liquidation_price=2600.0,
            risk_amount=10.0,
            rr_ratio=2.0,
            setup_reason="Breakout",
            executed_price=2650.0,
            status=OrderStatus.OPEN,
            remaining_size=1.0,
            remaining_lots=0.01,
            ticket=123456,
        )
        self.engine.active_positions[pos.id] = pos
        self.broker._positions["123456"] = Position(
            symbol="XAUUSD",
            side="buy",
            qty=0.01,
            entry_price=2650.0,
            current_price=2660.0,
            unrealized_pnl=10.0,
            ticket=123456,
            id="123456",
        )

        closed = self.engine.close_manually(symbol="XAUUSD", current_price=2660.0, reason="Manual Take Profit")
        self.assertIsNotNone(closed)
        # 0.01 lots of Gold with 100 contract size = 1 oz. Gain of $10 on 1 oz = $10.00 (not $0.10)
        self.assertEqual(closed.realized_pnl, 10.0)
        self.assertEqual(closed.exit_price, 2660.0)
        self.assertEqual(self.broker.balance, 1010.0)
        wallet = self.engine.get_wallet_telemetry()
        self.assertEqual(wallet["balance"], 1010.0)

    def test_reconcile_external_mt5_close_syncs_pnl_and_journal(self):
        """Verify that an external MT5 close (e.g. SL hit) reconciles exact deal profit into pos.realized_pnl and journal."""
        self.broker.balance = 1000.0
        self.broker.equity = 1000.0

        pos = TradeOrder(
            id="gold-ext-1",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2670.0,
            tp2=2680.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=26.50,
            liquidation_price=2600.0,
            risk_amount=10.0,
            rr_ratio=2.0,
            setup_reason="Breakout",
            executed_price=2650.0,
            status=OrderStatus.OPEN,
            remaining_size=1.0,
            remaining_lots=0.01,
            ticket=999888,
        )
        self.engine.active_positions[pos.id] = pos
        self.broker._positions["999888"] = Position(
            symbol="XAUUSD",
            side="buy",
            qty=0.01,
            entry_price=2650.0,
            current_price=2650.0,
            ticket=999888,
            id="999888",
        )

        # External broker close event (e.g. SL hit at 2640)
        self.broker.close_position("XAUUSD", qty=0.01, price=2640.0, ticket=999888)
        self.assertEqual(len(self.broker._positions), 0)
        self.assertEqual(self.broker.balance, 990.0)

        # Engine reconciles with broker
        with patch.object(self.engine, "_notify_brain_close") as mock_notify:
            res = self.engine.reconcile_positions()
            self.assertEqual(res["closed_locally"], 1)
            mock_notify.assert_called_once()

        self.assertEqual(len(self.engine.active_positions), 0)
        self.assertEqual(len(self.engine.trade_history), 1)
        hist_pos = self.engine.trade_history[-1]
        self.assertEqual(hist_pos.id, "gold-ext-1")
        self.assertEqual(hist_pos.realized_pnl, -10.0)
        self.assertEqual(hist_pos.exit_price, 2640.0)
        self.assertEqual(hist_pos.status, OrderStatus.CLOSED)
        self.assertIn("MT5", hist_pos.close_reason)


if __name__ == "__main__":
    unittest.main()
