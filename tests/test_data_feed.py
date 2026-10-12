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

    def test_multi_timeframe_fetches_each_timeframe_directly(self):
        feed = self.make_feed(pd.DataFrame())
        calls = []

        def fetch(limit=5, timeframe=None):
            calls.append(timeframe)
            idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
            return pd.DataFrame(
                {
                    "open": [100.0, 101.0, 102.0],
                    "high": [101.0, 102.0, 103.0],
                    "low": [99.0, 100.0, 101.0],
                    "close": [100.5, 101.5, 102.5],
                    "volume": [1.0, 2.0, 3.0],
                },
                index=idx,
            )

        feed.fetch = fetch
        frames = feed.multi_tf(limit_1m=1500)
        self.assertEqual(calls, ["1m", "5m", "15m", "1h", "4h", "1d"])
        self.assertEqual(len(frames["daily"]), 3)
        self.assertIn("4h", frames)


if __name__ == "__main__":
    unittest.main()
