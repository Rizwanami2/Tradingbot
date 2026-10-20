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


def build_learner_block_keyboard(note: str = ""):
    """Build interactive action buttons when a trade setup is blocked by Autonomous Learner."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        import re
        note_str = str(note or "")
        m_hr = re.search(r"banned hour (\d+)", note_str, re.IGNORECASE)
        m_wd = re.search(r"banned weekday (\d+)", note_str, re.IGNORECASE)
        wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        buttons = []

        if m_wd:
            w = int(m_wd.group(1)) % 7
            w_name = wd_names[w]
            buttons.append([
                InlineKeyboardButton(f"🔓 Unban {w_name}", callback_data=f"learner:unban_wd:{w}"),
                InlineKeyboardButton("🔴 Turn Veto OFF", callback_data="learner:off"),
            ])
            buttons.append([
                InlineKeyboardButton("📜 View Rules", callback_data="learner:rules"),
                InlineKeyboardButton("🔓 Clear All Bans", callback_data="learner:clearbans"),
            ])
        elif m_hr:
            h = int(m_hr.group(1)) % 24
            buttons.append([
                InlineKeyboardButton(f"🔓 Unban Hour {h:02d}:00", callback_data=f"learner:unban:{h}"),
                InlineKeyboardButton("🔴 Turn Veto OFF", callback_data="learner:off"),
            ])
            buttons.append([
                InlineKeyboardButton("📜 View Rules", callback_data="learner:rules"),
                InlineKeyboardButton("🟢 Turn Veto ON", callback_data="learner:on"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton("🔴 Turn Veto OFF", callback_data="learner:off"),
                InlineKeyboardButton("📜 View Rules", callback_data="learner:rules"),
            ])
            buttons.append([
                InlineKeyboardButton("🟢 Turn Veto ON", callback_data="learner:on"),
                InlineKeyboardButton("🔓 Clear All Bans", callback_data="learner:clearbans"),
            ])
        return InlineKeyboardMarkup(buttons)
    except Exception as e:
        logger.debug("build_learner_block_keyboard error: %s", e)
        return None


def build_learner_toggle_keyboard(enabled: bool = True):
    """Build interactive control keyboard for Autonomous Learner."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        state_btn = (
            InlineKeyboardButton("🔴 Pause Veto (OFF)", callback_data="learner:off")
            if enabled
            else InlineKeyboardButton("🟢 Activate Veto (ON)", callback_data="learner:on")
        )
        buttons = [
            [state_btn, InlineKeyboardButton("📜 Rules", callback_data="learner:rules")],
            [InlineKeyboardButton("🔍 Mined Flaws", callback_data="learner:flaws"), InlineKeyboardButton("🔓 Clear Hour Bans", callback_data="learner:clearhours")],
            [InlineKeyboardButton("🔄 Refresh Status", callback_data="learner:status"), InlineKeyboardButton("🧹 Reset Defaults", callback_data="learner:reset")],
        ]
        return InlineKeyboardMarkup(buttons)
    except Exception as e:
        logger.debug("build_learner_toggle_keyboard error: %s", e)
        return None


async def handle_learner_callback(data: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle interactive inline buttons for Autonomous Learner and veto filtering."""
    if not (data.startswith("learner:") or data.startswith("brain:")):
        return False
    query = getattr(update, "callback_query", None)
    if query:
        try:
            await query.answer("AI & Veto features have been completely removed.")
            await query.edit_message_text(
                "ℹ️ **Autonomous Learner & Veto Features Completely Removed**\n"
                "• All veto filters, hour bans, and setup bans have been removed.\n"
                "• All trade signals execute directly.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    msg = (
        "ℹ️ **Autonomous Learner & Veto Features Completely Removed**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• All veto filters, hour bans, weekday bans, and setup bans have been disabled.\n"
        "• All trade signals execute directly without veto blocks."
    )
    await safe_reply(update, msg, parse_mode="Markdown")
    return True


async def learner_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ️ **Autonomous Learner & Veto Features Completely Removed**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• All veto filters, hour bans, weekday bans, and setup bans have been disabled.\n"
        "• All trade signals execute directly without veto blocks."
    )
    await safe_reply(update, msg, parse_mode="Markdown")
    return


async def veto_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Unified /veto command — informs that veto features have been removed."""
    await learner_command(update, ctx)


async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inform that autonomous learner & veto features have been removed."""
    await learner_command(update, ctx)


async def train_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Trigger advanced heavy training across all AI and autonomous models."""
    subcmd = (ctx.args[0].lower() if ctx.args else "heavy")
    if subcmd == "status":
        try:
            if os.path.exists("heavy_training_report.json"):
                with open("heavy_training_report.json", "r", encoding="utf-8") as f:
                    rep = json.load(f)
                from heavy_trainer import HeavyTrainer
                text = HeavyTrainer().format_markdown_report(rep)
                await safe_reply(update, text, parse_mode="Markdown")
                return
        except Exception as e:
            await safe_reply(update, f"Could not load report: {e}", parse_mode=None)
            return

    heavy = (subcmd != "quick")
    await safe_reply(
        update,
        f"⏳ *Starting {'Heavy' if heavy else 'Quick'} Bot Training Pipeline*...\n"
        f"_Simulating multi-asset history across BTC, Gold, ETH, SOL, EUR, mining flaws, and training AI weights._",
        parse_mode="Markdown",
    )
    try:
        from heavy_trainer import HeavyTrainer
        trainer = HeavyTrainer()
        summary = trainer.train_all(heavy=heavy)
        report = trainer.format_markdown_report(summary)
        await safe_reply(update, report, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /train command: %s", e)
        await safe_reply(update, f"Training error: {e}", parse_mode=None)


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

    if sym in ("ALL", "HEAVY"):
        await safe_reply(update, "⏳ *Starting Heavy Bot Training Pipeline across all assets*...", parse_mode="Markdown")
        try:
            from heavy_trainer import HeavyTrainer
            trainer = HeavyTrainer(years=years)
            summary = trainer.train_all(heavy=True)
            rep = trainer.format_markdown_report(summary)
            await safe_reply(update, rep, parse_mode="Markdown")
            return
        except Exception as e:
            logger.exception("Error in /relearn all command: %s", e)
            await safe_reply(update, f"Relearn error: {e}", parse_mode=None)
            return

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
        ("train", train_command),
        ("learner", learner_command),
        ("autonomous", learner_command),
        ("veto", learner_command),
        ("learnerveto", learner_command),
    ]:
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)
    logger.info("Registered /brain /relearn /train /learner /veto")
