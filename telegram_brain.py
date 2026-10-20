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
            await query.answer()
        except Exception:
            pass

    from autonomous_learner import get_brain
    brain = get_brain(auto_bootstrap=False)
    action = data.split(":", 1)[1] if ":" in data else ""

    if action in ("off", "disable", "pause"):
        brain.set_enabled(False)
        try:
            from ai_advanced_suite import get_ai_suite
            get_ai_suite().set_enabled(False)
        except Exception:
            pass
        msg = (
            "🔴 *Autonomous Learner Filter Paused*\n"
            "• Trade signal vetoing is **DISABLED**.\n"
            "• All autotrade setups will pass through without hour or setup bans.\n"
            "• Turn back on anytime with `/learner on` or `/veto on`."
        )
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(False))
        return True

    elif action in ("on", "enable", "activate"):
        brain.set_enabled(True)
        try:
            from ai_advanced_suite import get_ai_suite
            get_ai_suite().set_enabled(True)
        except Exception:
            pass
        msg = (
            "🟢 *Autonomous Learner Filter Activated*\n"
            "• Trade signal vetoing is **ACTIVE**.\n"
            "• Signals are protected against learned flaw hours and bad setups.\n"
            "• Pause anytime with `/learner off` or `/veto off`."
        )
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))
        return True

    elif action == "toggle":
        new_val = not getattr(brain.knowledge, "enabled", True)
        brain.set_enabled(new_val)
        try:
            from ai_advanced_suite import get_ai_suite
            get_ai_suite().set_enabled(new_val)
        except Exception:
            pass
        st = "ACTIVATED 🟢" if new_val else "PAUSED 🔴"
        msg = (
            f"🧠 *Autonomous Learner Filter:* `{st}`\n"
            f"• Signal vetoing is {'active' if new_val else 'disabled'}."
        )
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(new_val))
        return True

    elif action.startswith("unban:"):
        h_str = action.split(":", 1)[1]
        try:
            h = int(h_str) % 24
            ok = brain.unban_hour(h)
            if ok:
                msg = f"🔓 *Unbanned UTC Hour {h:02d}:00*\n• Trades during {h:02d}:00 UTC will now execute freely."
            else:
                msg = f"ℹ️ *UTC Hour {h:02d}:00 was not in the banned list.*"
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        except Exception as e:
            await safe_reply(update, f"Unban error: {e}", parse_mode=None)
        return True

    elif action.startswith("unban_wd:") or action.startswith("unban_weekday:"):
        w_str = action.split(":", 1)[1]
        try:
            w = int(w_str) % 7
            wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            w_name = wd_names[w]
            brain.unban_weekday(w)
            msg = (
                f"🔓 *Unbanned {w_name} (Weekday {w})*\n"
                f"• Trades on {w_name}s will now execute freely without autonomous veto.\n"
                f"• Weekday bans disabled by policy to maintain continuous trading."
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        except Exception as e:
            await safe_reply(update, f"Unban weekday error: {e}", parse_mode=None)
        return True

    elif action in ("clearweekdays", "clearweekday", "clear_weekdays"):
        cnt = brain.clear_banned_weekdays()
        msg = f"🔓 *Cleared all {cnt} banned weekdays*\n• All days of the week are now completely unblocked."
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    elif action in ("clearhours", "clearhour"):
        cnt = brain.clear_banned_hours()
        msg = f"🔓 *Cleared all {cnt} banned UTC hours*\n• All trading hours are now unblocked."
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    elif action in ("clearbans", "clearallbans"):
        res = brain.clear_bans()
        msg = (
            f"🔓 *Cleared all active learned bans*\n"
            f"• Hours cleared: `{res['hours']}`\n"
            f"• Weekdays cleared: `{res['weekdays']}`\n"
            f"• Setups cleared: `{res['setups']}`"
        )
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    elif action == "rules":
        text = brain.format_rules()
        await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    elif action == "flaws":
        text = brain.format_flaws()
        await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    elif action == "reset":
        res = brain.reset_memory()
        msg = (
            "🧹 *Autonomous Learner Memory Reset*\n"
            "• Baseline defaults restored.\n"
            "• All learned bans and flaw memory cleared."
        )
        await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))
        return True

    elif action == "status":
        text = brain.summary()
        await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
        return True

    return False


