"""Autonomous Learner Telegram & CLI Interface."""
from __future__ import annotations

import logging
from typing import Any, List
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("TelegramBrain")


async def safe_reply(
    update: Update,
    text: str,
    parse_mode: str | None = "Markdown",
    bot: Any = None,
    reply_markup: Any = None,
):
    """Send reply, falling back cleanly to plain text if Telegram fails to parse entities."""
    if not update:
        return None
    if len(text) > 4000:
        text = text[:3990] + "\n..."

    msg = None
    if getattr(update, "message", None) is not None:
        msg = update.message
    elif getattr(update, "effective_message", None) is not None:
        msg = update.effective_message
    elif getattr(update, "edited_message", None) is not None:
        msg = update.edited_message
    elif getattr(update, "callback_query", None) is not None and getattr(update.callback_query, "message", None) is not None:
        msg = update.callback_query.message

    clean_text = (
        text.replace("**", "*")
        .replace("```", "")
        .replace("`", "")
        .replace("[", "(")
        .replace("]", ")")
    )

    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup

    if msg and hasattr(msg, "reply_text"):
        if parse_mode:
            try:
                return await msg.reply_text(text, parse_mode=parse_mode, **kwargs)
            except Exception as e:
                logger.warning("Markdown reply failed (%s), falling back to plain text", e)
        try:
            return await msg.reply_text(text, parse_mode=None, **kwargs)
        except Exception as e:
            logger.warning("Plain reply failed (%s), falling back to clean text", e)
            try:
                return await msg.reply_text(clean_text, parse_mode=None, **kwargs)
            except Exception as e2:
                logger.warning("Clean text reply failed (%s)", e2)

    chat = getattr(update, "effective_chat", None) or (getattr(msg, "chat", None) if msg else None)
    chat_id = getattr(chat, "id", None) if chat else None
    _bot = bot or getattr(update, "_bot", None)
    if not _bot and hasattr(update, "get_bot"):
        try:
            _bot = update.get_bot()
        except Exception:
            pass

    if chat_id and _bot and hasattr(_bot, "send_message"):
        if parse_mode:
            try:
                return await _bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs)
            except Exception as e:
                logger.warning("Markdown send_message failed (%s), falling back to plain text", e)
        try:
            return await _bot.send_message(chat_id=chat_id, text=text, parse_mode=None, **kwargs)
        except Exception as e:
            logger.warning("send_message failed (%s), trying clean text", e)
            try:
                return await _bot.send_message(chat_id=chat_id, text=clean_text, parse_mode=None, **kwargs)
            except Exception as e2:
                logger.error("All reply and send_message attempts failed: %s", e2)
                return None

    return None


_safe_reply = safe_reply


