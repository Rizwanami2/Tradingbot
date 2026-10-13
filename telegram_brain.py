"""Optional read-only /brain command for autonomous learner status."""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("TelegramBrain")


async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from autonomous_learner import get_brain
        text = get_brain(auto_bootstrap=False).summary()
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Brain error: `{e}`", parse_mode="Markdown")


async def relearn_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Force a fresh 5y bootstrap (optional; not required for normal operation)."""
    await update.message.reply_text("⏳ Re-learning from history (up to 5y)...")
    try:
        from autonomous_learner import get_brain
        sym = ctx.args[0].upper() if ctx.args else "XAUUSD"
        summary = get_brain(auto_bootstrap=False).bootstrap(symbol=sym, years=5.0)
        await update.message.reply_text(
            f"✅ Bootstrap done\n```\n{summary}\n```", parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Relearn error: `{e}`", parse_mode="Markdown")


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
