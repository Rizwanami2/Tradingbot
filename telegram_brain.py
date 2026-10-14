"""Optional read-only /brain command for autonomous learner status."""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("TelegramBrain")


async def safe_reply(update: Update, text: str, parse_mode: str | None = "Markdown"):
    """Send reply, falling back cleanly to plain text if Telegram fails to parse entities."""
    if not update or not getattr(update, "message", None):
        return None
    if len(text) > 4000:
        text = text[:3990] + "\n..."
    if parse_mode:
        try:
            return await update.message.reply_text(text, parse_mode=parse_mode)
        except Exception as e:
            logger.warning("Markdown reply failed (%s), falling back to plain text", e)
    try:
        return await update.message.reply_text(text, parse_mode=None)
    except Exception as e:
        logger.error("Failed to send message: %s", e)
        return None


_safe_reply = safe_reply


async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from autonomous_learner import get_brain
        text = get_brain(auto_bootstrap=False).summary()
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /brain command: %s", e)
        await safe_reply(update, f"Brain error: {e}", parse_mode=None)


async def relearn_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Force a fresh 5y bootstrap (optional; not required for normal operation)."""
    await safe_reply(update, "⏳ Re-learning from history (up to 5y)...", parse_mode=None)
    try:
        from autonomous_learner import get_brain
        sym = ctx.args[0].upper() if ctx.args else "XAUUSD"
        summary = get_brain(auto_bootstrap=False).bootstrap(symbol=sym, years=5.0)
        await safe_reply(
            update, f"✅ Bootstrap done\n```\n{summary}\n```", parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Error in /relearn command: %s", e)
        await safe_reply(update, f"Relearn error: {e}", parse_mode=None)


def register_brain(application) -> None:
    existing_cmds = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing_cmds.update(h.commands)

    for cmd, handler in [
        ("brain", brain_command),
        ("relearn", relearn_command),
    ]:
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)
    logger.info("Registered /brain /relearn")
