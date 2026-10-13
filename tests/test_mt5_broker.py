"""
Unit tests for MetaTrader 5 (MT5) Broker Integration — Paper Simulation & Live Mode.
"""

import unittest
from unittest.mock import MagicMock, patch

from execution import (
    BrokerConfigurationError,
    create_broker,
    create_broker_fail_closed,
)
from mt5_broker import (
    MT5AccountInfo,
    MT5Broker,
    resolve_mt5_symbol,
)
from risk_manager import RiskManager
from execution_engine import ExecutionEngine, OrderStatus


class TestMT5Broker(unittest.TestCase):
    def test_resolve_mt5_symbol_aliases(self):
        # Basic alias resolution
        self.assertEqual(resolve_mt5_symbol("GOLD"), "XAUUSD")
        self.assertEqual(resolve_mt5_symbol("XAU"), "XAUUSD")
        self.assertEqual(resolve_mt5_symbol("BTCUSDT"), "BTCUSD")
        self.assertEqual(resolve_mt5_symbol("ETHUSDT"), "ETHUSD")
        self.assertEqual(resolve_mt5_symbol("SOLUSDT"), "SOLUSD")
        self.assertEqual(resolve_mt5_symbol("BNBUSDT"), "BNBUSD")
        self.assertEqual(resolve_mt5_symbol("EURUSD"), "EURUSD")

    def test_resolve_mt5_symbol_with_broker_suffixes(self):
        broker_symbols = ["XAUUSD.m", "BTCUSD.raw", "ETHUSD_i", "EURUSD.pro"]
        self.assertEqual(resolve_mt5_symbol("GOLD", broker_symbols), "XAUUSD.m")
        self.assertEqual(resolve_mt5_symbol("BTCUSDT", broker_symbols), "BTCUSD.raw")
        self.assertEqual(resolve_mt5_symbol("ETH", broker_symbols), "ETHUSD_i")
        self.assertEqual(resolve_mt5_symbol("EURUSD", broker_symbols), "EURUSD.pro")

    def test_mt5_paper_order_lifecycle(self):
        broker = MT5Broker(mode="paper", initial_balance=5000.0)
        self.assertEqual(broker.mode, "paper")
        self.assertEqual(broker.balance, 5000.0)
        self.assertEqual(broker.equity, 5000.0)

        # Place BUY order
        order = broker.place_order(
            symbol="XAUUSD",
            side="buy",
            qty=0.10,
            price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.filled_qty, 0.10)
        self.assertEqual(order.filled_price, 2000.0)

        # Verify open position
        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        self.assertEqual(pos.symbol, "XAUUSD")
        self.assertEqual(pos.side, "buy")
        self.assertEqual(pos.qty, 0.10)
        self.assertEqual(pos.entry_price, 2000.0)
        self.assertEqual(pos.stop_loss, 1990.0)
        self.assertEqual(pos.take_profit, 2020.0)

        # Mark price update (price increases to 2010.0 -> +$1.00 on 0.10 oz)
        broker.update_mark_price("XAUUSD", 2010.0)
        self.assertAlmostEqual(pos.unrealized_pnl, 1.0, places=2)
        # Equity = initial (5000.0) - open fee (0.08) + unrealized PnL (1.00) = 5000.92
        self.assertAlmostEqual(broker.equity, 5000.92, places=2)

        # Modify position SL / TP
        mod_ok = broker.modify_position("XAUUSD", stop_loss=1995.0, take_profit=2030.0)
        self.assertTrue(mod_ok)
        self.assertEqual(pos.stop_loss, 1995.0)
        self.assertEqual(pos.take_profit, 2030.0)

        # Close half position @ 2010.0
        close_ok = broker.close_position("XAUUSD", qty=0.05, price=2010.0)
        self.assertTrue(close_ok)
        self.assertAlmostEqual(pos.qty, 0.05, places=4)

        # Close remaining position @ 2020.0
        close_all_ok = broker.close_position("XAUUSD", price=2020.0)
        self.assertTrue(close_all_ok)
        self.assertEqual(len(broker.get_positions()), 0)

    def test_mt5_account_telemetry(self):
        broker = MT5Broker(
            mode="paper",
            initial_balance=10000.0,
            login=123456,
            server="Test-Demo",
        )
        info = broker.get_account_info()
        self.assertIsInstance(info, MT5AccountInfo)
        self.assertEqual(info.login, 123456)
        self.assertEqual(info.server, "Test-Demo")
        self.assertEqual(info.trade_mode, "paper")
        self.assertEqual(info.balance, 10000.0)
        self.assertEqual(info.equity, 10000.0)

        summary_text = info.summary()
        self.assertIn("Test-Demo", summary_text)
        self.assertIn("10,000.00", summary_text)

        summary_dict = broker.get_account_summary()
        self.assertEqual(summary_dict["broker"], "MT5")
        self.assertEqual(summary_dict["mode"], "paper")
        self.assertEqual(summary_dict["balance"], 10000.0)

    def test_mt5_live_fail_closed_missing_credentials(self):
        # Missing credentials in live mode raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="live", login=0, password="", server="")

        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="live", login=12345, password="", server="MetaQuotes-Demo")

    def test_create_broker_mt5_routing(self):
        # 1. 'mt5' mode creates MT5Broker (defaults to paper simulation)
        b1 = create_broker(broker_mode="mt5", initial_balance=2500.0)
        self.assertIsInstance(b1, MT5Broker)
        self.assertEqual(b1.mode, "paper")
        self.assertEqual(b1.balance, 2500.0)

        # 2. 'mt5_paper' mode creates MT5Broker paper
        b2 = create_broker(broker_mode="mt5_paper", initial_balance=1500.0)
        self.assertIsInstance(b2, MT5Broker)
        self.assertEqual(b2.mode, "paper")

        # 3. 'mt5_live' mode without credentials fails closed
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="mt5_live")

    def test_execution_engine_with_mt5_broker(self):
        rm = RiskManager(equity=10000.0, symbol="BTCUSDT")
        b = MT5Broker(mode="paper", initial_balance=10000.0)
        eng = ExecutionEngine(risk_manager=rm, broker=b)

        setup_sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 60000.0,
            "stop": 59000.0,
            "tp1": 62000.0,
            "tp2": 64000.0,
            "lots": 0.05,
            "size": 0.05,
            "risk_amount": 50.0,
            "rr": 2.0,
        }
        order = eng.register_setup(setup_sig)
        self.assertEqual(order.status, OrderStatus.PENDING)

        # Feed candle triggering entry
        candle = {"high": 60100.0, "low": 59900.0, "close": 60050.0, "open": 60000.0}
        events = eng.update_market_candle("BTCUSDT", candle)
        self.assertIn("BTCUSDT", eng.active_positions)
        self.assertEqual(eng.active_positions["BTCUSDT"].status, OrderStatus.OPEN)

        # Verify broker also has the position
        pos = b.get_positions()
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0].symbol, "BTCUSD")

        # Close position
        closed = eng.close_position("BTCUSDT", reason="Manual test")
        self.assertIsNotNone(closed)
        self.assertEqual(len(b.get_positions()), 0)


if __name__ == "__main__":
    unittest.main()
