"""
Unit tests for Native Live Broker Environments on Linux:
- OANDABroker (Native Forex & Metals REST v20)
- AlpacaBroker (Native US Equities, ETFs & Crypto REST)
- CCXTBroker (Native Crypto Exchanges 100+)
- Unified broker factory routing with fail-closed safety
"""

import unittest
from unittest.mock import MagicMock, patch

from execution import (
    BrokerConfigurationError,
    CCXTAccountInfo,
    CCXTBroker,
    Order,
    Position,
    create_broker,
    create_broker_fail_closed,
)
from oanda_broker import (
    OANDAAccountInfo,
    OANDABroker,
    resolve_oanda_symbol,
)
from alpaca_broker import (
    AlpacaAccountInfo,
    AlpacaBroker,
    resolve_alpaca_symbol,
)


class TestNativeBrokers(unittest.TestCase):

    # =========================================================================
    # OANDA v20 REST Broker Tests
    # =========================================================================
    def test_oanda_symbol_resolution(self):
        self.assertEqual(resolve_oanda_symbol("XAUUSD"), "XAU_USD")
        self.assertEqual(resolve_oanda_symbol("GOLD"), "XAU_USD")
        self.assertEqual(resolve_oanda_symbol("XAU"), "XAU_USD")
        self.assertEqual(resolve_oanda_symbol("EURUSD"), "EUR_USD")
        self.assertEqual(resolve_oanda_symbol("GBPUSD"), "GBP_USD")
        self.assertEqual(resolve_oanda_symbol("BTCUSD"), "BTC_USD")
        self.assertEqual(resolve_oanda_symbol("BTCUSDT"), "BTC_USD")
        self.assertEqual(resolve_oanda_symbol("EUR_USD"), "EUR_USD")

    def test_oanda_fail_closed_missing_credentials(self):
        with self.assertRaises(BrokerConfigurationError):
            OANDABroker(account_id="", api_token="")

        with self.assertRaises(BrokerConfigurationError):
            OANDABroker(account_id="101-001-12345678-001", api_token="")

    @patch("requests.request")
    def test_oanda_connection_and_telemetry(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"account": {"id": "101-001-99887766-001", "balance": "100000.00", "NAV": "100250.00", "marginUsed": "500.00", "marginAvailable": "99750.00", "marginRate": "0.02", "unrealizedPL": "250.00", "currency": "USD", "openPositionCount": 1}}'
        mock_resp.json.return_value = {
            "account": {
                "id": "101-001-99887766-001",
                "balance": "100000.00",
                "NAV": "100250.00",
                "marginUsed": "500.00",
                "marginAvailable": "99750.00",
                "marginRate": "0.02",
                "unrealizedPL": "250.00",
                "currency": "USD",
                "openPositionCount": 1,
            }
        }
        mock_req.return_value = mock_resp

        broker = OANDABroker(
            account_id="101-001-99887766-001",
            api_token="mock_token_abc123",
            environment="practice",
        )
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.balance, 100000.0)
        self.assertEqual(broker.equity, 100250.0)
        self.assertEqual(broker.margin, 500.0)
        self.assertEqual(broker.free_margin, 99750.0)

        info = broker.get_account_info()
        self.assertIsInstance(info, OANDAAccountInfo)
        self.assertEqual(info.account_id, "101-001-99887766-001")
        self.assertEqual(info.environment, "practice")
        self.assertAlmostEqual(info.margin_level, (100250.0 / 500.0) * 100.0)
        self.assertIn("101-001-99887766-001", info.summary())

        summary = broker.get_account_summary()
        self.assertEqual(summary["broker"], "OANDA")
        self.assertEqual(summary["balance"], 100000.0)

    @patch("requests.request")
    def test_oanda_order_lifecycle(self, mock_req):
        def mock_dispatch(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "orders" in url and method == "POST":
                resp.text = '{"orderFillTransaction": {"id": "501", "units": "10", "price": "2350.50", "accountBalance": "100000.00"}}'
                resp.json.return_value = {
                    "orderFillTransaction": {
                        "id": "501",
                        "units": "10",
                        "price": "2350.50",
                        "accountBalance": "100000.00",
                    }
                }
            elif "openPositions" in url:
                resp.text = '{"positions": [{"instrument": "XAU_USD", "long": {"units": "10", "averagePrice": "2350.50", "unrealizedPL": "35.00"}}]}'
                resp.json.return_value = {
                    "positions": [
                        {
                            "instrument": "XAU_USD",
                            "long": {
                                "units": "10",
                                "averagePrice": "2350.50",
                                "unrealizedPL": "35.00",
                            },
                        }
                    ]
                }
            elif "close" in url:
                resp.text = '{"longOrderFillTransaction": {"id": "502", "units": "10", "price": "2354.00"}}'
                resp.json.return_value = {
                    "longOrderFillTransaction": {"id": "502", "units": "10", "price": "2354.00"}
                }
            else:
                resp.text = '{"account": {"balance": "100000.00", "NAV": "100035.00", "marginUsed": "470.00", "marginAvailable": "99565.00"}}'
                resp.json.return_value = {
                    "account": {
                        "balance": "100000.00",
                        "NAV": "100035.00",
                        "marginUsed": "470.00",
                        "marginAvailable": "99565.00",
                    }
                }
            return resp

        mock_req.side_effect = mock_dispatch

        broker = OANDABroker(account_id="101-001-99887766-001", api_token="test_token")

        # Place Buy order for Gold
        order = broker.place_order(
            symbol="XAUUSD",
            side="buy",
            qty=10,
            price=2350.50,
            stop_loss=2340.0,
            take_profit=2370.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.symbol, "XAU_USD")
        self.assertEqual(order.filled_price, 2350.50)

        # Get positions
        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "XAU_USD")
        self.assertEqual(positions[0].side, "buy")
        self.assertEqual(positions[0].qty, 10.0)

        # Close position
        closed = broker.close_position("XAUUSD")
        self.assertTrue(closed)

    # =========================================================================
    # Alpaca REST Broker Tests
    # =========================================================================
    def test_alpaca_symbol_resolution(self):
        self.assertEqual(resolve_alpaca_symbol("AAPL"), "AAPL")
        self.assertEqual(resolve_alpaca_symbol("BTCUSDT"), "BTC/USD")
        self.assertEqual(resolve_alpaca_symbol("ETHUSD"), "ETH/USD")
        self.assertEqual(resolve_alpaca_symbol("BTC/USD"), "BTC/USD")

    def test_alpaca_fail_closed_missing_credentials(self):
        with self.assertRaises(BrokerConfigurationError):
            AlpacaBroker(api_key="", api_secret="")

    @patch("requests.request")
    def test_alpaca_connection_and_orders(self, mock_req):
        def mock_dispatch(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "account" in url:
                resp.text = '{"account_number": "PA3NABCDEF", "cash": "50000.00", "equity": "52500.00", "buying_power": "200000.00", "initial_margin": "1200.00", "maintenance_margin": "800.00", "currency": "USD"}'
                resp.json.return_value = {
                    "account_number": "PA3NABCDEF",
                    "cash": "50000.00",
                    "equity": "52500.00",
                    "buying_power": "200000.00",
                    "initial_margin": "1200.00",
                    "maintenance_margin": "800.00",
                    "currency": "USD",
                }
            elif "orders" in url and method == "POST":
                resp.text = '{"id": "alpaca_ord_1", "status": "filled", "filled_avg_price": "180.50", "filled_qty": "10"}'
                resp.json.return_value = {
                    "id": "alpaca_ord_1",
                    "status": "filled",
                    "filled_avg_price": "180.50",
                    "filled_qty": "10",
                }
            elif "positions" in url and method == "GET":
                resp.text = '[{"symbol": "AAPL", "side": "long", "qty": "10", "avg_entry_price": "180.50", "unrealized_pl": "45.00"}]'
                resp.json.return_value = [
                    {
                        "symbol": "AAPL",
                        "side": "long",
                        "qty": "10",
                        "avg_entry_price": "180.50",
                        "unrealized_pl": "45.00",
                    }
                ]
            elif "positions" in url and method == "DELETE":
                resp.text = '{"status": "closed"}'
                resp.json.return_value = {"status": "closed"}
            return resp

        mock_req.side_effect = mock_dispatch

        broker = AlpacaBroker(api_key="mock_key", api_secret="mock_sec", is_paper=True)
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.balance, 50000.0)
        self.assertEqual(broker.equity, 52500.0)

        info = broker.get_account_info()
        self.assertIsInstance(info, AlpacaAccountInfo)
        self.assertEqual(info.account_number, "PA3NABCDEF")
        self.assertEqual(info.environment, "paper")

        # Place bracket order
        order = broker.place_order(
            symbol="AAPL",
            side="buy",
            qty=10,
            price=180.50,
            stop_loss=175.0,
            take_profit=190.0,
        )
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.symbol, "AAPL")

        # Check positions
        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "AAPL")

        # Close position
        closed = broker.close_position("AAPL")
        self.assertTrue(closed)

    # =========================================================================
    # CCXT Broker Enhanced Tests
    # =========================================================================
    def test_ccxt_broker_properties_and_telemetry(self):
        with patch("ccxt.binance") as mock_binance_cls:
            mock_ex = MagicMock()
            mock_ex.fetch_balance.return_value = {
                "total": {"USDT": 15000.0},
                "free": {"USDT": 12000.0},
                "used": {"USDT": 3000.0},
            }
            mock_ex.fetch_positions.return_value = [
                {
                    "symbol": "BTC/USDT",
                    "contracts": 0.5,
                    "side": "long",
                    "entryPrice": 60000.0,
                    "unrealizedPnl": 250.0,
                }
            ]
            mock_binance_cls.return_value = mock_ex

            broker = CCXTBroker(
                exchange_id="binance",
                api_key="mock_key",
                api_secret="mock_secret",
                is_testnet=True,
            )
            self.assertTrue(broker.is_connected)
            self.assertEqual(broker.mode, "testnet")

            info = broker.get_account_info()
            self.assertIsInstance(info, CCXTAccountInfo)
            self.assertEqual(info.exchange_id, "binance")
            self.assertEqual(info.balance, 15000.0)
            self.assertEqual(info.margin, 3000.0)
            self.assertEqual(info.free_margin, 12000.0)
            self.assertEqual(info.equity, 15250.0)
            self.assertEqual(info.open_position_count, 1)

            summary = broker.get_account_summary()
            self.assertEqual(summary["exchange"], "binance")
            self.assertEqual(summary["mode"], "testnet")

    # =========================================================================
    # Unified Factory Routing Tests
    # =========================================================================
    def test_unified_broker_factory_routing(self):
        # 1. Paper broker
        b_paper = create_broker(broker_mode="paper", initial_balance=5000.0)
        self.assertEqual(b_paper.balance, 5000.0)

        # 2. OANDA practice broker
        with patch("requests.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"account": {"balance": "100000.00", "NAV": "100000.00"}}'
            mock_resp.json.return_value = {"account": {"balance": "100000.00", "NAV": "100000.00"}}
            mock_req.return_value = mock_resp

            b_oanda = create_broker(
                broker_mode="oanda",
                oanda_account_id="101-001-123",
                oanda_api_token="token_abc",
            )
            self.assertIsInstance(b_oanda, OANDABroker)
            self.assertEqual(b_oanda.environment, "practice")

        # 3. Alpaca paper broker
        with patch("requests.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"account_number": "PA123", "cash": "25000.00", "equity": "25000.00"}'
            mock_resp.json.return_value = {"account_number": "PA123", "cash": "25000.00", "equity": "25000.00"}
            mock_req.return_value = mock_resp

            b_alpaca = create_broker(
                broker_mode="alpaca",
                alpaca_api_key="key_abc",
                alpaca_api_secret="sec_xyz",
            )
            self.assertIsInstance(b_alpaca, AlpacaBroker)
            self.assertTrue(b_alpaca.is_paper)

        # 4. Fail-closed safety on missing credentials
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="oanda", oanda_account_id="", oanda_api_token="")

        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="alpaca", alpaca_api_key="", alpaca_api_secret="")


if __name__ == "__main__":
    unittest.main()
