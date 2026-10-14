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


async def board_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        text = pack.board()
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /board command: %s", e)
        await safe_reply(update, f"Board error: {e}", parse_mode=None)


async def journal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        text = pack.journal.telegram_report()
        recent = pack.journal.closed_summary(8)
        if recent:
            text += "\n\n*Recent closes:*"
            for r in reversed(recent):
                icon = "✅" if r.get("won") else "❌"
                text += (
                    f"\n{icon} `{r.get('symbol')}` {r.get('direction')} "
                    f"R=`{r.get('r_multiple')}` PnL=`{r.get('pnl')}`"
                )
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /journal command: %s", e)
        await safe_reply(update, f"Journal error: {e}", parse_mode=None)


async def agents_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        await safe_reply(update, pack.agent_stats.telegram_report(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /agents command: %s", e)
        await safe_reply(update, f"Agents error: {e}", parse_mode=None)


async def tiers_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get_pack()
        await safe_reply(update, pack.risk_tiers.telegram_line(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /tiers command: %s", e)
        await safe_reply(update, f"Tiers error: {e}", parse_mode=None)


def register_feature_pack(application) -> None:
    existing_cmds = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing_cmds.update(h.commands)

    for cmd, handler in [
        ("board", board_command),
        ("journal", journal_command),
        ("agents", agents_command),
        ("tiers", tiers_command),
    ]:
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)
    logger.info("Registered Feature Pack: /board /journal /agents /tiers")
