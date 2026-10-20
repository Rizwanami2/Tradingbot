"""
Telegram commands for Feature Pack v3
=====================================
Register alongside telegram_advanced:

    from telegram_feature_pack import register_feature_pack
    register_feature_pack(app)
"""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("TelegramFeaturePack")

# singleton pack so state survives across commands in same process
_pack = None

def _get_pack():
    global _pack
    if _pack is None:
        try:
            import config
            equity = getattr(config, "INITIAL_EQUITY", 10000.0)
        except Exception:
            equity = 10000.0
        from feature_pack import FeaturePack
        _pack = FeaturePack(equity=equity, paper_mode=True)
    return _pack


from telegram_brain import safe_reply


async def _feature_removed_reply(update: Update, cmd: str):
    await safe_reply(
        update,
        f"ℹ️ Command `/{cmd}` has been completely removed.\n"
        "• All strategy signals execute directly without feature or veto gates.\n"
        "• Use `/commands` to view active trading commands.",
        parse_mode="Markdown",
    )


async def board_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "board")


async def journal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "journal")


async def agents_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "agents")


async def tiers_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "tiers")


def register_feature_pack(application) -> None:
    logger.info("Feature Pack commands (/board /journal /agents /tiers) removed by request")
