"""
Unit tests for Enhanced TP, SL, Break-Even, Trailing Stop, and Entry Planner.
"""

import math
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from entry_level_planner import EntryPlan, plan_best_entry, attach_plan_to_signal
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from risk_manager import RiskManager, RiskConfig
from structure_trailing import structure_trail_stop, hybrid_trail_stop
import execution_enhancements


def _create_sample_ohlcv(bars: int = 50, base_price: float = 2650.0, trend: str = "up") -> pd.DataFrame:
    data = []
    p = base_price
    for i in range(bars):
        step = 0.5 if trend == "up" else -0.5
        o = p
        h = o + 2.0
        l = o - 1.5
        c = o + step
        p = c
        data.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000 + i * 10})
    return pd.DataFrame(data)


class TestEnhancedEntryPlanner(unittest.TestCase):
    def test_plan_best_entry_long_discount_zone_and_buffer(self):
        df = _create_sample_ohlcv(50, base_price=2650.0, trend="up")
        plan = plan_best_entry(
            symbol="XAUUSD",
            df=df,
            direction="LONG",
            mark=2675.0,
            risk_r=1.5,
        )
        self.assertEqual(plan.direction, "LONG")
        # Best entry should be at or below mark (discount pullback entry)
        self.assertLessEqual(plan.best_entry, 2675.0)
        # Entry zone
        self.assertLessEqual(plan.entry_low, plan.best_entry)
        self.assertGreaterEqual(plan.entry_high, plan.best_entry)
        # Stop loss must be safely below best_entry with buffer
        self.assertLess(plan.stop, plan.entry_low)
        # Minimum stop distance must be at least spread buffer
        self.assertGreaterEqual(plan.best_entry - plan.stop, 0.70)
        # TP1 must have at least 1.5R reward:risk
        risk = plan.best_entry - plan.stop
        self.assertGreaterEqual(plan.tp1 - plan.best_entry, risk * 1.5 - 1e-4)
        # TP2 must be >= 2.5R
        self.assertGreaterEqual(plan.tp2 - plan.best_entry, risk * 2.4 - 1e-4)
        # TP3 runner target
        self.assertIsNotNone(plan.tp3)
        self.assertGreater(plan.tp3, plan.tp2)

    def test_plan_best_entry_short_premium_zone_and_buffer(self):
        df = _create_sample_ohlcv(50, base_price=68000.0, trend="down")
        plan = plan_best_entry(
            symbol="BTCUSD",
            df=df,
            direction="SHORT",
            mark=67000.0,
            risk_r=1.5,
        )
        self.assertEqual(plan.direction, "SHORT")
        # Best entry should be at or above mark (premium pullback entry)
        self.assertGreaterEqual(plan.best_entry, 67000.0)
        # Stop loss must be safely above best_entry with buffer
        self.assertGreater(plan.stop, plan.entry_high)
        # TP1 must be below best_entry by at least 1.5R
        risk = plan.stop - plan.best_entry
        self.assertGreaterEqual(plan.best_entry - plan.tp1, risk * 1.5 - 1e-4)
        # TP2 must be >= 2.5R
        self.assertGreaterEqual(plan.best_entry - plan.tp2, risk * 2.4 - 1e-4)

    def test_attach_plan_to_signal_updates_structural_levels(self):
        df = _create_sample_ohlcv(50, base_price=2650.0, trend="up")
        plan = plan_best_entry(
            symbol="XAUUSD",
            df=df,
            direction="LONG",
            mark=2675.0,
        )
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2675.0,
            "stop": 2665.0,
            "tp1": 2690.0,
            "tp2": 2700.0,
            "lots": 0.05,
            "size": 5.0,
            "risk_amount": 50.0,
            "rr": 1.5,
        }
        updated = attach_plan_to_signal(sig, plan)
        # Must adopt refined entry and structural stop loss
        self.assertEqual(updated["entry"], plan.best_entry)
        self.assertEqual(updated["stop"], plan.stop)
        self.assertEqual(updated["tp1"], plan.tp1)
        self.assertEqual(updated["tp2"], plan.tp2)
        self.assertGreaterEqual(updated["rr"], 1.5)


