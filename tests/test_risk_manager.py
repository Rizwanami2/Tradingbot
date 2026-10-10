"""
Unit tests for RiskManager.
"""

import os
import unittest
from risk_manager import RiskManager, RiskConfig, INSTRUMENTS, TradeRiskResult


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.state_file = "test_risk_state.json"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD", state_file=self.state_file)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def test_initial_state(self):
        self.assertEqual(self.rm.state.equity, 10000.0)
        self.assertEqual(self.rm.symbol, "XAUUSD")
        can_trade, reason = self.rm.can_trade()
        self.assertTrue(can_trade)
        self.assertEqual(reason, "ok")

    def test_risk_calculation(self):
        risk_pct, reason = self.rm.current_risk_pct()
        self.assertGreaterEqual(risk_pct, self.rm.config.min_risk_pct)
        self.assertLessEqual(risk_pct, self.rm.config.max_risk_pct)

    def test_lot_size_calculation(self):
        lots, pips, pip_val = self.rm.calc_lots(risk_amount=25.0, stop_distance=10.0)
        self.assertGreater(lots, 0.0)
        self.assertGreater(pips, 0.0)
        self.assertEqual(pip_val, 1.0)

    def test_autotrade_plan(self):
        plan = self.rm.autotrade_plan(entry=2650.0, stop=2640.0, direction="long")
        self.assertTrue(plan.get("ok"))
        self.assertEqual(plan.get("direction"), "long")
        self.assertGreater(plan.get("tp1"), 2650.0)
        self.assertGreater(plan.get("lots"), 0.0)

    def test_auto_breakeven_stop(self):
        # Entry 2650, SL 2640, risk = 10
        # When price reaches +0.5R (2655), should trigger early lock
        new_stop, reason = self.rm.auto_breakeven_stop(
            "long", entry=2650.0, current_stop=2640.0, highest=2655.0, lowest=2640.0, initial_risk=10.0
        )
        self.assertGreater(new_stop, 2640.0)
        self.assertIn("Auto BE lock", reason)

        # When price reaches +1.0R (2660), should trigger full BE with buffer
        new_stop2, reason2 = self.rm.auto_breakeven_stop(
            "long", entry=2650.0, current_stop=new_stop, highest=2660.0, lowest=2640.0, initial_risk=10.0
        )
        self.assertGreater(new_stop2, 2650.0)
        self.assertIn("full", reason2)

    def test_update_after_trade(self):
        initial_eq = self.rm.state.equity
        self.rm.update_after_trade(pnl=50.0, r_multiple=2.0)
        self.assertEqual(self.rm.state.equity, initial_eq + 50.0)
        self.assertEqual(self.rm.state.win_streak, 1)
        self.assertEqual(self.rm.state.consecutive_losses, 0)

        # Record a loss
        self.rm.update_after_trade(pnl=-25.0, r_multiple=-1.0)
        self.assertEqual(self.rm.state.win_streak, 0)
        self.assertEqual(self.rm.state.consecutive_losses, 1)


if __name__ == "__main__":
    unittest.main()
