"""
Tests for Active Positions Slots Control (Manual/Auto Max 6)
and Verification that Trade Setup Levels Remain Unaltered by Unconfirmed Shifts.
"""
import io
import unittest
from unittest.mock import patch

import config
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from main import run_cli_interactive
from risk_manager import RiskManager
from strategy_profiles import apply_profile


class TestSlotsModeAndMinProfit(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager()
        self.rm.state.equity = 10000.0
        self.rm.state.peak_equity = 10000.0
        self.engine = ExecutionEngine(risk_manager=self.rm)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        config.set_slots_mode("auto")

    def tearDown(self):
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        config.set_slots_mode("auto")

    def test_slots_mode_manual_and_auto_clamping(self):
        """Verify setting manual mode (clamped between 1 and 6) and switching back to auto."""
        # 1. Manual mode with valid count
        ok, msg = self.engine.set_slots_mode("manual", 3)
        self.assertTrue(ok)
        self.assertEqual(self.engine.slots_mode, "manual")
        self.assertEqual(self.engine.max_open_trades, 3)
        self.assertEqual(self.engine.manual_slots, 3)
        self.assertIn("MANUAL mode: 3/6 slots", msg)

        # 2. Manual mode clamped at upper bound max 6
        ok, msg = self.engine.set_slots_mode("manual", 10)
        self.assertTrue(ok)
        self.assertEqual(self.engine.max_open_trades, 6)
        self.assertEqual(self.engine.manual_slots, 6)
        self.assertIn("6/6 slots", msg)

        # 3. Manual mode clamped at lower bound min 1
        ok, msg = self.engine.set_slots_mode("manual", 0)
        self.assertTrue(ok)
        self.assertEqual(self.engine.max_open_trades, 1)
        self.assertEqual(self.engine.manual_slots, 1)
        self.assertIn("1/6 slots", msg)

        # 4. Switch to auto mode
        ok, msg = self.engine.set_slots_mode("auto")
        self.assertTrue(ok)
        self.assertEqual(self.engine.slots_mode, "auto")
        self.assertIn("AUTOMATIC mode", msg)
        self.assertGreaterEqual(self.engine.max_open_trades, 1)
        self.assertLessEqual(self.engine.max_open_trades, 6)

    def test_update_auto_slots_dynamic_equity_tiers(self):
        """Verify dynamic auto slots calculation scales properly based on account equity."""
        self.engine.set_slots_mode("auto")

        # Micro equity: <= 2 slots
        self.rm.state.equity = 150.0
        if hasattr(self.engine.broker, "equity"):
            self.engine.broker.equity = 150.0
            self.engine.broker.balance = 150.0
        slots = self.engine.update_auto_slots()
        self.assertLessEqual(slots, 2)
        self.assertGreaterEqual(slots, 1)

        # Small equity ($500): 3 slots
        self.rm.state.equity = 500.0
        if hasattr(self.engine.broker, "equity"):
            self.engine.broker.equity = 500.0
            self.engine.broker.balance = 500.0
        slots = self.engine.update_auto_slots()
        self.assertEqual(slots, 3)

        # Medium equity ($1500): 4 slots
        self.rm.state.equity = 1500.0
        if hasattr(self.engine.broker, "equity"):
            self.engine.broker.equity = 1500.0
            self.engine.broker.balance = 1500.0
        slots = self.engine.update_auto_slots()
        self.assertEqual(slots, 4)

        # Large equity ($10000): max 6 slots
        self.rm.state.equity = 10000.0
        if hasattr(self.engine.broker, "equity"):
            self.engine.broker.equity = 10000.0
            self.engine.broker.balance = 10000.0
        slots = self.engine.update_auto_slots()
        self.assertEqual(slots, 6)

    def test_strategy_profile_respects_manual_slots(self):
        """Verify apply_profile preserves manual slot selection."""
        # Set manual slots to 2
        self.engine.set_slots_mode("manual", 2)
        self.assertEqual(self.engine.max_open_trades, 2)

        # Apply scalp profile
        apply_profile("scalp", engine=self.engine)
        self.assertEqual(self.engine.max_open_trades, 2)

        # Apply balanced profile
        apply_profile("balanced", engine=self.engine)
        self.assertEqual(self.engine.max_open_trades, 2)

        # When set back to auto, profile defaults to 6
        self.engine.set_slots_mode("auto")
        apply_profile("scalp", engine=self.engine)
        self.assertEqual(self.engine.max_open_trades, 6)

    def test_trade_setup_levels_remain_unaltered_by_unconfirmed_profit_shift(self):
        """Verify setup registration leaves TP1 and TP2 exactly as calculated by signal."""
        # LONG setup with tight TP1 distance
        sig_long = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2700.0,
            "stop": 2690.0,
            "tp1": 2700.25,
            "tp2": 2700.80,
            "lots": 0.01,
            "size": 1.0,
            "leverage": 20.0,
            "margin_required": 10.0,
            "risk_amount": 10.0,
            "rr": 1.5,
        }
        order_long = self.engine.register_setup(sig_long)
        # Original consensus levels are strictly preserved without synthetic floor shifting
        self.assertEqual(order_long.tp1, 2700.25)
        self.assertEqual(order_long.tp2, 2700.80)

        # SHORT setup with tight TP1 distance
        sig_short = {
            "symbol": "XAUUSD",
            "direction": "SHORT",
            "entry": 2700.0,
            "stop": 2710.0,
            "tp1": 2699.75,
            "tp2": 2699.20,
            "lots": 0.01,
            "size": 1.0,
            "leverage": 20.0,
            "margin_required": 10.0,
            "risk_amount": 10.0,
            "rr": 1.5,
        }
        order_short = self.engine.register_setup(sig_short)
        self.assertEqual(order_short.tp1, 2699.75)
        self.assertEqual(order_short.tp2, 2699.20)

    def test_scalp_break_even_and_trailing(self):
        """Verify scalp progressive break-even locks standard break-even without synthetic floor distortion."""
        self.engine.scalp_mode = True
        self.engine.scalp_tp_usd = 2.50

        # Create active scalp position
        order = TradeOrder(
            id="SCALP-PROFIT-TEST",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2700.0,
            stop_loss=2695.0,
            tp1=2705.0,
            tp2=2710.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=15.0,
            liquidation_price=2650.0,
            risk_amount=5.0,
            rr_ratio=1.0,
            setup_reason="Scalp profit test",
            status=OrderStatus.OPEN,
            executed_price=2700.0,
            remaining_size=1.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Market moves to 2701.0 (unrealized profit = +$1.00)
        candle = {"open": 2700.0, "high": 2701.0, "low": 2699.8, "close": 2700.8}
        self.engine.update_market_candle("XAUUSD", candle)

        # Verify stop loss is moved to break-even (+0.02% buffer: 2700 * 1.0002 = 2700.54)
        self.assertGreater(order.stop_loss, 2700.0)
        self.assertEqual(order.be_stage, 1)

    def test_format_positions_detail_includes_slots(self):
        """Verify format_positions_detail shows slots mode without min profit floor text."""
        self.engine.set_slots_mode("manual", 4)
        detail = self.engine.format_positions_detail()

        self.assertIn("0/4 active | 4 slots free", detail)
        self.assertIn("Slots Mode:", detail)
        self.assertIn("Manual (4/6)", detail)
        self.assertNotIn("Min Profit Floor", detail)

    def test_cli_slots_and_positions_commands(self):
        """Verify CLI interactive handling for slots mode and positions commands."""
        input_data = io.StringIO(
            "slots 4\n"
            "slots auto\n"
            "positions 3\n"
            "positions auto\n"
            "exit\n"
        )
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Active positions limit set to MANUAL mode: 4/6 slots", out)
        self.assertIn("Active positions limit set to AUTOMATIC mode", out)
        self.assertIn("Active positions limit set to MANUAL mode: 3/6 slots", out)


if __name__ == "__main__":
    unittest.main()
