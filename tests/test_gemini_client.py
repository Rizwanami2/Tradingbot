"""
Unit tests for GeminiClient and Fallback Trading Assistant.
"""

import unittest
from gemini_client import GeminiClient


class TestGeminiClient(unittest.TestCase):
    def setUp(self):
        # Initialize without an API key to test local fallback intelligence
        self.client = GeminiClient(api_key="")

    def test_fallback_greeting(self):
        res = self.client.ask(chat_id=1, user_text="Hello")
        self.assertIn("Trading Assistant", res)
        self.assertIn("/price", res)

    def test_fallback_setup_analysis(self):
        ctx = "[LIVE MARKET DATA: XAUTUSD Mark: 2650.00 | 24h: +0.50%]"
        res = self.client.ask(chat_id=1, user_text="Give me a long setup on XAUTUSD", market_context=ctx)
        self.assertIn("Stop Loss", res)
        self.assertIn("Target 1", res)
        self.assertIn("2650", res)

    def test_fallback_risk_query(self):
        res = self.client.ask(chat_id=1, user_text="What are your risk rules?")
        self.assertIn("Break-Even", res)
        self.assertIn("Daily Loss", res)

    def test_history_reset(self):
        self.client.ask(chat_id=2, user_text="Hello")
        self.assertGreater(len(self.client.get_history(2)), 0)
        self.client.reset(chat_id=2)
        self.assertEqual(len(self.client.get_history(2)), 0)

    def test_generate_trade_rationale_fallback(self):
        trade_data = {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": 2650.0,
            "stop_loss": 2642.0,
            "take_profit": 2668.0,
            "lots": 0.05,
            "reason": "Bullish SFP + Order Block mitigation",
            "regime": "Trending",
        }
        res = self.client.generate_trade_rationale(trade_data)
        self.assertIn("AI TRADE RATIONALE", res)
        self.assertIn("XAUUSD BUY", res)
        self.assertIn("2,650.00", res)
        self.assertIn("Thesis", res)
        self.assertIn("Invalidation", res)


if __name__ == "__main__":
    unittest.main()
