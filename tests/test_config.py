"""
Unit tests for configuration loader and utilities.
"""

import os
import unittest
from datetime import datetime, timezone, timedelta
from config import _safe_int, _safe_float, load_dotenv
import config
from multi_agent_trading_bot import NewsAgent


class TestConfigAndUtils(unittest.TestCase):
    def test_safe_int(self):
        self.assertEqual(_safe_int("42", 10), 42)
        self.assertEqual(_safe_int("42.0", 10), 42)
        self.assertEqual(_safe_int("invalid", 10), 10)
        self.assertEqual(_safe_int("", 10), 10)
        self.assertEqual(_safe_int(None, 10), 10)

    def test_safe_float(self):
        self.assertEqual(_safe_float("42.5", 10.0), 42.5)
        self.assertEqual(_safe_float("invalid", 10.0), 10.0)
        self.assertEqual(_safe_float("", 10.0), 10.0)
        self.assertEqual(_safe_float(None, 10.0), 10.0)

    def test_config_defaults(self):
        self.assertGreater(config.POLL_INTERVAL, 0)
        self.assertGreater(config.INITIAL_EQUITY, 0.0)
        self.assertGreater(config.DEFAULT_LEVERAGE, 0.0)
        self.assertIn(config.DEFAULT_SYMBOL, ("XAUTUSD", "XAUUSD"))

    def test_news_agent_naive_and_aware_datetimes(self):
        agent = NewsAgent(enabled=True, window_min=30)
        now_utc = datetime.now(timezone.utc)
        agent._cal = [(now_utc, "High Impact Event")]
        agent._cal_at = 9999999999.0

        # Test with aware datetime in window
        blackout, msg = agent.blackout(now_utc + timedelta(minutes=5))
        self.assertTrue(blackout)
        self.assertIn("High Impact Event", msg)

        # Test with naive datetime in window (should not raise TypeError)
        naive_dt = datetime.utcnow() + timedelta(minutes=5)
        blackout_naive, msg_naive = agent.blackout(naive_dt)
        self.assertTrue(blackout_naive)

        # Test outside window
        blackout_far, _ = agent.blackout(now_utc + timedelta(hours=5))
        self.assertFalse(blackout_far)


if __name__ == "__main__":
    unittest.main()
