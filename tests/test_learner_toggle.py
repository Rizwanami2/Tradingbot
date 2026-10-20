"""
Unit tests for Autonomous Learner On/Off toggle, unban controls, and veto command handlers.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

from autonomous_learner import AutonomousLearner, Knowledge
import signal_pipeline
from telegram_brain import (
    build_learner_block_keyboard,
    build_learner_toggle_keyboard,
    handle_learner_callback,
    learner_command,
    veto_command,
    brain_command,
)


class TestLearnerToggleAndUnban(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mem_path = os.path.join(self.tmpdir.name, "test_learner_mem.json")
        self.brain = AutonomousLearner(memory_path=self.mem_path, auto_bootstrap=False)
        self.brain.knowledge.banned_hours = [14, 21]
        self.brain.knowledge.banned_weekdays = [5, 6]
        self.brain.knowledge.banned_setups = {"short|ranging": "consecutive losses", "long|breakout": "weak expectancy"}
        self.brain._save()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_enable_disable_toggle(self):
        self.assertTrue(self.brain.is_enabled())
        self.assertFalse(self.brain.disable())
        self.assertFalse(self.brain.is_enabled())
        self.assertFalse(self.brain.knowledge.enabled)

        # Reload from disk to verify persistence
        reloaded = AutonomousLearner(memory_path=self.mem_path, auto_bootstrap=False)
        self.assertFalse(reloaded.is_enabled())

        self.assertTrue(self.brain.enable())
        self.assertTrue(self.brain.is_enabled())
        self.assertTrue(self.brain.knowledge.enabled)

    def test_advise_signal_when_disabled(self):
        # When enabled, hour 14 is blocked
        ok, reason, mult = self.brain.advise_signal(hour=14, score=2.5, rr=2.5, agreement=3, conviction=0.8)
        self.assertFalse(ok)
        self.assertIn("banned hour 14 UTC", reason)

        # When disabled, all trades pass through
        self.brain.disable()
        ok_dis, reason_dis, mult_dis = self.brain.advise_signal(hour=14, score=0.5, rr=0.5, agreement=1, conviction=0.2)
        self.assertTrue(ok_dis)
        self.assertIn("brain filtering is disabled", reason_dis)
        self.assertEqual(mult_dis, 1.0)

    def test_unban_hour(self):
        self.assertIn(14, self.brain.knowledge.banned_hours)
        unbanned = self.brain.unban_hour(14)
        self.assertTrue(unbanned)
        self.assertNotIn(14, self.brain.knowledge.banned_hours)
        self.assertIn(21, self.brain.knowledge.banned_hours)

        # Calling unban on non-banned hour returns False
        self.assertFalse(self.brain.unban_hour(14))

        # Test clear all banned hours
        cleared = self.brain.clear_banned_hours()
        self.assertEqual(cleared, 1)
        self.assertEqual(self.brain.knowledge.banned_hours, [])

    def test_unban_weekday_and_setups(self):
        self.brain.knowledge.banned_weekdays = [5, 6]
        self.assertTrue(self.brain.unban_weekday(5))
        self.assertNotIn(5, self.brain.knowledge.banned_weekdays)
        self.assertEqual(self.brain.clear_banned_weekdays(), 1)
        self.assertEqual(self.brain.knowledge.banned_weekdays, [])

        # Unban by name (monday, mon, etc.)
        self.brain.knowledge.banned_weekdays = [0, 1]
        self.assertTrue(self.brain.unban_weekday("monday"))
        self.assertNotIn(0, self.brain.knowledge.banned_weekdays)
        self.assertTrue(self.brain.unban_weekday("tue"))
        self.assertNotIn(1, self.brain.knowledge.banned_weekdays)

        removed = self.brain.unban_setup("ranging")
        self.assertEqual(removed, ["short|ranging"])
        self.assertNotIn("short|ranging", self.brain.knowledge.banned_setups)
        self.assertIn("long|breakout", self.brain.knowledge.banned_setups)

        cleared_bans = self.brain.clear_bans()
        self.assertEqual(cleared_bans["setups"], 1)
        self.assertEqual(self.brain.knowledge.banned_setups, {})

    def test_weekday_policy_disables_hard_veto(self):
        # By default allow_weekday_bans is False: weekday 0 (Monday) does NOT veto
        self.brain.knowledge.allow_weekday_bans = False
        self.brain.knowledge.banned_weekdays = [0]
        ok, reason, mult = self.brain.advise_signal(weekday=0, score=2.5, rr=2.5, agreement=3, conviction=0.8)
        self.assertTrue(ok)
        self.assertNotIn(0, self.brain.knowledge.banned_weekdays)  # auto-purged

        # When explicitly enabled, weekday ban vetoes
        self.brain.set_allow_weekday_bans(True)
        self.brain.knowledge.banned_weekdays = [0]
        ok_veto, reason_veto, _ = self.brain.advise_signal(weekday=0, score=2.5, rr=2.5, agreement=3, conviction=0.8)
        self.assertFalse(ok_veto)
        self.assertIn("banned weekday 0", reason_veto)


class TestLearnerTelegramHandlers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = Chat(id=999, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

        import autonomous_learner
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mem_path = os.path.join(self.tmpdir.name, "mem.json")
        self.test_brain = AutonomousLearner(memory_path=self.mem_path, auto_bootstrap=False)
        self.test_brain.knowledge.banned_hours = [14]
        self._orig_brain = autonomous_learner._brain
        autonomous_learner._brain = self.test_brain

    async def asyncTearDown(self):
        import autonomous_learner
        autonomous_learner._brain = self._orig_brain
        self.tmpdir.cleanup()

    async def test_learner_off_command(self):
        self.ctx.args = ["off"]
        await learner_command(self.update, self.ctx)
        self.assertFalse(self.test_brain.is_enabled())
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autonomous Learner Filter Paused", text)
        self.assertIn("DISABLED", text)

    async def test_learner_on_command(self):
        self.test_brain.disable()
        self.ctx.args = ["on"]
        await learner_command(self.update, self.ctx)
        self.assertTrue(self.test_brain.is_enabled())
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autonomous Learner Filter Activated", text)

    async def test_learner_unban_command(self):
        self.assertIn(14, self.test_brain.knowledge.banned_hours)
        self.ctx.args = ["unban", "14"]
        await learner_command(self.update, self.ctx)
        self.assertNotIn(14, self.test_brain.knowledge.banned_hours)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Unbanned UTC Hour 14:00", text)

    async def test_learner_clearhours_command(self):
        self.test_brain.knowledge.banned_hours = [10, 14, 20]
        self.ctx.args = ["clearhours"]
        await learner_command(self.update, self.ctx)
        self.assertEqual(self.test_brain.knowledge.banned_hours, [])
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Cleared all 3 banned UTC hours", text)

    async def test_learner_unban_weekday_command(self):
        self.test_brain.knowledge.banned_weekdays = [0]
        self.ctx.args = ["unban", "monday"]
        await learner_command(self.update, self.ctx)
        self.assertNotIn(0, self.test_brain.knowledge.banned_weekdays)
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Unbanned Monday (Weekday 0)", text)

    async def test_learner_clearweekdays_command(self):
        self.test_brain.knowledge.banned_weekdays = [0, 4]
        self.ctx.args = ["clearweekdays"]
        await learner_command(self.update, self.ctx)
        self.assertEqual(self.test_brain.knowledge.banned_weekdays, [])
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Cleared all 2 banned weekdays", text)

    async def test_veto_command_alias(self):
        self.ctx.args = ["off"]
        await veto_command(self.update, self.ctx)
        self.assertFalse(self.test_brain.is_enabled())

    async def test_build_learner_block_keyboard(self):
        note = "AUTO: banned hour 14 UTC (learned)"
        kb = build_learner_block_keyboard(note)
        self.assertIsNotNone(kb)
        buttons = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("learner:off", buttons)
        self.assertIn("learner:unban:14", buttons)
        self.assertIn("learner:rules", buttons)
        self.assertIn("learner:on", buttons)

    async def test_build_learner_block_keyboard_weekday(self):
        note = "AUTO: banned weekday 0 (learned)"
        kb = build_learner_block_keyboard(note)
        self.assertIsNotNone(kb)
        buttons = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("learner:unban_wd:0", buttons)
        self.assertIn("learner:off", buttons)
        self.assertIn("learner:clearbans", buttons)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertTrue(any("Unban Monday" in t for t in btn_texts))

    async def test_handle_learner_callback(self):
        self.update.callback_query = MagicMock()
        self.update.callback_query.answer = AsyncMock()
        self.update.callback_query.message = self.message

        # Test callback learner:off
        handled = await handle_learner_callback("learner:off", self.update, self.ctx)
        self.assertTrue(handled)
        self.assertFalse(self.test_brain.is_enabled())

        # Test callback learner:unban:14
        self.test_brain.knowledge.banned_hours = [14]
        handled = await handle_learner_callback("learner:unban:14", self.update, self.ctx)
        self.assertTrue(handled)
        self.assertNotIn(14, self.test_brain.knowledge.banned_hours)

        # Test callback learner:unban_wd:0
        self.test_brain.knowledge.banned_weekdays = [0]
        handled_wd = await handle_learner_callback("learner:unban_wd:0", self.update, self.ctx)
        self.assertTrue(handled_wd)
        self.assertNotIn(0, self.test_brain.knowledge.banned_weekdays)

        # Test callback learner:on
        handled = await handle_learner_callback("learner:on", self.update, self.ctx)
        self.assertTrue(handled)
        self.assertTrue(self.test_brain.is_enabled())

    def test_try_paper_execute_veto_message(self):
        engine_mock = MagicMock()
        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 85000.0,
            "vetoed": True,
            "veto_reason": "AUTO: banned hour 14 UTC (learned)",
        }
        ok, note = signal_pipeline.try_paper_execute(engine_mock, sig)
        self.assertFalse(ok)
        self.assertIn("Trade execution blocked by Autonomous Learner: AUTO: banned hour 14 UTC (learned)", note)
        self.assertIn("/learner off", note)
        self.assertIn("/veto off", note)
        self.assertIn("/learner unban 14", note)

    def test_try_paper_execute_weekday_veto_message(self):
        engine_mock = MagicMock()
        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 85000.0,
            "vetoed": True,
            "veto_reason": "AUTO: banned weekday 0 (learned)",
        }
        ok, note = signal_pipeline.try_paper_execute(engine_mock, sig)
        self.assertFalse(ok)
        self.assertIn("Trade execution blocked by Autonomous Learner: AUTO: banned weekday 0 (learned)", note)
        self.assertIn("/learner unban monday", note)
        self.assertIn("/learner off", note)

    async def test_autotrade_command_learner_subcommands(self):
        from main import autotrade_command
        self.ctx.args = ["learner", "off"]
        await autotrade_command(self.update, self.ctx)
        self.assertFalse(self.test_brain.is_enabled())
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade: Autonomous Learner Veto PAUSED", text)

        self.ctx.args = ["veto", "on"]
        await autotrade_command(self.update, self.ctx)
        self.assertTrue(self.test_brain.is_enabled())
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade: Autonomous Learner Veto ACTIVATED", text)
