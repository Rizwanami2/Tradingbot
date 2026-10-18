"""Unit tests for MT5 Autotrade Chart Calibration, Positions Live Telemetry,
Cooldown Active/Deactivate, and Spam Trade (Max 6) Modes.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

import config
from anti_spam import AntiSpamRateLimiter, get_anti_spam_limiter
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from mt5_broker import MockMT5Broker
from main import (
    cooldown_command,
    spam_command,
    positions_command,
    run_cli_interactive,
    engine,
)


class TestSpamAndMT5Autotrade(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.limiter = get_anti_spam_limiter()
        self.limiter.reset()
        self.limiter.set_spam_mode(False)
        self.limiter.set_mode("auto")

        self.broker = MockMT5Broker(initial_balance=15000.0)
        self.broker.login = 888999
        self.broker.server = "Exness-Real5"
        engine.broker = self.broker
        engine.active_positions.clear()
        engine.pending_setups.clear()

        self.chat = Chat(id=99999, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    def tearDown(self):
        self.limiter.reset()
        self.limiter.set_spam_mode(False)
        self.limiter.set_mode("auto")
        engine.active_positions.clear()
        engine.pending_setups.clear()

    async def test_cooldown_active_and_deactivate_commands(self):
        """Verify /cooldown active and /cooldown deactivate commands work as expected."""
        # Test active
        self.ctx.args = ["active"]
        await cooldown_command(self.update, self.ctx)
        msg_active = self.message.reply_text.call_args[0][0]
        self.assertIn("Cooldown ACTIVATED", msg_active)
        self.assertEqual(self.limiter.get_mode(), "on")

        # Test deactivate
        self.ctx.args = ["deactivate"]
        await cooldown_command(self.update, self.ctx)
        msg_deact = self.message.reply_text.call_args[0][0]
        self.assertIn("Cooldown DEACTIVATED", msg_deact)
        self.assertEqual(self.limiter.get_mode(), "off")

        # Test activate alias
        self.ctx.args = ["activate"]
        await cooldown_command(self.update, self.ctx)
        self.assertEqual(self.limiter.get_mode(), "on")

    async def test_spam_trade_commands_on_off(self):
        """Verify /spam on and /spam off controls spamming trade mode."""
        # Enable spam mode
        self.ctx.args = ["on"]
        await spam_command(self.update, self.ctx)
        msg_on = self.message.reply_text.call_args[0][0]
        self.assertIn("Spam Trade Mode ACTIVATED", msg_on)
        self.assertIn("Max `6` concurrent trades", msg_on)
        self.assertIn("BYPASSED", msg_on)
        self.assertTrue(self.limiter.spam_trade_enabled)
        self.assertEqual(self.limiter.spam_trade_max_trades, 6)
        self.assertGreaterEqual(engine.max_open_trades, 6)

        # Disable spam mode
        self.ctx.args = ["off"]
        await spam_command(self.update, self.ctx)
        msg_off = self.message.reply_text.call_args[0][0]
        self.assertIn("Spam Trade Mode DEACTIVATED", msg_off)
        self.assertFalse(self.limiter.spam_trade_enabled)

    def test_spam_trade_allows_max_6_without_cooldown(self):
        """Verify spam trade mode bypasses cooldowns and allows up to 6 positions."""
        self.limiter.set_spam_mode(True, max_trades=6, engine=engine)
        self.assertTrue(self.limiter.spam_trade_enabled)

        # Record a loss on XAUUSD which would normally trigger a 90s loss cooldown
        self.limiter.record_loss("XAUUSD", 50.0)

        # In spam trade mode, check_can_enter should succeed despite the loss!
        can_enter, reason = self.limiter.check_can_enter(
            symbol="XAUUSD",
            current_price=2700.0,
            engine=engine,
            max_open=6,
        )
        self.assertTrue(can_enter)
        self.assertIn("Spam trade mode active", reason)

        # Fill up 5 positions
        for i in range(5):
            oid = f"ORD-{i}"
            order = TradeOrder(
                id=oid,
                symbol="XAUUSD",
                direction="LONG",
                entry_level=2700.0,
                stop_loss=2690.0,
                tp1=2715.0,
                tp2=2725.0,
                tp3=None,
                lots=0.01,
                size=1.0,
                leverage=20.0,
                margin_required=10.0,
                liquidation_price=2600.0,
                risk_amount=10.0,
                rr_ratio=1.5,
                setup_reason="SpamTest",
                status=OrderStatus.OPEN,
                executed_price=2700.0,
            )
            engine.active_positions[oid] = order

        # 6th position should be allowed
        can_6, r_6 = self.limiter.check_can_enter("BTCUSD", 68000.0, engine=engine, max_open=6)
        self.assertTrue(can_6)

        # Add 6th position
        o6 = TradeOrder(
            id="ORD-6", symbol="BTCUSD", direction="LONG", entry_level=68000.0,
            stop_loss=67500.0, tp1=69000.0, tp2=70000.0, tp3=None, lots=0.01, size=0.01,
            leverage=20.0, margin_required=10.0, liquidation_price=60000.0, risk_amount=10.0, rr_ratio=1.5,
            setup_reason="SpamTest", status=OrderStatus.OPEN, executed_price=68000.0
        )
        engine.active_positions[o6.id] = o6

        # 7th position must be blocked by capacity
        can_7, r_7 = self.limiter.check_can_enter("XAUUSD", 2700.0, engine=engine, max_open=6)
        self.assertFalse(can_7)
        self.assertIn("Spam trade capacity reached (6/6)", r_7)

    def test_positions_header_displays_mt5_account_and_live_balance(self):
        """Verify format_positions_detail prominently shows MT5 account, live balance & equity."""
        old_scalp = getattr(engine, "scalp_mode", False)
        engine.scalp_mode = False
        try:
            self.broker.balance = 15000.0
            self.broker.login = 888999
            self.broker.server = "Exness-Real5"

            # Create active position
            pos = TradeOrder(
                id="pos-gold",
                symbol="XAUUSD",
                direction="LONG",
                entry_level=2710.0,
                stop_loss=2700.0,
                tp1=2725.0,
                tp2=2735.0,
                tp3=None,
                lots=0.05,
                size=1.0,
                leverage=20.0,
                margin_required=50.0,
                liquidation_price=2650.0,
                risk_amount=25.0,
                rr_ratio=2.0,
                setup_reason="Breakout",
                status=OrderStatus.OPEN,
                executed_price=2710.0,
                remaining_size=1.0,
            )
            engine.active_positions[pos.id] = pos
            engine.update_market_candle("XAUUSD", {"open": 2710.0, "high": 2714.0, "low": 2710.0, "close": 2715.0})

            detail = engine.format_positions_detail()
            self.assertIn("🏛️ **MT5 Live Account:**", detail)
            self.assertIn("888999", detail)
            self.assertIn("Exness-Real5", detail)
            self.assertIn("💵 **Balance:** `$15,000.00 USD`", detail)
            self.assertIn("Live Equity:", detail)
            self.assertIn("Floating PnL:", detail)
            self.assertIn("+$5.00 USD", detail)
        finally:
            engine.scalp_mode = old_scalp

    def test_cli_cooldown_and_spam_commands(self):
        """Verify CLI interactive handling for cooldown active/deactivate and spam on/off."""
        input_data = io.StringIO(
            "cooldown active\n"
            "cooldown deactivate\n"
            "spam on\n"
            "spam off\n"
            "exit\n"
        )
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Anti-Spam Mode set to MANUAL ON", out)
        self.assertIn("Anti-Spam Mode set to OFF", out)
        self.assertIn("Spam Trade Mode ACTIVATED", out)
        self.assertIn("Spam Trade Mode DEACTIVATED", out)

    def test_active_positions_slots_max_6(self):
        """Verify active positions slots maximum is 6 across engine, profiles, and display."""
        from strategy_profiles import apply_profile

        # 1. Apply default scalp profile and verify max_open_trades is 6
        apply_profile("scalp", engine=engine)
        self.assertEqual(engine.max_open_trades, 6)
        self.assertEqual(config.MAX_OPEN_TRADES, 6)

        # 2. Verify slots_available starts at 6 and display reflects 0/6 active | 6 slots free
        engine.active_positions.clear()
        engine.pending_setups.clear()
        self.assertEqual(engine.slots_available(), 6)
        detail = engine.format_positions_detail()
        self.assertIn("0/6 active | 6 slots free", detail)

        # 3. Add 6 active positions
        for i in range(6):
            oid = f"TEST-SLOT-{i}"
            sym = "XAUUSD" if i < 3 else "BTCUSD"
            price = 2700.0 + i if sym == "XAUUSD" else 68000.0 + i
            p = TradeOrder(
                id=oid, symbol=sym, direction="LONG", entry_level=price,
                stop_loss=price - 10.0, tp1=price + 20.0, tp2=price + 30.0, tp3=None,
                lots=0.01, size=1.0, leverage=20.0, margin_required=10.0, liquidation_price=price - 50.0,
                risk_amount=10.0, rr_ratio=2.0, setup_reason="SlotTest", status=OrderStatus.OPEN,
                executed_price=price, remaining_size=1.0,
            )
            engine.active_positions[oid] = p

        self.assertEqual(len(engine.active_positions), 6)
        self.assertEqual(engine.slots_available(), 0)
        detail_full = engine.format_positions_detail()
        self.assertIn("6/6 active | 0 slots free", detail_full)

        # 4. Attempting to register a 7th setup must fail with max 6 reached
        sig7 = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2750.0, "stop": 2740.0,
            "tp1": 2770.0, "tp2": 2780.0, "lots": 0.01, "size": 1.0, "leverage": 20.0,
            "margin_required": 10.0, "risk_amount": 10.0, "rr": 2.0,
        }
        with self.assertRaises(RuntimeError) as ctx_err:
            engine.register_setup(sig7)
        self.assertIn("Max concurrent trades reached (6)", str(ctx_err.exception))

        # 5. Close 1 position and verify 1 slot frees up
        engine.active_positions.pop("TEST-SLOT-0")
        self.assertEqual(engine.slots_available(), 1)
        detail_freed = engine.format_positions_detail()
        self.assertIn("5/6 active | 1 slots free", detail_freed)

        # 6. Verify balanced profile also defaults to 6
        apply_profile("balanced", engine=engine)
        self.assertEqual(engine.max_open_trades, 6)


if __name__ == "__main__":
    unittest.main()
