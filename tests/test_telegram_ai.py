"""Unit tests for Telegram /ai command, interactive dashboard, manual controls, and callbacks."""
from __future__ import annotations

import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

from ai_advanced_suite import get_ai_suite
from telegram_ai import (
    ai_command,
    build_ai_dashboard,
    handle_ai_callback,
)


class TestTelegramAI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.message.text = "/ai"
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []
        self.suite = get_ai_suite()
        self.suite.set_enabled(True)
        self.suite.set_control_mode("auto")
        self.suite.set_learn_freeze(False)
        self.suite.reset_safety_locks()

    async def test_ai_dashboard_default(self):
        await ai_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        msg = self.message.reply_text.call_args[0][0]
        markup = self.message.reply_text.call_args[1].get("reply_markup")
        self.assertIn("AI Advanced Suite & Manual Control Hub", msg)
        self.assertIn("AI Suite Status", msg)
        self.assertIn("Control Mode", msg)
        self.assertIsNotNone(markup)

    async def test_ai_help(self):
        self.ctx.args = ["help"]
        await ai_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("AI Advanced Suite & Manual Control Guide", msg)
        self.assertIn("/ai manual", msg)
        self.assertIn("/ai override", msg)
        self.assertIn("/ai reset", msg)

    async def test_ai_toggle_on_off(self):
        self.ctx.args = ["off"]
        await ai_command(self.update, self.ctx)
        self.assertFalse(self.suite.enabled)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("OFF", msg)

        self.ctx.args = ["on"]
        await ai_command(self.update, self.ctx)
        self.assertTrue(self.suite.enabled)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("ON", msg)

    async def test_ai_mode_manual_and_auto(self):
        self.ctx.args = ["manual"]
        await ai_command(self.update, self.ctx)
        self.assertTrue(self.suite.is_manual_mode())
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("MANUAL", msg)

        self.ctx.args = ["auto"]
        await ai_command(self.update, self.ctx)
        self.assertFalse(self.suite.is_manual_mode())
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("AUTO", msg)

    async def test_ai_override_and_reset(self):
        self.ctx.args = ["override"]
        await ai_command(self.update, self.ctx)
        self.assertTrue(self.suite.state.get("override_next"))
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("ACTIVATED", msg)

        self.ctx.args = ["reset"]
        await ai_command(self.update, self.ctx)
        self.assertFalse(self.suite.state.get("override_next"))
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("reset successfully", msg)

    async def test_ai_persona_and_bias(self):
        self.ctx.args = ["persona", "low"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(self.suite.state.get("risk_persona"), "low")

        self.ctx.args = ["bias", "long"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(self.suite.state.get("bias_day"), "long")

        self.ctx.args = ["bias", "clear"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(self.suite.state.get("bias_day"), "")

    async def test_ai_freeze_unfreeze(self):
        self.ctx.args = ["freeze"]
        await ai_command(self.update, self.ctx)
        self.assertTrue(self.suite.learn_freeze)

        self.ctx.args = ["unfreeze"]
        await ai_command(self.update, self.ctx)
        self.assertFalse(self.suite.learn_freeze)

    async def test_ai_scorecard_and_ceiling(self):
        self.ctx.args = ["scorecard", "65"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(self.suite.min_scorecard, 65.0)

        self.ctx.args = ["ceiling", "2.0"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(self.suite.lot_ceiling, 2.0)

    async def test_ai_health_and_critique(self):
        self.ctx.args = ["health"]
        await ai_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("AI Advanced Suite Health Telemetry", msg)

        self.ctx.args = ["critique"]
        await ai_command(self.update, self.ctx)
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("Critique", msg)

    async def test_handle_ai_callbacks(self):
        query_mock = MagicMock()
        query_mock.answer = AsyncMock()
        query_mock.edit_message_text = AsyncMock()
        update_mock = MagicMock(spec=Update)
        update_mock.callback_query = query_mock

        # Toggle on/off
        query_mock.data = "ai:toggle"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        query_mock.edit_message_text.assert_called()

        # Switch to manual
        query_mock.data = "ai:mode:manual"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertTrue(self.suite.is_manual_mode())

        # Switch to auto
        query_mock.data = "ai:mode:auto"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertFalse(self.suite.is_manual_mode())

        # Persona low
        query_mock.data = "ai:persona:low"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertEqual(self.suite.state.get("risk_persona"), "low")

        # Bias long
        query_mock.data = "ai:bias:long"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertEqual(self.suite.state.get("bias_day"), "long")

        # Reset locks
        query_mock.data = "ai:reset_locks"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)

        # Refresh
        query_mock.data = "ai:refresh"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)

        # Switch to advanced view
        query_mock.data = "ai:view:advanced"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertEqual(self.suite.state.get("ui_mode"), "advanced")

        # Switch to classic view
        query_mock.data = "ai:view:classic"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)
        self.assertEqual(self.suite.state.get("ui_mode"), "classic")

        # Clear bans callback
        query_mock.data = "ai:clearbans"
        handled = await handle_ai_callback(update_mock, self.ctx)
        self.assertTrue(handled)

    async def test_ai_classic_and_advance_mode_switch(self):
        # Default is classic mode
        self.ctx.args = []
        await ai_command(self.update, self.ctx)
        msg_default = self.message.reply_text.call_args[0][0]
        self.assertIn("Classic Mode", msg_default)
        self.assertIn("AI & Veto Combined", msg_default)

        # Switch to advanced mode
        self.ctx.args = ["advance"]
        await ai_command(self.update, self.ctx)
        msg_adv = self.message.reply_text.call_args[0][0]
        self.assertIn("Advanced Command Mode Activated", msg_adv)
        self.assertIn("Advanced View", msg_adv)
        self.assertEqual(self.suite.state.get("ui_mode"), "advanced")

        # Switch back to classic mode
        self.ctx.args = ["classic"]
        await ai_command(self.update, self.ctx)
        msg_classic = self.message.reply_text.call_args[0][0]
        self.assertIn("Classic Command Mode Activated", msg_classic)
        self.assertIn("Classic Mode", msg_classic)
        self.assertEqual(self.suite.state.get("ui_mode"), "classic")

    async def test_ai_combined_on_off_syncs_learner(self):
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)

        # Turn OFF
        self.ctx.args = ["off"]
        await ai_command(self.update, self.ctx)
        self.assertFalse(self.suite.enabled)
        self.assertFalse(brain.is_enabled())
        msg_off = self.message.reply_text.call_args[0][0]
        self.assertIn("OFF", msg_off)
        self.assertIn("AI & Veto Protection PAUSED", msg_off)

        # Turn ON
        self.ctx.args = ["on"]
        await ai_command(self.update, self.ctx)
        self.assertTrue(self.suite.enabled)
        self.assertTrue(brain.is_enabled())
        msg_on = self.message.reply_text.call_args[0][0]
        self.assertIn("ON", msg_on)
        self.assertIn("AI & Veto Protection ACTIVATED", msg_on)

    async def test_ai_unban_and_clearbans(self):
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        brain.knowledge.banned_hours = [14]
        brain.knowledge.banned_weekdays = [0]

        # Unban hour via /ai unban 14
        self.ctx.args = ["unban", "14"]
        await ai_command(self.update, self.ctx)
        self.assertNotIn(14, brain.knowledge.banned_hours)
        msg_unban = self.message.reply_text.call_args[0][0]
        self.assertIn("Unbanned UTC Hour 14:00", msg_unban)

        # Unban monday via /ai unban monday
        self.ctx.args = ["unban", "monday"]
        await ai_command(self.update, self.ctx)
        self.assertNotIn(0, brain.knowledge.banned_weekdays)
        msg_unban_wd = self.message.reply_text.call_args[0][0]
        self.assertIn("Unbanned Monday", msg_unban_wd)

        # Clear bans via /ai clearbans
        brain.knowledge.banned_hours = [10, 15]
        self.ctx.args = ["clearbans"]
        await ai_command(self.update, self.ctx)
        self.assertEqual(brain.knowledge.banned_hours, [])
        msg_clear = self.message.reply_text.call_args[0][0]
        self.assertIn("Cleared All Active Bans", msg_clear)

    async def test_veto_command_combined_dashboard(self):
        from telegram_brain import veto_command
        self.ctx.args = []
        await veto_command(self.update, self.ctx)
        self.message.reply_text.assert_called()
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("AI Advanced Suite & Manual Control Hub", msg)
        self.assertIn("Classic Mode", msg)

    def test_cli_ai_interactive(self):
        from main import run_cli_interactive
        input_data = io.StringIO(
            "ai status\n"
            "ai manual\n"
            "ai auto\n"
            "ai override\n"
            "ai reset\n"
            "ai persona high\n"
            "ai bias long\n"
            "ai freeze\n"
            "ai unfreeze\n"
            "ai scorecard 62\n"
            "ai ceiling 1.5\n"
            "ai health\n"
            "ai help\n"
            "exit\n"
        )
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("AI Advanced Suite", out)
        self.assertIn("Control mode set to MANUAL", out)
        self.assertIn("Control mode set to AUTO", out)
        self.assertIn("Manual 1-trade override", out)
        self.assertIn("Safety locks reset", out)
        self.assertIn("Risk persona set to high", out)
        self.assertIn("Day bias set to LONG", out)


if __name__ == "__main__":
    unittest.main()