async def learner_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle /learner and /veto commands:
    /learner [status]
    /learner on | off
    /learner unban <hour|setup|hours>
    /learner clearhours | clearbans
    /learner rules | flaws | reset
    """
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        args: List[str] = list(getattr(ctx, "args", []) or [])
        subcmd = args[0].lower() if args else "status"

        if subcmd in ("on", "enable", "start", "1", "true"):
            brain.set_enabled(True)
            try:
                from ai_advanced_suite import get_ai_suite
                get_ai_suite().set_enabled(True)
            except Exception:
                pass
            msg = (
                "🟢 *Autonomous Learner Filter Activated*\n"
                "• Live signals are actively checked against learned filters.\n"
                "• Flawed hours, banned setups, and low-expectancy patterns will be vetoed.\n"
                "• Combined AI & Veto protection is active.\n\n"
                "💡 _To pause vetoing anytime: `/learner off` or `/veto off`_"
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))

        elif subcmd in ("off", "disable", "pause", "stop", "0", "false"):
            brain.set_enabled(False)
            try:
                from ai_advanced_suite import get_ai_suite
                get_ai_suite().set_enabled(False)
            except Exception:
                pass
            msg = (
                "🔴 *Autonomous Learner Filter Paused*\n"
                "• Signal vetoing is **DISABLED** (signals pass through unblocked).\n"
                "• Autonomous learner will continue logging trade outcomes in background.\n\n"
                "💡 _To re-enable vetoing anytime: `/learner on` or `/veto on`_"
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(False))

        elif subcmd in ("unban", "allow"):
            target = args[1].lower() if len(args) > 1 else ""
            wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

            # Handle: /learner unban weekday 0 or /learner unban wd mon
            if target in ("weekday", "wd", "day") and len(args) > 2:
                target = args[2].lower()
            elif target in ("hour", "hours") and len(args) > 2 and args[2].isdigit():
                target = args[2]

            w = brain.parse_weekday(target) if hasattr(brain, "parse_weekday") else None
            # If target explicitly named a day or matches weekday map
            if w is not None and (target in getattr(brain, "WEEKDAY_MAP", {}) or (target.isdigit() and int(target) in brain.knowledge.banned_weekdays and int(target) not in brain.knowledge.banned_hours)):
                ok = brain.unban_weekday(w)
                w_name = wd_names[w]
                if ok:
                    msg = f"🔓 *Unbanned {w_name} (Weekday {w})*\n• Trades on {w_name}s will no longer be blocked by Autonomous Learner."
                else:
                    msg = f"ℹ️ *{w_name} (Weekday {w}) was not in the banned weekdays list.*"
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target.isdigit():
                h = int(target) % 24
                if h in brain.knowledge.banned_weekdays and h not in brain.knowledge.banned_hours:
                    ok = brain.unban_weekday(h)
                    w_name = wd_names[h % 7]
                    msg = f"🔓 *Unbanned {w_name} (Weekday {h})*\n• Trades on {w_name}s will no longer be blocked."
                else:
                    ok = brain.unban_hour(h)
                    if ok:
                        msg = f"🔓 *Unbanned UTC Hour {h:02d}:00*\n• Trades during hour {h:02d}:00 UTC will no longer be blocked."
                    else:
                        msg = f"ℹ️ *UTC Hour {h:02d}:00 was not in the banned list.*"
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target in ("weekdays", "allweekdays", "weekday"):
                cnt = brain.clear_banned_weekdays()
                msg = f"🔓 *Cleared all {cnt} banned weekdays*\n• All days of the week are now completely unblocked."
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target in ("all", "allbans", "everything"):
                res = brain.clear_bans()
                msg = (
                    f"🔓 *Cleared all active learned bans*\n"
                    f"• Hours cleared: `{res['hours']}`\n"
                    f"• Weekdays cleared: `{res['weekdays']}`\n"
                    f"• Setups cleared: `{res['setups']}`"
                )
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target in ("hours", "allhours"):
                cnt = brain.clear_banned_hours()
                msg = f"🔓 *Cleared all {cnt} banned UTC hours*\n• All trading hours are now unblocked."
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target:
                removed = brain.unban_setup(target)
                if removed:
                    msg = f"🔓 *Unbanned Setup(s):* `{', '.join(removed)}`"
                else:
                    msg = f"ℹ️ *No banned setups matching '{target}' found.*"
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            else:
                msg = (
                    "ℹ️ *Usage for unbanning:*\n"
                    "• `/learner unban monday` (or `mon`, `tue`, `wed`, etc.)\n"
                    "• `/learner unban weekday 0`\n"
                    "• `/learner unban weekdays` (clears all weekday bans)\n"
                    "• `/learner unban <hour>` (e.g. `/learner unban 14`)\n"
                    "• `/learner unban hours` (clears all hour bans)\n"
                    "• `/learner unban <setup_name>`\n"
                    "• `/learner clearbans` or `/learner unban all`"
                )
                await safe_reply(update, msg, parse_mode="Markdown")

        elif subcmd in ("clearweekdays", "clearweekday", "unbanweekdays"):
            cnt = brain.clear_banned_weekdays()
            msg = f"🔓 *Cleared all {cnt} banned weekdays*\n• All days of the week are now completely unblocked."
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("clearhours", "clearhour"):
            cnt = brain.clear_banned_hours()
            msg = f"🔓 *Cleared all {cnt} banned UTC hours*\n• All trading hours are now unblocked."
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("clearbans", "clearallbans", "unbanall"):
            res = brain.clear_bans()
            msg = (
                f"🔓 *Cleared all active learned bans*\n"
                f"• Hours cleared: `{res['hours']}`\n"
                f"• Weekdays cleared: `{res['weekdays']}`\n"
                f"• Setups cleared: `{res['setups']}`"
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("weekdaybans", "weekdayban"):
            opt = args[1].lower() if len(args) > 1 else "status"
            if opt in ("on", "1", "true", "enable"):
                brain.set_allow_weekday_bans(True)
                msg = "⚠️ *Weekday Bans Policy ENABLED*\n• Autonomous learner may veto signals on historically weak weekdays."
            elif opt in ("off", "0", "false", "disable"):
                brain.set_allow_weekday_bans(False)
                msg = "🟢 *Weekday Bans Policy DISABLED*\n• Autonomous learner will NEVER veto signals based on the day of the week."
            else:
                cur = getattr(brain.knowledge, "allow_weekday_bans", False)
                st = "🟢 DISABLED (Never ban days)" if not cur else "🔴 ENABLED"
                msg = f"📅 *Learner Weekday Ban Policy:* `{st}`\n• Use `/learner weekdaybans off` or `/learner weekdaybans on`."
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("rules", "filters", "rule"):
            text = brain.format_rules()
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("flaws", "flaw", "sanctions"):
            text = brain.format_flaws()
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("reset", "clear"):
            res = brain.reset_memory()
            msg = (
                "🧹 *Autonomous Learner Memory Reset*\n"
                "• All learned bans, weak regimes, and agent weights have been reset.\n"
                "• Baseline defaults restored.\n"
                "• Trade logs cleared. Fresh learning cycle started."
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))

        elif subcmd in ("status", "info"):
            text = brain.summary()
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("help", "commands"):
            msg = (
                "🧠 *Autonomous Learner & Veto Commands:*\n"
                "• `/learner on` or `/veto on` — Activate learned signal vetoing\n"
                "• `/learner off` or `/veto off` — Pause signal vetoing (pass all trades)\n"
                "• `/learner unban <hour>` — Unban a specific UTC hour (e.g. `/learner unban 14`)\n"
                "• `/learner clearhours` — Clear all banned hours\n"
                "• `/learner clearbans` — Clear all bans (hours, weekdays, setups)\n"
                "• `/learner rules` — View active learned thresholds & bans\n"
                "• `/learner flaws` — View detected statistical flaws\n"
                "• `/learner reset` — Reset memory to baseline defaults\n"
                "• `/autotrade veto on|off` — Toggle veto filter via autotrade"
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        else:
            text = brain.summary() + "\n\n_Quick toggle: `/learner on` | `/learner off` | `/learner unban <hour>`_"
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

    except Exception as e:
        logger.exception("Error in /learner command: %s", e)
        await safe_reply(update, f"Learner error: {e}", parse_mode=None)


async def veto_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Unified /veto command — confirms pure Multi-Agent & Brain mode or delegates subcommands."""
    args = list(getattr(ctx, "args", []) or [])
    if args:
        sub = args[0].lower()
        if sub not in ("status", "dash", "dashboard", "hub"):
            await learner_command(update, ctx)
            return
    msg = (
        "🟢 **Pure Multi-Agent & Brain Mode Active**\n\n"
        "• **Signal Engine:** Multi-Agent Consensus (`multi_agent_trading_bot.py`)\n"
        "  _Technical, Sentiment, Risk, MT5 Chart, and CIO Arbiter agents generate & approve trades._\n\n"
        "• **Learning & Sizing:** Autonomous Brain (`autonomous_learner.py`)\n"
        "  _Learns from trade outcomes, tracks win rate/expectancy, and scales regime lot sizes._\n\n"
        "• **Signal Vetoing:** **Disabled / Removed**\n"
        "  _Consensus signals flow directly to execution without getting blocked by veto filters._\n\n"
        "💡 _View live learning metrics and journal stats anytime with `/brain`._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle /brain and subcommands:
    /brain [status]
    /brain reset
    /brain relearn [SYM] [YEARS]
    /brain rules
    /brain flaws
    /brain on | off
    /brain unban <hour|setup|all>
    /brain clearhours | clearbans
    /brain help
    """
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        args: List[str] = list(getattr(ctx, "args", []) or [])
        subcmd = args[0].lower() if args else "status"

        if subcmd in ("status", "info"):
            text = brain.summary()
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

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
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))

        elif subcmd in ("relearn", "train", "bootstrap"):
            sym = args[1].upper() if len(args) > 1 else "XAUUSD"
            years = 5.0
            if len(args) > 2:
                try:
                    years = max(0.5, min(5.0, float(args[2])))
                except ValueError:
                    pass
            if sym in ("ALL", "HEAVY"):
                await safe_reply(update, "⏳ *Starting Heavy Bot Training Pipeline across all assets*...", parse_mode="Markdown")
                from heavy_trainer import HeavyTrainer
                trainer = HeavyTrainer(years=years)
                summary = trainer.train_all(heavy=True)
                rep = trainer.format_markdown_report(summary)
                await safe_reply(update, rep, parse_mode="Markdown")
                return

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
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("flaws", "flaw", "sanctions"):
            text = brain.format_flaws()
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("on", "enable", "start", "1", "true"):
            brain.set_enabled(True)
            msg = (
                "🟢 *Autonomous Learner Filter Activated*\n"
                "• Live signals are actively checked against learned filters.\n"
                "• Banned setups, weak hours, and low expectancy patterns will be vetoed.\n"
                "• Regime size multipliers are active."
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(True))

        elif subcmd in ("off", "disable", "pause", "stop", "0", "false"):
            brain.set_enabled(False)
            msg = (
                "🔴 *Autonomous Learner Filter Paused*\n"
                "• Live signal vetoing is disabled (signals will pass through unblocked).\n"
                "• Autonomous learner will continue logging trade outcomes in background."
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(False))

        elif subcmd in ("unban", "allow"):
            target = args[1].lower() if len(args) > 1 else ""
            if target in ("hour", "hours") and len(args) > 2 and args[2].isdigit():
                target = args[2]
            if target.isdigit():
                h = int(target) % 24
                ok = brain.unban_hour(h)
                if ok:
                    msg = f"🔓 *Unbanned UTC Hour {h:02d}:00*\n• Trades during hour {h:02d}:00 UTC will no longer be blocked."
                else:
                    msg = f"ℹ️ *UTC Hour {h:02d}:00 was not in the banned list.*"
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target in ("all", "hours", "allhours"):
                cnt = brain.clear_banned_hours()
                msg = f"🔓 *Cleared all {cnt} banned UTC hours*\n• All trading hours are now unblocked."
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            elif target:
                removed = brain.unban_setup(target)
                if removed:
                    msg = f"🔓 *Unbanned Setup(s):* `{', '.join(removed)}`"
                else:
                    msg = f"ℹ️ *No banned setups matching '{target}' found.*"
                await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))
            else:
                msg = (
                    "ℹ️ *Usage for unbanning:*\n"
                    "• `/brain unban <hour>` (e.g. `/brain unban 14`)\n"
                    "• `/brain unban hours` (clears all hour bans)\n"
                    "• `/brain unban <setup_name>`\n"
                    "• `/brain clearbans` (clears all active bans)"
                )
                await safe_reply(update, msg, parse_mode="Markdown")

        elif subcmd in ("clearhours", "clearhour"):
            cnt = brain.clear_banned_hours()
            msg = f"🔓 *Cleared all {cnt} banned UTC hours*\n• All trading hours are now unblocked."
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("clearbans", "clearallbans", "unbanall"):
            res = brain.clear_bans()
            msg = (
                f"🔓 *Cleared all active learned bans*\n"
                f"• Hours cleared: `{res['hours']}`\n"
                f"• Weekdays cleared: `{res['weekdays']}`\n"
                f"• Setups cleared: `{res['setups']}`"
            )
            await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

        elif subcmd in ("help", "commands"):
            msg = (
                "🧠 *Autonomous Learner Commands:*\n"
                "• `/train [heavy|quick|status]` — Deep multi-asset AI & autonomous training\n"
                "• `/brain` or `/brain status` — Overall status, memory & win rates\n"
                "• `/brain rules` — Active learned thresholds, bans & agent weights\n"
                "• `/brain flaws` — Detected flaws and current sanctions\n"
                "• `/brain on` / `/brain off` — Enable or pause active signal filtering\n"
                "• `/brain unban <hour>` — Unban a specific UTC hour\n"
                "• `/brain clearhours` — Clear all banned hours\n"
                "• `/brain relearn [SYM|ALL] [YEARS]` — Bootstrap memory from up to 5Y data\n"
                "• `/brain reset` — Clear memory & restore clean baseline defaults"
            )
            await safe_reply(update, msg, parse_mode="Markdown")

        else:
            # Fall back to summary with usage hint
            text = brain.summary() + "\n\n_Subcommands: `/train`, `/brain status`, `/brain rules`, `/brain flaws`, `/brain unban <hour>`, `/brain relearn`, `/brain reset`, `/brain on|off`_"
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_learner_toggle_keyboard(brain.is_enabled()))

    except Exception as e:
        logger.exception("Error in /brain command: %s", e)
        await safe_reply(update, f"Brain error: {e}", parse_mode=None)


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
        ("veto", veto_command),
        ("learnerveto", veto_command),
    ]:
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)
    logger.info("Registered /brain /relearn /train /learner /veto")