async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle /brain and subcommands:
    /brain [status]
    /brain reset
    /brain relearn [SYM] [YEARS]
    /brain rules
    /brain flaws
    /brain on | off
    /brain help
    """
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        args: List[str] = list(getattr(ctx, "args", []) or [])
        subcmd = args[0].lower() if args else "status"

        if subcmd in ("status", "info"):
            text = brain.summary()
            await safe_reply(update, text, parse_mode="Markdown")

        elif subcmd in ("reset", "clear"):
            res = brain.reset_memory()
            msg = (
                "🧹 *Autonomous Learner Memory Reset*\n"
                "• All learned bans, weak regimes, and agent weights have been reset.\n"
                f"• Baseline restored: Min score `≥{res['min_score']:.2f}`, "
                f"RR `≥1:{res['min_rr']:.2f}`, Agreement `≥{res['min_agreement']}`, "
                f"Conviction `≥{res['conviction_floor']:.0%}`\n"
                "• Trade logs cleared. Fresh learning cycle started."
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        elif subcmd in ("relearn", "train", "bootstrap"):
            sym = args[1].upper() if len(args) > 1 else "XAUUSD"
            years = 5.0
            if len(args) > 2:
                try:
                    years = max(0.5, min(5.0, float(args[2])))
                except ValueError:
                    pass
            await safe_reply(update, f"⏳ Re-learning from {sym} history (up to {years:.1f}y)...", parse_mode=None)
            res = brain.bootstrap(symbol=sym, years=years)
            rep = (
                f"✅ *Autonomous Retraining Complete ({sym})*\n"
                f"• Replayed Trades: `{res.get('trades', 0)}`\n"
                f"• Replay Expectancy: `{res.get('expectancy', 0.0):+.3f}R`\n"
                f"• Flaws Mined: `{res.get('flaws', 0)}`\n"
                f"• Active Banned Setups: `{res.get('banned_setups', 0)}`\n"
                f"• Banned UTC Hours: `{res.get('banned_hours', [])}`\n"
                f"• Filter Thresholds: score `≥{res.get('min_score', 1.8):.2f}` | RR `≥{res.get('min_rr', 1.8):.2f}`\n"
                f"_Learned knowledge persisted to memory._"
            )
            await safe_reply(update, rep, parse_mode="Markdown")

        elif subcmd in ("rules", "filters", "rule"):
            text = brain.format_rules()
            await safe_reply(update, text, parse_mode="Markdown")

        elif subcmd in ("flaws", "flaw", "sanctions"):
            text = brain.format_flaws()
            await safe_reply(update, text, parse_mode="Markdown")

        elif subcmd in ("on", "enable", "start", "1", "true"):
            brain.set_enabled(True)
            msg = (
                "🟢 *Autonomous Learner Filter Activated*\n"
                "• Live signals are actively checked against learned filters.\n"
                "• Banned setups, weak hours, and low expectancy patterns will be vetoed.\n"
                "• Regime size multipliers are active."
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        elif subcmd in ("off", "disable", "pause", "stop", "0", "false"):
            brain.set_enabled(False)
            msg = (
                "🔴 *Autonomous Learner Filter Paused*\n"
                "• Live signal vetoing is disabled (signals will pass through unblocked).\n"
                "• Autonomous learner will continue logging trade outcomes in background."
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        elif subcmd in ("help", "commands"):
            msg = (
                "🧠 *Autonomous Learner Commands:*\n"
                "• `/brain` or `/brain status` — Overall status, memory & win rates\n"
                "• `/brain rules` — Active learned thresholds, bans & agent weights\n"
                "• `/brain flaws` — Detected flaws and current sanctions\n"
                "• `/brain on` / `/brain off` — Enable or pause active signal filtering\n"
                "• `/brain relearn [SYM] [YEARS]` — Bootstrap memory from up to 5Y data\n"
                "• `/brain reset` — Clear memory & restore clean baseline defaults"
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        else:
            # Fall back to summary with usage hint
            text = brain.summary() + "\n\n_Subcommands: `/brain status`, `/brain rules`, `/brain flaws`, `/brain relearn`, `/brain reset`, `/brain on|off`_"
            await safe_reply(update, text, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error in /brain command: %s", e)
        await safe_reply(update, f"Brain error: {e}", parse_mode=None)


async def relearn_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Force a fresh 5y bootstrap (optional; not required for normal operation)."""
    sym = "XAUUSD"
    years = 5.0
    if ctx.args:
        sym = ctx.args[0].upper()
        if len(ctx.args) > 1:
            try:
                years = max(0.5, min(5.0, float(ctx.args[1])))
            except ValueError:
                pass

    await safe_reply(update, f"⏳ Re-learning from {sym} history (up to {years:.1f}y)...", parse_mode=None)
    try:
        from autonomous_learner import get_brain
        res = get_brain(auto_bootstrap=False).bootstrap(symbol=sym, years=years)
        rep = (
            f"✅ *Autonomous Retraining Complete ({sym})*\n"
            f"• Replayed Trades: `{res.get('trades', 0)}`\n"
            f"• Replay Expectancy: `{res.get('expectancy', 0.0):+.3f}R`\n"
            f"• Flaws Mined: `{res.get('flaws', 0)}`\n"
            f"• Active Banned Setups: `{res.get('banned_setups', 0)}`\n"
            f"• Banned UTC Hours: `{res.get('banned_hours', [])}`\n"
            f"• Filter Thresholds: score `≥{res.get('min_score', 1.8):.2f}` | RR `≥{res.get('min_rr', 1.8):.2f}`\n"
            f"_Learned knowledge persisted to memory._"
        )
        await safe_reply(update, rep, parse_mode="Markdown")
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
