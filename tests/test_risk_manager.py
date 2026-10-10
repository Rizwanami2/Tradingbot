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

    def test_leverage_adjustment(self):
        # Default leverage
        self.assertEqual(self.rm.get_leverage(), 20.0)

        # Valid leverage adjustments between 1x and 500x
        for lev in [1.0, 10.0, 50.0, 200.0, 500.0]:
            ok, msg = self.rm.set_leverage(lev)
            self.assertTrue(ok)
            self.assertEqual(self.rm.get_leverage(), lev)
            if lev > 50.0:
                self.assertIn("High Leverage Warning", msg)

        # Invalid leverage adjustments (out of bounds)
        ok, msg = self.rm.set_leverage(0.5)
        self.assertFalse(ok)
        self.assertIn("out of bounds", msg)

        ok, msg = self.rm.set_leverage(501.0)
        self.assertFalse(ok)
        self.assertIn("out of bounds", msg)

        ok, msg = self.rm.set_leverage("invalid")
        self.assertFalse(ok)
        self.assertIn("Invalid leverage", msg)

    def test_margin_and_liquidation_calculation(self):
        self.rm.set_leverage(100.0)
        margin = self.rm.calc_margin(notional_value=10000.0)
        self.assertEqual(margin, 100.0)

        self.rm.set_leverage(500.0)
        margin_500 = self.rm.calc_margin(notional_value=10000.0)
        self.assertEqual(margin_500, 20.0)

        # Liquidation price
        self.rm.set_leverage(100.0)
        liq_long = self.rm.calc_liquidation_price("long", entry=2650.0, leverage=100.0)
        self.assertLess(liq_long, 2650.0)
        self.assertGreater(liq_long, 2600.0)

        liq_short = self.rm.calc_liquidation_price("short", entry=2650.0, leverage=100.0)
        self.assertGreater(liq_short, 2650.0)

    def test_autotrade_plan_with_leverage(self):
        self.rm.set_leverage(200.0)
        # At 200x leverage, liquidation is ~2644.70, so stop at 2648.0 is safe
        plan = self.rm.autotrade_plan(entry=2650.0, stop=2648.0, direction="long")
        self.assertTrue(plan.get("ok"))
        self.assertEqual(plan.get("leverage"), 200.0)
        self.assertGreater(plan.get("margin_required"), 0.0)
        self.assertGreater(plan.get("notional_value"), 0.0)
        self.assertIsNotNone(plan.get("liquidation_price"))

        # A stop at 2640.0 is below liquidation price and must be rejected
        unsafe_plan = self.rm.autotrade_plan(entry=2650.0, stop=2640.0, direction="long")
        self.assertFalse(unsafe_plan.get("ok"))
        self.assertIn("Liquidation Price", unsafe_plan.get("reason"))

    def test_lots_for_risk_zero_and_negative(self):
        from risk_manager import lots_for_risk
        # Zero or negative equity/risk should return 0.0 lots
        self.assertEqual(lots_for_risk(equity=0.0, risk_pct=0.18, entry=2650.0, stop=2640.0), 0.0)
        self.assertEqual(lots_for_risk(equity=-100.0, risk_pct=0.18, entry=2650.0, stop=2640.0), 0.0)
        self.assertEqual(lots_for_risk(equity=10000.0, risk_pct=0.0, entry=2650.0, stop=2640.0), 0.0)
        self.assertEqual(lots_for_risk(equity=10000.0, risk_pct=-0.5, entry=2650.0, stop=2640.0), 0.0)

    def test_state_load_fallbacks(self):
        import tempfile, json
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump({"equity": 12000.0, "peak_equity": 12500.0}, f)
            temp_path = f.name
        try:
            rm = RiskManager(equity=10000.0, state_file=temp_path)
            self.assertEqual(rm.state.total_wins, 0)
            self.assertEqual(rm.state.total_trades, 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
