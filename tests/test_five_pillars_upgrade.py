#!/usr/bin/env python3
"""
Unit tests for the 5 Key Pillars Upgrade:
1. Advanced & Accurate Multi-Agent Level Entry (across all profiles).
2. Auto-Adjusting Trade Lifecycle (TP, SL, Trailing, TP3 runners).
3. Post-Trade Close Cooldown Workflow & Next Setup Pre-Planning.
4. Elevated Agent Intelligence (MTF alignment, structural levels).
5. Unified Collective Consensus (No Agent Left Behind).
"""
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

from entry_level_planner import (
    EntryPlan,
    plan_best_entry,
    attach_plan_to_signal,
    _mtf_confluence_levels,
    _institutional_pivot_levels,
    _ote_fibonacci_levels,
    _supply_demand_zone_levels,
)
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager
from multi_agent_trading_bot import Arbiter, Dir, Vote, TrendAgent, MT5ChartAgent
from autotrade_worker import execute_post_trade_cooldown_workflow
from anti_spam import get_anti_spam_limiter


def _make_sample_df(mark: float = 2650.0, n: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n, freq="1min")
    closes = np.linspace(mark - 15.0, mark, n)
    highs = closes + 2.5
    lows = closes - 2.5
    opens = closes - 0.5
    volumes = np.full(n, 100.0)
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=dates)


def _make_order(**kwargs) -> TradeOrder:
    defaults = {
        "id": "order_test",
        "symbol": "XAUUSD",
        "direction": "LONG",
        "entry_level": 2650.0,
        "stop_loss": 2645.0,
        "tp1": 2655.0,
        "tp2": 2665.0,
        "tp3": 2675.0,
        "lots": 0.10,
        "size": 1.0,
        "leverage": 20.0,
        "margin_required": 132.5,
        "liquidation_price": 2500.0,
        "risk_amount": 50.0,
        "rr_ratio": 2.0,
        "setup_reason": "Test setup",
        "remaining_size": 1.0,
        "remaining_lots": 0.10,
        "status": OrderStatus.OPEN,
    }
    defaults.update(kwargs)
    return TradeOrder(**defaults)


