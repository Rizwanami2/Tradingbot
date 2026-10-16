"""
Unit tests for RiskManager.
"""

import os
import unittest
from risk_manager import RiskManager


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
        self.assertEqual(self.rm.state.peak_equity, 10000.0)
        self.assertEqual(self.rm.state.consecutive_losses, 0)

    def test_risk_calculation(self):
        plan = self.rm.position_size(entry=2650.0, stop=2640.0, symbol="XAUUSD")
        self.assertGreater(plan.lots, 0.0)
        self.assertGreater(plan.risk_amount, 0.0)

    def test_lot_size_calculation(self):
        from risk_manager import lots_for_risk
        lots = lots_for_risk(equity=10000.0, risk_pct=0.18, entry=2650.0, stop=2640.0)
        self.assertGreater(lots, 0.0)

    def test_autotrade_plan(self):
        plan = self.rm.autotrade_plan(entry=2650.0, stop=2640.0, direction="long")
        self.assertTrue(plan.get("ok"))
        self.assertEqual(plan.get("direction"), "long")
        self.assertGreater(plan.get("tp1"), 2650.0)
        self.assertGreater(plan.get("lots"), 0.0)

    def test_auto_breakeven_stop(self):
        new_stop, reason = self.rm.auto_breakeven_stop(
            "long", entry=2650.0, current_stop=2640.0, highest=2655.0, lowest=2640.0, initial_risk=10.0
        )
        self.assertGreater(new_stop, 2640.0)
        self.assertIn("Auto BE lock", reason)

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

        self.rm.update_after_trade(pnl=-25.0, r_multiple=-1.0)
        self.assertEqual(self.rm.state.win_streak, 0)
        self.assertEqual(self.rm.state.consecutive_losses, 1)

    def test_leverage_adjustment(self):
        # Default leverage is conservative (RiskConfig.leverage)
        self.assertEqual(self.rm.get_leverage(), self.rm.config.leverage)

        # Valid leverage adjustments within configured bounds (default 1x-20x)
        for lev in [1.0, 5.0, 10.0, 20.0]:
            ok, msg = self.rm.set_leverage(lev)
            self.assertTrue(ok, msg)
            self.assertEqual(self.rm.get_leverage(), lev)

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
        # Within conservative max leverage (default max 20x)
        ok, _ = self.rm.set_leverage(20.0)
        self.assertTrue(ok)
        margin = self.rm.calc_margin(notional_value=10000.0)
        self.assertEqual(margin, 500.0)  # 10000 / 20

        ok, _ = self.rm.set_leverage(10.0)
        self.assertTrue(ok)
        margin_10 = self.rm.calc_margin(notional_value=10000.0)
        self.assertEqual(margin_10, 1000.0)

        # Liquidation price at 20x
        self.rm.set_leverage(20.0)
        liq_long = self.rm.calc_liquidation_price("long", entry=2650.0, leverage=20.0)
        self.assertLess(liq_long, 2650.0)

        liq_short = self.rm.calc_liquidation_price("short", entry=2650.0, leverage=20.0)
        self.assertGreater(liq_short, 2650.0)

    def test_autotrade_plan_with_leverage(self):
        self.rm.set_leverage(20.0)
        # Tight stop still above liquidation at 20x should be accepted
        plan = self.rm.autotrade_plan(entry=2650.0, stop=2648.0, direction="long")
        self.assertTrue(plan.get("ok"), plan)
        self.assertEqual(plan.get("leverage"), 20.0)
        self.assertGreater(plan.get("margin_required"), 0.0)
        self.assertGreater(plan.get("notional_value"), 0.0)
        self.assertIsNotNone(plan.get("liquidation_price"))

        # Stop beyond liquidation price must be rejected
        liq = self.rm.calc_liquidation_price("long", entry=2650.0, leverage=20.0)
        unsafe_stop = float(liq) - 5.0
        unsafe_plan = self.rm.autotrade_plan(entry=2650.0, stop=unsafe_stop, direction="long")
        self.assertFalse(unsafe_plan.get("ok"), unsafe_plan)
        self.assertTrue(
            "Liquidation" in str(unsafe_plan.get("reason", ""))
            or "liquidation" in str(unsafe_plan.get("reason", "")).lower()
            or "stop" in str(unsafe_plan.get("reason", "")).lower()
        )

    def test_lots_for_risk_zero_and_negative(self):
        from risk_manager import lots_for_risk
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


    def test_risk_management_reset(self):
        # 1. Test consecutive losses block & reset
        self.rm.state.consecutive_losses = 5
        can, why = self.rm.can_trade()
        self.assertFalse(can)
        self.assertIn("consecutive losses", why)

        res = self.rm.reset_daily_risk()
        self.assertEqual(res["consecutive_losses"], 0)
        self.assertEqual(self.rm.state.consecutive_losses, 0)
        self.assertTrue(res["can_trade"])

        # 2. Test daily loss limit block & reset
        self.rm.state.daily_pnl = -500.0  # -5% > max_daily_loss_pct (3%)
        can, why = self.rm.can_trade()
        self.assertFalse(can)
        self.assertIn("daily loss limit", why)

        res = self.rm.reset_daily_risk()
        self.assertEqual(res["daily_pnl"], 0.0)
        self.assertEqual(self.rm.state.daily_pnl, 0.0)
        can_after, _ = self.rm.can_trade()
        self.assertTrue(can_after)

        # 3. Test weekly loss limit block & reset
        self.rm.state.weekly_pnl = -800.0  # -8% > max_weekly_loss_pct (6%)
        can, why = self.rm.can_trade()
        self.assertFalse(can)
        self.assertIn("weekly loss limit", why)

        res = self.rm.reset_weekly_risk()
        self.assertEqual(res["weekly_pnl"], 0.0)
        self.assertEqual(self.rm.state.weekly_pnl, 0.0)
        can_after_w, _ = self.rm.can_trade()
        self.assertTrue(can_after_w)

        # 4. Test all risk reset
        self.rm.state.peak_equity = 15000.0
        self.rm.state.equity = 1000.0
        self.rm.state.daily_pnl = -200.0
        self.rm.state.weekly_pnl = -500.0
        self.rm.state.consecutive_losses = 4
        self.rm.state.mode = "recovery"
        self.rm.state.rf = 0.5

        res_all = self.rm.reset_all_risk(new_equity=5000.0)
        self.assertEqual(res_all["equity"], 5000.0)
        self.assertEqual(res_all["peak_equity"], 5000.0)
        self.assertEqual(res_all["daily_pnl"], 0.0)
        self.assertEqual(res_all["weekly_pnl"], 0.0)
        self.assertEqual(res_all["consecutive_losses"], 0)
        self.assertEqual(res_all["mode"], "normal")
        self.assertEqual(res_all["rf"], 1.0)
        self.assertTrue(res_all["can_trade"])


if __name__ == "__main__":
    unittest.main()
