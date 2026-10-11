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


async def board_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        text = pack.board()
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Board error: `{e}`", parse_mode="Markdown")


async def journal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        text = pack.journal.telegram_report()
        recent = pack.journal.closed_summary(8)
        if recent:
            text += "\n\n**Recent closes:**"
            for r in reversed(recent):
                icon = "✅" if r.get("won") else "❌"
                text += (
                    f"\n{icon} `{r.get('symbol')}` {r.get('direction')} "
                    f"R=`{r.get('r_multiple')}` PnL=`{r.get('pnl')}`"
                )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Journal error: `{e}`", parse_mode="Markdown")


async def agents_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        await update.message.reply_text(pack.agent_stats.telegram_report(), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Agents error: `{e}`", parse_mode="Markdown")


async def tiers_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        await update.message.reply_text(pack.risk_tiers.telegram_line(), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Tiers error: `{e}`", parse_mode="Markdown")


def register_feature_pack(application) -> None:
    application.add_handler(CommandHandler("board", board_command))
    application.add_handler(CommandHandler("journal", journal_command))
    application.add_handler(CommandHandler("agents", agents_command))
    application.add_handler(CommandHandler("tiers", tiers_command))
    logger.info("Registered Feature Pack: /board /journal /agents /tiers")
