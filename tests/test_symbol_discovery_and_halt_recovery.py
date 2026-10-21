"""
Unit tests for MT5 Symbol Discovery, Alias Resolution, and Execution Halt Self-Healing.
"""

import unittest
from unittest.mock import MagicMock, patch

from mt5_broker import (
    get_candidate_mt5_symbols,
    resolve_mt5_symbol,
    select_and_resolve_symbol,
    _RESOLVED_CACHE,
)
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from risk_manager import RiskManager
import signal_pipeline


class TestSymbolDiscoveryAndCandidates(unittest.TestCase):
    def setUp(self):
        _RESOLVED_CACHE.clear()

    def tearDown(self):
        _RESOLVED_CACHE.clear()

    def test_gold_candidate_symbols(self):
        candidates = get_candidate_mt5_symbols("XAUUSD")
        self.assertIn("XAUUSD", candidates)
        self.assertIn("GOLD", candidates)
        self.assertIn("XAUUSD.m", candidates)
        self.assertIn("GOLD.m", candidates)
        self.assertIn("GOLD.raw", candidates)
        self.assertIn("GOLD_i", candidates)
        self.assertIn("XAUUSD.raw", candidates)
        self.assertIn("XAUUSD+", candidates)
        self.assertIn("XAUUSD#", candidates)
        self.assertIn("XAUUSDb", candidates)
        self.assertIn("XAUUSDmicro", candidates)

    def test_btc_candidate_symbols(self):
        candidates = get_candidate_mt5_symbols("BTCUSDT")
        self.assertIn("BTCUSD", candidates)
        self.assertIn("BTCUSDT", candidates)
        self.assertIn("BITCOIN", candidates)
        self.assertIn("BTCUSD.m", candidates)
        self.assertIn("BTCUSD.raw", candidates)
        self.assertIn("BTCUSD+", candidates)

    def test_forex_candidate_symbols(self):
        candidates = get_candidate_mt5_symbols("EURUSD")
        self.assertIn("EURUSD", candidates)
        self.assertIn("EURUSD.m", candidates)
        self.assertIn("EURUSD.raw", candidates)
        self.assertIn("EURUSD.pro", candidates)

    def test_resolve_mt5_symbol_with_various_broker_names(self):
        # Broker uses GOLD.raw
        self.assertEqual(resolve_mt5_symbol("XAUUSD", ["GOLD.raw", "EURUSD"]), "GOLD.raw")
        # Broker uses GOLD
        self.assertEqual(resolve_mt5_symbol("XAUUSD", ["GOLD", "EURUSD"]), "GOLD")
        # Broker uses GOLD_i
        self.assertEqual(resolve_mt5_symbol("XAUUSD", ["GOLD_i", "EURUSD"]), "GOLD_i")
        # Broker uses XAUUSDb
        self.assertEqual(resolve_mt5_symbol("XAUUSD", ["XAUUSDb", "EURUSD"]), "XAUUSDb")
        # Broker uses XAUUSD+
        self.assertEqual(resolve_mt5_symbol("XAUUSD", ["XAUUSD+", "EURUSD"]), "XAUUSD+")
        # Broker uses BITCOIN
        self.assertEqual(resolve_mt5_symbol("BTCUSD", ["BITCOIN", "EURUSD"]), "BITCOIN")

    def test_select_and_resolve_symbol_with_mock_native_mt5(self):
        mock_info = MagicMock()
        mock_info.name = "GOLD.m"
        mock_info.visible = True

        mock_sym1 = MagicMock()
        mock_sym1.name = "EURUSD"
        mock_sym2 = MagicMock()
        mock_sym2.name = "GOLD.m"

        mock_native = MagicMock()
        # symbol_info returns None for XAUUSD, but mock_info for GOLD.m
        def mock_symbol_info(sym):
            if sym == "GOLD.m":
                return mock_info
            return None

        mock_native.symbol_info.side_effect = mock_symbol_info
        mock_native.symbols_get.return_value = (mock_sym1, mock_sym2)
        mock_native.symbol_select.return_value = True

        with patch("mt5_broker.native_mt5", mock_native):
            resolved_name, info = select_and_resolve_symbol("XAUUSD")
            self.assertEqual(resolved_name, "GOLD.m")
            self.assertIsNotNone(info)
            mock_native.symbol_select.assert_called_with("GOLD.m", True)

            # Subsequent call should hit cache without calling symbols_get again
            mock_native.symbols_get.reset_mock()
            cached_name, cached_info = select_and_resolve_symbol("XAUUSD")
            self.assertEqual(cached_name, "GOLD.m")
            mock_native.symbols_get.assert_not_called()


