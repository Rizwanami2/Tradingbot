"""
Unit tests for MetaTrader 5 (MT5) Broker Integration — Paper Simulation & Live Mode.
"""

import unittest
from unittest.mock import MagicMock, patch

from execution import (
    BrokerConfigurationError,
    create_broker,
)
from mt5_broker import (
    MT5AccountInfo,
    MT5Broker,
    MockMT5Broker,
    resolve_mt5_symbol,
    _RESOLVED_CACHE,
)
from risk_manager import RiskManager
from execution_engine import ExecutionEngine, OrderStatus


class TestMT5Broker(unittest.TestCase):
    def setUp(self):
        _RESOLVED_CACHE.clear()

    def tearDown(self):
        _RESOLVED_CACHE.clear()

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

    def test_mt5_paper_mode_removed(self):
        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="paper", initial_balance=5000.0)
        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="demo")
        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="sim")

    def test_mt5_account_telemetry(self):
        info = MT5AccountInfo(
            login=123456,
            server="Test-Live",
            trade_mode="live",
            connected=True,
            balance=10000.0,
            equity=10000.0,
            margin=50.0,
            free_margin=9950.0,
            margin_level_pct=20000.0,
            leverage=100.0,
            currency="USD",
        )
        self.assertEqual(info.login, 123456)
        self.assertEqual(info.server, "Test-Live")
        self.assertEqual(info.trade_mode, "live")
        self.assertEqual(info.balance, 10000.0)
        self.assertEqual(info.equity, 10000.0)

        summary_text = info.summary()
        self.assertIn("Test-Live", summary_text)
        self.assertIn("10,000.00", summary_text)

    def test_mt5_live_fail_closed_missing_credentials(self):
        # Missing credentials in live mode raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="live", login=0, password="", server="")

        with self.assertRaises(BrokerConfigurationError):
            MT5Broker(mode="live", login=12345, password="", server="MetaQuotes-Demo")

    def test_create_broker_mt5_routing(self):
        # 1. 'mt5' mode requires credentials, fails closed without them
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="mt5")

        # 2. 'mt5_paper' mode raises BrokerConfigurationError (paper removed)
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="mt5_paper")

        # 3. 'mt5_live' mode without credentials fails closed
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="mt5_live")

    def test_execution_engine_with_mt5_broker(self):
        rm = RiskManager(equity=10000.0, symbol="BTCUSDT")
        b = MagicMock(spec=MT5Broker)
        b.mode = "live"
        b.is_connected = True
        b.balance = 10000.0
        b.equity = 10000.0
        mock_order = MagicMock()
        mock_order.status = "filled"
        mock_order.filled_qty = 0.05
        mock_order.filled_price = 60000.0
        b.place_order.return_value = mock_order
        mock_pos = MagicMock()
        mock_pos.symbol = "BTCUSD"
        b.get_positions.return_value = [mock_pos]
        b.close_position.return_value = True

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
        self.assertIsNotNone(events)
        self.assertIn("BTCUSDT", eng.active_positions)
        self.assertEqual(eng.active_positions["BTCUSDT"].status, OrderStatus.OPEN)

        # Verify broker also has the position
        pos = b.get_positions()
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0].symbol, "BTCUSD")

        # Close position
        b.get_positions.return_value = []
        closed = eng.close_position("BTCUSDT", reason="Manual test")
        self.assertIsNotNone(closed)
        self.assertEqual(len(b.get_positions()), 0)

    def test_mt5_cloud_live_mode_on_linux(self):
        # On Linux/Cloud without Windows native MT5 or gateway URL, broker operates in Cloud MT5 Live mode
        with patch("mt5_broker.native_mt5", None):
            broker = MT5Broker(
                mode="live",
                login=10054330,
                password="secret_password",
                server="Mtrading-live",
                gateway_url="",
            )
            self.assertTrue(broker.is_connected)
            self.assertEqual(broker.login, 10054330)
            self.assertEqual(broker.server, "Mtrading-live")

            # Place order
            order = broker.place_order(
                symbol="BTCUSD",
                side="buy",
                qty=0.05,
                price=65000.0,
                stop_loss=64000.0,
                take_profit=68000.0,
            )
            self.assertEqual(order.status, "filled")
            self.assertTrue(order.id.startswith("MT5-"))
            self.assertEqual(order.filled_price, 65000.0)

            # Positions
            positions = broker.get_positions()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0].symbol, "BTCUSD")

            # Modify
            self.assertTrue(broker.modify_position("BTCUSD", stop_loss=64500.0, take_profit=69000.0))
            self.assertEqual(broker.positions["BTCUSD"].stop_loss, 64500.0)

            # Close
            self.assertTrue(broker.close_position("BTCUSD", qty=0.05, price=66000.0))
            self.assertEqual(len(broker.get_positions()), 0)

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

    def test_mt5_account_info_warnings(self):
        info_blocked = MT5AccountInfo(
            login=555123,
            server="Demo-Server",
            trade_mode="live",
            connected=True,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            free_margin=10000.0,
            margin_level_pct=999.0,
            leverage=50.0,
            trade_allowed=False,
            trade_expert=False,
            terminal_trade_allowed=False,
        )
        summary = info_blocked.summary()
        self.assertIn("Algo Trading is DISABLED", summary)
        self.assertIn("Account Trading is DISABLED", summary)
        self.assertIn("Automated/EA Trading is DISABLED", summary)

    def test_check_trading_readiness_mock(self):
        broker = MockMT5Broker()
        res = broker.check_trading_readiness(["XAUUSD", "BTCUSD"])
        self.assertTrue(res["ready"])
        self.assertTrue(res["terminal_algo_trading"])
        self.assertTrue(res["account_trading_allowed"])
        self.assertTrue(res["broker_expert_allowed"])
        self.assertIn("XAUUSD", res["symbols"])
        self.assertIn("BTCUSD", res["symbols"])
        self.assertEqual(len(res["blockers"]), 0)

    def test_order_send_preflight_checks(self):
        import mt5_broker
        from execution import ExecutionError
        mock_mt5 = MagicMock()
        mock_term = MagicMock()
        mock_term.trade_allowed = False
        mock_mt5.terminal_info.return_value = mock_term
        mock_mt5.initialize.return_value = True
        mock_mt5.login.return_value = True

        with patch.object(mt5_broker, "native_mt5", mock_mt5):
            broker = MT5Broker(mode="live", login=123456, password="pw", server="srv")
            with self.assertRaises(ExecutionError) as ctx:
                broker.place_order("BTCUSD", "buy", 0.01)
            self.assertIn("Algo Trading is DISABLED", str(ctx.exception))

            mock_term.trade_allowed = True
            mock_acc = MagicMock()
            mock_acc.trade_allowed = False
            mock_acc.trade_expert = True
            mock_mt5.account_info.return_value = mock_acc

            with self.assertRaises(ExecutionError) as ctx:
                broker.place_order("BTCUSD", "buy", 0.01)
            self.assertIn("Trading is DISABLED for MT5 account", str(ctx.exception))

    def test_dynamic_filling_mode_fallback(self):
        import mt5_broker
        mock_mt5 = MagicMock()
        mock_term = MagicMock()
        mock_term.trade_allowed = True
        mock_acc = MagicMock()
        mock_acc.trade_allowed = True
        mock_acc.trade_expert = True
        mock_acc.balance = 10000.0
        mock_acc.leverage = 50.0
        mock_acc.currency = "USD"
        mock_acc.company = "Broker"
        mock_mt5.terminal_info.return_value = mock_term
        mock_mt5.account_info.return_value = mock_acc
        mock_mt5.initialize.return_value = True
        mock_mt5.login.return_value = True
        mock_mt5.symbol_select.return_value = True
        mock_sinfo = MagicMock()
        mock_sinfo.trade_mode = 4
        mock_sinfo.filling_mode = 3 # Both FOK and IOC
        mock_sinfo.volume_min = 0.01
        mock_sinfo.volume_max = 100.0
        mock_sinfo.volume_step = 0.01
        mock_mt5.symbol_info.return_value = mock_sinfo
        mock_tick = MagicMock()
        mock_tick.ask = 65000.0
        mock_tick.bid = 64990.0
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.ORDER_TIME_GTC = 0
        mock_mt5.ORDER_FILLING_IOC = 1
        mock_mt5.ORDER_FILLING_FOK = 0
        mock_mt5.ORDER_FILLING_RETURN = 2
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.TRADE_RETCODE_UNSUPPORTED_FILLING_MODE = 10030

        # First attempt with IOC returns 10030 (Unsupported filling mode)
        res_fail = MagicMock()
        res_fail.retcode = 10030
        res_fail.comment = "Unsupported filling mode"
        # Second attempt with FOK succeeds
        res_ok = MagicMock()
        res_ok.retcode = 10009
        res_ok.order = 998877
        res_ok.price = 65000.0
        res_ok.volume = 0.05
        mock_mt5.order_send.side_effect = [res_fail, res_ok]

        with patch.object(mt5_broker, "native_mt5", mock_mt5):
            broker = MT5Broker(mode="live", login=123456, password="pw", server="srv")
            order = broker.place_order("BTCUSD", "buy", 0.05)
            self.assertEqual(order.id, "998877")
            self.assertEqual(order.status, "filled")
            self.assertEqual(mock_mt5.order_send.call_count, 2)

    def test_mt5_ea_bridge_connection(self):
        broker = MT5Broker(mode="live", backend="ea_bridge")
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.effective_backend, "ea_bridge")
        self.assertEqual(broker.backend, "ea_bridge")

    def test_mt5_ea_bridge_order_placement(self):
        from bridge import ea_client
        mock_buy = MagicMock(return_value={"ok": True, "ticket": 554433, "price": 2650.50, "volume": 0.05, "retcode": 10009})
        with patch.object(ea_client, "buy", mock_buy):
            broker = MT5Broker(mode="live", backend="ea_bridge")
            order = broker.place_order("XAUUSD", "buy", 0.05, stop_loss=2640.0, take_profit=2670.0)
            mock_buy.assert_called_once_with("XAUUSD", 0.05, sl=2640.0, tp=2670.0, magic=112233)
            self.assertEqual(order.id, "554433")
            self.assertEqual(order.status, "filled")
            self.assertEqual(order.filled_price, 2650.50)
            self.assertEqual(len(broker.get_positions()), 1)

    def test_mt5_ea_bridge_close_position(self):
        from bridge import ea_client
        mock_buy = MagicMock(return_value={"ok": True, "ticket": 554433, "price": 2650.50, "volume": 0.05, "retcode": 10009})
        mock_close = MagicMock(return_value={"ok": True, "closed": 1})
        mock_close_all = MagicMock(return_value={"ok": True, "closed": 1})
        with patch.object(ea_client, "buy", mock_buy), \
             patch.object(ea_client, "close", mock_close), \
             patch.object(ea_client, "close_all", mock_close_all):
            broker = MT5Broker(mode="live", backend="ea_bridge")
            broker.place_order("XAUUSD", "buy", 0.05)
            self.assertEqual(len(broker.get_positions()), 1)

            # Close single position
            res = broker.close_position("XAUUSD", ticket=554433)
            self.assertTrue(res)
            mock_close.assert_called_once_with(symbol="XAUUSD", ticket=554433, magic=112233)
            self.assertEqual(len(broker.get_positions()), 0)

            # Close all
            res_all = broker.close_position("ALL")
            self.assertTrue(res_all)
            mock_close_all.assert_called_once_with(magic=112233)

    def test_mt5_ea_bridge_modify_position(self):
        from bridge import ea_client
        mock_buy = MagicMock(return_value={"ok": True, "ticket": 554433, "price": 2650.50, "volume": 0.05, "retcode": 10009})
        mock_modify = MagicMock(return_value={"ok": True, "modified": True, "ticket": 554433})
        with patch.object(ea_client, "buy", mock_buy), patch.object(ea_client, "modify", mock_modify):
            broker = MT5Broker(mode="live", backend="ea_bridge")
            broker.place_order("XAUUSD", "buy", 0.05, stop_loss=2640.0)

            mod_ok = broker.modify_position("XAUUSD", stop_loss=2645.0, take_profit=2680.0, ticket=554433)
            self.assertTrue(mod_ok)
            mock_modify.assert_called_once_with(symbol="XAUUSD", ticket=554433, sl=2645.0, tp=2680.0, magic=112233)
            pos = broker.get_positions()[0]
            self.assertEqual(pos.stop_loss, 2645.0)
            self.assertEqual(pos.take_profit, 2680.0)

    def test_mt5_ea_bridge_account_info(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 2, "volume": 0.10, "login": 887766})
        with patch.object(ea_client, "status", mock_status):
            broker = MT5Broker(mode="live", backend="ea_bridge")
            info = broker.get_account_info()
            self.assertEqual(info.login, 887766)
            self.assertEqual(info.company, "EA-Bridge (MetaTrader 5)")
            self.assertTrue(info.connected)

    def test_format_mt5_error_auth_failed(self):
        from mt5_broker import format_mt5_error
        err = (-6, "Terminal: Authorization failed")
        diag = format_mt5_error(err, login=10055086, server="Mtrading-Live")
        self.assertIn("Authorization Failed", diag)
        self.assertIn("10055086", diag)
        self.assertIn("Mtrading-Live", diag)
        self.assertIn("Open an Account", diag)
        self.assertIn("Mtrading", diag)
        self.assertIn("EA File-Bridge", diag)

    def test_format_mt5_error_ipc(self):
        from mt5_broker import format_mt5_error
        err = (-10001, "IPC connection failed")
        diag = format_mt5_error(err, login=10055086, server="Mtrading-Live")
        self.assertIn("IPC Error", diag)
        self.assertIn("MetaTrader 5 terminal is not responding", diag)

    def test_mt5_connect_passes_credentials_to_initialize(self):
        import mt5_broker
        mock_mt5 = MagicMock()
        mock_mt5.terminal_info.return_value = None  # Terminal not yet started
        mock_mt5.initialize.return_value = True
        mock_acc = MagicMock(login=10055086, balance=15000.0, leverage=100.0, currency="USD", company="MTrading")
        mock_acc.trade_allowed = True
        mock_acc.trade_expert = True
        mock_mt5.account_info.return_value = mock_acc

        with patch.object(mt5_broker, "native_mt5", mock_mt5):
            broker = MT5Broker(mode="live", login=10055086, password="Password123", server="Mtrading-Live")
            self.assertTrue(broker.is_connected)
            # Verify credentials were passed directly to initialize
            mock_mt5.initialize.assert_called_once()
            init_kwargs = mock_mt5.initialize.call_args[1]
            self.assertEqual(init_kwargs.get("login"), 10055086)
            self.assertEqual(init_kwargs.get("password"), "Password123")
            self.assertEqual(init_kwargs.get("server"), "Mtrading-Live")
            self.assertEqual(init_kwargs.get("timeout"), 60000)

    def test_mt5_connect_auth_failure_raises_actionable_error(self):
        import mt5_broker
        mock_mt5 = MagicMock()
        mock_mt5.terminal_info.return_value = None
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (-6, "Terminal: Authorization failed")
        mock_mt5.login.return_value = False

        with patch.object(mt5_broker, "native_mt5", mock_mt5):
            with self.assertRaises(BrokerConfigurationError) as ctx:
                MT5Broker(mode="live", login=10055086, password="WrongPassword", server="Mtrading-Live")
            err_msg = str(ctx.exception)
            self.assertIn("Authorization Failed", err_msg)
            self.assertIn("10055086", err_msg)
            self.assertIn("Mtrading-Live", err_msg)

    def test_get_contract_size_mt5_and_mock(self):
        mock_b = MockMT5Broker()
        self.assertEqual(mock_b.get_contract_size("XAUUSD"), 100.0)
        self.assertEqual(mock_b.get_contract_size("GOLD"), 100.0)
        self.assertEqual(mock_b.get_contract_size("BTCUSD"), 1.0)
        self.assertEqual(mock_b.get_contract_size("EURUSD"), 100000.0)

    def test_get_closed_deal_info_native_mt5(self):
        import mt5_broker
        mock_mt5 = MagicMock()
        mock_mt5.initialize.return_value = True
        mock_mt5.login.return_value = True
        mock_acc = MagicMock(login=10055086, balance=15000.0, leverage=100.0, currency="USD", company="MTrading", trade_allowed=True, trade_expert=True)
        mock_mt5.account_info.return_value = mock_acc
        mock_mt5.terminal_info.return_value = MagicMock(trade_allowed=True)
        mock_mt5.DEAL_ENTRY_OUT = 1

        d1 = MagicMock(ticket=55501, entry=1, profit=15.0, commission=-0.50, swap=-0.25, fee=0.0, price=2665.0, comment="[sl 2665.0]")
        mock_mt5.history_deals_get.return_value = [d1]

        with patch.object(mt5_broker, "native_mt5", mock_mt5):
            broker = MT5Broker(mode="live", login=10055086, password="Password123", server="Mtrading-Live")
            deal_info = broker.get_closed_deal_info(123456)
            self.assertIsNotNone(deal_info)
            self.assertEqual(deal_info["ticket"], 123456)
            self.assertEqual(deal_info["deal_ticket"], 55501)
            # Net profit = 15.0 - 0.50 - 0.25 = 14.25
            self.assertEqual(deal_info["profit"], 14.25)
            self.assertEqual(deal_info["total_profit"], 14.25)
            self.assertEqual(deal_info["price"], 2665.0)
            self.assertEqual(deal_info["comment"], "[sl 2665.0]")


if __name__ == "__main__":
    unittest.main()