class TestFivePillarsUpgrade(unittest.TestCase):
    def setUp(self):
        get_anti_spam_limiter().reset()
        self.risk = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.risk)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    def tearDown(self):
        get_anti_spam_limiter().reset()
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()

    # -------------------------------------------------------------------------
    # PILLAR 1: Advanced & Accurate Multi-Agent Level Entry across Profiles
    # -------------------------------------------------------------------------
    def test_pillar1_ote_and_pivot_levels_extracted(self):
        df = _make_sample_df(2650.0, 60)
        # OTE Golden Pocket
        ote_long = _ote_fibonacci_levels(df, "LONG")
        self.assertIn("ote_618", ote_long)
        self.assertIn("ote_705", ote_long)
        self.assertIn("ote_786", ote_long)
        self.assertLess(ote_long["ote_786"], ote_long["ote_618"])

        # Institutional Floor Pivots
        pivots = _institutional_pivot_levels("XAUUSD", df)
        self.assertIn("mt5_pivot", pivots)
        self.assertIn("mt5_r1", pivots)
        self.assertIn("mt5_s1", pivots)
        self.assertGreater(pivots["mt5_r1"], pivots["mt5_pivot"])
        self.assertLess(pivots["mt5_s1"], pivots["mt5_pivot"])

    def test_pillar1_swing_profile_expanded_rr_and_structural_targets(self):
        df = _make_sample_df(2650.0, 60)
        plan_swing = plan_best_entry(
            symbol="XAUUSD",
            df=df,
            direction="LONG",
            mark=2650.0,
            profile="swing",
        )
        self.assertEqual(plan_swing.direction, "LONG")
        self.assertGreaterEqual(plan_swing.rr, 2.5)
        self.assertIsNotNone(plan_swing.tp3)
        self.assertGreater(plan_swing.tp3, plan_swing.tp2)

    def test_pillar1_profile_tailoring_gold_and_crypto(self):
        df_gold = _make_sample_df(2650.0, 60)
        plan_gold = plan_best_entry("XAUUSD", df_gold, direction="LONG", mark=2650.0, profile="gold_only")
        # Gold has minimum spread buffer to absorb wicks
        self.assertGreaterEqual(plan_gold.best_entry - plan_gold.stop, 0.40)

        df_btc = _make_sample_df(82000.0, 60)
        plan_btc = plan_best_entry("BTCUSDT", df_btc, direction="LONG", mark=82000.0, profile="crypto")
        # Crypto has dynamic buffer for BTC volatility
        self.assertGreaterEqual(plan_btc.best_entry - plan_btc.stop, 12.0)

    # -------------------------------------------------------------------------
    # PILLAR 2: Dynamic Auto-Adjusting Trade Lifecycle (TP, SL, TP3)
    # -------------------------------------------------------------------------
    def test_pillar2_tp3_final_runner_target_hit(self):
        """When TP3 runner target is reached, position closes with full cycle completion."""
        pos = _make_order(
            id="test_tp3_order",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            executed_price=2650.0,
            stop_loss=2648.0,
            tp1=2655.0,
            tp2=2660.0,
            tp3=2675.0,
            size=1.0,
            remaining_size=0.20,
            lots=0.10,
            remaining_lots=0.02,
            partials_done=2,
            status=OrderStatus.OPEN,
        )
        self.engine.active_positions[pos.id] = pos

        mock_broker = MagicMock()
        mock_broker.close_position.return_value = True
        self.engine.broker = mock_broker

        candle = {
            "open": 2670.0,
            "high": 2676.0,  # Reaches TP3 (2675.0)
            "low": 2669.0,
            "close": 2675.5,
        }
        events = self.engine.update_market_candle("XAUUSD", candle)

        self.assertTrue(any(ev.get("event") == "TP3_CLOSE" for ev in events))
        self.assertEqual(pos.status, OrderStatus.CLOSED)
        self.assertIn("TP3 Runner Final Hit", pos.close_reason)
        self.assertEqual(len(self.engine.active_positions), 0)

    def test_pillar2_dynamic_structure_trailing_tightens_stop(self):
        """Structure trailing ratchets stop loss behind swing pivots."""
        from execution_enhancements import apply_structure_trail
        pos = _make_order(
            id="test_trail_order",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            executed_price=2650.0,
            stop_loss=2645.0,
            tp1=2655.0,
            tp2=2665.0,
            size=1.0,
            remaining_size=0.50,
            partials_done=1,
            status=OrderStatus.OPEN,
            highest_price=2658.0,
        )
        pos.recent_lows = [2650.0, 2652.0, 2653.0, 2654.0]
        pos.recent_highs = [2652.0, 2655.0, 2657.0, 2658.0]

        ev = apply_structure_trail(pos, atr_value=1.5, current_price=2657.0)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event"], "STRUCTURE_TRAIL")
        self.assertGreater(pos.stop_loss, 2645.0)
        self.assertLess(pos.stop_loss, 2657.0)

    # -------------------------------------------------------------------------
    # PILLAR 3: Post-Trade Close Cooldown Workflow & Next Setup Pre-Planning
    # -------------------------------------------------------------------------
    def test_pillar3_trade_closed_records_telemetry_for_cooldown(self):
        """Position close records detailed telemetry in engine._last_closed_trade."""
        pos = _make_order(
            id="order_close_test",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            executed_price=2650.0,
            exit_price=2656.0,
            stop_loss=2645.0,
            realized_pnl=60.0,
            status=OrderStatus.CLOSED,
            close_reason="Target 1 Hit",
        )
        self.engine._notify_trade_closed(pos, r_mult=1.5)

        self.assertIn("XAUUSD", self.engine._last_closed_trade)
        telemetry = self.engine._last_closed_trade["XAUUSD"]
        self.assertEqual(telemetry["symbol"], "XAUUSD")
        self.assertEqual(telemetry["direction"], "LONG")
        self.assertEqual(telemetry["realized_pnl"], 60.0)
        self.assertEqual(telemetry["r_multiple"], 1.5)
        self.assertTrue(telemetry["won"])

    def test_pillar3_execute_post_trade_cooldown_workflow(self):
        """During cooldown, execute workflow reassesses structure and stages next entry plan."""
        import asyncio
        df = _make_sample_df(2650.0, 60)
        data = {"1m": df}
        candle_dict = {"open": 2649.0, "high": 2651.0, "low": 2648.5, "close": 2650.5}
        last_trade = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "realized_pnl": 45.0,
            "r_multiple": 1.5,
            "close_reason": "Scalp Target 1 Hit",
        }

        report = asyncio.run(execute_post_trade_cooldown_workflow(
            symbol="XAUUSD",
            engine=self.engine,
            arbiter=Arbiter(),
            risk_mgr=self.risk,
            df1=df,
            data=data,
            candle_dict=candle_dict,
            last_trade=last_trade,
            profile="scalp",
        ))

        self.assertIsNotNone(report)
        self.assertIn("POST-TRADE COOLDOWN WORKFLOW", report)
        self.assertIn("XAUUSD", report)
        self.assertIn("Staged Next Setup", report)

        # Pre-calculated setup is safely staged in engine._staged_next_setups
        self.assertIn("XAUUSD", self.engine._staged_next_setups)
        staged = self.engine._staged_next_setups["XAUUSD"]
        self.assertIsInstance(staged, EntryPlan)
        self.assertGreater(staged.best_entry, 0.0)

    # -------------------------------------------------------------------------
    # PILLAR 4: Elevate All Agents' Intelligence
    # -------------------------------------------------------------------------
    def test_pillar4_trend_agent_mtf_alignment(self):
        """TrendAgent elevates strength when M1, M15, and H1 align."""
        agent = TrendAgent()
        df1 = _make_sample_df(2650.0, 60)
        df15 = _make_sample_df(2650.0, 60)
        df1h = _make_sample_df(2650.0, 60)

        vote = agent.vote(df1, df15=df15, df1h=df1h)
        self.assertEqual(vote.agent, "TREND")
        # When all three align, strength is boosted to 0.85
        if vote.direction != Dir.FLAT:
            self.assertGreaterEqual(vote.strength, 0.65)
            self.assertIn("Multi-timeframe", vote.reason)

    def test_pillar4_mt5_chart_agent_outputs_structural_levels(self):
        """MT5ChartAgent provides stop_price, tp_price, and entry_price in its vote."""
        agent = MT5ChartAgent()
        df1 = _make_sample_df(2650.0, 60)
        vote = agent.vote(df1, symbol="XAUUSD")
        self.assertEqual(vote.agent, "MT5-CHART")
        if vote.direction != Dir.FLAT:
            self.assertIsNotNone(vote.stop_price)
            self.assertIsNotNone(vote.tp_price)
            self.assertIsNotNone(vote.entry_price)

    # -------------------------------------------------------------------------
    # PILLAR 5: Unified Collective Consensus
    # -------------------------------------------------------------------------
    def test_pillar5_arbiter_synthesizes_collective_agent_levels(self):
        """Arbiter synthesizes all agent proposed levels into the EntryPlan."""
        arb = Arbiter()
        arb.is_scalp = False
        df1 = _make_sample_df(2650.0, 60)

        class MockLevelAgent:
            def __init__(self, name, dir_, stop, tp, entry):
                self.name = name
                self.dir_ = dir_
                self.stop = stop
                self.tp = tp
                self.entry = entry
            def vote(self, *a, **k):
                return Vote(self.name, self.dir_, 0.92, "Consensus", stop_price=self.stop, tp_price=self.tp, entry_price=self.entry)

        arb.trend = MockLevelAgent("TREND", Dir.LONG, 2640.0, 2665.0, 2648.0)
        arb.liq = MockLevelAgent("LIQUIDITY", Dir.LONG, 2638.0, 2670.0, 2646.0)
        arb.ind = MockLevelAgent("INDICATORS", Dir.LONG, 2641.0, 2660.0, 2649.0)
        arb.ai = MockLevelAgent("AI-LEARNER", Dir.LONG, 2640.0, 2665.0, 2648.0)
        arb.news = MockLevelAgent("NEWS", Dir.LONG, 2640.0, 2665.0, 2648.0)

        sig, why, votes = arb.decide(df1, None, None, None, None, self.risk, symbol="XAUUSD", on_demand=True)
        self.assertIsNotNone(sig)
        self.assertTrue(sig.get("is_perfect_entry", False))
        self.assertIn("entry_plan", sig)
        eplan = sig["entry_plan"]
        # Collective agent levels were injected into levels dict
        levels = eplan.get("levels", {})
        self.assertTrue(any("agent_" in k or "trend_" in k or "liquidity_" in k for k in levels))


if __name__ == "__main__":
    unittest.main()
