"""
Unit tests for Single-Direction Protection (preventing simultaneous BUY/SELL)
and Cooldown Enforcement across BOTH Automatic (Autotrade) and Manual (Signal) trades.
"""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from anti_spam import AntiSpamRateLimiter, get_anti_spam_limiter
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from mt5_broker import MockMT5Broker, Position
from risk_manager import RiskManager
from signal_pipeline import try_paper_execute


class TestCooldownAndSingleDirection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state_file = "test_cooldown_single_dir_state.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        self.broker = MockMT5Broker(initial_balance=10000.0)
        self.engine = ExecutionEngine(risk_manager=self.rm, broker=self.broker)
        self.limiter = get_anti_spam_limiter()
        self.limiter.reset()
        self.limiter.set_mode("on")
        self.limiter.set_entry_cooldown(45)

    def tearDown(self):
        self.limiter.reset()
        self.limiter.set_mode("auto")
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_opposing_direction_blocked_in_antispam_and_engine(self):
        """Active BUY on Gold strictly prevents opening SELL on Gold in both anti_spam and engine."""
        # 1. Open a LONG position on Gold
        buy_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0, "stop": 2640.0,
            "tp1": 2665.0, "tp2": 2675.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 20.0, "rr": 1.5,
        }
        order_buy = self.engine.register_setup(buy_sig)
        candle = {"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}
        self.engine.update_market_candle("XAUUSD", candle)
        self.assertIn("XAUUSD", self.engine.active_positions)
        self.assertEqual(order_buy.status, OrderStatus.OPEN)

        # 2. Check anti_spam: incoming SHORT must be rejected
        can_sell, reason_sell = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2651.0, engine=self.engine, direction="SHORT"
        )
        self.assertFalse(can_sell)
        self.assertIn("cannot BUY and SELL at the same time", reason_sell)

        # 3. Check engine.register_setup: incoming SHORT must raise RuntimeError
        sell_sig = {
            "symbol": "XAUUSD", "direction": "SHORT", "entry": 2651.0, "stop": 2660.0,
            "tp1": 2640.0, "tp2": 2630.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 18.0, "rr": 1.5,
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(sell_sig)
        self.assertIn("cannot hold BUY and SELL at the same time", str(ctx.exception))

    def test_opposing_direction_blocked_in_manual_try_paper_execute(self):
        """Manual signal execution (/signal) is strictly blocked if an opposing position exists."""
        # Active SHORT on BTC
        sell_btc = {
            "symbol": "BTCUSDT", "direction": "SHORT", "entry": 65000.0, "stop": 66000.0,
            "tp1": 63500.0, "tp2": 62000.0, "lots": 0.01, "size": 0.01, "leverage": 10.0,
            "margin_required": 65.0, "risk_amount": 10.0, "rr": 1.5,
        }
        self.engine.register_setup(sell_btc)
        c = {"open": 65000.0, "high": 65100.0, "low": 64900.0, "close": 64950.0}
        self.engine.update_market_candle("BTCUSDT", c)

        # Now attempt manual execution of LONG on BTC via try_paper_execute
        long_sig = {
            "symbol": "BTCUSDT", "direction": "LONG", "entry": 64950.0, "stop": 64000.0,
            "tp1": 66000.0, "tp2": 67000.0, "lots": 0.01, "size": 0.01, "leverage": 10.0,
            "margin_required": 65.0, "risk_amount": 9.5, "rr": 1.5,
        }
        ok, note = try_paper_execute(self.engine, long_sig)
        self.assertFalse(ok)
        self.assertIn("cannot BUY and SELL at the same time", note)

    def test_broker_external_positions_prevent_opposing_orders(self):
        """Even if local engine is empty, open positions on broker prevent opposing orders."""
        # Broker has an external BUY position on Gold
        ext_pos = Position(
            symbol="XAUUSD", side="buy", qty=0.05, entry_price=2645.0,
            stop_loss=2635.0, take_profit=2670.0, ticket=999111,
        )
        self.broker.get_positions = MagicMock(return_value=[ext_pos])

        # Attempt to open SHORT on Gold in anti_spam
        can_sell, reason = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2648.0, engine=self.engine, direction="SHORT"
        )
        self.assertFalse(can_sell)
        self.assertIn("cannot BUY and SELL at the same time", reason)

        # Attempt to register SHORT in engine
        short_sig = {
            "symbol": "XAUUSD", "direction": "SHORT", "entry": 2648.0, "stop": 2655.0,
            "tp1": 2635.0, "tp2": 2625.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 14.0, "rr": 1.5,
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(short_sig)
        self.assertIn("cannot hold BUY and SELL at the same time", str(ctx.exception))

    def test_single_direction_enforced_even_in_spam_trade_mode(self):
        """Even if spam trade mode is active, opposing BUY/SELL is never allowed."""
        self.limiter.set_spam_mode(True, max_trades=6, engine=self.engine)

        buy_order = TradeOrder(
            id="order-spam-buy", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
            stop_loss=2640.0, tp1=2665.0, tp2=2675.0, tp3=None, lots=0.02, size=2.0,
            leverage=20.0, margin_required=50.0, liquidation_price=2600.0, risk_amount=20.0,
            rr_ratio=1.5, setup_reason="Spam Long", status=OrderStatus.OPEN, executed_price=2650.0,
            remaining_size=2.0,
        )
        self.engine.active_positions[buy_order.id] = buy_order

        # In spam mode, incoming SHORT must still be blocked by opposing direction check
        can_short, reason = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2652.0, engine=self.engine, direction="SHORT"
        )
        self.assertFalse(can_short)
        self.assertIn("cannot BUY and SELL at the same time", reason)

    def test_cooldown_enforced_on_both_auto_and_manual(self):
        """Entry cooldown blocks rapid consecutive entries for BOTH auto and manual."""
        self.limiter.set_entry_cooldown(30)
        self.limiter.record_entry("XAUUSD", entry_price=2650.0, timestamp=1000.0)

        # 1. Autotrade check at t=1010s (10s elapsed < 30s cd) -> BLOCKED
        can_auto, reason_auto = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2660.0, engine=self.engine, timestamp=1010.0, bypass_cooldowns=False
        )
        self.assertFalse(can_auto)
        self.assertIn("Entry cooldown for XAUUSD", reason_auto)

        # 2. Manual try_paper_execute at t=1010s -> BLOCKED
        sig_manual = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2660.0, "stop": 2650.0,
            "tp1": 2675.0, "tp2": 2685.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 20.0, "rr": 1.5,
        }
        with patch("time.time", return_value=1010.0):
            ok_man, note_man = try_paper_execute(self.engine, sig_manual)
            self.assertFalse(ok_man)
            self.assertIn("Entry cooldown for XAUUSD", note_man)

        # 3. After cooldown expires at t=1035s (> 30s cd) -> ALLOWED
        can_after, _ = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2660.0, engine=self.engine, timestamp=1035.0, bypass_cooldowns=False
        )
        self.assertTrue(can_after)

    def test_loss_cooldown_enforced_on_both_auto_and_manual(self):
        """Loss cooldown blocks subsequent entries for BOTH auto and manual after SL hit."""
        self.limiter.loss_cooldown_sec = 60
        self.limiter.record_loss("XAUUSD", loss_amount=50.0, timestamp=2000.0)

        # At t=2020s (20s elapsed < 60s loss cd) -> BLOCKED
        can_auto, r_auto = self.limiter.check_can_enter(
            symbol="XAUUSD", current_price=2645.0, engine=self.engine, timestamp=2020.0, bypass_cooldowns=False
        )
        self.assertFalse(can_auto)
        self.assertIn("Loss cooldown for XAUUSD", r_auto)

        # Manual try_paper_execute at t=2020s -> BLOCKED
        sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2645.0, "stop": 2635.0,
            "tp1": 2660.0, "tp2": 2670.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 20.0, "rr": 1.5,
        }
        with patch("time.time", return_value=2020.0):
            ok_man, note_man = try_paper_execute(self.engine, sig)
            self.assertFalse(ok_man)
            self.assertIn("Loss cooldown for XAUUSD", note_man)

    async def test_cooldown_telegram_command_sets_seconds(self):
        """Verify /cooldown <seconds> configures entry cooldown and responds with confirmation."""
        from main import cooldown_command

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = ["35"]

        await cooldown_command(update, ctx)

        self.assertEqual(self.limiter.entry_cooldown_sec, 35)
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Entry cooldown set to `35s`", call_text)
        self.assertIn("BOTH Auto & Manual", call_text)

    async def test_telegram_cooldown_manual_and_all(self):
        from main import cooldown_command, build_cooldown_keyboard
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()

        # 1. /cooldown manual 30
        ctx.args = ["manual", "30"]
        await cooldown_command(update, ctx)
        self.assertEqual(self.limiter.entry_cooldown_sec, 30)
        self.assertIn(self.limiter.get_mode(), ("manual", "on"))
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("MANUAL ON", call_text)
        self.assertIn("30s", call_text)

        # 2. /cooldown all 60
        update.message.reply_text.reset_mock()
        ctx.args = ["all", "60"]
        await cooldown_command(update, ctx)
        self.assertEqual(self.limiter.entry_cooldown_sec, 60)
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("ALL Symbols", call_text)
        self.assertIn("60s", call_text)

        # 3. /cooldown all reset
        self.limiter.global_last_entry = 12345.0
        update.message.reply_text.reset_mock()
        ctx.args = ["all", "reset"]
        await cooldown_command(update, ctx)
        self.assertEqual(self.limiter.global_last_entry, 0.0)
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("reset across ALL symbols globally", call_text)

        # 4. Verify keyboard markup buttons
        markup = build_cooldown_keyboard(self.limiter)
        cb_datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("cd:mode:auto", cb_datas)
        self.assertIn("cd:mode:on", cb_datas)
        self.assertIn("cd:sec:15", cb_datas)
        self.assertIn("cd:sec:30", cb_datas)
        self.assertIn("cd:sec:60", cb_datas)
        self.assertIn("cd:sec:120", cb_datas)
        self.assertIn("cd:reset", cb_datas)
        self.assertIn("cd:view:all", cb_datas)


if __name__ == "__main__":
    unittest.main()
