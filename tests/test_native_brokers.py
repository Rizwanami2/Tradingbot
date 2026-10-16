"""
Unit tests verifying removal of legacy native brokers (OANDA, Alpaca, CCXT, Paper)
and fail-closed safety.
MetaTrader 5 Live ('mt5_live') is the exclusive execution engine.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from execution import (
    BrokerConfigurationError,
    CCXTBroker,
    create_broker,
    create_broker_fail_closed,
)
from oanda_broker import (
    OANDABroker,
    resolve_oanda_symbol,
)
from alpaca_broker import (
    AlpacaBroker,
    resolve_alpaca_symbol,
)


class TestNativeBrokers(unittest.TestCase):

    # =========================================================================
    # 1. Symbol Resolution Helpers (Pure functions)
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

    def test_alpaca_symbol_resolution(self):
        self.assertEqual(resolve_alpaca_symbol("BTCUSD"), "BTC/USD")
        self.assertEqual(resolve_alpaca_symbol("BTCUSDT"), "BTC/USD")
        self.assertEqual(resolve_alpaca_symbol("ETHUSD"), "ETH/USD")
        self.assertEqual(resolve_alpaca_symbol("ETHUSDT"), "ETH/USD")
        self.assertEqual(resolve_alpaca_symbol("AAPL"), "AAPL")
        self.assertEqual(resolve_alpaca_symbol("SPY"), "SPY")

    # =========================================================================
    # 2. Broker Initialization Fail-Closed Tests (Removed Brokers)
    # =========================================================================
    def test_oanda_broker_initialization_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            OANDABroker()

        with self.assertRaises(BrokerConfigurationError):
            OANDABroker(account_id="101-001-99887766-001", api_token="test_token")

    def test_alpaca_broker_initialization_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            AlpacaBroker()

        with self.assertRaises(BrokerConfigurationError):
            AlpacaBroker(api_key="key", api_secret="secret")

    def test_ccxt_broker_initialization_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            CCXTBroker()

        with self.assertRaises(BrokerConfigurationError):
            CCXTBroker(exchange_id="binance", api_key="k", api_secret="s")

    # =========================================================================
    # 3. Unified Broker Factory Routing (Fail-Closed Safety)
    # =========================================================================
    def test_unified_broker_factory_routing(self):
        # 1. Paper broker raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="paper", initial_balance=5000.0)

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("paper")

        # 2. OANDA broker raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="oanda_live")

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("oanda")

        # 3. Alpaca broker raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="alpaca_live")

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("alpaca")

        # 4. CCXT / Legacy live raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="live", exchange_id="binance")

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed("live")

        # 5. MT5 Live routing succeeds when factory is provided
        mock_mt5 = MagicMock()
        b_mt5 = create_broker_fail_closed(
            "mt5_live",
            mt5_live_factory=lambda: mock_mt5,
        )
        self.assertEqual(b_mt5, mock_mt5)

    # =========================================================================
    # 4. Telegram Commands Removal Notices
    # =========================================================================
    def test_telegram_removed_broker_commands(self):
        from main import oanda_command, alpaca_command, ccxt_command

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = []

        # /oanda
        asyncio.run(oanda_command(update, ctx))
        call_msg = update.message.reply_text.call_args[0][0]
        self.assertIn("OANDA Broker Removed", call_msg)
        self.assertIn("MetaTrader 5", call_msg)

        # /alpaca
        asyncio.run(alpaca_command(update, ctx))
        call_msg = update.message.reply_text.call_args[0][0]
        self.assertIn("Alpaca Broker Removed", call_msg)
        self.assertIn("MetaTrader 5", call_msg)

        # /ccxt
        asyncio.run(ccxt_command(update, ctx))
        call_msg = update.message.reply_text.call_args[0][0]
        self.assertIn("CCXT Live Broker Removed", call_msg)
        self.assertIn("MetaTrader 5", call_msg)


if __name__ == "__main__":
    unittest.main()
