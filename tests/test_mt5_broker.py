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
        self.assertEqual(resolve_mt5_symbol("XAUT"), "XAUUSD")
        self.assertEqual(resolve_mt5_symbol("PAXG"), "XAUUSD")
        self.assertEqual(resolve_mt5_symbol("BTCUSDT"), "BTCUSD")
        self.assertEqual(resolve_mt5_symbol("BTC"), "BTCUSD")
        self.assertEqual(resolve_mt5_symbol("EURUSD"), "EURUSD")

    def test_resolve_mt5_symbol_with_broker_suffixes(self):
        broker_symbols = ["XAUUSD.m", "BTCUSD.raw", "EURUSD.pro"]
        self.assertEqual(resolve_mt5_symbol("GOLD", broker_symbols), "XAUUSD.m")
        self.assertEqual(resolve_mt5_symbol("BTCUSDT", broker_symbols), "BTCUSD.raw")
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

    def test_mt5_live_fail_closed_without_gateway_or_package(self):
        # When MetaTrader5 is unavailable and no gateway is configured, fail closed
        with patch("mt5_broker.native_mt5", None):
            with self.assertRaises(BrokerConfigurationError) as ctx:
                MT5Broker(
                    mode="live",
                    login=10054330,
                    password="secret_password",
                    server="Mtrading-live",
                    gateway_url="",
                )
            self.assertIn("Native MetaTrader5 library is only available on Windows", str(ctx.exception))

    def test_mt5_gateway_integration(self):
        # Test connecting and querying account telemetry via REST gateway
        def mock_request(method, url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "/connect" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "login": 10054330,
                    "server": "Mtrading-live",
                    "balance": 12500.0,
                    "equity": 12650.0,
                    "currency": "USD",
                    "leverage": 100,
                }
            elif "/account" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "login": 10054330,
                    "server": "Mtrading-live",
                    "balance": 12500.0,
                    "equity": 12650.0,
                    "margin": 250.0,
                    "margin_free": 12400.0,
                    "margin_level": 5060.0,
                    "currency": "USD",
                    "company": "MTrading Global",
                    "leverage": 100,
                }
            return mock_resp

        with patch("requests.request", side_effect=mock_request):
            broker = MT5Broker(
                mode="live",
                login=10054330,
                password="secret_password",
                server="Mtrading-live",
                gateway_url="http://192.168.1.100:5000",
            )
            self.assertTrue(broker.is_connected)
            self.assertEqual(broker.balance, 12500.0)
            self.assertEqual(broker.equity, 12650.0)

            info = broker.get_account_info()
            self.assertEqual(info.login, 10054330)
            self.assertEqual(info.server, "Mtrading-live")
            self.assertEqual(info.margin, 250.0)
            self.assertEqual(info.margin_level, 5060.0)
            self.assertEqual(info.currency, "USD")

    def test_mt5_gateway_orders_and_positions(self):
        # Test order placement, positions querying, modification, and closing via REST gateway
        stored_positions = [
            {
                "ticket": 554433,
                "symbol": "BTCUSD",
                "side": "buy",
                "qty": 0.10,
                "entry_price": 60000.0,
                "sl": 59000.0,
                "tp": 63000.0,
                "unrealized_pnl": 150.0,
                "profit": 150.0,
            }
        ]

        def mock_request(method, url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "/connect" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "balance": 10000.0,
                    "equity": 10150.0,
                    "currency": "USD",
                }
            elif "/order" in url:
                payload = kwargs.get("json", {})
                mock_resp.json.return_value = {
                    "ok": True,
                    "ticket": 554433,
                    "symbol": payload.get("symbol", "BTCUSD"),
                    "side": payload.get("side", "buy"),
                    "qty": payload.get("qty", 0.10),
                    "price": 60000.0,
                    "sl": payload.get("sl", 0.0),
                    "tp": payload.get("tp", 0.0),
                }
            elif "/positions" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "positions": stored_positions,
                }
            elif "/modify" in url:
                payload = kwargs.get("json", {})
                mock_resp.json.return_value = {
                    "ok": True,
                    "ticket": payload.get("ticket"),
                    "sl": payload.get("sl"),
                    "tp": payload.get("tp"),
                }
            elif "/close" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "ticket": kwargs.get("json", {}).get("ticket"),
                    "price": 61500.0,
                    "profit": 150.0,
                }
            return mock_resp

        with patch("requests.request", side_effect=mock_request):
            broker = MT5Broker(
                mode="live",
                login=10054330,
                password="secret_password",
                server="Mtrading-live",
                gateway_url="http://192.168.1.100:5000",
            )
            # Place order
            order = broker.place_order(
                symbol="BTCUSDT",
                side="buy",
                qty=0.10,
                price=60000.0,
                stop_loss=59000.0,
                take_profit=63000.0,
            )
            self.assertEqual(order.status, "filled")
            self.assertEqual(order.filled_price, 60000.0)

            # Query positions
            positions = broker.get_positions()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0].symbol, "BTCUSD")
            self.assertEqual(positions[0].unrealized_pnl, 150.0)

            # Modify position
            mod_ok = broker.modify_position("BTCUSD", stop_loss=59500.0, take_profit=64000.0)
            self.assertTrue(mod_ok)

            # Close position
            close_ok = broker.close_position("BTCUSD", qty=0.10, price=61500.0)
            self.assertTrue(close_ok)


if __name__ == "__main__":
    unittest.main()
