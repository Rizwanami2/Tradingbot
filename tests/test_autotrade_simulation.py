"""
End-to-End Autotrade Simulation & Verification Test Suite.
Verifies the complete pipeline:
Multi-agent signals -> EA Bridge execution -> Position tracking ->
Anti-spam / concurrency limiter -> Trailing stop / Auto-BE -> Telegram events & Lifecycle.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import config
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from mt5_broker import MT5Broker, _RESOLVED_CACHE
from risk_manager import RiskManager
from bridge import ea_client


class TestAutotradeSimulation(unittest.TestCase):
    def setUp(self):
        _RESOLVED_CACHE.clear()
        self.test_dir = tempfile.mkdtemp(prefix="ea_bridge_sim_")
        ea_client.set_bridge_dir(self.test_dir)
        self._orig_backend = getattr(config, "MT5_BACKEND", "auto")
        config.MT5_BACKEND = "ea_bridge"

        self.state_file = os.path.join(self.test_dir, "test_sim_state.json")
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)
        self.rm.set_leverage(20.0)

        self.events = []
        def on_event(event_name, order, message):
            self.events.append({"event": event_name, "order": order, "message": message})

        # Initialize broker with EA Bridge backend
        self.broker = MT5Broker(mode="live", backend="ea_bridge", magic=112233)
        self.engine = ExecutionEngine(
            risk_manager=self.rm,
            broker=self.broker,
            event_callback=on_event,
            state_file=self.state_file,
        )

    def tearDown(self):
        _RESOLVED_CACHE.clear()
        config.MT5_BACKEND = self._orig_backend
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_e2e_signal_to_ea_bridge_execution_and_trailing_stop(self):
        """Simulate end-to-end signal registration, EA order fill, trailing stop BE, and close."""
        # 1. Mock EA response for order send (buy/sell), modify, and close
        mock_buy = MagicMock(return_value={
            "ok": True,
            "ticket": 789101,
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.05,
            "price": 2650.0,
            "sl": 2640.0,
            "tp": 2665.0,
            "time": "2026-10-17 12:00:00",
        })
        mock_modify = MagicMock(return_value={
            "ok": True,
            "ticket": 789101,
            "sl": 2650.0,
            "tp": 2665.0,
        })
        mock_close = MagicMock(return_value={
            "ok": True,
            "ticket": 789101,
            "symbol": "XAUUSD",
            "price": 2675.0,
        })

        with patch.object(ea_client, "buy", mock_buy), \
             patch.object(ea_client, "modify", mock_modify), \
             patch.object(ea_client, "close", mock_close):

            # Step 1: Register setup from multi-agent signal
            sig = {
                "symbol": "XAUUSD",
                "direction": "LONG",
                "entry": 2650.0,
                "stop": 2640.0,
                "tp1": 2665.0,
                "tp2": 2675.0,
                "lots": 0.05,
                "size": 5.0,
                "leverage": 20.0,
                "margin_required": 132.5,
                "risk_amount": 50.0,
                "rr": 2.0,
                "reason": "Trend 5-Agent SMC Confluence",
            }

            order = self.engine.register_setup(sig, reason="Trend 5-Agent SMC Confluence")
            self.assertEqual(order.status, OrderStatus.PENDING)
            self.assertEqual(len(self.events), 1)
            self.assertEqual(self.events[0]["event"], "SETUP_REGISTERED")
            self.assertIn("Waiting for trigger", self.events[0]["message"])

            # Step 2: Market candle arrives that does not reach entry (e.g. 2652 - 2655)
            self.engine.update_market_candle("XAUUSD", {
                "open": 2653.0, "high": 2655.0, "low": 2652.0, "close": 2653.0
            })
            self.assertEqual(order.status, OrderStatus.PENDING)
            mock_buy.assert_not_called()

            # Step 3: Market candle arrives that touches entry (2648 - 2651)
            self.engine.update_market_candle("XAUUSD", {
                "open": 2652.0, "high": 2651.0, "low": 2648.0, "close": 2650.0
            })
            self.assertEqual(order.status, OrderStatus.OPEN)
            self.assertEqual(order.ticket, "789101")
            self.assertEqual(order.executed_price, 2650.0)
            mock_buy.assert_called_once()
            args, kwargs = mock_buy.call_args
            self.assertEqual(args[0], "XAUUSD")
            self.assertEqual(args[1], 0.05)
            self.assertEqual(kwargs.get("sl"), 2640.0)

            # Check TRADE_EXECUTED notification
            exec_events = [e for e in self.events if e["event"] == "TRADE_EXECUTED"]
            self.assertEqual(len(exec_events), 1)
            self.assertIn("MT5 LIVE TRADE CONFIRMED", exec_events[0]["message"])
            self.assertIn("XAUUSD", exec_events[0]["message"])

            # Step 4: Price rallies towards TP1 (touches 2665.0) -> Auto-BE / trailing stop triggered
            self.engine.update_market_candle("XAUUSD", {
                "open": 2650.0, "high": 2666.0, "low": 2650.0, "close": 2665.5
            })
            # Verify modify_position was called via EA client
            mock_modify.assert_called()
            mod_args, mod_kwargs = mock_modify.call_args
            self.assertEqual(mod_kwargs.get("ticket"), 789101)
            self.assertGreaterEqual(mod_kwargs.get("sl"), 2650.0)

            # Check Auto-BE / TP1 notification event
            be_events = [e for e in self.events if e["event"] in ("AUTO_BE_LOCKED", "TP1_PARTIAL_TAKEN")]
            self.assertTrue(len(be_events) >= 1)

            # Step 5: Price hits TP2 or manual close is requested
            res = self.engine.close_position(order.id, reason="Target achieved")
            self.assertTrue(res)
            # mock_close called twice: 1st for 50% partial TP1, 2nd for final close
            self.assertEqual(mock_close.call_count, 2)
            c_args, c_kwargs = mock_close.call_args
            self.assertEqual(c_kwargs.get("ticket"), 789101)
            self.assertEqual(c_kwargs.get("symbol"), "XAUUSD")

            self.assertEqual(order.status, OrderStatus.CLOSED)
            self.assertNotIn(order.id, self.engine.active_positions)
            self.assertIn(order, self.engine.trade_history)

    def test_anti_spam_concurrency_limiter(self):
        """Verify max concurrent trades limit and duplicate setup throttling."""
        self.engine.max_open_trades = 2

        sig1 = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 20.0,
            "rr": 2.0,
        }
        sig2 = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 65000.0,
            "stop": 64000.0,
            "tp1": 67000.0,
            "tp2": 68000.0,
            "lots": 0.01,
            "size": 1.0,
            "leverage": 10.0,
            "margin_required": 65.0,
            "risk_amount": 10.0,
            "rr": 2.0,
        }
        sig3 = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2655.0,
            "stop": 2645.0,
            "tp1": 2670.0,
            "tp2": 2680.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 20.0,
            "rr": 2.0,
        }

        # Slot 1: Active position
        o1 = self.engine.register_setup(sig1)
        o1.status = OrderStatus.OPEN
        self.engine.active_positions[o1.id] = o1
        self.engine.pending_setups.pop(o1.id, None)
        self.assertEqual(self.engine.open_trade_count(), 1)
        self.assertEqual(self.engine.slots_available(), 1)

        # Slot 2: Active position
        o2 = self.engine.register_setup(sig2)
        o2.status = OrderStatus.OPEN
        self.engine.active_positions[o2.id] = o2
        self.engine.pending_setups.pop(o2.id, None)
        self.assertEqual(self.engine.open_trade_count(), 2)
        self.assertEqual(self.engine.slots_available(), 0)

        # Slot 3 attempt: must be rejected by concurrency limiter
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(sig3)
        self.assertIn("Max concurrent trades reached", str(ctx.exception))

    def test_broker_rejection_fails_closed(self):
        """If EA Bridge returns failure, engine cancels setup, dispatches failure, and halts live."""
        mock_buy = MagicMock(return_value={
            "ok": False,
            "error": "MT5 Trade Retcode 10018: Market is closed",
            "hint": "Check symbol trading hours",
        })

        with patch.object(ea_client, "buy", mock_buy):
            sig = {
                "symbol": "XAUUSD",
                "direction": "LONG",
                "entry": 2650.0,
                "stop": 2640.0,
                "tp1": 2665.0,
                "tp2": 2675.0,
                "lots": 0.02,
                "size": 2.0,
                "leverage": 20.0,
                "margin_required": 50.0,
                "risk_amount": 20.0,
                "rr": 2.0,
            }
            order = self.engine.register_setup(sig)

            # Trigger fill attempt
            self.engine.update_market_candle("XAUUSD", {
                "open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0
            })

            # Order should be cancelled, not open
            self.assertEqual(order.status, OrderStatus.CANCELLED)
            self.assertIn("Execution failed", order.close_reason)
            self.assertTrue(self.engine.execution_halted)

            fail_events = [e for e in self.events if e["event"] == "BROKER_EXECUTION_FAILED"]
            self.assertEqual(len(fail_events), 1)
            self.assertIn("EXECUTION HALTED", fail_events[0]["message"])

    def test_anti_spam_rate_limiter_cooldown_and_backoff(self):
        """Verify anti-spam cooldown, consecutive loss backoff, and price distance protection."""
        from anti_spam import AntiSpamRateLimiter
        limiter = AntiSpamRateLimiter(
            entry_cooldown_sec=10,
            loss_cooldown_sec=30,
            global_cooldown_sec=5,
            min_price_distance_pct=0.10,
        )

        t0 = 100000.0
        # 1. Normal first entry: allowed
        can_enter, reason = limiter.check_can_enter("XAUUSD", 2650.0, engine=self.engine, max_open=2, timestamp=t0)
        self.assertTrue(can_enter)

        # Record entry at t0
        limiter.record_entry("XAUUSD", 2650.0, timestamp=t0)

        # 2. Immediate re-entry attempt at t0 + 1s: blocked by global cooldown
        can_enter2, reason2 = limiter.check_can_enter("XAUUSD", 2650.0, engine=self.engine, max_open=2, timestamp=t0 + 1.0)
        self.assertFalse(can_enter2)
        self.assertIn("global cooldown", reason2.lower())

        # 3. Simulate loss: activates loss cooldown
        limiter.record_loss("XAUUSD", 25.0, timestamp=t0 + 12.0)
        # At t0 + 15.0: global (5s) and entry (10s) elapsed, but within 30s loss cooldown
        can_enter3, reason3 = limiter.check_can_enter("XAUUSD", 2640.0, engine=self.engine, max_open=2, timestamp=t0 + 15.0)
        self.assertFalse(can_enter3)
        self.assertIn("loss cooldown", reason3.lower())

        # 4. Reset symbol clears lock
        limiter.reset("XAUUSD")
        can_enter4, _ = limiter.check_can_enter("XAUUSD", 2650.0, engine=self.engine, max_open=2, timestamp=t0 + 15.0)
        self.assertTrue(can_enter4)


if __name__ == "__main__":
    unittest.main()
