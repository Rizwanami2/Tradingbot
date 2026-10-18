"""Tests for AI Advanced Suite gates."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ai_advanced_suite import AIAdvancedSuite, SignalContext


class TestAIAdvancedSuite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.environ["AI_ADVANCED_ENABLED"] = "true"
        os.environ["AI_SCORECARD_BALANCE_ONLY"] = "true"
        self.suite = AIAdvancedSuite(state_path=Path(self.tmp.name))

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def _ctx(self, **kw):
        base = dict(
            symbol="XAUUSD",
            direction="LONG",
            conviction=0.7,
            agreement=3,
            rr=2.0,
            regime="trend",
            profile="scalp",
            structure_ok=True,
        )
        base.update(kw)
        return SignalContext(**base)

    def test_allows_clean_signal(self):
        r = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r.allow)

    def test_revenge_block(self):
        self.suite.state["last_loss_ts"] = __import__("time").time()
        r = self.suite.evaluate_entry(self._ctx())
        self.assertFalse(r.allow)
        self.assertTrue(any("revenge" in x.lower() for x in r.reasons))

    def test_scorecard_balance_only(self):
        r = self.suite.evaluate_entry(self._ctx(conviction=0.2, agreement=0, rr=0.5, profile="scalp"))
        self.assertIsInstance(r.allow, bool)
        r2 = self.suite.evaluate_entry(
            self._ctx(conviction=0.2, agreement=0, rr=0.2, profile="balance", news_risk=0.9)
        )
        self.assertFalse(r2.allow)

    def test_lot_ceiling(self):
        self.assertLessEqual(self.suite.apply_lot_ceiling(5.0), self.suite.lot_ceiling)

    def test_on_trade_closed_updates_daily(self):
        self.suite.on_trade_closed("XAUUSD", won=False, r_multiple=-1.0, mistake_tag="late_entry")
        self.assertEqual(self.suite.state["consec_losses"], 1)
        self.assertIn("late_entry", self.suite.state.get("mistakes", {}))


if __name__ == "__main__":
    unittest.main()
