"""Unit tests for Auto vs. Manual Lot Size Selection.

Tests:
1. Configuration functions (get_lot_size_mode, set_lot_size_mode, get_manual_lot_size, set_manual_lot_size).
2. RiskManager lot sizing modes (Auto dynamic risk vs. Manual fixed lot sizing, per-symbol overrides, margin protection).
3. ExecutionEngine and signal pipeline lot_mode preservation.
4. Telegram commands (/lot, /lot auto, /lot manual, /lot <size>, and inline callbacks).
5. Interactive CLI (lot, lot auto, lot manual, lot <size>).
"""

import io
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat, CallbackQuery
from telegram.ext import ContextTypes

import config
from risk_manager import RiskManager, RiskConfig, INSTRUMENTS
from execution_engine import ExecutionEngine, OrderStatus
from main import (
    lot_command,
    build_lot_dashboard,
    autotrade_callback_handler,
    run_cli_interactive,
    risk_mgr,
    engine,
)


class TestLotSizeConfig(unittest.TestCase):
    def setUp(self):
        self._orig_mode = config.get_lot_size_mode()
        self._orig_manual = config.get_manual_lot_size()
        self._orig_sizes = dict(config.MANUAL_LOT_SIZES)

    def tearDown(self):
        config.set_lot_size_mode(self._orig_mode, persist=False)
        config.set_manual_lot_size(self._orig_manual, persist=False)
        config.MANUAL_LOT_SIZES = dict(self._orig_sizes)

    def test_lot_size_mode_get_and_set(self):
        config.set_lot_size_mode("manual", persist=False)
        self.assertEqual(config.get_lot_size_mode(), "manual")

        config.set_lot_size_mode("auto", persist=False)
        self.assertEqual(config.get_lot_size_mode(), "auto")

        config.set_lot_size_mode("fixed", persist=False)
        self.assertEqual(config.get_lot_size_mode(), "manual")

    def test_manual_lot_size_global_and_symbol(self):
        config.set_manual_lot_size(0.05, persist=False)
        self.assertEqual(config.get_manual_lot_size(), 0.05)
        self.assertEqual(config.get_manual_lot_size("XAUUSD"), 0.05)

        # Override for specific symbol
        config.set_manual_lot_size(0.02, symbol="XAUUSD", persist=False)
        self.assertEqual(config.get_manual_lot_size("XAUUSD"), 0.02)
        self.assertEqual(config.get_manual_lot_size("BTCUSDT"), 0.05)


class TestRiskManagerLotSelection(unittest.TestCase):
    def setUp(self):
        self.state_file = "test_rm_lot_state.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        self.rm.config.lot_mode = "auto"
        self.rm.config.manual_lot_size = 0.01

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_auto_lot_sizing(self):
        self.rm.set_auto_lot()
        self.assertEqual(self.rm.config.lot_mode, "auto")

        # In auto mode, lots are calculated from equity, risk %, and stop distance
        plan = self.rm.position_size(entry=2650.0, stop=2648.0, symbol="XAUUSD")
        self.assertGreater(plan.lots, 0.0)
        self.assertEqual(plan.lot_mode, "auto")
        self.assertNotIn("manual", plan.reason.lower())

        # If stop distance increases, auto lots decrease
        plan_wide = self.rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertLess(plan_wide.lots, plan.lots)

    def test_manual_lot_sizing_fixed(self):
        ok, msg = self.rm.set_manual_lot(0.05)
        self.assertTrue(ok, msg)
        self.assertEqual(self.rm.config.lot_mode, "manual")
        self.assertEqual(self.rm.get_manual_lot(), 0.05)

        # In manual mode, lots should remain fixed at 0.05 regardless of stop distance
        plan1 = self.rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertEqual(plan1.lots, 0.05)
        self.assertEqual(plan1.lot_mode, "manual")
        self.assertIn("manual lot", plan1.reason)

        plan2 = self.rm.position_size(entry=2650.0, stop=2620.0, symbol="XAUUSD")
        self.assertEqual(plan2.lots, 0.05)
        self.assertEqual(plan2.lot_mode, "manual")

    def test_per_symbol_manual_lot(self):
        self.rm.set_manual_lot(0.05)  # Global default 0.05
        self.rm.set_manual_lot(0.02, symbol="XAUUSD")  # Gold override 0.02
        self.rm.set_manual_lot(0.003, symbol="BTCUSDT")  # BTC override 0.003

        plan_gold = self.rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertEqual(plan_gold.lots, 0.02)

        plan_btc = self.rm.position_size(entry=65000.0, stop=64500.0, symbol="BTCUSDT")
        self.assertEqual(plan_btc.lots, 0.003)

    def test_manual_lot_validation(self):
        # Negative lot
        ok, msg = self.rm.set_manual_lot(-0.01)
        self.assertFalse(ok)

        # Exceeds max lot
        ok, msg = self.rm.set_manual_lot(50.0)
        self.assertFalse(ok)
        self.assertIn("exceeds maximum", msg)

        # Below minimum lot for instrument
        ok, msg = self.rm.set_manual_lot(0.000001)
        self.assertFalse(ok)
        self.assertIn("below minimum", msg)

    def test_manual_mode_margin_capacity_protection(self):
        # Set manual lot that would demand excessive margin relative to equity
        self.rm.state.equity = 100.0  # tiny equity
        self.rm.set_manual_lot(5.0)  # 5 lots of Gold is ~$1.3M notional!
        plan = self.rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        # Should be protected by margin capacity ceiling
        self.assertLess(plan.lots, 5.0)

    def test_autotrade_plan_lot_mode(self):
        self.rm.set_auto_lot()
        p1 = self.rm.autotrade_plan(entry=2650.0, stop=2645.0, direction="long")
        self.assertTrue(p1.get("ok"))
        self.assertEqual(p1.get("lot_mode"), "auto")

        self.rm.set_manual_lot(0.04)
        p2 = self.rm.autotrade_plan(entry=2650.0, stop=2645.0, direction="long")
        self.assertTrue(p2.get("ok"))
        self.assertEqual(p2.get("lot_mode"), "manual")
        self.assertEqual(p2.get("lots"), 0.04)

    def test_summary_reflects_lot_mode(self):
        self.rm.set_auto_lot()
        self.assertIn("Lot:AUTO", self.rm.summary())

        self.rm.set_manual_lot(0.05)
        self.assertIn("Lot:MANUAL(0.05)", self.rm.summary())


