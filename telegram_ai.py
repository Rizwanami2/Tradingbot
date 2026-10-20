"""Telegram /ai command handler - AI & Veto features removed."""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler

from telegram_brain import safe_reply

logger = logging.getLogger("TelegramAI")


def build_ai_dashboard(suite=None, mode: Optional[str] = None) -> Tuple[str, InlineKeyboardMarkup]:
    """Inform that AI and Veto features have been removed."""
    text = (
        "ℹ️ **AI & Veto Features Completely Removed**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• AI Advanced Suite gates and Autonomous Learner vetoes are disabled.\n"
        "• All signals execute directly without AI filtering or veto blocks.\n"
        "• Autotrade and manual signals run without interference."
    )
    return text, InlineKeyboardMarkup([])


async def ai_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """AI and Veto features have been completely removed."""
    msg = (
        "ℹ️ **AI and Veto Features Completely Removed**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• AI suite gates and Autonomous Learner vetoes are disabled.\n"
        "• All trades and signals execute directly without AI filtering or veto blocks.\n"
        "• Strategy rules and technical filters execute trades unimpeded."
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def handle_ai_callback(data: Any, update: Optional[Update] = None, ctx: Optional[ContextTypes.DEFAULT_TYPE] = None) -> bool:
    """Handle interactive inline buttons for the AI control dashboard."""
    if isinstance(data, Update):
        if update is not None and not isinstance(update, Update):
            ctx = update
        update = data
        data = str(getattr(getattr(update, "callback_query", None), "data", "") or "")
    else:
        data = str(data or "")

    query = getattr(update, "callback_query", None)
    if query:
        try:
            await query.answer("AI & Veto features have been completely removed.")
            await query.edit_message_text(
                "ℹ️ **AI and Veto Features Completely Removed**\n"
                "• All trades execute directly without AI gates or veto blocks.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True
    return False


def register_ai_commands(application) -> None:
    """AI commands removed."""
    logger.info("AI commands removed by request")
