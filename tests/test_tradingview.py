"""
Unit tests for TradingView Data Feed Provider.
"""

import unittest
from tradingview_feed import resolve_tv_params, fetch_tradingview_quote, fetch_tradingview_bars


class TestTradingViewFeed(unittest.TestCase):
    def test_param_resolution(self):
        # Gold
        sym, ex, scr = resolve_tv_params("XAUUSD")
        self.assertEqual(sym, "XAUUSD")
        self.assertEqual(ex, "OANDA")
        self.assertEqual(scr, "cfd")

        # Crypto
        sym_btc, ex_btc, scr_btc = resolve_tv_params("BTCUSD")
        self.assertIn(sym_btc, ("BTCUSDT", "BTCUSD"))
        self.assertEqual(ex_btc, "BINANCE")
        self.assertEqual(scr_btc, "crypto")

        # Forex
        sym_fx, ex_fx, scr_fx = resolve_tv_params("EURUSD")
        self.assertEqual(sym_fx, "EURUSD")
        self.assertEqual(ex_fx, "FX_IDC")
        self.assertEqual(scr_fx, "forex")

    def test_fetch_quote(self):
        quote = fetch_tradingview_quote("XAUUSD")
        self.assertIn("symbol", quote)
        self.assertIn("mark", quote)
        self.assertGreater(quote["mark"], 0.0)
        self.assertIn("source", quote)
        self.assertIn("recommendation", quote)

    def test_fetch_bars(self):
        df = fetch_tradingview_bars("XAUUSD", limit=20)
        self.assertEqual(len(df), 20)
        self.assertTrue(all(col in df.columns for col in ["open", "high", "low", "close", "volume"]))
        self.assertGreater(df["close"].iloc[-1], 0.0)


if __name__ == "__main__":
    unittest.main()
