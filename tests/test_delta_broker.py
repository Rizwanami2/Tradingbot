import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from delta_broker import DeltaBroker, MockDeltaBroker, resolve_delta_symbol
from execution import (
    BrokerConfigurationError,
    create_broker,
    create_broker_fail_closed,
)
from execution_engine import ExecutionEngine, OrderStatus
from mt5_broker import MockMT5Broker
from risk_manager import RiskManager


class TestDeltaBroker(unittest.TestCase):

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

    def test_delta_broker_initialization_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            DeltaBroker()

    def test_delta_broker_with_credentials_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            DeltaBroker(
                api_key="valid_key",
                api_secret="valid_secret",
                leverage=50.0,
            )

    def test_factory_delta_routing_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("delta")

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("delta_live")

        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="delta")

        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="delta_live")

    def test_mock_delta_broker_features(self):
        broker = MockDeltaBroker(balance=10000.0)
        self.assertTrue(broker.is_connected)
        self.assertEqual(broker.balance, 10000.0)
        self.assertEqual(broker.equity, 10000.0)
        self.assertEqual(broker.margin, 0.0)
        self.assertEqual(broker.free_margin, 10000.0)

        # Place order
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

        # Update mark price
        broker.update_mark_price("BTCUSDT", 69000.0)
        self.assertEqual(broker.mark_prices["BTCUSD"], 69000.0)

        # Modify position
        self.assertTrue(broker.modify_position("BTCUSDT", stop_loss=67500.0, take_profit=71000.0))
        pos = broker.positions["BTCUSD"]
        self.assertEqual(pos.stop_loss, 67500.0)
        self.assertEqual(pos.take_profit, 71000.0)

        # Close position
        self.assertTrue(broker.close_position("BTCUSDT", price=69000.0))
        self.assertNotIn("BTCUSD", broker.positions)
        self.assertGreater(broker.balance, 10000.0)

    def test_delta_execution_engine_autotrade_trigger(self):
        events = []

        def callback(event_name, order, message):
            events.append({"event": event_name, "order": order, "message": message})

        broker = MockDeltaBroker(balance=50000.0)
        state_file = "test_delta_ee_risk.json"
        if os.path.exists(state_file):
            os.remove(state_file)
        rm = RiskManager(equity=50000.0, symbol="BTCUSDT", state_file=state_file)
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
        order = engine.register_setup(sig, reason="Delta Autotrade Trigger Test")
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertIn("BTCUSDT", engine.pending_setups)

        # Market candle triggering entry
        candle = {"open": 67950.0, "high": 68050.0, "low": 67900.0, "close": 68010.0}
        trig_events = engine.update_market_candle("BTCUSDT", candle)

        self.assertIn(order.id, engine.active_positions)
        self.assertNotIn(order.id, engine.pending_setups)
        self.assertEqual(order.status, OrderStatus.OPEN)

        exec_events = [e for e in trig_events if e["event"] == "TRADE_EXECUTED"]
        self.assertTrue(len(exec_events) >= 1)
        msg = exec_events[0]["message"]
        self.assertIn("DELTA LIVE TRADE CONFIRMED", msg)
        self.assertIn("Delta Exchange India Live Execution", msg)

        # Close position
        closed_order = engine.close_position("BTCUSDT", current_price=68500.0, reason="Manual exit")
        self.assertIsNotNone(closed_order)
        self.assertEqual(closed_order.status, OrderStatus.CLOSED)
        if os.path.exists(state_file):
            os.remove(state_file)

    def test_mt5_execution_engine_autotrade_trigger(self):
        events = []

        def callback(event_name, order, message):
            events.append({"event": event_name, "order": order, "message": message})

        broker = MockMT5Broker()
        state_file = "test_mt5_ee_risk.json"
        if os.path.exists(state_file):
            os.remove(state_file)
        rm = RiskManager(equity=10000.0, symbol="BTCUSDT", state_file=state_file)
        engine = ExecutionEngine(risk_manager=rm, broker=broker, event_callback=callback)

        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 68000.0,
            "stop": 67000.0,
            "tp1": 69000.0,
            "tp2": 70000.0,
            "lots": 0.01,
            "size": 0.01,
            "leverage": 20.0,
            "margin_required": 34.0,
            "liquidation_price": 64600.0,
            "risk_amount": 10.0,
            "rr": 1.5,
        }
        order = engine.register_setup(sig, reason="MT5 Autotrade Trigger Test")
        self.assertEqual(order.status, OrderStatus.PENDING)

        candle = {"open": 67950.0, "high": 68050.0, "low": 67900.0, "close": 68010.0}
        trig_events = engine.update_market_candle("BTCUSDT", candle)

        self.assertIn(order.id, engine.active_positions)
        exec_events = [e for e in trig_events if e["event"] == "TRADE_EXECUTED"]
        self.assertTrue(len(exec_events) >= 1)
        msg = exec_events[0]["message"]
        self.assertIn("MT5 LIVE TRADE CONFIRMED", msg)
        self.assertIn("MT5 Live Execution", msg)

        if os.path.exists(state_file):
            os.remove(state_file)

    def test_telegram_delta_command_live_and_testnet_connect(self):
        from main import delta_command, engine

        orig_broker = engine.broker
        orig_mode = getattr(config, "BROKER_MODE", "mt5_live")

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()

        # 1. Test live connect with mocked DeltaBroker
        mock_instance = MagicMock()
        mock_instance.balance = 50000.0
        mock_instance.equity = 50000.0
        mock_instance.free_margin = 50000.0
        mock_instance.currency = "USD"
        mock_instance.base_url = "https://api.india.delta.exchange"
        mock_instance.is_testnet = False
        mock_instance.get_positions.return_value = []

        with patch("delta_broker.DeltaBroker", return_value=mock_instance):
            ctx.args = ["live", "test_key", "test_secret"]
            asyncio.run(delta_command(update, ctx))
            self.assertTrue(update.message.reply_text.called)
            call_msg = update.message.reply_text.call_args[0][0]
            self.assertIn("Delta Exchange India LIVE Connected Successfully", call_msg)
            self.assertIn("50,000.00", call_msg)
            self.assertEqual(config.BROKER_MODE, "delta_live")
            self.assertEqual(engine.broker, mock_instance)

        # 2. Test testnet connect with mocked DeltaBroker
        mock_testnet_instance = MagicMock()
        mock_testnet_instance.balance = 25000.0
        mock_testnet_instance.equity = 25000.0
        mock_testnet_instance.free_margin = 25000.0
        mock_testnet_instance.currency = "USD"
        mock_testnet_instance.base_url = "https://cdn-ind.testnet.deltaex.org"
        mock_testnet_instance.is_testnet = True
        mock_testnet_instance.get_positions.return_value = []

        with patch("delta_broker.DeltaBroker", return_value=mock_testnet_instance):
            ctx.args = ["testnet", "testnet_key", "testnet_secret"]
            asyncio.run(delta_command(update, ctx))
            self.assertTrue(update.message.reply_text.called)
            call_msg = update.message.reply_text.call_args[0][0]
            self.assertIn("Delta Exchange Testnet Connected Successfully", call_msg)
            self.assertEqual(config.BROKER_MODE, "delta_testnet")
            self.assertEqual(engine.broker, mock_testnet_instance)

        # Restore
        engine.broker = orig_broker
        config.BROKER_MODE = orig_mode


if __name__ == "__main__":
    unittest.main()
