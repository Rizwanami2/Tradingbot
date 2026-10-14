"""
Unit tests for Native Delta Exchange India REST Broker:
- DeltaBroker (Native Delta Exchange India REST & Derivatives)
- Symbol resolution (BTCUSD, BTCUSDT, XAUTUSD, ETHUSD)
- HMAC-SHA256 signature authentication
- Fail-closed safety on missing credentials
- Telemetry, wallet balance, and equity tracking
- Order placement with server-side SL/TP brackets
- Leverage manually set (1x to 200x)
- Dynamic auto-leverage up to max 200x
- Position queries and position closure
- Unified broker factory routing (create_broker_fail_closed)
- Telegram /delta and /leverage command handlers
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from delta_broker import (
    DELTA_INDIA_LIVE_URL,
    DELTA_TESTNET_URL,
    DeltaAccountInfo,
    DeltaBroker,
    resolve_delta_symbol,
)
from execution import (
    BrokerConfigurationError,
    ExecutionError,
    Order,
    Position,
    create_broker,
    create_broker_fail_closed,
)


class TestDeltaBroker(unittest.TestCase):

    # =========================================================================
    # 1. Symbol Resolution Tests
    # =========================================================================
    def test_delta_symbol_resolution(self):
        self.assertEqual(resolve_delta_symbol("BTC"), "BTCUSD")
        self.assertEqual(resolve_delta_symbol("BTCUSD"), "BTCUSD")
        self.assertEqual(resolve_delta_symbol("BTCUSDT"), "BTCUSD")
        self.assertEqual(resolve_delta_symbol("BTC/USDT"), "BTCUSD")
        self.assertEqual(resolve_delta_symbol("XAUUSD"), "XAUTUSD")
        self.assertEqual(resolve_delta_symbol("GOLD"), "XAUTUSD")
        self.assertEqual(resolve_delta_symbol("XAUT"), "XAUTUSD")
        self.assertEqual(resolve_delta_symbol("XAUTUSD"), "XAUTUSD")
        self.assertEqual(resolve_delta_symbol("PAXG"), "PAXGUSD")
        self.assertEqual(resolve_delta_symbol("ETH"), "ETHUSD")
        self.assertEqual(resolve_delta_symbol("ETHUSDT"), "ETHUSD")
        self.assertEqual(resolve_delta_symbol("ETHUSD"), "ETHUSD")
        self.assertEqual(resolve_delta_symbol("SOLUSDT"), "SOLUSD")

    # =========================================================================
    # 2. HMAC-SHA256 Signature Generation Tests
    # =========================================================================
    def test_signature_generation(self):
        broker = DeltaBroker.__new__(DeltaBroker)
        broker.api_key = "test_key"
        broker.api_secret = "test_secret"

        method = "POST"
        path = "/v2/products/27/orders/leverage"
        payload_str = '{"leverage":"50"}'
        timestamp = "1700000000"

        sig, ts = broker._generate_signature(
            method=method,
            path_with_query=path,
            payload_str=payload_str,
            timestamp=timestamp,
        )
        self.assertEqual(ts, "1700000000")
        # Precomputed HMAC-SHA256 for POST1700000000/v2/products/27/orders/leverage{"leverage":"50"} with test_secret
        import hashlib, hmac
        expected_raw = method + timestamp + path + payload_str
        expected_sig = hmac.new(b"test_secret", expected_raw.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(sig, expected_sig)

    # =========================================================================
    # 3. Fail-Closed Credential Validation
    # =========================================================================
    def test_fail_closed_missing_credentials(self):
        with self.assertRaises(BrokerConfigurationError):
            DeltaBroker(api_key="", api_secret="")

        with self.assertRaises(BrokerConfigurationError):
            DeltaBroker(api_key="valid_key", api_secret="")

        with self.assertRaises(BrokerConfigurationError):
            DeltaBroker(api_key="", api_secret="valid_secret")

    # =========================================================================
    # 4. Connection & Account Telemetry Tests
    # =========================================================================
    @patch("requests.request")
    def test_connection_and_telemetry(self, mock_req):
        def mock_dispatch(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "products" in url:
                resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [
                        {
                            "id": 27,
                            "symbol": "BTCUSD",
                            "contract_value": "0.001",
                            "default_leverage": "200.0",
                        }
                    ],
                }
            elif "wallet/balances" in url:
                resp.text = '{"success": true, "result": [{"asset_symbol": "USDT", "balance": "10000.00", "blocked_margin": "1500.00", "free_margin": "8500.00"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [
                        {
                            "asset_symbol": "USDT",
                            "balance": "10000.00",
                            "blocked_margin": "1500.00",
                            "free_margin": "8500.00",
                        }
                    ],
                }
            elif "positions/margined" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            return resp

        mock_req.side_effect = mock_dispatch

        broker = DeltaBroker(
            api_key="test_key_123",
            api_secret="test_secret_456",
            base_url=DELTA_INDIA_LIVE_URL,
            is_testnet=False,
            leverage=50.0,
        )

        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.mode, "live")
        self.assertEqual(broker.balance, 10000.0)
        self.assertEqual(broker.margin, 1500.0)
        self.assertEqual(broker.free_margin, 8500.0)
        self.assertEqual(broker.equity, 10000.0)

        info = broker.get_account_info()
        self.assertIsInstance(info, DeltaAccountInfo)
        self.assertEqual(info.environment, "live")
        self.assertEqual(info.max_leverage, 200.0)
        self.assertIn("Delta Exchange India", info.summary())

        summary = broker.get_account_summary()
        self.assertEqual(summary["broker"], "Delta Exchange India")
        self.assertEqual(summary["mode"], "live")
        self.assertEqual(summary["balance"], 10000.0)

    # =========================================================================
    # 5. Manual Leverage Setting (1x to 200x)
    # =========================================================================
    @patch("requests.request")
    def test_manual_leverage_setting(self, mock_req):
        posted_payload = {}

        def mock_dispatch(method, url, **kwargs):
            nonlocal posted_payload
            resp = MagicMock()
            resp.status_code = 200
            if "products/27/orders/leverage" in url and method == "POST":
                posted_payload = json.loads(kwargs.get("data", "{}"))
                resp.text = '{"success": true, "result": {"product_id": 27, "leverage": "100"}}'
                resp.json.return_value = {"success": True, "result": {"product_id": 27, "leverage": "100"}}
            elif "products" in url:
                resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}],
                }
            elif "wallet/balances" in url:
                resp.text = '{"success": true, "result": [{"asset_symbol": "USD", "balance": "5000.00", "blocked_margin": "0.0", "free_margin": "5000.0"}]}'
                resp.json.return_value = {"success": True, "result": [{"asset_symbol": "USD", "balance": "5000.00", "blocked_margin": "0.0", "free_margin": "5000.0"}]}
            elif "positions/margined" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            return resp

        import json
        mock_req.side_effect = mock_dispatch

        broker = DeltaBroker(api_key="k", api_secret="s")

        # Set valid leverage 100x
        ok, msg = broker.set_leverage("BTCUSD", 100)
        self.assertTrue(ok)
        self.assertEqual(posted_payload.get("leverage"), "100")
        self.assertEqual(broker.get_leverage("BTCUSD"), 100.0)

        # Set max 200x
        ok, msg = broker.set_leverage("BTCUSD", 200)
        self.assertTrue(ok)
        self.assertEqual(posted_payload.get("leverage"), "200")
        self.assertEqual(broker.get_leverage("BTCUSD"), 200.0)

        # Reject out of bounds > 200x
        ok, msg = broker.set_leverage("BTCUSD", 250)
        self.assertFalse(ok)
        self.assertIn("out of bounds", msg)

        # Reject < 1x
        ok, msg = broker.set_leverage("BTCUSD", 0.5)
        self.assertFalse(ok)

    # =========================================================================
    # 6. Dynamic Auto-Leverage Setting (Max 200x)
    # =========================================================================
    @patch("requests.request")
    def test_auto_leverage_calculation_and_cap(self, mock_req):
        posted_leverages = []

        def mock_dispatch(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "orders/leverage" in url and method == "POST":
                p = json.loads(kwargs.get("data", "{}"))
                posted_leverages.append(p.get("leverage"))
                resp.text = '{"success": true, "result": {}}'
                resp.json.return_value = {"success": True, "result": {}}
            elif "products" in url:
                resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}],
                }
            elif "wallet/balances" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            elif "positions/margined" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            return resp

        import json
        mock_req.side_effect = mock_dispatch

        broker = DeltaBroker(api_key="k", api_secret="s", auto_leverage=True, max_leverage=200.0)

        # Ultra-tight stop (0.2% distance) -> Should scale high, capped at 200x
        lev, msg = broker.auto_set_leverage("BTCUSD", entry=100000.0, stop_loss=99800.0)
        self.assertGreaterEqual(lev, 50.0)
        self.assertLessEqual(lev, 200.0)
        self.assertIn("Auto-set leverage", msg)

        # Normal swing stop (5% distance) -> Should scale conservatively
        lev_swing, msg_swing = broker.auto_set_leverage("BTCUSD", entry=100000.0, stop_loss=95000.0)
        self.assertLess(lev_swing, 30.0)
        self.assertGreaterEqual(lev_swing, 1.0)

    # =========================================================================
    # 7. Order Placement with Bracket Orders
    # =========================================================================
    @patch("requests.request")
    def test_order_placement_and_bracket(self, mock_req):
        order_payload = {}

        def mock_dispatch(method, url, **kwargs):
            nonlocal order_payload
            resp = MagicMock()
            resp.status_code = 200
            if "orders" in url and method == "POST":
                order_payload = json.loads(kwargs.get("data", "{}"))
                resp.text = '{"success": true, "result": {"id": "112233", "product_id": 27, "state": "closed", "average_fill_price": "65000.0", "size": 10}}'
                resp.json.return_value = {
                    "success": True,
                    "result": {
                        "id": "112233",
                        "product_id": 27,
                        "state": "closed",
                        "average_fill_price": "65000.0",
                        "size": 10,
                    },
                }
            elif "products" in url:
                resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}],
                }
            elif "wallet/balances" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            elif "positions/margined" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            return resp

        import json
        mock_req.side_effect = mock_dispatch

        broker = DeltaBroker(api_key="k", api_secret="s", auto_leverage=False)

        order = broker.place_order(
            symbol="BTCUSD",
            side="buy",
            qty=0.01,  # 0.01 BTC / 0.001 contract_value = 10 contracts
            order_type="market",
            price=65000.0,
            stop_loss=64500.0,
            take_profit=66000.0,
        )

        self.assertIsInstance(order, Order)
        self.assertEqual(order.id, "112233")
        self.assertEqual(order.status, "filled")
        self.assertEqual(order.filled_price, 65000.0)

        # Check submitted payload
        self.assertEqual(order_payload.get("product_id"), 27)
        self.assertEqual(order_payload.get("size"), 10)
        self.assertEqual(order_payload.get("side"), "buy")
        self.assertEqual(order_payload.get("order_type"), "market_order")
        self.assertEqual(order_payload.get("bracket_stop_loss_price"), "64500.00")
        self.assertEqual(order_payload.get("bracket_take_profit_price"), "66000.00")

    # =========================================================================
    # 8. Positions & Closing
    # =========================================================================
    @patch("requests.request")
    def test_get_positions_and_close(self, mock_req):
        close_payload = {}

        def mock_dispatch(method, url, **kwargs):
            nonlocal close_payload
            resp = MagicMock()
            resp.status_code = 200
            if "positions/margined" in url:
                resp.text = '{"success": true, "result": [{"product_id": 27, "product_symbol": "BTCUSD", "size": 15, "entry_price": "64000.0", "unrealized_pnl": "150.0", "mark_price": "65000.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [
                        {
                            "product_id": 27,
                            "product_symbol": "BTCUSD",
                            "size": 15,
                            "entry_price": "64000.0",
                            "unrealized_pnl": "150.0",
                            "mark_price": "65000.0",
                        }
                    ],
                }
            elif "orders" in url and method == "POST":
                close_payload = json.loads(kwargs.get("data", "{}"))
                resp.text = '{"success": true, "result": {"id": "9988", "state": "closed"}}'
                resp.json.return_value = {"success": True, "result": {"id": "9988", "state": "closed"}}
            elif "products" in url:
                resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
                resp.json.return_value = {
                    "success": True,
                    "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}],
                }
            elif "wallet/balances" in url:
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
            return resp

        import json
        mock_req.side_effect = mock_dispatch

        broker = DeltaBroker(api_key="k", api_secret="s")
        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "BTCUSD")
        self.assertEqual(positions[0].side, "buy")
        self.assertEqual(positions[0].qty, 15.0)
        self.assertEqual(positions[0].unrealized_pnl, 150.0)

        # Close position
        ok = broker.close_position("BTCUSD")
        self.assertTrue(ok)
        self.assertEqual(close_payload.get("product_id"), 27)
        self.assertEqual(close_payload.get("side"), "sell")  # Opposite side
        self.assertEqual(close_payload.get("size"), 15)
        self.assertTrue(close_payload.get("reduce_only"))

    # =========================================================================
    # 9. Unified Broker Factory Routing Tests
    # =========================================================================
    @patch("requests.request")
    def test_factory_delta_live_routing(self, mock_req):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"success": true, "result": []}'
        resp.json.return_value = {"success": True, "result": []}
        mock_req.return_value = resp

        b_delta = create_broker(
            broker_mode="delta_live",
            delta_api_key="mock_key",
            delta_api_secret="mock_secret",
            delta_leverage=100.0,
            delta_max_leverage=200.0,
        )
        self.assertIsInstance(b_delta, DeltaBroker)
        self.assertEqual(b_delta.mode, "live")
        self.assertEqual(b_delta.default_leverage, 100.0)
        self.assertEqual(b_delta.max_leverage, 200.0)

        # Fail-closed check
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="delta_live", delta_api_key="", delta_api_secret="")

    # =========================================================================
    # 10. Telegram /delta and /leverage Command Handlers
    # =========================================================================
    @patch("requests.request")
    def test_telegram_delta_and_leverage_commands(self, mock_req):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"success": true, "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}]}'
        resp.json.return_value = {
            "success": True,
            "result": [{"id": 27, "symbol": "BTCUSD", "contract_value": "0.001", "default_leverage": "200.0"}],
        }
        mock_req.return_value = resp

        import asyncio

        async def _test():
            import config
            from main import delta_command, leverage_command, engine, risk_mgr

            orig_mode = getattr(config, "BROKER_MODE", "paper")
            orig_broker = getattr(engine, "broker", None)
            orig_lev = risk_mgr.get_leverage()

            try:
                # Test /delta status without active broker
                engine.broker = None
                update = MagicMock()
                update.message.reply_text = AsyncMock()
                ctx = MagicMock()
                ctx.args = ["status"]

                await delta_command(update, ctx)
                update.message.reply_text.assert_called()
                self.assertIn("Delta Exchange India", update.message.reply_text.call_args[0][0])

                # Test /delta live connect
                ctx.args = ["live", "test_key", "test_secret"]
                await delta_command(update, ctx)
                self.assertIsInstance(engine.broker, DeltaBroker)
                self.assertIn("Connected Successfully", update.message.reply_text.call_args[0][0])

                # Test direct /delta <KEY> <SECRET> without "live" keyword
                ctx.args = ["test_key_direct", "test_secret_direct"]
                await delta_command(update, ctx)
                self.assertIsInstance(engine.broker, DeltaBroker)
                self.assertEqual(engine.broker.api_key, "test_key_direct")
                self.assertIn("Connected Successfully", update.message.reply_text.call_args[0][0])

                # Test /delta leverage 150
                ctx.args = ["leverage", "150"]
                await delta_command(update, ctx)
                self.assertEqual(risk_mgr.get_leverage(), 150.0)
                self.assertIn("150x", update.message.reply_text.call_args[0][0])

                # Test /delta auto_leverage on
                ctx.args = ["auto_leverage", "on"]
                await delta_command(update, ctx)
                self.assertTrue(engine.broker.auto_leverage)
                self.assertIn("ENABLED", update.message.reply_text.call_args[0][0])

                # Test /leverage 200
                ctx.args = ["200"]
                await leverage_command(update, ctx)
                self.assertEqual(risk_mgr.get_leverage(), 200.0)
                self.assertIn("200x", update.message.reply_text.call_args[0][0])

                # Test /leverage auto on
                ctx.args = ["auto", "on"]
                await leverage_command(update, ctx)
                self.assertIn("ACTIVATED", update.message.reply_text.call_args[0][0])
            finally:
                config.BROKER_MODE = orig_mode
                engine.broker = orig_broker
                risk_mgr.set_leverage(orig_lev)

        asyncio.run(_test())

    # =========================================================================
    # 11. Clock Skew Synchronization & Response Date Header
    # =========================================================================
    def test_clock_synchronization_from_date_header(self):
        broker = DeltaBroker.__new__(DeltaBroker)
        broker.api_key = "test_key"
        broker.api_secret = "test_secret"
        broker._time_offset = 0.0

        mock_resp = MagicMock()
        mock_resp.headers = {"Date": "Wed, 14 Oct 2026 19:47:38 GMT"}
        broker._sync_time_from_response(mock_resp)

        import time
        from email.utils import parsedate_to_datetime
        expected_dt = parsedate_to_datetime("Wed, 14 Oct 2026 19:47:38 GMT")
        expected_diff = expected_dt.timestamp() - time.time()
        self.assertAlmostEqual(broker._time_offset, expected_diff, delta=2.0)

    # =========================================================================
    # 12. Testnet Auto-Fallback & Key Sanitization
    # =========================================================================
    @patch("requests.request")
    def test_testnet_fallback_and_key_sanitization(self, mock_req):
        requested_urls = []

        def mock_dispatch(method, url, **kwargs):
            requested_urls.append(url)
            resp = MagicMock()
            if "cdn-ind.testnet.deltaex.org" in url and "wallet/balances" in url:
                # Primary India testnet returns 401 invalid_api_key
                resp.status_code = 401
                resp.text = '{"error": {"code": "invalid_api_key"}}'
                resp.json.return_value = {"error": {"code": "invalid_api_key"}}
                return resp
            elif "testnet-api.delta.exchange" in url and "wallet/balances" in url:
                # Fallback Global testnet succeeds
                resp.status_code = 200
                resp.text = '{"success": true, "result": [{"asset_symbol": "USDT", "balance": "5000.00"}]}'
                resp.json.return_value = {"success": True, "result": [{"asset_symbol": "USDT", "balance": "5000.00"}]}
                return resp
            elif "products" in url:
                resp.status_code = 200
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
                return resp
            elif "positions/margined" in url:
                resp.status_code = 200
                resp.text = '{"success": true, "result": []}'
                resp.json.return_value = {"success": True, "result": []}
                return resp
            resp.status_code = 200
            resp.text = "{}"
            resp.json.return_value = {}
            return resp

        mock_req.side_effect = mock_dispatch

        # Pass keys with surrounding spaces and quotes
        broker = DeltaBroker(
            api_key=' "my_quoted_key" ',
            api_secret=" 'my_secret_key' ",
            is_testnet=True,
        )

        self.assertEqual(broker.api_key, "my_quoted_key")
        self.assertEqual(broker.api_secret, "my_secret_key")
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.balance, 5000.0)
        self.assertEqual(broker.base_url, "https://testnet-api.delta.exchange")


if __name__ == "__main__":
    unittest.main()
