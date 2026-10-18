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


async def digest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await safe_reply(update, _get().digest(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /digest command: %s", e)
        await safe_reply(update, f"Digest error: {e}", parse_mode=None)


async def replay_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await safe_reply(update, "Usage: `/replay TRADE_ID`", parse_mode="Markdown")
        return
    try:
        text = _get().replay(ctx.args[0])
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /replay command: %s", e)
        await safe_reply(update, f"Replay error: {e}", parse_mode=None)


async def export_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        csv_text = _get().export_csv()
        if len(csv_text) > 3500:
            csv_text = csv_text[:3400] + "\n…(truncated)"
        await safe_reply(update, f"```\n{csv_text}\n```", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /export command: %s", e)
        await safe_reply(update, f"Export error: {e}", parse_mode=None)


async def setups_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        pack = _get()
        text = "🎯 **Setups & Expectancy**\n" + pack.edge.setup_report() + "\n\n" + pack.edge.calibration_report()
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /setups command: %s", e)
        await safe_reply(update, f"Setups error: {e}", parse_mode=None)


async def abooks_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await safe_reply(update, _get().ab.report(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /abooks command: %s", e)
        await safe_reply(update, f"A/B error: {e}", parse_mode=None)


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
    try:
        h = _get().health
        if ctx.args and ctx.args[0].lower() in ("reset", "ok", "clear"):
            h.record_ok()
            await safe_reply(update, "🟢 Health counter reset.", parse_mode=None)
        else:
            await safe_reply(update, h.status(), parse_mode=None)
    except Exception as e:
        logger.exception("Error in /heal command: %s", e)
        await safe_reply(update, f"Heal error: {e}", parse_mode=None)


def register_feature_pack_v4(application) -> None:
    existing_cmds = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing_cmds.update(h.commands)

    for cmd, handler in [
        ("digest", digest_command),
        ("replay", replay_command),
        ("export", export_command),
        ("setups", setups_command),
        ("abooks", abooks_command),
        ("quality", quality_command),
        ("heal", heal_command),
    ]:
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)
    logger.info("Registered FP4: /digest /replay /export /setups /abooks /quality /heal")
