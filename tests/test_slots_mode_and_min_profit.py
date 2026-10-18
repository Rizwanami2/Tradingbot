"""
Tests for Active Positions Slots Control (Manual/Auto Max 6)
and Minimum $0.50 Profit Guarantee on Every Autotrade.
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
        config.set_min_trade_profit(0.50)

    def tearDown(self):
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        config.set_slots_mode("auto")
        config.set_min_trade_profit(0.50)

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

    def test_min_trade_profit_guarantee_tp1_adjustment(self):
        """Verify setup registration adjusts TP1 to guarantee minimum $0.50 profit."""
        self.engine.min_profit_usd = 0.50

        # LONG setup with insufficient TP1 distance (0.10 pt distance = $0.05 profit at 50% partial)
        sig_long = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2700.0,
            "stop": 2690.0,
            "tp1": 2700.10,  # too close
            "tp2": 2700.20,
            "lots": 0.01,
            "size": 1.0,
            "leverage": 20.0,
            "margin_required": 10.0,
            "risk_amount": 10.0,
            "rr": 1.5,
        }
        order_long = self.engine.register_setup(sig_long)
        # 50% close on 1.0 size = 0.5 size. To get $0.50 profit: min delta = 0.50 / 0.5 = 1.0 pt.
        # So TP1 must be at least 2701.0
        self.assertGreaterEqual(order_long.tp1, 2701.0)
        self.assertGreaterEqual(order_long.tp2, order_long.tp1)

        # SHORT setup with insufficient TP1 distance
        sig_short = {
            "symbol": "XAUUSD",
            "direction": "SHORT",
            "entry": 2700.0,
            "stop": 2710.0,
            "tp1": 2699.90,  # too close
            "tp2": 2699.80,
            "lots": 0.01,
            "size": 1.0,
            "leverage": 20.0,
            "margin_required": 10.0,
            "risk_amount": 10.0,
            "rr": 1.5,
        }
        order_short = self.engine.register_setup(sig_short)
        self.assertLessEqual(order_short.tp1, 2699.0)
        self.assertLessEqual(order_short.tp2, order_short.tp1)

    def test_scalp_break_even_and_trailing_locks_min_profit(self):
        """Verify scalp progressive break-even locks in at least +$0.50 profit once activated."""
        self.engine.scalp_mode = True
        self.engine.min_profit_usd = 0.50
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

        # Market rallies to 2701.0 (unrealized profit = +$1.00 > $0.50)
        candle = {"open": 2700.0, "high": 2701.0, "low": 2699.8, "close": 2700.8}
        self.engine.update_market_candle("XAUUSD", candle)

        # Verify stop loss is moved to lock in at least +$0.50 profit (entry + 0.50)
        self.assertGreaterEqual(order.stop_loss, 2700.50)

        # Market pulls back and hits the secured stop loss
        pullback_candle = {"open": 2700.8, "high": 2700.9, "low": 2700.4, "close": 2700.4}
        events = self.engine.update_market_candle("XAUUSD", pullback_candle)

        # Verify trade closed with at least +$0.50 realized profit
        self.assertIn(order.status, (OrderStatus.CLOSED,))
        self.assertGreaterEqual(order.realized_pnl, 0.50)

    def test_format_positions_detail_includes_slots_and_min_profit(self):
        """Verify format_positions_detail shows slots mode and guaranteed profit floor."""
        self.engine.set_slots_mode("manual", 4)
        self.engine.min_profit_usd = 0.50
        detail = self.engine.format_positions_detail()

        self.assertIn("0/4 active | 4 slots free", detail)
        self.assertIn("Slots Mode:", detail)
        self.assertIn("Manual (4/6)", detail)
        self.assertIn("Min Profit", detail)
        self.assertIn("+$0.50 USD", detail)

    def test_cli_slots_and_positions_commands(self):
        """Verify CLI interactive handling for slots mode and positions commands."""
        input_data = io.StringIO(
            "slots 4\n"
            "slots auto\n"
            "slots minprofit 0.75\n"
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
        self.assertIn("Minimum profit guarantee set to +$0.75 USD per trade", out)
        self.assertIn("Active positions limit set to MANUAL mode: 3/6 slots", out)


if __name__ == "__main__":
    unittest.main()
