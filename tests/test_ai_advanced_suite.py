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
        # AI veto removed: signal always allowed
        self.suite.state["last_loss_ts"] = __import__("time").time()
        r = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r.allow)
        self.assertIn("AI veto disabled", r.reasons)

    def test_scorecard_balance_only(self):
        # AI veto removed: all profiles allowed without scorecard block
        r = self.suite.evaluate_entry(self._ctx(conviction=0.2, agreement=0, rr=0.5, profile="scalp"))
        self.assertTrue(r.allow)
        r2 = self.suite.evaluate_entry(
            self._ctx(conviction=0.2, agreement=0, rr=0.2, profile="balance", news_risk=0.9)
        )
        self.assertTrue(r2.allow)

    def test_lot_ceiling(self):
        self.assertLessEqual(self.suite.apply_lot_ceiling(5.0), self.suite.lot_ceiling)

    def test_on_trade_closed_updates_daily(self):
        self.suite.on_trade_closed("XAUUSD", won=False, r_multiple=-1.0, mistake_tag="late_entry")
        self.assertEqual(self.suite.state["consec_losses"], 1)
        self.assertIn("late_entry", self.suite.state.get("mistakes", {}))

    def test_manual_mode_allows_with_advisory_reasons(self):
        import time
        self.suite.state["last_loss_ts"] = time.time()
        # AI veto removed: both modes allow entry without veto
        r_auto = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r_auto.allow)

        msg = self.suite.set_manual_mode(True)
        self.assertIn("MANUAL", msg)
        self.assertTrue(self.suite.is_manual_mode())
        r_manual = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r_manual.allow)

    def test_manual_override_single_trade(self):
        # AI veto removed: signals always allowed
        r1 = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r1.allow)
        r2 = self.suite.evaluate_entry(self._ctx())
        self.assertTrue(r2.allow)

    def test_reset_safety_locks(self):
        import time
        self.suite.state["last_loss_ts"] = time.time()
        self.suite.state["consec_losses"] = 4
        self.suite.state["fill_rejects"] = 10
        self.suite.state["daily"]["realized_r"] = -5.0

        msg = self.suite.reset_safety_locks()
        self.assertIn("reset successfully", msg)
        self.assertEqual(self.suite.state["last_loss_ts"], 0.0)
        self.assertEqual(self.suite.state["consec_losses"], 0)
        self.assertEqual(self.suite.state["fill_rejects"], 0)
        self.assertEqual(self.suite.state["daily"]["realized_r"], 0.0)

    def test_learn_freeze_bug_fix(self):
        self.suite.learn_freeze = True
        self.suite.state["learn_frozen"] = True

        msg = self.suite.set_learn_freeze(False)
        self.assertIn("False", msg)
        self.assertFalse(self.suite.learn_freeze)
        self.assertFalse(self.suite.state["learn_frozen"])

    def test_parameter_setters_and_summary(self):
        self.suite.set_min_scorecard(68.0)
        self.assertEqual(self.suite.min_scorecard, 68.0)
        self.assertEqual(self.suite.state["min_scorecard"], 68.0)

        self.suite.set_lot_ceiling(2.5)
        self.assertEqual(self.suite.lot_ceiling, 2.5)
        self.assertEqual(self.suite.state["lot_ceiling"], 2.5)

        self.suite.set_scorecard_balance_only(False)
        self.assertFalse(self.suite.balance_mode_only_scorecard)
        self.assertFalse(self.suite.state["balance_mode_only_scorecard"])

        s = self.suite.get_summary_dict()
        self.assertEqual(s["min_scorecard"], 68.0)
        self.assertEqual(s["lot_ceiling"], 2.5)
        self.assertFalse(s["balance_only_scorecard"])


if __name__ == "__main__":
    unittest.main()
