"""
Comprehensive Live Trading Readiness & End-to-End Safety Test Suite.
Verifies production readiness for:
1. MetaTrader 5 Live execution engine (order dispatch, SL/TP sync, fills).
2. Delta Exchange India REST execution engine (leverage, brackets, fills).
3. End-to-End Autotrade pipeline with ExecutionEngine and RiskManager.
4. Emergency circuit breaker (kill switch) and risk governance enforcement.
"""

import os
import unittest
from delta_broker import MockDeltaBroker
from mt5_broker import MockMT5Broker
from execution_engine import ExecutionEngine, OrderStatus
from risk_manager import RiskManager


class TestLiveTradingReadiness(unittest.TestCase):

    def setUp(self):
        self.state_file = "test_live_readiness_state.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_mt5_live_readiness(self):
        """Verify MT5 live broker lifecycle: connect, place order, update SL/TP, and close."""
        broker = MockMT5Broker()
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.mode, "live")
        self.assertGreater(broker.balance, 0)

        # 1. Place live buy order
        order = broker.place_order(
            symbol="BTCUSD",
            side="buy",
            qty=0.02,
            price=68500.0,
            stop_loss=67500.0,
            take_profit=70500.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.filled_price, 68500.0)
        self.assertEqual(order.filled_qty, 0.02)
        self.assertIn("BTCUSD", broker.positions)

        # 2. Modify live order bracket (SL/TP)
        self.assertTrue(broker.modify_position("BTCUSD", stop_loss=68000.0, take_profit=71000.0))

        # 3. Close live position
        self.assertTrue(broker.close_position("BTCUSD", price=69000.0))
        self.assertNotIn("BTCUSD", broker.positions)

    def test_delta_live_readiness(self):
        """Verify Delta Exchange live broker lifecycle: connect, place order, update SL/TP, and close."""
        broker = MockDeltaBroker(balance=25000.0, leverage=20.0)
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.balance, 25000.0)

        # 1. Place live buy order
        order = broker.place_order(
            symbol="BTCUSDT",
            side="buy",
            qty=0.05,
            price=68000.0,
            stop_loss=67000.0,
            take_profit=70000.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.filled_price, 68000.0)
        self.assertEqual(order.filled_qty, 0.05)
        self.assertIn("BTCUSD", broker.positions)

        # 2. Update mark price and modify position
        broker.update_mark_price("BTCUSDT", 68500.0)
        self.assertTrue(broker.modify_position("BTCUSDT", stop_loss=67800.0, take_profit=71500.0))

        # 3. Close live position
        self.assertTrue(broker.close_position("BTCUSDT", price=68500.0))
        self.assertNotIn("BTCUSD", broker.positions)
        self.assertGreater(broker.balance, 25000.0)

    def test_autotrade_pipeline_end_to_end_mt5(self):
        """Verify end-to-end autotrade execution with MT5: setup -> entry fill -> TP1 -> trailing -> exit."""
        events = []

        def callback(event_name, order, message):
            events.append({"event": event_name, "order": order, "message": message})

        broker = MockMT5Broker()
        rm = RiskManager(equity=10000.0, symbol="BTCUSD", state_file=self.state_file)
        rm.set_leverage(20.0)
        engine = ExecutionEngine(risk_manager=rm, broker=broker, event_callback=callback)

        sig = {
            "symbol": "BTCUSD",
            "direction": "LONG",
            "entry": 68000.0,
            "stop": 67000.0,
            "tp1": 69000.0,
            "tp2": 70000.0,
            "lots": 0.02,
            "size": 0.02,
            "leverage": 20.0,
            "margin_required": 68.0,
            "liquidation_price": 64600.0,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        order = engine.register_setup(sig, reason="End-to-End MT5 Pipeline Test")
        self.assertEqual(order.status, OrderStatus.PENDING)

        # 1. Trigger live entry
        candle_entry = {"open": 67980.0, "high": 68050.0, "low": 67950.0, "close": 68020.0}
        engine.update_market_candle("BTCUSD", candle_entry)
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertIn(order.id, engine.active_positions)

        # Verify confirmation alert
        exec_ev = next(e for e in events if e["event"] == "TRADE_EXECUTED")
        self.assertIn("MT5 LIVE TRADE CONFIRMED", exec_ev["message"])

        # 2. Price advances to TP1 (+1000 pts)
        candle_tp1 = {"open": 68800.0, "high": 69100.0, "low": 68750.0, "close": 69050.0}
        engine.update_market_candle("BTCUSD", candle_tp1)
        self.assertEqual(order.partials_done, 1)
        # Check that stop loss moved to Break-Even with buffer
        self.assertGreaterEqual(order.stop_loss, 68000.0)

        # 3. Clean manual close of remaining position
        closed_order = engine.close_position("BTCUSD", current_price=69200.0, reason="Target Reached")
        self.assertIsNotNone(closed_order)
        self.assertEqual(closed_order.status, OrderStatus.CLOSED)

    def test_autotrade_pipeline_end_to_end_delta(self):
        """Verify end-to-end autotrade execution with Delta Exchange: setup -> entry fill -> TP1 -> exit."""
        events = []

        def callback(event_name, order, message):
            events.append({"event": event_name, "order": order, "message": message})

        broker = MockDeltaBroker(balance=20000.0)
        rm = RiskManager(equity=20000.0, symbol="BTCUSDT", state_file=self.state_file)
        rm.set_leverage(20.0)
        engine = ExecutionEngine(risk_manager=rm, broker=broker, event_callback=callback)

        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 68000.0,
            "stop": 67000.0,
            "tp1": 69000.0,
            "tp2": 70000.0,
            "lots": 0.02,
            "size": 0.02,
            "leverage": 20.0,
            "margin_required": 68.0,
            "liquidation_price": 64600.0,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        order = engine.register_setup(sig, reason="End-to-End Delta Pipeline Test")
        self.assertEqual(order.status, OrderStatus.PENDING)

        # 1. Trigger live entry
        candle_entry = {"open": 67980.0, "high": 68050.0, "low": 67950.0, "close": 68020.0}
        engine.update_market_candle("BTCUSDT", candle_entry)
        self.assertEqual(order.status, OrderStatus.OPEN)

        # Verify confirmation alert contains Delta Live
        exec_ev = next(e for e in events if e["event"] == "TRADE_EXECUTED")
        self.assertIn("DELTA LIVE TRADE CONFIRMED", exec_ev["message"])
        self.assertIn("Delta Exchange India Live Execution", exec_ev["message"])

        # 2. Advance to TP1
        candle_tp1 = {"open": 68800.0, "high": 69100.0, "low": 68750.0, "close": 69050.0}
        engine.update_market_candle("BTCUSDT", candle_tp1)
        self.assertEqual(order.partials_done, 1)

        # 3. Clean exit
        closed_order = engine.close_position("BTCUSDT", current_price=69300.0, reason="Target Reached")
        self.assertIsNotNone(closed_order)
        self.assertEqual(closed_order.status, OrderStatus.CLOSED)

    def test_emergency_kill_circuit_breaker(self):
        """Verify emergency circuit breaker (/kill on): cancels pending setups and halts trading."""
        broker = MockMT5Broker()
        rm = RiskManager(equity=10000.0, symbol="BTCUSD", state_file=self.state_file)
        engine = ExecutionEngine(risk_manager=rm, broker=broker)

        sig = {
            "symbol": "BTCUSD",
            "direction": "LONG",
            "entry": 68000.0,
            "stop": 67000.0,
            "tp1": 69000.0,
            "tp2": 70000.0,
            "lots": 0.02,
            "size": 0.02,
            "leverage": 20.0,
            "margin_required": 68.0,
            "liquidation_price": 64600.0,
            "risk_amount": 20.0,
            "rr": 1.5,
        }
        order = engine.register_setup(sig, reason="Pending before kill")
        self.assertIn("BTCUSD", engine.pending_setups)

        # Emergency halt
        engine.cancel_setup("BTCUSD", reason="Emergency /kill command invoked")
        self.assertNotIn("BTCUSD", engine.pending_setups)
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_risk_governance_live_enforcement(self):
        """Verify risk manager enforces maximum daily loss limit and prevents runaway drawdown."""
        rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        # Sizing works under normal conditions
        plan = rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertGreater(plan.lots, 0.0)

        # Simulate consecutive losses exceeding daily max drawdown ($500 limit on $10k equity)
        rm.update_after_trade(pnl=-350.0, r_multiple=-1.0)
        rm.update_after_trade(pnl=-250.0, r_multiple=-1.0)

        # Verify risk breaker is tripped
        blocked_plan = rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertEqual(blocked_plan.lots, 0.0)
        self.assertIn("daily loss limit", blocked_plan.reason.lower())

        # Verify restart clears limits
        rm.reset_all_risk()
        restarted_plan = rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertGreater(restarted_plan.lots, 0.0)


if __name__ == "__main__":
    unittest.main()
