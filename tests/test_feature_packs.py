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
from autonomous_learner import AutonomousLearner
from position_manager import PositionManager
from trade_journal import TradeJournal
from risk_tiers import RiskTiers
from signal_quality import evaluate_signal_quality
from learning_edge import LearningEdge
from research_helpers import ABBooks


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

            # Test advise_signal always passes now that veto features are completely removed
            brain.knowledge.banned_setups["LONG|bad"] = "test ban"
            ok, reason, mult = brain.advise_signal(setup="LONG|bad")
            self.assertTrue(ok)
            self.assertIn("veto features removed completely", reason)
            brain.set_enabled(False)
            ok, reason, mult = brain.advise_signal(setup="LONG|bad")
            self.assertTrue(ok)
            brain.set_enabled(True)

            # Test format_rules and format_flaws
            rules_str = brain.format_rules()
            self.assertIn("Rules & Active Filters", rules_str)
            self.assertIn("Min Score", rules_str)

            flaws_str = brain.format_flaws()
            self.assertIn("Flaw Mining", flaws_str)

            # Test reset_memory
            res = brain.reset_memory()
            self.assertEqual(res["status"], "reset")
            self.assertEqual(brain.knowledge.total_live_trades, 0)
            self.assertEqual(len(brain.knowledge.banned_setups), 0)

            # Test scalp vs swing R:R filtering
            brain.knowledge.min_rr = 1.90
            brain.knowledge.min_score = 1.95
            brain.knowledge.min_agreement = 4
            # Veto removed: Non-scalp passes without veto
            ok_swing, reason_swing, _ = brain.advise_signal("LONG", score=2.0, rr=1.00, agreement=4, conviction=0.6, is_scalp=False)
            self.assertTrue(ok_swing)
            self.assertIn("pass learned filters", reason_swing)

            # Scalp with rr=1.00 passes learned filters
            ok_scalp, reason_scalp, _ = brain.advise_signal("LONG", score=1.2, rr=1.00, agreement=2, conviction=0.5, is_scalp=True)
            self.assertTrue(ok_scalp)
            self.assertIn("pass learned filters", reason_scalp)

            # Veto removed: Scalp passes without veto
            ok_bad_scalp, reason_bad_scalp, _ = brain.advise_signal("LONG", score=1.2, rr=0.70, agreement=2, conviction=0.5, is_scalp=True)
            self.assertTrue(ok_bad_scalp)
            self.assertIn("pass learned filters", reason_bad_scalp)

    def test_trade_journal_brain_wiring(self):
        import autonomous_learner
        with tempfile.TemporaryDirectory() as tmpdir:
            jpath = os.path.join(tmpdir, "journal.json")
            mpath = os.path.join(tmpdir, "brain.json")
            test_brain = AutonomousLearner(memory_path=mpath)
            old_brain = autonomous_learner._brain
            autonomous_learner._brain = test_brain
            try:
                journal = TradeJournal(path=jpath)
                tid = journal.record_open(
                    symbol="XAUUSD", direction="LONG", entry=2000.0,
                    stop=1990.0, tp1=2020.0, size=0.1, risk_amount=10.0,
                    regime="trend", session="LONDON", agents=["TREND"],
                )
                journal.record_close(trade_id=tid, exit_price=2015.0, auto_learn=True)
                self.assertEqual(test_brain.knowledge.total_live_trades, 1)
                self.assertGreater(len(test_brain._trade_log), 0)
            finally:
                autonomous_learner._brain = old_brain

    def test_telegram_brain_subcommands(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from telegram_brain import brain_command, relearn_command

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        # Test /brain status
        ctx.args = ["status"]
        asyncio.run(brain_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /brain rules
        ctx.args = ["rules"]
        asyncio.run(brain_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /brain flaws
        ctx.args = ["flaws"]
        asyncio.run(brain_command(update, ctx))
        self.assertTrue(update.message.reply_text.called)

        # Test /brain on / off
        ctx.args = ["off"]
        asyncio.run(brain_command(update, ctx))
        ctx.args = ["on"]
        asyncio.run(brain_command(update, ctx))

        # Test /brain reset
        ctx.args = ["reset"]
        asyncio.run(brain_command(update, ctx))

        # Test /relearn
        ctx.args = ["XAUUSD", "1.0"]
        asyncio.run(relearn_command(update, ctx))


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

    def test_telegram_brain_safe_reply_and_summary(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from telegram_brain import brain_command, safe_reply
        from telegram.error import BadRequest

        # Check summary formatting
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_mem.json")
            brain = AutonomousLearner(memory_path=path)
            brain.knowledge.improvement_log.append("Test entry with bans_setup=0 and `range`")
            summary = brain.summary()
            self.assertIn("Autonomous Learner", summary)
            self.assertIn("• `Test entry with bans_setup=0 and 'range'`", summary)

        # Test safe_reply normal call
        async def run_async_tests():
            update = MagicMock()
            update.message = MagicMock()
            update.message.reply_text = AsyncMock()

            await safe_reply(update, "Hello *world*", parse_mode="Markdown")
            self.assertEqual(update.message.reply_text.call_count, 1)
            self.assertEqual(update.message.reply_text.call_args[1].get("parse_mode"), "Markdown")

            # Test safe_reply fallback on entity parsing failure
            update2 = MagicMock()
            update2.message = MagicMock()
            async def failing_reply(text, parse_mode=None):
                if parse_mode == "Markdown":
                    raise BadRequest("Can't parse entities: can't find end of the entity starting at byte offset 529")
                return "ok"

            update2.message.reply_text = AsyncMock(side_effect=failing_reply)
            await safe_reply(update2, "Invalid _markdown", parse_mode="Markdown")
            self.assertEqual(update2.message.reply_text.call_count, 2)
            self.assertIsNone(update2.message.reply_text.call_args_list[1][1].get("parse_mode"))

            # Test brain_command executes cleanly
            update3 = MagicMock()
            update3.message = MagicMock()
            update3.message.reply_text = AsyncMock()
            await brain_command(update3, None)
            self.assertGreaterEqual(update3.message.reply_text.call_count, 1)

        asyncio.run(run_async_tests())


if __name__ == "__main__":
    unittest.main()
