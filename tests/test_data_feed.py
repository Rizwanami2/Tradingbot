"""Regression tests for fail-closed market-data behavior."""
import unittest

import pandas as pd

from data_feed import DataFeed


class TestDataFeed(unittest.TestCase):
    def make_feed(self, frame):
        feed = DataFeed.__new__(DataFeed)
        feed.symbol = "BTCUSD"
        feed.source = "ccxt"
        feed.fetch = lambda limit=5, timeframe=None: frame
        return feed

    def test_missing_latest_price_does_not_return_synthetic_quote(self):
        frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = self.make_feed(frame).get_latest_price()
        self.assertFalse(result["ok"])
        self.assertEqual(result["source"], "unavailable")
        self.assertNotIn("mark", result)
        self.assertNotIn("close", result)
        self.assertIn("no synthetic price", result["error"])

    def test_latest_price_returns_actual_last_candle(self):
        index = pd.date_range("2026-01-01", periods=2, freq="min", tz="UTC")
        frame = pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [1.0, 2.0],
            },
            index=index,
        )
        result = self.make_feed(frame).get_latest_price()
        self.assertEqual(result["mark"], 102.0)
        self.assertEqual(result["close"], 102.0)
        self.assertEqual(result["source"], "ccxt")


if __name__ == "__main__":
    unittest.main()
