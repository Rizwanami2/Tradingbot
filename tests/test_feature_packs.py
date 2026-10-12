"""Unit tests for Feature Pack v3, v4, and Autonomous Learner modules."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
import numpy as np
import pandas as pd

from feature_pack import FeaturePack
from feature_pack_v4 import FeaturePackV4
from autonomous_learner import AutonomousLearner, get_brain
from position_manager import PositionManager
from trade_journal import TradeJournal
from agent_stats import AgentStats
from risk_tiers import RiskTiers
from signal_quality import evaluate_signal_quality
from learning_edge import LearningEdge
from ops_advanced import HealthMonitor, build_daily_digest, data_feed_fallback_order
from research_helpers import ABBooks, STRESS_SCENARIOS


def _sample_ohlcv(n: int = 100, base: float = 2650.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="1min")
    p = base + np.cumsum(rng.normal(0, 0.3, n))
    high = p + rng.uniform(0.1, 1.5, n)
    low = p - rng.uniform(0.1, 1.5, n)
    close = p + rng.normal(0, 0.1, n)
    volume = rng.integers(50, 1000, n)
    return pd.DataFrame({"open": p, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


class TestFeaturePacks(unittest.TestCase):
    def test_feature_pack_lifecycle(self):
        pack = FeaturePack(equity=10000.0, paper_mode=True)
        allowed, reason, mult = pack.gate_new_entry()
        self.assertTrue(allowed)
        self.assertEqual(mult, 1.0)

        # Open trade
        tid = pack.on_open(
            symbol="XAUUSD",
            direction="LONG",
            entry=2650.0,
            stop=2640.0,
            tp1=2670.0,
            size=0.1,
            risk_amount=50.0,
            agents=["TREND", "LIQUIDITY"],
            session="LONDON",
            score=3.5,
            conviction=0.75,
        )
        self.assertIsNotNone(tid)

        # Bar update
        action = pack.on_bar(tid, price=2660.0)
        self.assertIn("close", action)

        # Close trade
        res = pack.on_close(tid, exit_price=2670.0)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("won"))
        self.assertGreater(res.get("pnl", 0), 0)

        # Board
        board = pack.board()
        self.assertIn("Status Board", board)

    def test_feature_pack_v4_gate(self):
        pack = FeaturePackV4(equity=10000.0)
        df = _sample_ohlcv(80)
        ok, reasons, mult = pack.full_gate("XAUUSD", "LONG", df=df, spread=0.2)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reasons, list)
        self.assertGreaterEqual(mult, 0.0)

        digest = pack.digest()
        self.assertIn("Daily Digest", digest)

    def test_position_manager_scale_out(self):
        pm = PositionManager()
        pm.add("t1", "XAUUSD", "LONG", 100.0, 90.0, 110.0, size=0.5)
        # Price hits 1R target: 110.0
        act = pm.update("t1", 110.0)
        self.assertTrue(act["partial"] > 0)
        self.assertIn("1R", act["reason"])
        self.assertEqual(act["new_stop"], 100.5)  # Breakeven + buffer

    def test_risk_tier_transitions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_tiers.json")
            rt = RiskTiers(path=path)
            tier, reason, mult = rt.status()
            self.assertEqual(tier, "GREEN")
            self.assertEqual(mult, 1.0)

            # Record daily loss past soft limit (-1.5R) -> YELLOW
            rt.record_closed(r_multiple=-1.6, won=False)
            tier, reason, mult = rt.status()
            self.assertEqual(tier, "YELLOW")
            self.assertEqual(mult, 0.5)

            # Record daily loss past hard limit (-2.5R) -> ORANGE
            rt.record_closed(r_multiple=-1.0, won=False)
            tier, reason, mult = rt.status()
            self.assertEqual(tier, "ORANGE")
            self.assertEqual(mult, 0.0)

    def test_autonomous_learner_advice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "test_auto_mem.json")
            brain = AutonomousLearner(memory_path=mem_path)
            # Default advice without ban
            ok, reason, mult = brain.advise_signal("LONG", score=2.5, rr=2.0, agreement=4, conviction=0.7)
            self.assertTrue(ok)
            self.assertGreater(mult, 0.0)

            # Record trade close
            brain.on_trade_closed(r_multiple=1.5, won=True, setup="LONG|trend|LONDON", regime="trend", agents=["TREND"])
            self.assertEqual(brain.knowledge.total_live_trades, 1)
            summary = brain.summary()
            self.assertIn("Autonomous Learner", summary)

    def test_signal_quality(self):
        df = _sample_ohlcv(50)
        res = evaluate_signal_quality(df, direction="LONG", spread=0.1)
        self.assertIsInstance(res.allowed, bool)
        self.assertGreaterEqual(res.score, 0.0)
        self.assertIsInstance(res.reasons, list)

    def test_learning_edge_brain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "edge.json")
            leb = LearningEdge(path=path, ban_after_losses=3)
            # 3 consecutive losses on setup should ban it
            for _ in range(3):
                leb.record(tag="bad_setup", won=False, r=-1.0)
            banned, reason = leb.is_banned("bad_setup")
            self.assertTrue(banned)
            self.assertIn("consecutive losses", reason)

    def test_ab_paper_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_ab.json")
            ab = ABBooks(path=path)
            self.assertIn("strict", ab.books)
            self.assertIn("balanced", ab.books)
            ab.record(book="balanced", won=True, r=2.0)
            self.assertEqual(ab.books["balanced"].trades, 1)
            self.assertEqual(ab.books["balanced"].wins, 1)
            rep = ab.report()
            self.assertIn("A/B Paper Books", rep)


if __name__ == "__main__":
    unittest.main()
