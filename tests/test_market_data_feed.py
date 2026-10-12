"""Regression tests for the live multi-agent market-data path."""
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from multi_agent_trading_bot import MarketDataFeed


class TestMarketDataFeed(unittest.TestCase):
    def test_provider_failure_does_not_substitute_demo_candles(self):
        feed = MarketDataFeed.__new__(MarketDataFeed)
        feed.symbol = "BTCUSDT"
        feed.source = "tradingview"
        feed.exchange_id = "binance"
        feed._improved = None
        with patch("tradingview_feed.fetch_tradingview_bars", return_value=pd.DataFrame()):
            result = feed.fetch(limit=50, timeframe="1m")
        self.assertTrue(result.empty)
        self.assertIn("close", result.columns)

    def test_higher_timeframes_are_fetched_natively(self):
        feed = MarketDataFeed.__new__(MarketDataFeed)
        feed.symbol = "BTCUSDT"
        feed.source = "tradingview"
        feed.exchange_id = "binance"
        fake = Mock()
        idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
        frame = pd.DataFrame(
            {"open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2],
             "close": [1.5, 2.5, 3.5], "volume": [10, 20, 30]},
            index=idx,
        )
        fake.fetch.return_value = frame
        feed._improved = fake
        frames = feed.multi_tf(limit_1m=100)
        self.assertEqual(
            [call.kwargs["timeframe"] for call in fake.fetch.call_args_list],
            ["1m", "5m", "15m", "1h", "4h", "1d"],
        )
        self.assertEqual(len(frames["daily"]), 3)

    def test_explicit_demo_mode_is_the_only_synthetic_fallback(self):
        feed = MarketDataFeed.__new__(MarketDataFeed)
        feed.symbol = "BTCUSDT"
        feed.source = "demo"
        feed.exchange_id = "binance"
        feed._improved = None
        feed._demo = Mock(return_value=pd.DataFrame({"close": [1.0]}))
        result = feed.fetch(limit=1, timeframe="1m")
        self.assertEqual(float(result["close"].iloc[0]), 1.0)


if __name__ == "__main__":
    unittest.main()
