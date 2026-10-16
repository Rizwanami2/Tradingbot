"""
Unit tests verifying Delta Exchange broker removal and fail-closed safety.
Exclusive execution broker is MetaTrader 5 Live ('mt5_live').
"""

import unittest

from delta_broker import DeltaBroker, resolve_delta_symbol
from execution import (
    BrokerConfigurationError,
    create_broker,
    create_broker_fail_closed,
)


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


if __name__ == "__main__":
    unittest.main()
