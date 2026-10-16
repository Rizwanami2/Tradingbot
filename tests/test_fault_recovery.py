"""
Fault Recovery, Restart Recovery, and Broker Safety Regression Test Suite.
Validates:
1. Restart recovery: saving state to disk and restoring active positions and pending setups.
2. Broker position reconciliation and external position adoption.
3. Fail-closed behavior on broker position-query failure (OrderStateUnknownError + execution halt).
4. Fail-closed behavior on broker close failure (retaining local position + execution halt).
5. Broker-side SL/TP bracket synchronization and fault handling.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from execution import OrderStateUnknownError, Position
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from delta_broker import MockDeltaBroker
from mt5_broker import MockMT5Broker
from risk_manager import RiskManager
import config


class TestFaultRecoveryAndBrokerSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "execution_state.json")
        self.rm_state_file = os.path.join(self.temp_dir.name, "risk_state.json")
        self.risk_manager = RiskManager(equity=10000.0, symbol="BTCUSD", state_file=self.rm_state_file)
        self.risk_manager.set_leverage(20.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_restart_recovery_persists_and_restores_positions(self):
        """Verify saving execution state to disk and restoring it exactly upon restart."""
        events = []
        engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=MockMT5Broker(),
            state_file=self.state_file,
            event_callback=lambda ev, order, msg: events.append(ev),
        )

        # Register setup and fill entry
        sig = {
            "symbol": "BTCUSD",
            "direction": "LONG",
            "entry": 68000.0,
            "stop": 67000.0,
            "tp1": 69000.0,
            "tp2": 70000.0,
            "lots": 0.05,
            "size": 0.05,
            "leverage": 20.0,
            "margin_required": 170.0,
            "liquidation_price": 64600.0,
            "risk_amount": 50.0,
            "rr": 2.0,
        }
        order = engine.register_setup(sig, reason="Recovery Test Setup")
        engine.update_market_candle("BTCUSD", {"open": 67990.0, "high": 68050.0, "low": 67950.0, "close": 68010.0})
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertIn(order.id, engine.active_positions)

        # Register a second pending setup
        sig2 = {
            "symbol": "XAUUSD",
            "direction": "SHORT",
            "entry": 2750.0,
            "stop": 2770.0,
            "tp1": 2720.0,
            "tp2": 2700.0,
            "lots": 0.1,
            "size": 0.1,
            "leverage": 20.0,
            "margin_required": 137.5,
            "liquidation_price": 2887.5,
            "risk_amount": 20.0,
            "rr": 2.5,
        }
        order2 = engine.register_setup(sig2, reason="Pending Gold Setup")
        self.assertIn(order2.id, engine.pending_setups)

        # Persist state
        engine.save_state(self.state_file)
        self.assertTrue(os.path.isfile(self.state_file))

        # Simulate bot restart with a fresh engine instance
        restarted_engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=MockMT5Broker(),
            state_file=self.state_file,
        )
        self.assertTrue(restarted_engine.restore_state(self.state_file))

        # Verify active positions and pending setups restored
        self.assertEqual(len(restarted_engine.active_positions), 1)
        self.assertIn(order.id, restarted_engine.active_positions)
        restored_pos = restarted_engine.active_positions[order.id]
        self.assertEqual(restored_pos.symbol, "BTCUSD")
        self.assertEqual(restored_pos.direction, "LONG")
        self.assertEqual(restored_pos.entry_level, 68000.0)
        self.assertEqual(restored_pos.lots, 0.05)
        self.assertEqual(restored_pos.status, OrderStatus.OPEN)

        self.assertEqual(len(restarted_engine.pending_setups), 1)
        self.assertIn(order2.id, restarted_engine.pending_setups)
        restored_setup = restarted_engine.pending_setups[order2.id]
        self.assertEqual(restored_setup.symbol, "XAUUSD")
        self.assertEqual(restored_setup.direction, "SHORT")
        self.assertEqual(restored_setup.entry_level, 2750.0)

    def test_reconcile_positions_adopts_untracked_broker_positions(self):
        """Verify reconcile_positions adopts open broker positions not yet tracked locally."""
        broker = MockDeltaBroker(balance=20000.0)
        # Broker has an open position
        broker.place_order(
            symbol="BTCUSDT",
            side="buy",
            qty=0.05,
            price=68000.0,
            stop_loss=67000.0,
            take_profit=70000.0,
        )

        engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=broker,
            state_file=self.state_file,
        )
        self.assertEqual(len(engine.active_positions), 0)

        # Reconcile
        summary = engine.reconcile_positions()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["broker_count"], 1)
        self.assertEqual(summary["adopted"], 1)
        self.assertEqual(len(engine.active_positions), 1)

        adopted = list(engine.active_positions.values())[0]
        self.assertIn("BTC", adopted.symbol.upper())
        self.assertEqual(adopted.direction, "LONG")
        self.assertEqual(adopted.status, OrderStatus.OPEN)

    def test_broker_position_query_failure_fails_closed(self):
        """Verify position query errors raise OrderStateUnknownError and halt execution."""
        broker = MockDeltaBroker()
        # Disconnect broker to trigger OrderStateUnknownError in get_positions
        broker._connected = False

        engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=broker,
            state_file=self.state_file,
        )

        # Should raise OrderStateUnknownError and halt execution
        with self.assertRaises(OrderStateUnknownError):
            engine.recover_positions_from_broker()
        self.assertTrue(engine.execution_halted)

        with self.assertRaises(OrderStateUnknownError):
            engine.reconcile_positions()
        self.assertTrue(engine.execution_halted)

    def test_manual_close_broker_failure_fails_closed_and_retains_position(self):
        """Verify manual close failure halts execution, retains position, and dispatches event."""
        events = []
        broker = MockMT5Broker()
        # Mock broker.close_position to return False (reject)
        broker.close_position = MagicMock(return_value=False)

        engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=broker,
            state_file=self.state_file,
            event_callback=lambda ev, order, msg: events.append({"event": ev, "order": order, "msg": msg}),
        )

        # Create active position
        dummy = TradeOrder(
            id="ORDER-CLOSE-FAIL",
            symbol="BTCUSD",
            direction="LONG",
            entry_level=68000.0,
            stop_loss=67000.0,
            tp1=69000.0,
            tp2=70000.0,
            tp3=None,
            lots=0.05,
            size=0.05,
            leverage=20.0,
            margin_required=170.0,
            liquidation_price=None,
            risk_amount=50.0,
            rr_ratio=2.0,
            setup_reason="Close failure test",
            status=OrderStatus.OPEN,
            executed_price=68000.0,
            remaining_size=0.05,
        )
        engine.active_positions[dummy.id] = dummy

        # Attempt to close position — broker rejected
        with self.assertRaises(OrderStateUnknownError):
            engine.close_position("BTCUSD", current_price=68500.0)

        # Verify fail-closed behavior
        self.assertTrue(engine.execution_halted)
        self.assertIn(dummy.id, engine.active_positions)  # Position RETAINED!
        self.assertEqual(engine.active_positions[dummy.id].status, OrderStatus.OPEN)

        # Verify critical event dispatched
        fail_ev = next((e for e in events if e["event"] == "BROKER_CLOSE_FAILED"), None)
        self.assertIsNotNone(fail_ev)
        self.assertIn("CRITICAL", fail_ev["msg"])

    def test_stop_loss_trigger_broker_failure_fails_closed(self):
        """Verify automatic stop loss hit halts execution and retains position when broker close fails."""
        events = []
        broker = MockDeltaBroker(balance=20000.0)
        # Mock close_position to raise exception
        broker.close_position = MagicMock(side_effect=RuntimeError("Delta gateway timeout"))

        engine = ExecutionEngine(
            risk_manager=self.risk_manager,
            broker=broker,
            state_file=self.state_file,
            event_callback=lambda ev, order, msg: events.append({"event": ev, "order": order, "msg": msg}),
        )

        dummy = TradeOrder(
            id="ORDER-SL-FAIL",
            symbol="BTCUSDT",
            direction="LONG",
            entry_level=68000.0,
            stop_loss=67000.0,
            tp1=69000.0,
            tp2=70000.0,
            tp3=None,
            lots=0.05,
            size=0.05,
            leverage=20.0,
            margin_required=170.0,
            liquidation_price=None,
            risk_amount=50.0,
            rr_ratio=2.0,
            setup_reason="SL failure test",
            status=OrderStatus.OPEN,
            executed_price=68000.0,
            remaining_size=0.05,
        )
        engine.active_positions[dummy.id] = dummy

        # Candle triggers stop loss
        candle = {"open": 67500.0, "high": 67600.0, "low": 66800.0, "close": 66900.0}
        engine.update_market_candle("BTCUSDT", candle)

        # Verify fail-closed behavior
        self.assertTrue(engine.execution_halted)
        self.assertIn(dummy.id, engine.active_positions)  # Position RETAINED!
        fail_ev = next((e for e in events if e["event"] == "BROKER_CLOSE_FAILED"), None)
        self.assertIsNotNone(fail_ev)

    def test_broker_bracket_sltp_sync_and_modify(self):
        """Verify broker-side SL/TP bracket synchronization on MT5 and Delta Exchange."""
        mt5_broker = MockMT5Broker()
        order = mt5_broker.place_order("BTCUSD", "buy", 0.02, price=68000.0, stop_loss=67000.0, take_profit=70000.0)
        self.assertTrue(mt5_broker.modify_position("BTCUSD", stop_loss=67500.0, take_profit=71000.0))
        pos = mt5_broker.positions["BTCUSD"]
        self.assertEqual(pos.stop_loss, 67500.0)
        self.assertEqual(pos.take_profit, 71000.0)

        delta_broker = MockDeltaBroker(balance=20000.0)
        delta_order = delta_broker.place_order("BTCUSDT", "buy", 0.02, price=68000.0, stop_loss=67000.0, take_profit=70000.0)
        self.assertTrue(delta_broker.modify_position("BTCUSDT", stop_loss=67500.0, take_profit=71000.0))
        d_pos = delta_broker.positions["BTCUSD"]
        self.assertEqual(d_pos.stop_loss, 67500.0)
        self.assertEqual(d_pos.take_profit, 71000.0)


if __name__ == "__main__":
    unittest.main()
