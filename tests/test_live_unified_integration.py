import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

import config
from main import (
    regime_command,
    session_command,
    slippage_command,
    analytics_command,
    kelly_command,
    vstops_command,
    bridge_command,
    engine,
    risk_mgr,
    arbiter,
)
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager
from virtual_stops import VirtualStopManager, get_virtual_stop_manager
from execution_analytics import ExecutionAnalyticsTracker, get_execution_tracker
from regime_auto_switcher import RegimeAutoSwitcher


class TestLiveUnifiedIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.context.args = []

    async def test_regime_command_default(self):
        self.context.args = []
        await regime_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Market Regime & Strategy Profile", call_text)
        self.assertIn("Active Profile:", call_text)

    async def test_regime_command_auto(self):
        self.context.args = ["auto"]
        await regime_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Market Regime Auto-Evaluation", call_text)
        self.assertIn("Regime Detected:", call_text)

    async def test_regime_command_calibrate(self):
        self.context.args = ["calibrate"]
        await regime_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Dynamic Rolling Sharpe Agent Weights", call_text)

    async def test_regime_command_switch(self):
        self.context.args = ["switch", "scalp"]
        await regime_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("switched to `scalp`", call_text)

    async def test_regime_command_manual(self):
        from config import is_regime_auto
        self.context.args = ["manual", "balanced"]
        await regime_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("MANUAL", call_text)
        self.assertFalse(is_regime_auto())

    def test_build_regime_keyboard_has_both_modes_and_profiles(self):
        from main import build_regime_keyboard
        markup = build_regime_keyboard(is_auto=True, current_profile="scalp")
        buttons = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("regime:mode:auto", buttons)
        self.assertIn("regime:mode:manual", buttons)
        self.assertIn("regime:set:scalp", buttons)
        self.assertIn("regime:set:balanced", buttons)
        self.assertIn("regime:set:swing", buttons)
        self.assertIn("regime:set:gold_only", buttons)
        self.assertIn("regime:set:crypto", buttons)
        self.assertIn("regime:eval", buttons)

    async def test_kelly_command_formatting_safe(self):
        from main import kelly_command
        self.context.args = ["XAUUSD"]
        await kelly_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Fractional Kelly Criterion", call_text)
        self.assertIn("Raw Kelly Fraction", call_text)
        self.assertNotIn("Raw Kelly f*", call_text)

    async def test_session_command_default(self):
        self.context.args = []
        await session_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("END-OF-SESSION PERFORMANCE REPORT", call_text)
        self.assertIn("Window:", call_text)

    async def test_session_command_specific(self):
        self.context.args = ["LONDON"]
        await session_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("LONDON", call_text)

    async def test_slippage_command(self):
        self.context.args = []
        await slippage_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Broker Execution Quality & Slippage Analytics", call_text)
        self.assertIn("Execution Grade:", call_text)

    async def test_analytics_alias(self):
        self.assertEqual(slippage_command, analytics_command)

    async def test_kelly_command(self):
        self.context.args = ["XAUUSD"]
        await kelly_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Fractional Kelly Criterion & Portfolio Risk", call_text)
        self.assertIn("Portfolio Value at Risk", call_text)

    async def test_vstops_command_view_and_clear(self):
        v_mgr = get_virtual_stop_manager()
        v_mgr.register(
            symbol="XAUUSD",
            ticket="999888",
            direction="BUY",
            entry_price=2650.0,
            virtual_stop=2640.0,
            virtual_tp1=2670.0,
        )
        self.context.args = []
        await vstops_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("Client-Side Virtual Hidden Stops", call_text)
        self.assertIn("999888", call_text)

        # Test clear
        self.context.args = ["clear"]
        await vstops_command(self.update, self.context)
        call_text_clear = self.message.reply_text.call_args[0][0]
        self.assertIn("Cleared", call_text_clear)

    async def test_bridge_command_sim_and_deploy(self):
        self.context.args = ["sim"]
        await bridge_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)
        call_text = self.message.reply_text.call_args[0][0]
        self.assertIn("EA Bridge Self-Diagnostic", call_text)

        self.context.args = ["deploy"]
        await bridge_command(self.update, self.context)
        self.assertTrue(self.message.reply_text.called)

    def test_risk_manager_kelly_and_var(self):
        rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        k_res = rm.kelly_position_size(entry=2650.0, stop=2640.0, fraction_mult=0.25)
        self.assertGreater(k_res.recommended_lots, 0.0)
        self.assertGreater(k_res.applied_kelly_fraction, 0.0)

        var_res = rm.portfolio_var(confidence_level=0.99)
        self.assertGreater(var_res.parametric_var_usd, 0.0)
        self.assertGreater(var_res.historical_var_usd, 0.0)

    def test_execution_engine_virtual_stop_trigger(self):
        eng = ExecutionEngine(risk_manager=RiskManager())
        eng.broker = MagicMock()
        order = TradeOrder(
            id="vstop_test_01",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2670.0,
            tp2=2685.0,
            tp3=None,
            lots=0.1,
            size=10.0,
            leverage=20.0,
            margin_required=132.5,
            liquidation_price=2500.0,
            risk_amount=100.0,
            rr_ratio=2.0,
            setup_reason="Virtual stop test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            current_price=2650.0,
            remaining_size=10.0,
            remaining_lots=0.1,
            ticket="TKT_VSTOP_123",
        )
        eng.active_positions[order.id] = order
        v_mgr = get_virtual_stop_manager()
        v_mgr.register(
            symbol="XAUUSD",
            ticket="TKT_VSTOP_123",
            direction="LONG",
            entry_price=2650.0,
            virtual_stop=2642.0,
            virtual_tp1=2675.0,
        )

        # Trigger tick below virtual stop (2641.0 < 2642.0)
        candle = {"open": 2643.0, "high": 2643.0, "low": 2640.0, "close": 2641.0}
        events = eng.update_market_candle("XAUUSD", candle)

        # Order should be closed because virtual stop triggered
        self.assertEqual(order.status, OrderStatus.CLOSED)
        self.assertIn("Virtual Stop Loss triggered", order.close_reason)


if __name__ == "__main__":
    unittest.main()
