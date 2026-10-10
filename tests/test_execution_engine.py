"""
Unit tests for Automated Trade Execution Engine & Order Manager.
"""

import os
import unittest
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager, RiskConfig


class TestExecutionEngine(unittest.TestCase):
    def setUp(self):
        self.state_file = "test_engine_risk_state.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        self.rm.set_leverage(100.0)
        self.events = []

        def callback(event_name, order, message):
            self.events.append({"event": event_name, "order": order, "message": message})

        self.engine = ExecutionEngine(risk_manager=self.rm, event_callback=callback)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_pending_setup_registration(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        order = self.engine.register_setup(sig, reason="Trend + SMC confluence")
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertIn("XAUUSD", self.engine.pending_setups)
        self.assertNotIn("XAUUSD", self.engine.active_positions)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["event"], "SETUP_REGISTERED")

    def test_setup_level_execution_trigger(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)

        # 1. Candle that does NOT reach entry level (price stays above 2652)
        candle_miss = {"open": 2655.0, "high": 2658.0, "low": 2652.0, "close": 2654.0}
        events = self.engine.update_market_candle("XAUUSD", candle_miss)
        self.assertEqual(len(events), 0)
        self.assertIn("XAUUSD", self.engine.pending_setups)
        self.assertNotIn("XAUUSD", self.engine.active_positions)

        # 2. Candle that touches entry level (low dips to 2649.5 <= 2650)
        candle_touch = {"open": 2653.0, "high": 2654.0, "low": 2649.5, "close": 2651.0}
        events = self.engine.update_market_candle("XAUUSD", candle_touch)
        self.assertTrue(any(ev["event"] == "TRADE_EXECUTED" for ev in events))
        self.assertNotIn("XAUUSD", self.engine.pending_setups)
        self.assertIn("XAUUSD", self.engine.active_positions)
        active_order = self.engine.active_positions["XAUUSD"]
        self.assertEqual(active_order.status, OrderStatus.OPEN)
        self.assertEqual(active_order.executed_price, 2650.0)

    def test_progressive_auto_breakeven_lock(self):
        # Directly place an active trade
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        # Execute order
        self.engine.update_market_candle("XAUUSD", {"open": 2651.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        # Advance price to +0.5R (2655.0)
        candle_be_lock = {"open": 2650.0, "high": 2655.5, "low": 2650.0, "close": 2654.0}
        events = self.engine.update_market_candle("XAUUSD", candle_be_lock)
        self.assertTrue(any(ev["event"] == "AUTO_BE_LOCKED" for ev in events))
        self.assertGreater(self.engine.active_positions["XAUUSD"].stop_loss, 2640.0)

    def test_tp1_and_tp2_partials(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        # Execute order
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        # Hit TP1 (2665.0)
        candle_tp1 = {"open": 2655.0, "high": 2666.0, "low": 2654.0, "close": 2664.0}
        events = self.engine.update_market_candle("XAUUSD", candle_tp1)
        self.assertTrue(any(ev["event"] == "TP1_PARTIAL_TAKEN" for ev in events))
        pos = self.engine.active_positions["XAUUSD"]
        self.assertEqual(pos.partials_done, 1)
        self.assertGreater(pos.realized_pnl, 0.0)
        self.assertGreaterEqual(pos.stop_loss, 2650.0)  # BE secured

        # Hit TP2 (2675.0)
        candle_tp2 = {"open": 2665.0, "high": 2676.0, "low": 2663.0, "close": 2674.0}
        events2 = self.engine.update_market_candle("XAUUSD", candle_tp2)
        self.assertTrue(any(ev["event"] == "TP2_PARTIAL_TAKEN" for ev in events2))
        self.assertEqual(pos.partials_done, 2)

    def test_stop_loss_exit(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        # Market dips to stop loss
        candle_sl = {"open": 2645.0, "high": 2646.0, "low": 2639.0, "close": 2639.5}
        events = self.engine.update_market_candle("XAUUSD", candle_sl)
        self.assertTrue(any(ev["event"] == "TRADE_CLOSED" for ev in events))
        self.assertNotIn("XAUUSD", self.engine.active_positions)
        self.assertEqual(len(self.engine.trade_history), 1)
        self.assertEqual(self.engine.trade_history[0].status, OrderStatus.CLOSED)

    def test_manual_close(self):
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 100.0,
            "margin_required": 53.0,
            "liquidation_price": 2623.5,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        closed = self.engine.close_manually("XAUUSD", current_price=2658.0, reason="User manual close")
        self.assertIsNotNone(closed)
        self.assertEqual(closed.status, OrderStatus.CLOSED)
        self.assertEqual(closed.exit_price, 2658.0)
        self.assertGreater(closed.realized_pnl, 0.0)
        self.assertNotIn("XAUUSD", self.engine.active_positions)

    def test_portfolio_summary_with_leverage(self):
        summary = self.engine.get_portfolio_summary()
        self.assertEqual(summary["equity"], 10000.0)
        self.assertEqual(summary["leverage"], 100.0)
        self.assertEqual(summary["used_margin"], 0.0)
        self.assertEqual(summary["free_margin"], 10000.0)


if __name__ == "__main__":
    unittest.main()