class TestExecutionEngineLotMode(unittest.TestCase):
    def test_order_preserves_lot_mode(self):
        rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        eng = ExecutionEngine(risk_manager=rm)

        sig_auto = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2680.0,
            "lots": 0.02,
            "size": 0.02,
            "leverage": 20.0,
            "margin_required": 265.0,
            "risk_amount": 20.0,
            "rr": 1.5,
            "lot_mode": "auto",
        }
        order1 = eng.register_setup(sig_auto)
        self.assertEqual(order1.lot_mode, "auto")

        sig_manual = dict(sig_auto)
        sig_manual["lot_mode"] = "manual"
        sig_manual["lots"] = 0.05
        order2 = eng.register_setup(sig_manual)
        self.assertEqual(order2.lot_mode, "manual")
        self.assertEqual(order2.lots, 0.05)


class TestLotTelegramCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.message.text = "/lot"
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.user = MagicMock()
        self.user.id = 999
        self.update.effective_user = self.user

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []
        self._orig_mode = risk_mgr.config.lot_mode
        self._orig_lot = risk_mgr.config.manual_lot_size

    async def asyncTearDown(self):
        risk_mgr.set_lot_mode(self._orig_mode)
        risk_mgr.config.manual_lot_size = self._orig_lot

    async def test_lot_command_view_dashboard(self):
        self.ctx.args = []
        await lot_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Lot Size Management", text)
        self.assertIn("Active Mode:", text)

    async def test_lot_command_switch_auto(self):
        self.ctx.args = ["auto"]
        await lot_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Auto Lot Sizing Activated", text)
        self.assertEqual(risk_mgr.config.lot_mode, "auto")

    async def test_lot_command_set_manual_with_size(self):
        self.ctx.args = ["manual", "0.05"]
        await lot_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Manual Lot Sizing Activated", text)
        self.assertIn("`0.0500` lots", text)
        self.assertEqual(risk_mgr.config.lot_mode, "manual")
        self.assertEqual(risk_mgr.get_manual_lot(), 0.05)

    async def test_lot_command_direct_number(self):
        self.ctx.args = ["0.08"]
        await lot_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Manual Lot Sizing Activated", text)
        self.assertIn("`0.0800` lots", text)
        self.assertEqual(risk_mgr.config.lot_mode, "manual")
        self.assertEqual(risk_mgr.get_manual_lot(), 0.08)

    async def test_lot_callback_handler(self):
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = self.message
        query.from_user = self.user
        self.update.callback_query = query
        self.update.effective_user = self.user

        # Callback lot:auto
        query.data = "lot:auto"
        await autotrade_callback_handler(self.update, self.ctx)
        self.assertEqual(risk_mgr.config.lot_mode, "auto")

        # Callback lot:manual:0.02
        query.data = "lot:manual:0.02"
        await autotrade_callback_handler(self.update, self.ctx)
        self.assertEqual(risk_mgr.config.lot_mode, "manual")
        self.assertEqual(risk_mgr.get_manual_lot(), 0.02)


class TestLotCLIInteractive(unittest.TestCase):
    def setUp(self):
        self._orig_mode = risk_mgr.config.lot_mode
        self._orig_lot = risk_mgr.config.manual_lot_size

    def tearDown(self):
        risk_mgr.set_lot_mode(self._orig_mode)
        risk_mgr.config.manual_lot_size = self._orig_lot

    def test_cli_lot_interactive(self):
        input_data = io.StringIO("lot\nlot auto\nlot manual 0.04\nlot 0.07\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Lot Sizing Mode", out)
        self.assertIn("Lot sizing mode set to AUTO", out)
        self.assertIn("Manual lot size set to 0.0400 lots", out)
        self.assertIn("Manual lot size set to 0.0700 lots", out)
        self.assertEqual(risk_mgr.get_manual_lot(), 0.07)


if __name__ == "__main__":
    unittest.main()
