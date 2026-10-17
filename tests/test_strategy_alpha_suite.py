#!/usr/bin/env python3
"""
Comprehensive Unit Tests for Advanced Strategy & Execution Alpha Suite:
1. End-of-Session Performance Reports (session_reporter.py)
2. Multi-Timeframe FVG & Order Block Confluence (mtf_fvg_ob.py)
3. Asian Range & London Open Breakout (asian_range_breakout.py)
4. Virtual / Hidden Stops (virtual_stops.py)
5. Execution Latency & Realized Slippage Tracker (execution_analytics.py)
6. Regime Auto-Switcher & Rolling Sharpe Calibrator (regime_auto_switcher.py)
7. Fractional Kelly Dynamic Sizing & VaR 99% (kelly_criterion.py)
8. Multi-Agent Consensus Visualizer (consensus_visualizer.py)
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, time, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from asian_range_breakout import (
    AsianBreakoutAgent,
    AsianDir,
    compute_asian_range,
    detect_asian_london_setup,
)
from consensus_visualizer import format_agent_consensus_matrix
from execution_analytics import ExecutionAnalyticsTracker
from kelly_criterion import (
    calculate_kelly_fraction,
    calculate_kelly_lot_size,
    compute_value_at_risk,
)
from mtf_fvg_ob import (
    MTFConfluenceAgent,
    MTFDir,
    evaluate_mtf_confluence,
    extract_fair_value_gaps,
    extract_order_blocks,
)
from regime_auto_switcher import (
    RegimeAutoSwitcher,
    calibrate_rolling_sharpe_weights,
    compute_regime_indicators,
)
from session_reporter import (
    compile_session_summary,
    format_session_telegram_message,
)
from virtual_stops import VirtualStopManager


def _make_df(n: int = 50, base_price: float = 2650.0, freq: str = "1min") -> pd.DataFrame:
    prices = [base_price + i * 0.1 for i in range(n)]
    dates = pd.date_range("2026-10-17 08:00:00", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.4 for p in prices],
        "low": [p - 0.4 for p in prices],
        "close": [p + 0.05 for p in prices],
        "volume": [1000.0] * n,
    }, index=dates)


class TestSessionReporter(unittest.TestCase):

    def test_summary_compilation(self):
        sample_trades = [
            {"symbol": "XAUUSD", "pnl": 50.0, "r_multiple": 1.5, "closed_at": "2026-10-17T09:30:00Z"},
            {"symbol": "BTCUSDT", "pnl": 75.0, "r_multiple": 2.0, "closed_at": "2026-10-17T11:00:00Z"},
            {"symbol": "XAUUSD", "pnl": -25.0, "r_multiple": -1.0, "closed_at": "2026-10-17T14:15:00Z"},
        ]
        summary = compile_session_summary("LONDON", trades=sample_trades)
        self.assertEqual(summary.total_trades, 3)
        self.assertEqual(summary.wins, 2)
        self.assertEqual(summary.losses, 1)
        self.assertAlmostEqual(summary.win_rate_pct, 66.7, places=1)
        self.assertAlmostEqual(summary.net_pnl_usd, 100.0)
        self.assertAlmostEqual(summary.total_r, 2.5)
        self.assertAlmostEqual(summary.profit_factor, 5.0)  # 125 / 25
        self.assertEqual(summary.best_trade_pnl, 75.0)

        msg = format_session_telegram_message(summary)
        self.assertIn("END-OF-SESSION PERFORMANCE REPORT: LONDON SESSION", msg)
        self.assertIn("66.7%", msg)
        self.assertIn("+$100.00", msg)


class TestMTFFVGAndOrderBlock(unittest.TestCase):

    def test_fvg_extraction(self):
        df = _make_df(30, 2650.0)
        # Create clear Bullish FVG: candle 10 high < candle 12 low
        df.iloc[10, df.columns.get_loc("high")] = 2648.0
        df.iloc[11, df.columns.get_loc("low")] = 2649.0
        df.iloc[11, df.columns.get_loc("high")] = 2654.0
        df.iloc[12, df.columns.get_loc("low")] = 2651.0  # Gap between 2648.0 and 2651.0

        fvgs = extract_fair_value_gaps(df, "1H")
        self.assertGreater(len(fvgs), 0)
        bullish_fvg = [f for f in fvgs if f.direction == MTFDir.BULLISH]
        self.assertGreater(len(bullish_fvg), 0)
        self.assertAlmostEqual(bullish_fvg[0].bottom, 2648.0)
        self.assertAlmostEqual(bullish_fvg[0].top, 2651.0)

    def test_order_block_extraction(self):
        df = _make_df(30, 2650.0)
        # Down candle at 15
        df.iloc[15, df.columns.get_loc("open")] = 2652.0
        df.iloc[15, df.columns.get_loc("close")] = 2648.0
        df.iloc[15, df.columns.get_loc("high")] = 2653.0
        df.iloc[15, df.columns.get_loc("low")] = 2647.0
        # Aggressive up displacement at 16
        df.iloc[16, df.columns.get_loc("open")] = 2648.0
        df.iloc[16, df.columns.get_loc("close")] = 2656.0  # Closes above cur_h
        df.iloc[16, df.columns.get_loc("high")] = 2657.0

        obs = extract_order_blocks(df, "1H")
        self.assertGreater(len(obs), 0)
        bull_ob = [o for o in obs if o.direction == MTFDir.BULLISH]
        self.assertGreater(len(bull_ob), 0)
        self.assertEqual(bull_ob[0].low, 2647.0)

    def test_mtf_confluence_agent(self):
        agent = MTFConfluenceAgent()
        df1 = _make_df(20, 2650.0)
        vote = agent.vote(df1)
        self.assertEqual(vote.agent, "MTF-CONFLUENCE")
        self.assertIn(vote.direction, (MTFDir.BULLISH, MTFDir.BEARISH, MTFDir.NEUTRAL))


class TestAsianRangeBreakout(unittest.TestCase):

    def test_asian_range_computation(self):
        # 24 hours of 1-hour candles
        dates = pd.date_range("2026-10-17 00:00:00", periods=24, freq="1h", tz="UTC")
        prices = [2650.0 + i for i in range(24)]
        df = pd.DataFrame({
            "open": prices,
            "high": [p + 2.0 for p in prices],
            "low": [p - 2.0 for p in prices],
            "close": prices,
            "volume": [1000.0] * 24,
        }, index=dates)

        asian = compute_asian_range(df)
        self.assertTrue(asian.valid)
        self.assertGreater(asian.high, 0.0)
        self.assertGreater(asian.low, 0.0)
        self.assertAlmostEqual(asian.mid, (asian.high + asian.low) / 2.0)
        self.assertGreater(asian.range_pts, 0.0)

    def test_asian_breakout_agent(self):
        agent = AsianBreakoutAgent()
        df = _make_df(30, 2650.0)
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "ASIAN-BREAKOUT")
        self.assertIn(vote.direction, (AsianDir.LONG, AsianDir.SHORT, AsianDir.FLAT))


class TestVirtualStops(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_vstop_"))
        self.state_file = self.tmp_dir / "vstops.json"
        self.mgr = VirtualStopManager(self.state_file)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_virtual_stop_breach_long(self):
        self.mgr.register(
            symbol="XAUUSD",
            ticket="1001",
            direction="BUY",
            entry_price=2650.0,
            virtual_stop=2642.0,
            virtual_tp1=2665.0,
        )
        # Price safe at 2645.0
        trig = self.mgr.check_ticks("XAUUSD", 2645.0)
        self.assertEqual(len(trig), 0)

        # Price ticks below virtual stop to 2641.50
        trig = self.mgr.check_ticks("XAUUSD", 2641.50)
        self.assertEqual(len(trig), 1)
        rec, reason = trig[0]
        self.assertEqual(rec.ticket, "1001")
        self.assertIn("Stop Loss triggered", reason)
        self.assertFalse(rec.active)

    def test_virtual_tp_breach_short(self):
        self.mgr.register(
            symbol="BTCUSDT",
            ticket="2002",
            direction="SELL",
            entry_price=68000.0,
            virtual_stop=68800.0,
            virtual_tp1=66500.0,
        )
        # Price ticks down to TP1
        trig = self.mgr.check_ticks("BTCUSDT", 66450.0)
        self.assertEqual(len(trig), 1)
        rec, reason = trig[0]
        self.assertIn("Take Profit triggered", reason)


class TestExecutionAnalytics(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_analytics_"))
        self.file = self.tmp_dir / "analytics.json"
        self.tracker = ExecutionAnalyticsTracker(self.file)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_record_fill_and_report(self):
        # Buy filled at higher price (adverse)
        rec1 = self.tracker.record_fill(
            ticket="101",
            symbol="XAUUSD",
            action="BUY",
            requested_price=2650.0,
            executed_price=2650.4,
            volume=0.1,
            latency_ms=45.2,
        )
        self.assertTrue(rec1.is_adverse)
        self.assertAlmostEqual(rec1.slippage_pts, 0.4)

        # Sell filled at higher price (favorable)
        rec2 = self.tracker.record_fill(
            ticket="102",
            symbol="XAUUSD",
            action="SELL",
            requested_price=2650.0,
            executed_price=2650.3,
            volume=0.1,
            latency_ms=62.0,
        )
        self.assertFalse(rec2.is_adverse)

        report = self.tracker.generate_report()
        self.assertEqual(report.total_executions, 2)
        self.assertGreater(report.avg_latency_ms, 0.0)
        self.assertIn(report.execution_rating, ("A+", "A", "B", "C", "F"))


class TestRegimeAutoSwitcher(unittest.TestCase):

    def test_compute_regime_indicators(self):
        df = _make_df(35, 2650.0)
        metrics = compute_regime_indicators(df)
        self.assertIn("adx", metrics)
        self.assertIn("atr_pct", metrics)
        self.assertIn("bb_width", metrics)
        self.assertGreater(metrics["adx"], 0.0)

    def test_evaluate_and_switch(self):
        switcher = RegimeAutoSwitcher(cooldown_seconds=0.0)
        df = _make_df(35, 2650.0)
        res = switcher.evaluate_and_switch(df, symbol="XAUUSD")
        self.assertIsNotNone(res.current_profile)
        self.assertIn("adx", res.metrics)

    def test_calibrate_rolling_sharpe_weights(self):
        weights = calibrate_rolling_sharpe_weights()
        self.assertIn("TREND", weights)
        self.assertIn("FAKE-BREAKOUT", weights)
        self.assertGreater(weights["FAKE-BREAKOUT"], 0.0)


class TestKellyCriterionAndVaR(unittest.TestCase):

    def test_fractional_kelly_sizing(self):
        # 60% win rate, 1.8 payoff ratio
        raw_f, applied_f = calculate_kelly_fraction(0.60, 1.80, fraction_mult=0.25)
        self.assertGreater(raw_f, 0.0)
        self.assertGreater(applied_f, 0.0)
        self.assertLessEqual(applied_f, raw_f)

        res = calculate_kelly_lot_size(
            equity=10000.0,
            entry_price=2650.0,
            stop_loss_price=2645.0,
            win_rate=0.60,
            payoff_ratio=1.80,
            fraction_mult=0.25,
        )
        self.assertGreater(res.recommended_lots, 0.0)
        self.assertGreater(res.dollar_risk, 0.0)
        self.assertLessEqual(res.risk_pct_of_equity, 3.0)

    def test_value_at_risk_computation(self):
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.015, 100)
        var_res = compute_value_at_risk(returns, portfolio_equity=10000.0, confidence_level=0.99)
        self.assertEqual(var_res.confidence_level, 0.99)
        self.assertGreater(var_res.parametric_var_usd, 0.0)
        self.assertGreater(var_res.historical_var_usd, 0.0)
        self.assertGreater(var_res.expected_shortfall_cvar_usd, 0.0)


class TestConsensusVisualizer(unittest.TestCase):

    def test_visualizer_matrix_rendering(self):
        from consensus_visualizer import DEFAULT_WEIGHTS
        self.assertEqual(DEFAULT_WEIGHTS.get("MTF-CONFLUENCE"), 1.40)
        self.assertEqual(DEFAULT_WEIGHTS.get("ASIAN-BREAKOUT"), 1.25)

        votes = [
            {"agent": "TREND", "direction": "LONG", "strength": 1.0},
            {"agent": "LIQUIDITY", "direction": "LONG", "strength": 0.85},
            {"agent": "FAKE-BREAKOUT", "direction": "LONG", "strength": 0.90},
            {"agent": "MTF-CONFLUENCE", "direction": "LONG", "strength": 0.80},
            {"agent": "ASIAN-BREAKOUT", "direction": "LONG", "strength": 0.80},
            {"agent": "INDICATORS", "direction": "FLAT", "strength": 0.0},
        ]
        matrix = format_agent_consensus_matrix(votes)
        self.assertIn("MULTI-AGENT CONSENSUS MATRIX", matrix)
        self.assertIn("TREND", matrix)
        self.assertIn("FAKE-BREAKOUT", matrix)
        self.assertIn("MTF-CONFLUENCE", matrix)
        self.assertIn("ASIAN-BREAKOUT", matrix)
        self.assertIn("CONSENSUS BUY", matrix)


if __name__ == "__main__":
    unittest.main()
