"""
Unit tests for the 150 Specialized Strategy Agents and 168-Agent Collective Consensus Swarm.

Verifies:
1. All 150 agents registered across 15 strategic clusters.
2. Every agent returns a valid Vote with direction, strength, reason, and key levels.
3. MarketContext aggregates all institutional pillars cleanly in memory.
4. Fast execution (< 25ms) for all 150 agents.
5. Integration into Arbiter yielding 168 total consensus votes.
6. Collective level planning with entry, stop loss, and take profit confluence.
7. Directional responsiveness in bullish vs bearish synthetic market regimes.
8. Cluster integrity and agent count verification.
"""
import time
import unittest
import numpy as np
import pandas as pd

from agent_swarm import (
    AgentSwarm,
    BaseSwarmAgent,
    Dir,
    MarketContext,
    Vote,
    build_market_context,
    get_agent_swarm,
)
from multi_agent_trading_bot import (
    Arbiter,
    MarketDataFeed,
    evaluate_symbol,
    Dir as BotDir,
)
from risk_manager import RiskManager


class TestAgentSwarm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 100-bar synthetic upward trend dataframe
        cls.dates = pd.date_range("2026-10-21 00:00", periods=100, freq="1min")
        cls.df_bull = pd.DataFrame(
            {
                "open": [2000.0 + i * 0.5 for i in range(100)],
                "high": [2003.0 + i * 0.5 for i in range(100)],
                "low": [1999.0 + i * 0.5 for i in range(100)],
                "close": [2002.0 + i * 0.5 for i in range(100)],
                "volume": [500.0 + i * 10 for i in range(100)],
            },
            index=cls.dates,
        )

        # 100-bar synthetic downward trend dataframe
        cls.df_bear = pd.DataFrame(
            {
                "open": [2050.0 - i * 0.5 for i in range(100)],
                "high": [2052.0 - i * 0.5 for i in range(100)],
                "low": [2047.0 - i * 0.5 for i in range(100)],
                "close": [2048.0 - i * 0.5 for i in range(100)],
                "volume": [500.0 + i * 10 for i in range(100)],
            },
            index=cls.dates,
        )

        cls.risk = RiskManager(equity=10000.0, symbol="XAUUSD")
        cls.swarm = get_agent_swarm()

    def test_total_swarm_agent_count_and_clusters(self):
        """Verify exactly 150 specialized strategy agents across the 15 strategic clusters."""
        summary = self.swarm.get_summary()
        self.assertEqual(summary["total_agents"], 150)
        self.assertEqual(len(self.swarm.agents), 150)

        expected_clusters = {
            "ICT_SMC": 12,
            "MTF_MATRIX": 10,
            "PIVOTS": 10,
            "FIBONACCI": 10,
            "SUPPLY_DEMAND": 10,
            "VOLATILITY": 10,
            "MOMENTUM": 10,
            "SESSION_MACRO": 10,
            "MEAN_REVERSION": 10,
            "AUTONOMOUS_BRAIN": 8,
            "VOLUME_SPREAD_ANALYSIS": 10,
            "MICROSTRUCTURE": 10,
            "ELLIOTT_CYCLES": 10,
            "MACRO_INTERMARKET": 10,
            "QUANT_STAT_ARB": 10,
        }
        self.assertEqual(summary["clusters"], expected_clusters)

    def test_market_context_aggregates_all_pillars(self):
        """Verify MarketContext precomputes all institutional pillars."""
        ctx = build_market_context("XAUUSD", self.df_bull)
        self.assertEqual(ctx.symbol, "XAUUSD")
        self.assertGreater(ctx.mark, 2000.0)
        self.assertGreater(ctx.atr, 0.0)
        # Pivots
        self.assertGreater(ctx.pivot, 0.0)
        self.assertGreater(ctx.r1, ctx.s1)
        self.assertGreater(ctx.cam_h4, ctx.cam_l4)
        # ICT / SMC
        self.assertGreater(ctx.ote_618, 0.0)
        self.assertGreater(ctx.swing_high, ctx.swing_low)
        # Moving Averages & Oscillators
        self.assertGreater(ctx.ema9, 0.0)
        self.assertIn(ctx.trend_stacked, (-1, 0, 1))
        self.assertGreater(ctx.rsi, 0.0)
        self.assertLess(ctx.rsi, 100.0)

    def test_swarm_sub_second_fast_execution(self):
        """Verify all 150 agents evaluate within 25 milliseconds."""
        ctx = build_market_context("XAUUSD", self.df_bull)
        t0 = time.time()
        votes = self.swarm.evaluate(ctx)
        t_elapsed = (time.time() - t0) * 1000.0

        self.assertEqual(len(votes), 150)
        self.assertLess(t_elapsed, 25.0, f"Swarm evaluation too slow: {t_elapsed:.2f}ms")

    def test_every_agent_emits_valid_vote_and_levels(self):
        """Verify every agent outputs valid directional votes, reasons, and structural levels."""
        ctx = build_market_context("XAUUSD", self.df_bull)
        votes = self.swarm.evaluate(ctx)

        for v in votes:
            self.assertIsInstance(v.agent, str)
            self.assertIn(v.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
            self.assertGreaterEqual(v.strength, 0.0)
            self.assertLessEqual(v.strength, 1.0)
            self.assertIsInstance(v.reason, str)
            if v.direction != Dir.FLAT:
                if v.stop_price is not None:
                    self.assertGreater(float(v.stop_price), 0.0)
                if v.tp_price is not None:
                    self.assertGreater(float(v.tp_price), 0.0)

    def test_directional_conviction_bullish_vs_bearish(self):
        """Verify swarm shifts conviction appropriately based on market structure."""
        ctx_bull = build_market_context("XAUUSD", self.df_bull)
        votes_bull = self.swarm.evaluate(ctx_bull)
        bull_longs = sum(1 for v in votes_bull if v.direction == Dir.LONG)
        bull_shorts = sum(1 for v in votes_bull if v.direction == Dir.SHORT)

        ctx_bear = build_market_context("XAUUSD", self.df_bear)
        votes_bear = self.swarm.evaluate(ctx_bear)
        bear_longs = sum(1 for v in votes_bear if v.direction == Dir.LONG)
        bear_shorts = sum(1 for v in votes_bear if v.direction == Dir.SHORT)

        self.assertGreater(bull_longs, bull_shorts)
        self.assertGreater(bear_shorts, bear_longs)

    def test_arbiter_yields_168_collective_consensus_votes(self):
        """Verify Arbiter integrates the 18 institutional agents + 150 swarm agents (168 total)."""
        arbiter = Arbiter()
        sig, msg, votes = arbiter.decide(
            self.df_bull, None, None, None, None, self.risk, symbol="XAUUSD", on_demand=True, include_swarm=True
        )

        self.assertEqual(len(votes), 168)
        agent_names = {v.agent for v in votes}

        # Check existing institutional agents
        institutional_baseline = {
            "TREND", "LIQUIDITY", "INDICATORS", "AI-LEARNER", "NEWS",
            "MT5-CHART", "ORR-STRATEGY", "SUPPLY-DEMAND", "OTE-STRATEGY",
            "VOLATILITY-SQUEEZE", "RIBBON-SCALP", "STOCH-RSI-SCALP", "AUTO-TREND-1M"
        }
        for ib in institutional_baseline:
            self.assertIn(ib, agent_names)

        # Check presence of representative swarm agents across clusters
        self.assertIn("ICT-BOS-CHOCH", agent_names)
        self.assertIn("MTF-HTF-BIAS", agent_names)
        self.assertIn("PIVOT-CAMARILLA", agent_names)
        self.assertIn("FIB-GOLDEN-POCKET-DEEP", agent_names)
        self.assertIn("SD-DROP-BASE-RALLY", agent_names)
        self.assertIn("BB-SQUEEZE-EXPANSION", agent_names)
        self.assertIn("RSI-REGULAR-DIVERGENCE", agent_names)
        self.assertIn("SESSION-LONDON-OPEN", agent_names)
        self.assertIn("VWAP-STANDARD-DEV-2", agent_names)
        self.assertIn("BRAIN-REGIME-ADAPT", agent_names)
        # 5 New Clusters
        self.assertIn("VSA-STOPPING-VOLUME", agent_names)
        self.assertIn("MICRO-BID-ASK-IMBALANCE", agent_names)
        self.assertIn("ELLIOTT-WAVE-3-IMPULSE", agent_names)
        self.assertIn("MACRO-DXY-INVERSE-BIAS", agent_names)
        self.assertIn("QUANT-KALMAN-FILTER-TREND", agent_names)

    def test_collective_agent_level_planning_synthesis(self):
        """Verify collective agent levels from swarm agents populate perfect entry planning."""
        arbiter = Arbiter()
        arbiter.is_scalp = False  # Balanced / swing mode synthesizes perfect entry planning
        sig, msg, votes = arbiter.decide(
            self.df_bull, None, None, None, None, self.risk, symbol="XAUUSD", on_demand=True, include_swarm=True
        )

        self.assertIsNotNone(sig)
        self.assertIn("entry_plan", sig)
        ep = sig["entry_plan"]
        self.assertGreater(ep.get("best_entry", 0.0), 0.0)
        self.assertGreater(ep.get("stop", 0.0), 0.0)
        self.assertGreater(ep.get("tp1", 0.0), 0.0)
        self.assertIn("levels", ep)
        self.assertGreater(len(ep["levels"]), 0)

    def test_empty_or_minimal_dataframe_fallback(self):
        """Verify swarm handles minimal/empty DataFrames safely without exceptions."""
        df_empty = pd.DataFrame()
        ctx_empty = build_market_context("XAUUSD", df_empty)
        votes = self.swarm.evaluate(ctx_empty)
        self.assertEqual(len(votes), 150)

        df_short = pd.DataFrame({"close": [2000.0, 2001.0, 2002.0], "open": [1999.0, 2000.0, 2001.0], "high": [2002.0, 2003.0, 2004.0], "low": [1998.0, 1999.0, 2000.0]})
        ctx_short = build_market_context("XAUUSD", df_short)
        votes_short = self.swarm.evaluate(ctx_short)
        self.assertEqual(len(votes_short), 150)

    def test_evaluate_symbol_returns_168_agent_votes(self):
        """Verify evaluate_symbol provides 168 votes under demo data."""
        res = evaluate_symbol("XAUUSD", source="demo", on_demand=True, include_swarm=True)
        self.assertEqual(res["symbol"], "XAUUSD")
        self.assertGreater(res["price"], 0.0)
        self.assertIn("votes", res)
        self.assertEqual(len(res["votes"]), 168)
        self.assertIn("mt5_chart", res)


if __name__ == "__main__":
    unittest.main()
