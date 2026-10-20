import io
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

import config
from anti_spam import AntiSpamRateLimiter, canonical_currency, get_anti_spam_limiter
from autonomous_learner import AutonomousLearner
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from main import cooldown_command, run_cli_interactive, engine
from risk_manager import RiskManager
from strategy_profiles import apply_profile, get_profile


class TestAntiSpamAndProfileScore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.risk_mgr = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        self.limiter = AntiSpamRateLimiter()
        self.limiter.reset()

        # Telegram mock
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    def tearDown(self):
        try:
            apply_profile("scalp")
        except Exception:
            pass

    def test_canonical_currency(self):
        self.assertEqual(canonical_currency("XAUUSD"), "XAU")
        self.assertEqual(canonical_currency("GOLD"), "XAU")
        self.assertEqual(canonical_currency("XAUTUSD"), "XAU")
        self.assertEqual(canonical_currency("PAXGUSDT"), "XAU")
        self.assertEqual(canonical_currency("BTCUSDT"), "BTC")
        self.assertEqual(canonical_currency("BTCUSD"), "BTC")
        self.assertEqual(canonical_currency("BTC"), "BTC")
        self.assertEqual(canonical_currency("XBT"), "BTC")
        self.assertEqual(canonical_currency("ETHUSDT"), "ETH")

    def test_antispam_modes_toggle(self):
        self.assertEqual(self.limiter.set_mode("auto"), "auto")
        self.assertEqual(self.limiter.get_mode(), "auto")
        self.assertEqual(self.limiter.set_mode("on"), "on")
        self.assertEqual(self.limiter.get_mode(), "on")
        self.assertEqual(self.limiter.set_mode("off"), "off")
        self.assertEqual(self.limiter.get_mode(), "off")
        self.assertEqual(self.limiter.set_mode("dynamic"), "auto")
        self.assertEqual(self.limiter.set_mode("strict"), "on")
        self.assertEqual(self.limiter.set_mode("disabled"), "off")

    def _make_order(self, oid: str, symbol: str, entry: float = 2650.0) -> TradeOrder:
        return TradeOrder(
            id=oid,
            symbol=symbol,
            direction="LONG",
            entry_level=entry,
            stop_loss=entry - 10.0,
            tp1=entry + 15.0,
            tp2=entry + 25.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=50.0,
            liquidation_price=None,
            risk_amount=20.0,
            rr_ratio=2.0,
            setup_reason="test",
            status=OrderStatus.OPEN,
            executed_price=entry,
        )

    def test_antispam_max_2_trades_same_currency(self):
        """Verify that max 2 trades are allowed per currency (Gold or BTC)."""
        self.limiter.set_mode("on")
        self.limiter.max_positions_per_symbol = 2

        # 1. First Gold position
        o1 = self._make_order("ORD-1", "XAUUSD", 2650.0)
        self.engine.active_positions[o1.id] = o1

        # Can enter 2nd Gold position (far enough in price)
        can_enter, reason = self.limiter.check_can_enter(
            symbol="GOLD",
            current_price=2660.0,
            engine=self.engine,
            max_open=4,
        )
        self.assertTrue(can_enter)

        # Add 2nd Gold position
        o2 = self._make_order("ORD-2", "GOLD", 2660.0)
        self.engine.active_positions[o2.id] = o2

        # 3rd Gold position must be blocked (2 already open for XAU)
        can_enter_3, reason_3 = self.limiter.check_can_enter(
            symbol="XAUUSD",
            current_price=2670.0,
            engine=self.engine,
            max_open=4,
        )
        self.assertFalse(can_enter_3)
        self.assertIn("Max 2 positions reached for currency XAU", reason_3)

        # But Bitcoin must be ALLOWED (0 BTC open)
        can_btc, reason_btc = self.limiter.check_can_enter(
            symbol="BTCUSDT",
            current_price=65000.0,
            engine=self.engine,
            max_open=4,
        )
        self.assertTrue(can_btc)

    def test_total_max_4_positions_across_gold_and_btc(self):
        """Verify total max 4 positions (2 Gold + 2 BTC) and rejection of 5th position."""
        self.limiter.set_mode("auto")
        self.limiter.max_positions_per_symbol = 2

        # 2 Gold
        self.engine.active_positions["ORD-G1"] = self._make_order("ORD-G1", "XAUUSD", 2650.0)
        self.engine.active_positions["ORD-G2"] = self._make_order("ORD-G2", "GOLD", 2670.0)

        # 2 BTC
        self.engine.active_positions["ORD-B1"] = self._make_order("ORD-B1", "BTCUSDT", 65000.0)
        self.engine.active_positions["ORD-B2"] = self._make_order("ORD-B2", "BTCUSD", 66000.0)

        self.assertEqual(len(self.engine.active_positions), 4)

        # 5th position attempt: must be blocked because total slots full (4/4)
        can_enter, reason = self.limiter.check_can_enter(
            symbol="XAUUSD",
            current_price=2680.0,
            engine=self.engine,
            max_open=4,
        )
        self.assertFalse(can_enter)
        self.assertIn("Total open slots full (4/4)", reason)

    def test_antispam_off_mode_bypasses_cooldowns(self):
        """In OFF mode, cooldowns and distance checks are bypassed, but hard capacity is kept."""
        self.limiter.set_mode("off")
        self.limiter.record_entry("XAUUSD", 2650.0, timestamp=100.0)

        # At timestamp 105s (within 45s entry cooldown), check entry
        can_enter, reason = self.limiter.check_can_enter(
            symbol="XAUUSD",
            current_price=2650.01,  # virtually same price
            engine=self.engine,
            max_open=4,
            timestamp=105.0,
        )
        self.assertTrue(can_enter)
        self.assertIn("anti-spam manual OFF", reason)

    def test_profile_score_concession_and_tight_strategy(self):
        """Verify that scalp gets score concession while balanced and swing require tight conviction."""
        learner = AutonomousLearner()
        learner.knowledge.enabled = True
        learner.knowledge.min_score = 1.30
        learner.knowledge.min_rr = 1.80
        learner.knowledge.min_agreement = 2
        learner.knowledge.conviction_floor = 0.50

        # Scalp concession: score 0.95 and rr 1.00 should PASS
        ok_scalp, reason_scalp, _ = learner.advise_signal(
            direction="LONG",
            score=0.95,
            rr=1.00,
            agreement=2,
            conviction=0.45,
            is_scalp=True,
            profile="scalp",
        )
        self.assertTrue(ok_scalp)
        self.assertIn("pass learned filters", reason_scalp)

        # Swing tight strategy: score 1.20 or rr 1.50 should FAIL (requires min_score 1.70, rr 2.50, agreement 3)
        ok_swing, reason_swing, _ = learner.advise_signal(
            direction="LONG",
            score=1.20,
            rr=1.50,
            agreement=2,
            conviction=0.55,
            is_scalp=False,
            profile="swing",
        )
        self.assertFalse(ok_swing)

        # Swing setup with high score (1.80), 3 agents, and 2.6 R:R should PASS
        ok_swing_pass, reason_swing_pass, _ = learner.advise_signal(
            direction="LONG",
            score=1.80,
            rr=2.60,
            agreement=3,
            conviction=0.70,
            is_scalp=False,
            profile="swing",
        )
        self.assertTrue(ok_swing_pass)

    async def test_telegram_antispam_command(self):
        """Verify Telegram /antispam command handles auto, on, off, reset, and status."""
        # 1. Status report
        self.ctx.args = []
        await cooldown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-Spam & Cooldown Telemetry", msg)
        self.assertIn("Operating Mode", msg)
        self.assertIn("(2 Gold, 2 BTC)", msg)

        # 2. Toggle AUTO
        self.ctx.args = ["auto"]
        await cooldown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-Spam Mode set to AUTO", msg)
        self.assertEqual(get_anti_spam_limiter().get_mode(), "auto")

        # 3. Toggle MANUAL ON
        self.ctx.args = ["on"]
        await cooldown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-Spam Mode set to MANUAL ON", msg)
        self.assertEqual(get_anti_spam_limiter().get_mode(), "on")

        # 4. Toggle OFF
        self.ctx.args = ["off"]
        await cooldown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-Spam Mode set to OFF", msg)
        self.assertEqual(get_anti_spam_limiter().get_mode(), "off")

        # 5. Reset
        self.ctx.args = ["reset"]
        await cooldown_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-spam cooldowns and loss streaks have been reset", msg)

    def test_cli_antispam_commands(self):
        """Verify interactive CLI antispam commands (status, auto, on, off, reset)."""
        input_data = io.StringIO("antispam\nantispam auto\nantispam on\nantispam off\nantispam reset\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Anti-Spam & Cooldown Telemetry", out)
        self.assertIn("Anti-Spam Mode set to AUTO", out)
        self.assertIn("Anti-Spam Mode set to MANUAL ON", out)
        self.assertIn("Anti-Spam Mode set to OFF", out)
    def test_agreement_three_not_blocked_under_any_profile(self):
        """Verify that agreement 3 is NEVER blocked by 'AUTO: agreement 3 < learned min 4' under any profile."""
        from autonomous_learner import AutonomousLearner
        learner = AutonomousLearner()
        learner.knowledge.enabled = True
        learner.knowledge.min_agreement = 4  # Simulate legacy or tightly escalated value
        learner.knowledge.min_score = 1.20
        learner.knowledge.min_rr = 1.00
        learner.knowledge.conviction_floor = 0.50

        # 1. Under scalp profile, min_agreement should be 2, agreement 3 must pass
        ok, reason, _ = learner.advise_signal("LONG", score=1.2, rr=1.2, agreement=3, conviction=0.6, profile="scalp")
        self.assertTrue(ok, f"Scalp should pass with agreement=3, got: {reason}")
        self.assertNotIn("learned min 4", reason)

        # 2. Under balanced profile, min_agreement should be capped at 3, agreement 3 must pass
        ok_bal, reason_bal, _ = learner.advise_signal("LONG", score=1.8, rr=1.9, agreement=3, conviction=0.6, profile="balanced")
        self.assertTrue(ok_bal, f"Balanced should pass with agreement=3, got: {reason_bal}")
        self.assertNotIn("learned min 4", reason_bal)

        # 3. Under swing profile, min_agreement should be capped at 3, agreement 3 must pass
        ok_sw, reason_sw, _ = learner.advise_signal("LONG", score=2.0, rr=2.6, agreement=3, conviction=0.7, profile="swing")
        self.assertTrue(ok_sw, f"Swing should pass with agreement=3, got: {reason_sw}")
        self.assertNotIn("learned min 4", reason_sw)

        # 4. Under default / unprovided profile, agreement 3 must pass
        ok_def, reason_def, _ = learner.advise_signal("LONG", score=1.8, rr=1.9, agreement=3, conviction=0.6)
        self.assertTrue(ok_def, f"Default should pass with agreement=3, got: {reason_def}")
        self.assertNotIn("learned min 4", reason_def)

    def test_autonomous_learner_load_and_corrections_cap(self):
        """Verify _load clamps min_agreement to 3 and _apply_corrections never escalates past 3."""
        import json, tempfile
        from autonomous_learner import AutonomousLearner
        with tempfile.TemporaryDirectory() as tmpdir:
            mpath = os.path.join(tmpdir, "test_memory.json")
            with open(mpath, "w", encoding="utf-8") as f:
                json.dump({"knowledge": {"min_agreement": 4, "min_score": 2.0, "min_rr": 2.0}}, f)
            learner = AutonomousLearner(memory_path=mpath)
            self.assertLessEqual(learner.knowledge.min_agreement, 3)

            # Test corrections escalation with poor historical win rate
            trades = [{"r": -0.5, "won": False, "hour": 0, "weekday": 1, "regime": "trend", "setup": "s1"} for _ in range(10)]
            learner._apply_corrections([], trades)
            self.assertLessEqual(learner.knowledge.min_agreement, 3)


if __name__ == "__main__":
    unittest.main()