class TestExecutionHaltRecovery(unittest.TestCase):
    def setUp(self):
        _RESOLVED_CACHE.clear()
        from anti_spam import get_anti_spam_limiter
        get_anti_spam_limiter().reset()
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.rm)

    def tearDown(self):
        _RESOLVED_CACHE.clear()
        from anti_spam import get_anti_spam_limiter
        get_anti_spam_limiter().reset()

    def test_self_healing_clears_stale_halt_when_positions_empty(self):
        # Simulate engine entering halted state from a prior failed broker attempt
        self.engine.execution_halted = True
        self.assertEqual(len(self.engine.active_positions), 0)
        self.assertEqual(len(self.engine.pending_setups), 0)

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

        # register_setup should auto-heal stale halt and succeed
        order = self.engine.register_setup(sig, reason="Self-healing test")
        self.assertFalse(self.engine.execution_halted)
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_execution_halt_persists_when_positions_active(self):
        # Simulate engine with an active position
        sig_init = {
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
        order = self.engine.register_setup(sig_init)
        order.status = OrderStatus.OPEN
        self.engine.active_positions[order.id] = order
        self.engine.pending_setups.clear()
        self.engine.execution_halted = True

        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 65000.0,
            "stop": 64000.0,
            "tp1": 67000.0,
            "lots": 0.05,
            "size": 0.05,
        }

        # Should raise RuntimeError because positions are still open and need reconciliation
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(sig)
        self.assertIn("Execution halted after uncertain broker state", str(ctx.exception))
        self.assertTrue(self.engine.execution_halted)

    def test_try_paper_execute_returns_false_on_broker_failure(self):
        # Setup an engine where market candle triggers entry but broker execution fails
        events = []
        def on_event(event_name, order, message):
            events.append({"event": event_name, "order": order, "message": message})

        mock_broker = MagicMock()
        mock_broker.place_order.side_effect = Exception("Symbol 'XAUUSD' not found in MT5 Market Watch.")
        mock_broker.is_connected = True

        engine = ExecutionEngine(risk_manager=self.rm, broker=mock_broker, event_callback=on_event)

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

        success, msg = signal_pipeline.try_paper_execute(engine, sig, candle_price=2650.0)
        # Must return False so Telegram doesn't show green checkmark (✅) on execution failure
        self.assertFalse(success)
        self.assertIn("EXECUTION HALTED", msg)
        self.assertIn("not found in MT5 Market Watch", msg)

    def test_try_paper_execute_self_heals_stale_halt(self):
        # When engine is halted but has no open positions or pending setups,
        # try_paper_execute resets the halt and proceeds with setup registration
        mock_broker = MagicMock()
        mock_broker.is_connected = True

        engine = ExecutionEngine(risk_manager=self.rm, broker=mock_broker)
        engine.execution_halted = True

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

        # Market candle at 2660.0 does not trigger immediate entry (pending setup)
        success, msg = signal_pipeline.try_paper_execute(engine, sig, candle_price=2660.0)
        self.assertTrue(success)
        self.assertFalse(engine.execution_halted)


if __name__ == "__main__":
    unittest.main()
