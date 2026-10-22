"""
Unit tests verifying that autotrade in the Balanced Profile operates cleanly without risk:
1. Multi-agent consensus setups pass CombinedBotAgent structure evaluation without 'setup_blocked'.
2. Structural Stop Loss, Take Profit, and R:R (>= 1.90) are preserved (never overwritten by micro-scalp brackets).
3. Risk is strictly limited (0.25% equity risk, pending limit pullback entry, zero capital loss on unreached fills).
"""
import unittest
from unittest.mock import MagicMock, patch

import config
from combined_bot_agent import CombinedBotAgent, AgentConfig
from entry_level_planner import EntryPlan, attach_plan_to_signal
from execution_engine import ExecutionEngine, OrderStatus
from risk_manager import RiskManager
from strategy_profiles import apply_profile, get_profile


class TestBalancedAutotradeNoRisk(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.rm)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def tearDown(self):
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def test_balanced_setup_not_blocked_by_combined_agent_structure_gate(self):
        """Consensus setups in balanced profile pass CombinedBotAgent structure gate."""
        cba = CombinedBotAgent()
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2720.0,
            "stop": 2705.0,
            "tp1": 2750.0,
            "tp2": 2770.0,
            "score": 39.79,
            "reason": "Consensus LONG score=39.79",
            "setup": "Consensus LONG score=39.79",
            "is_scalp": False,
            "fast_market_fill": False,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "rr": 2.0,
        }
        ctx = {
            "spread": 0.20,
            "open_trades": 0,
            "open_for_symbol": 0,
            "max_open": 6,
            "max_per_symbol": 3,
            "htf_bias": "LONG",
            "m1_direction": "LONG",
            "setup": sig["setup"],
            "entry": 2720.0,
        }
        ok, evaluated_sig, reasons = cba.evaluate(sig, ctx)
        self.assertTrue(ok, f"Expected OK but got reasons: {reasons}")
        self.assertIn("structure_ok", reasons)
        self.assertNotIn("setup_blocked", ", ".join(reasons))

    def test_balanced_setup_preserves_structural_brackets_without_risk(self):
        """CombinedBotAgent must NOT overwrite balanced structural brackets with micro scalp levels ($2.50/$3.00)."""
        cba = CombinedBotAgent()
        entry_px = 2720.0
        structural_sl = 2705.0  # 15.00 safe structural stop
        structural_tp1 = 2750.0  # 30.00 safe structural target (RR = 2.0)

        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": entry_px,
            "stop": structural_sl,
            "stop_loss": structural_sl,
            "tp": structural_tp1,
            "tp1": structural_tp1,
            "is_scalp": False,
            "rr": 2.0,
            "setup": "balanced_pullback",
        }
        ctx = {
            "spread": 0.20,
            "open_trades": 0,
            "open_for_symbol": 0,
            "max_open": 6,
            "max_per_symbol": 3,
            "htf_bias": "LONG",
            "m1_direction": "LONG",
            "setup": "balanced_pullback",
            "entry": entry_px,
        }
        ok, res_sig, reasons = cba.evaluate(sig, ctx)
        self.assertTrue(ok)
        # Verify structural levels are intact
        self.assertEqual(res_sig["sl"], structural_sl)
        self.assertEqual(res_sig["stop_loss"], structural_sl)
        self.assertEqual(res_sig["tp"], structural_tp1)
        self.assertEqual(res_sig["tp1"], structural_tp1)
        self.assertGreaterEqual(res_sig["rr"], 1.90)

    def test_balanced_profile_risk_parameters_and_execution_engine(self):
        """Applying balanced profile sets controlled risk parameters and limit order behavior."""
        prof, summary = apply_profile("balanced", engine=self.engine, risk_mgr=self.rm, config=config)
        self.assertEqual(prof.name, "balanced")
        self.assertFalse(prof.is_scalp)
        self.assertFalse(prof.fast_market_fill)
        self.assertEqual(prof.base_risk_pct, 0.25)  # 0.25% low equity risk
        self.assertGreaterEqual(prof.min_rr, 1.90)  # High R:R discipline
        self.assertFalse(config.SCALP_ENABLED)
        self.assertFalse(self.engine.scalp_mode)

        # Register balanced limit order setup
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2715.0,  # Planned limit pullback entry below current market
            "stop": 2700.0,
            "tp1": 2745.0,
            "tp2": 2760.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "risk_amount": 25.0,
            "rr": 2.0,
            "is_scalp": False,
            "fast_market_fill": False,
        }
        order = self.engine.register_setup(sig, reason="Balanced limit entry")
        # Registered as pending limit, NOT instantly market filled
        self.assertIn(order.id, self.engine.pending_setups)
        self.assertEqual(order.status, OrderStatus.PENDING)

        # Market is currently at 2722.0 (above entry) -> Should not execute yet
        c1 = {"open": 2721.0, "high": 2723.0, "low": 2720.0, "close": 2722.0}
        ev1 = self.engine.update_market_candle("XAUUSD", c1)
        self.assertFalse(any(e["event"] == "TRADE_EXECUTED" for e in ev1))
        self.assertIn(order.id, self.engine.pending_setups)

        # Market touches entry level (2715.0) -> Triggers execution safely
        self.engine.broker = MagicMock()
        mock_fill = MagicMock(status="filled", filled_qty=0.02, filled_price=2715.0)
        self.engine.broker.place_order.return_value = mock_fill

        c2 = {"open": 2717.0, "high": 2718.0, "low": 2714.5, "close": 2715.0}
        ev2 = self.engine.update_market_candle("XAUUSD", c2)
        self.assertTrue(any(e["event"] == "TRADE_EXECUTED" for e in ev2))
        self.assertIn(order.id, self.engine.active_positions)
        self.assertEqual(order.status, OrderStatus.OPEN)


if __name__ == "__main__":
    unittest.main()
