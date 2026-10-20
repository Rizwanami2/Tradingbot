"""Unit tests verifying AI and Veto removal and pure Multi-Agent mode confirmation."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from telegram import Update, Message, Chat
from telegram.ext import ContextTypes

from telegram_ai import (
    ai_command,
    register_ai_commands,
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

    async def test_ai_command_confirms_removal(self):
        await ai_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        msg = self.message.reply_text.call_args[0][0]
        self.assertIn("AI Suite & Veto Removed", msg)
        self.assertIn("Pure Multi-Agent Consensus", msg)

    def test_register_ai_commands_is_noop(self):
        app_mock = MagicMock()
        register_ai_commands(app_mock)
        app_mock.add_handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
