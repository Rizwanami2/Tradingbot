"""
Telegram commands for Feature Pack v4
=====================================
    from telegram_feature_pack_v4 import register_feature_pack_v4
    register_feature_pack_v4(app)

Commands:
  /digest   — daily digest now
  /replay ID
  /export   — journal CSV (text)
  /setups   — setup expectancy + bans
  /abooks   — A/B paper books
  /quality SYMBOL  — run signal quality on latest data
  /heal     — health status / reset error counter
"""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("TelegramFP4")

_pack = None

def _get():
    global _pack
    if _pack is None:
        try:
            import config
            eq = getattr(config, "INITIAL_EQUITY", 10000.0)
        except Exception:
            eq = 10000.0
        from feature_pack_v4 import FeaturePackV4
        _pack = FeaturePackV4(equity=eq, paper_mode=True)
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


async def digest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "digest")


async def replay_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "replay")


async def export_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "export")


async def setups_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "setups")


async def abooks_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "abooks")


async def quality_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        import config
        from multi_agent_trading_bot import MarketDataFeed
        from signal_quality import evaluate_signal_quality
        sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()
        feed = MarketDataFeed(symbol=sym, source=getattr(config, "DEFAULT_DATA_SOURCE", "demo"))
        data = feed.multi_tf(limit_1m=120)
        df = data.get("1m") if data else None
        if df is None or df.empty:
            await safe_reply(update, "No data", parse_mode=None)
            return
        q = evaluate_signal_quality(df, "LONG", spread=0.0)
        icon = "✅" if q.allowed else "❌"
        clean_reasons = [str(r).replace("`", "'") for r in q.reasons]
        reason_lines = "\n".join(f"• `{cr}`" for cr in clean_reasons)
        msg = f"{icon} *Quality {sym}* score=`{q.score:.2f}`\n" + reason_lines
        await safe_reply(update, msg, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /quality command: %s", e)
        await safe_reply(update, f"Quality error: {e}", parse_mode=None)


async def heal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _feature_removed_reply(update, "heal")


def register_feature_pack_v4(application) -> None:
    existing_cmds = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing_cmds.update(h.commands)

    if "quality" not in existing_cmds:
        application.add_handler(CommandHandler("quality", quality_command))
        existing_cmds.add("quality")
    logger.info("FP4 feature commands removed; /quality registered")
