"""
Unit tests for configuration loader and utilities.
"""

import unittest
from datetime import datetime, timezone, timedelta
from config import _safe_int, _safe_float
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
        self.assertEqual(config.POLL_INTERVAL, 10)
        self.assertEqual(config.FAST_POLL_INTERVAL, 3)
        self.assertEqual(config.DEFAULT_LEVERAGE, 20.0)
        self.assertEqual(config.BROKER_MODE, "mt5_live")
        self.assertGreater(config.INITIAL_EQUITY, 0.0)
        self.assertIn(config.DEFAULT_SYMBOL, ("XAUTUSD", "XAUUSD"))
        self.assertIn(config.MAX_OPEN_TRADES, (2, 4, 5, 6, 10))
        self.assertGreaterEqual(len(config.AUTOTRADE_SYMBOLS), 1)

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

    def test_apply_xau_scalp_and_accuracy_scripts(self):
        import tempfile
        from pathlib import Path
        from scripts.apply_xau_scalp import apply_env as apply_xau_scalp_env
        from scripts.apply_accuracy_mode import apply_env as apply_accuracy_env

        with tempfile.TemporaryDirectory() as tmpdir:
            test_env = Path(tmpdir) / ".env"
            test_env.write_text("BOT_PROFILE=swing\nBOT_MAX_OPEN_TRADES=5\n", encoding="utf-8")

            # Apply XAU Scalp
            apply_xau_scalp_env(test_env)
            txt = test_env.read_text(encoding="utf-8")
            self.assertIn("BOT_PROFILE=xau_scalp", txt)
            self.assertIn("BOT_SCALP=true", txt)
            self.assertIn("BOT_MAX_OPEN_TRADES=2", txt)
            self.assertIn("BOT_SCALP_TP_USD=3.0", txt)
            self.assertIn("BOT_SCALP_SL_USD=2.5", txt)

            # Apply Accuracy Mode
            apply_accuracy_env(test_env)
            txt2 = test_env.read_text(encoding="utf-8")
            self.assertIn("BOT_ACCURACY_MODE=true", txt2)
            self.assertIn("BOT_ACCURACY_MIN_QUALITY=0.45", txt2)
            self.assertIn("AI_MIN_SCORECARD=60", txt2)


if __name__ == "__main__":
    unittest.main()