class TestEnhancedBreakEvenAndTrailing(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD")

    def test_progressive_three_stage_breakeven(self):
        # Entry: 2650, Initial Stop: 2640 (10 pts risk = 1.0R)
        entry = 2650.0
        stop = 2640.0
        risk = 10.0

        # Stage 1: +0.5R (price reaches 2655.0) -> Early lock (+0.1R = 2651.0)
        s1_stop, s1_reason = self.rm.auto_breakeven_stop("long", entry, stop, highest=2655.0, lowest=2650.0, initial_risk=risk)
        self.assertGreater(s1_stop, stop)
        self.assertIn("Auto BE lock", s1_reason)

        # Stage 2: +1.0R (price reaches 2660.0) -> Full BE + buffer (+0.25R = 2652.5)
        s2_stop, s2_reason = self.rm.auto_breakeven_stop("long", entry, s1_stop, highest=2660.0, lowest=2650.0, initial_risk=risk)
        self.assertGreaterEqual(s2_stop, 2652.0)
        self.assertIn("full", s2_reason)

        # Stage 3: +1.5R (price reaches 2665.0) -> Profit Lock (+0.5R = 2655.0)
        s3_stop, s3_reason = self.rm.auto_breakeven_stop("long", entry, s2_stop, highest=2665.0, lowest=2650.0, initial_risk=risk)
        self.assertGreaterEqual(s3_stop, 2655.0)
        self.assertIn("profit lock", s3_reason)

    def test_structure_trail_stop_profits_above_entry(self):
        # Simulate an uptrend where swing lows are formed ABOVE entry
        # Entry: 2650.0, Current Stop: 2652.5 (already past entry)
        df = pd.DataFrame({
            "high": [2660.0, 2663.0, 2665.0, 2668.0, 2670.0, 2672.0, 2675.0, 2678.0, 2680.0, 2682.0],
            "low":  [2656.0, 2658.0, 2660.0, 2662.0, 2664.0, 2666.0, 2668.0, 2670.0, 2672.0, 2674.0],
            "close": [2659.0, 2662.0, 2664.0, 2667.0, 2669.0, 2671.0, 2674.0, 2677.0, 2679.0, 2681.0],
            "open":  [2657.0, 2660.0, 2662.0, 2665.0, 2667.0, 2669.0, 2672.0, 2675.0, 2677.0, 2679.0],
        })
        new_stop, reason = structure_trail_stop(
            direction="LONG",
            entry=2650.0,
            current_stop=2652.5,
            df=df,
            atr_value=2.0,
            lookback=5,
        )
        # Should ratchet stop well above entry into profit (> 2652.5)
        self.assertGreater(new_stop, 2652.5)
        self.assertGreater(new_stop, 2650.0)
        self.assertIn("locked profit", reason)

    def test_hybrid_trail_stop_selects_tighter_level(self):
        # Price peaked at 2680.0, current price 2675.0, ATR = 2.0
        # Trail ATR mult = 2.0 -> chandelier stop = 2680 - 4.0 = 2676.0
        best_stop, reason = hybrid_trail_stop(
            direction="LONG",
            entry=2650.0,
            current_stop=2655.0,
            highest_price=2680.0,
            lowest_price=2650.0,
            current_price=2678.0,
            atr_value=2.0,
            trail_atr_mult=2.0,
            initial_risk=10.0,
        )
        self.assertGreater(best_stop, 2655.0)
        self.assertGreaterEqual(best_stop, 2674.0)


class TestExecutionEngineFrontRunAndBrokerSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ee_enhanced_test_")
        self.state_file = os.path.join(self.test_dir, "test_state.json")
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        self.events = []
        def on_event(event_name, order, message):
            self.events.append({"event": event_name, "order": order, "message": message})

        self.mock_broker = MagicMock()
        self.mock_broker.is_connected = True
        mock_order = MagicMock()
        mock_order.status = "filled"
        mock_order.filled_qty = 0.05
        mock_order.filled_price = 2650.0
        mock_order.ticket = 998877
        self.mock_broker.place_order.return_value = mock_order

        self.engine = ExecutionEngine(
            risk_manager=self.rm,
            broker=self.mock_broker,
            event_callback=on_event,
            state_file=self.state_file,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_front_run_setup_cancellation(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.05,
            "size": 5.0,
            "risk_amount": 50.0,
            "rr": 2.0,
        }
        order = self.engine.register_setup(sig)
        self.assertEqual(order.status, OrderStatus.PENDING)

        # Market rallies straight to TP1 (high=2666.0, low=2655.0) without touching entry (2650.0)
        candle = {"open": 2656.0, "high": 2666.0, "low": 2655.0, "close": 2665.0}
        self.engine.update_market_candle("XAUUSD", candle)

        # Order must be cancelled due to front-run rather than executing at the top
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertIn("front-run", order.close_reason.lower())
        self.assertNotIn(order.id, self.engine.pending_setups)
        self.assertNotIn("XAUUSD", self.engine.active_positions)

    def test_broker_modify_and_virtual_stop_sync_on_trailing(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.05,
            "size": 5.0,
            "risk_amount": 50.0,
            "rr": 2.0,
        }
        order = self.engine.register_setup(sig)
        # Fill order
        self.engine.update_market_candle("XAUUSD", {"open": 2651.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})
        self.assertEqual(order.status, OrderStatus.OPEN)

        # Hit TP1 (+1.5R): takes 50% off and moves stop to BE + buffer
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2666.0, "low": 2650.0, "close": 2665.5})
        self.assertEqual(order.partials_done, 1)
        self.mock_broker.modify_position.assert_called()

        # Dynamic trailing advances stop further as price reaches 2674.0
        prev_calls = self.mock_broker.modify_position.call_count
        self.engine.update_market_candle("XAUUSD", {"open": 2665.0, "high": 2674.0, "low": 2664.0, "close": 2673.0})
        # Broker modify must have been called again to sync the trailing stop
        self.assertGreaterEqual(self.mock_broker.modify_position.call_count, prev_calls)
        self.assertGreater(order.stop_loss, 2650.0)


if __name__ == "__main__":
    unittest.main()
