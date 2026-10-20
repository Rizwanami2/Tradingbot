"""Telegram /ai commands and interactive manual control hub for AI Advanced Suite."""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler

from telegram_brain import safe_reply

logger = logging.getLogger("TelegramAI")


def build_ai_dashboard(suite=None, mode: Optional[str] = None) -> Tuple[str, InlineKeyboardMarkup]:
    """Build modern markdown dashboard with classic simple mode by default and deep advanced view."""
    if suite is None:
        from ai_advanced_suite import get_ai_suite
        suite = get_ai_suite()

    s = suite.get_summary_dict()

    from autonomous_learner import get_brain
    brain = get_brain(auto_bootstrap=False)

    active_mode = str(mode or suite.state.get("ui_mode", "classic")).lower()
    if active_mode not in ("classic", "advanced"):
        active_mode = "classic"

    ai_on = bool(s["enabled"])
    brain_on = bool(brain.is_enabled())

    # Combined AI & Veto status indicators
    if ai_on and brain_on:
        status_icon = "🟢 ACTIVE / ON (AI & Veto Combined)"
    elif ai_on:
        status_icon = "🟡 PARTIAL (AI Suite Active, Veto Paused)"
    elif brain_on:
        status_icon = "🟡 PARTIAL (Veto Active, AI Suite Paused)"
    else:
        status_icon = "🔴 PAUSED / OFF (All Filters Disabled)"

    mode_icon = "🕹️ MANUAL (Trader Supervised / Advisory)" if s["is_manual"] else "🤖 AUTO (Strict Algorithmic Gating)"

    # Learner veto active ban status
    b_hrs = getattr(brain.knowledge, "banned_hours", [])
    b_wds = getattr(brain.knowledge, "banned_weekdays", [])
    b_setups = getattr(brain.knowledge, "banned_setups", {})
    if not b_hrs and not b_wds and not b_setups:
        veto_summary = "🟢 Clean (No Active Bans)"
    else:
        ban_parts = []
        if b_hrs:
            ban_parts.append(f"Hours {sorted(b_hrs)}")
        if b_wds:
            wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            ban_parts.append(f"Days {[wd_names[w % 7] for w in b_wds]}")
        if b_setups:
            ban_parts.append(f"{len(b_setups)} Setups")
        veto_summary = f"🔴 Banned: {', '.join(ban_parts)}"

    if active_mode == "classic":
        text = (
            "🤖 **AI Advanced Suite & Manual Control Hub** (Classic Mode)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• **AI Suite Status:** `{status_icon}`\n"
            f"• **Control Mode:** `{mode_icon}`\n"
            f"• **Learner Veto:** `{veto_summary}`\n"
            f"• **Scorecard Gate:** Min `{s['min_scorecard']:.0f}` pts\n"
            f"• **Today Performance:** `{s['wins']}W / {s['losses']}L` | `{s['realized_r']:+.2f}R` (`{s['trades_today']}` trades)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 _Combined AI & Veto active. Simple 1-tap controls below:_"
        )

        btn_toggle = (
            InlineKeyboardButton("🔴 Turn OFF (Pause)", callback_data="ai:toggle")
            if (ai_on or brain_on)
            else InlineKeyboardButton("🟢 Turn ON (Activate)", callback_data="ai:toggle")
        )
        btn_mode = (
            InlineKeyboardButton("🤖 Switch to Auto", callback_data="ai:mode:auto")
            if s["is_manual"]
            else InlineKeyboardButton("🕹️ Switch to Manual", callback_data="ai:mode:manual")
        )
        markup = InlineKeyboardMarkup([
            [btn_toggle, btn_mode],
            [
                InlineKeyboardButton("🔓 Clear All Bans", callback_data="ai:clearbans"),
                InlineKeyboardButton("⚙️ Advanced View", callback_data="ai:view:advanced"),
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="ai:refresh"),
            ],
        ])
        return text, markup

    # Advanced View
    persona_map = {"low": "🟢 LOW (Conservative)", "med": "🟡 MED (Balanced)", "high": "🔴 HIGH (Aggressive)"}
    persona_str = persona_map.get(s["risk_persona"], s["risk_persona"].upper())

    bias_str = "⚪ NEUTRAL (Any)"
    if s["bias_day"] == "long":
        bias_str = "🟢 LONG ONLY"
    elif s["bias_day"] == "short":
        bias_str = "🔴 SHORT ONLY"

    learn_str = "❄️ FROZEN (Observe Only)" if s["learn_frozen"] else "🟢 ACTIVE (Continuous Feedback)"

    # Circuit breakers & safety locks
    if s["revenge_active"]:
        revenge_str = f"🔴 LOCKED ({s['revenge_left_sec']}s left)"
    else:
        revenge_str = "🟢 CLEAR"

    if s["loss_brake_tripped"]:
        loss_brake_str = f"🔴 TRIPPED ({s['realized_r']:+.2f}R)"
    else:
        loss_brake_str = "🟢 NORMAL"

    override_str = "⚡ ACTIVE (Next trade allowed)" if s["override_next"] else "⚪ None"
    score_scope = "Balance Profile Only" if s["balance_only_scorecard"] else "All Profiles"

    text = (
        "🤖 **AI Advanced Suite & Manual Control Hub** (Advanced View)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• **AI Suite Status:** `{status_icon}`\n"
        f"• **Control Mode:** `{mode_icon}`\n"
        f"• **Risk Persona:** `{persona_str}`\n"
        f"• **Day Bias:** `{bias_str}`\n"
        f"• **Learning State:** `{learn_str}`\n"
        f"• **Learner Veto:** `{veto_summary}`\n"
        f"• **Scorecard Gate:** Min `{s['min_scorecard']:.0f}` (`{score_scope}`)\n"
        f"• **Lot Ceiling:** `{s['lot_ceiling']:.2f} lots` max\n"
        f"• **Manual Override:** `{override_str}`\n\n"
        "📊 **Today Telemetry & Circuit Breakers:**\n"
        f"• **Daily Trades:** `{s['trades_today']}/{s['max_daily_trades']}` | **Realized R:** `{s['realized_r']:+.2f}R`\n"
        f"• **Record:** `{s['wins']}W / {s['losses']}L` | **Consec Losses:** `{s['consec_losses']}/{s['max_consec_losses']}`\n"
        f"• **Revenge Lock:** `{revenge_str}`\n"
        f"• **Daily Loss Brake:** `{loss_brake_str}`\n"
        f"• **Fill Rejects:** `{s['fill_rejects']}` | **Equity:** `${s['equity']:,.2f}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Deep telemetry active. Tap below to switch back to Classic or adjust:_"
    )

    btn_toggle_ai = (
        InlineKeyboardButton("🔴 Turn AI OFF", callback_data="ai:toggle")
        if (ai_on or brain_on)
        else InlineKeyboardButton("🟢 Turn AI ON", callback_data="ai:toggle")
    )
    btn_toggle_mode = (
        InlineKeyboardButton("🤖 Switch to Auto", callback_data="ai:mode:auto")
        if s["is_manual"]
        else InlineKeyboardButton("🕹️ Switch to Manual", callback_data="ai:mode:manual")
    )
    btn_freeze = (
        InlineKeyboardButton("🔥 Unfreeze Learn", callback_data="ai:unfreeze")
        if s["learn_frozen"]
        else InlineKeyboardButton("❄️ Freeze Learn", callback_data="ai:freeze")
    )

    markup = InlineKeyboardMarkup([
        [btn_toggle_ai, btn_toggle_mode],
        [
            InlineKeyboardButton("🎭 Low", callback_data="ai:persona:low"),
            InlineKeyboardButton("🎭 Med", callback_data="ai:persona:med"),
            InlineKeyboardButton("🎭 High", callback_data="ai:persona:high"),
        ],
        [
            InlineKeyboardButton("🟢 Bias Long", callback_data="ai:bias:long"),
            InlineKeyboardButton("🔴 Bias Short", callback_data="ai:bias:short"),
            InlineKeyboardButton("⚪ Clear Bias", callback_data="ai:bias:clear"),
        ],
        [
            btn_freeze,
            InlineKeyboardButton("🔓 Reset Locks", callback_data="ai:reset_locks"),
        ],
        [
            InlineKeyboardButton("⚡ Override Next", callback_data="ai:override"),
            InlineKeyboardButton("📋 AI Critique", callback_data="ai:critique"),
        ],
        [
            InlineKeyboardButton("🧠 Train Brain", callback_data="ai:train"),
            InlineKeyboardButton("❤️ Health", callback_data="ai:health"),
            InlineKeyboardButton("🔄 Refresh", callback_data="ai:refresh"),
        ],
        [
            InlineKeyboardButton("📱 Classic View (Clean)", callback_data="ai:view:classic"),
        ],
    ])
    return text, markup


async def ai_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """AI Advanced Suite & Manual Control: /ai [status|mode|manual|auto|override|reset|persona|bias|freeze|scorecard|ceiling|health|critique|train|help]"""
    try:
        from ai_advanced_suite import get_ai_suite

        suite = get_ai_suite()
        args = [a.lower() for a in (ctx.args or [])]
        sub = args[0] if args else "status"

        if sub in ("help", "commands", "?", "man"):
            msg = (
                "🤖 **AI Advanced Suite & Manual Control Guide**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• `/ai` or `/ai status` — Combined AI & Veto dashboard & live controls\n"
                "• `/ai classic` — Switch to Classic Clean Mode (concise 1-tap view)\n"
                "• `/ai advance` — Switch to Advanced Mode (deep telemetry & breakers)\n"
                "• `/ai on` / `/ai off` — Enable or disable combined AI & Veto protection\n"
                "• `/ai unban <hour|day|all>` — Unban hour (e.g. 14) or day (e.g. monday)\n"
                "• `/ai clearbans` — Clear all active bans & reset safety locks\n\n"
                "🕹️ **Manual Control & Overrides:**\n"
                "• `/ai mode manual` or `/ai manual` — Switch to Manual Control mode (gates advise without hard veto)\n"
                "• `/ai mode auto` or `/ai auto` — Switch to Autonomous Strict Gating mode\n"
                "• `/ai override` or `/ai allow` — 1-trade bypass of next veto (e.g. bypass revenge lock)\n"
                "• `/ai reset` or `/ai unlock` — Manually clear revenge lock, consec losses & loss brake\n\n"
                "⚙️ **Parameters & Risk Configuration:**\n"
                "• `/ai persona low|med|high` — Switch risk persona & size multiplier\n"
                "• `/ai bias long|short|clear` — Set or clear day directional filter\n"
                "• `/ai freeze` / `/ai unfreeze` — Freeze or resume weight updates & learning\n"
                "• `/ai scorecard <val>` — Set minimum signal scorecard (0-100, default 55)\n"
                "• `/ai scoreall on|off` — Toggle scorecard gating on all profiles vs balance only\n"
                "• `/ai ceiling <lots>` — Set max position lot size ceiling\n\n"
                "🔬 **Diagnostics & Learning:**\n"
                "• `/ai health` — Live EA bridge, tick freshness & fill rejection diagnostics\n"
                "• `/ai critique` — Weekly AI trading coach critique & rule recommendations\n"
                "• `/ai train [quick|deep]` — Run autonomous learner brain training\n"
                "• `/ai relearn [SYM]` — Retrain model on 5Y historical data"
            )
            await safe_reply(update, msg, parse_mode="Markdown")
            return

        if sub in ("classic", "simple", "clean"):
            suite.set_ui_mode("classic")
            text, markup = build_ai_dashboard(suite, mode="classic")
            await safe_reply(update, "📱 **Classic Command Mode Activated** (Clean & concise)\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("advance", "advanced", "deep", "pro"):
            suite.set_ui_mode("advanced")
            text, markup = build_ai_dashboard(suite, mode="advanced")
            await safe_reply(update, "⚙️ **Advanced Command Mode Activated** (Full deep telemetry)\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("status", "stat", "dashboard", "dash", "info", "show"):
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("on", "enable", "1", "true"):
            res = suite.set_enabled(True)
            try:
                from autonomous_learner import get_brain
                get_brain(auto_bootstrap=False).set_enabled(True)
            except Exception:
                pass
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"✅ **AI & Veto Protection ACTIVATED (ON)**\n• {res}\n• Autonomous Learner Veto: ON\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("off", "disable", "0", "false"):
            res = suite.set_enabled(False)
            try:
                from autonomous_learner import get_brain
                get_brain(auto_bootstrap=False).set_enabled(False)
            except Exception:
                pass
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"⚠️ **AI & Veto Protection PAUSED (OFF)**\n• {res}\n• Autonomous Learner Veto: OFF\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("clearbans", "clearallbans", "unbanall"):
            try:
                from autonomous_learner import get_brain
                brain = get_brain(auto_bootstrap=False)
                c_res = brain.clear_bans()
            except Exception:
                c_res = {"hours": 0, "weekdays": 0, "setups": 0}
            suite.reset_safety_locks()
            text, markup = build_ai_dashboard(suite)
            await safe_reply(
                update,
                f"🔓 **Cleared All Active Bans & Reset Safety Locks**\n"
                f"• Hours cleared: `{c_res['hours']}`\n"
                f"• Weekdays cleared: `{c_res['weekdays']}`\n"
                f"• Setups cleared: `{c_res['setups']}`\n"
                f"• Safety locks: `RESET`\n\n" + text,
                parse_mode="Markdown",
                reply_markup=markup,
            )
            return

        if sub in ("unban", "allow"):
            target = args[1].lower() if len(args) > 1 else ""
            wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            if target in ("weekday", "wd", "day") and len(args) > 2:
                target = args[2].lower()
            elif target in ("hour", "hours") and len(args) > 2 and args[2].isdigit():
                target = args[2]

            try:
                from autonomous_learner import get_brain
                brain = get_brain(auto_bootstrap=False)
                w = brain.parse_weekday(target) if hasattr(brain, "parse_weekday") else None
                if w is not None and (target in getattr(brain, "WEEKDAY_MAP", {}) or (target.isdigit() and int(target) in brain.knowledge.banned_weekdays and int(target) not in brain.knowledge.banned_hours)):
                    ok = brain.unban_weekday(w)
                    w_name = wd_names[w]
                    msg = f"🔓 **Unbanned {w_name} (Weekday {w})** from AI & Veto filter." if ok else f"ℹ️ **{w_name}** was not banned."
                elif target.isdigit():
                    h = int(target) % 24
                    if h in brain.knowledge.banned_weekdays and h not in brain.knowledge.banned_hours:
                        ok = brain.unban_weekday(h)
                        w_name = wd_names[h % 7]
                        msg = f"🔓 **Unbanned {w_name} (Weekday {h})** from AI & Veto filter."
                    else:
                        ok = brain.unban_hour(h)
                        msg = f"🔓 **Unbanned UTC Hour {h:02d}:00** from AI & Veto filter." if ok else f"ℹ️ Hour {h:02d}:00 was not banned."
                elif target in ("weekdays", "allweekdays", "weekday"):
                    cnt = brain.clear_banned_weekdays()
                    msg = f"🔓 **Cleared all {cnt} banned weekdays** from AI & Veto filter."
                elif target in ("hours", "allhours"):
                    cnt = brain.clear_banned_hours()
                    msg = f"🔓 **Cleared all {cnt} banned UTC hours** from AI & Veto filter."
                elif target in ("all", "allbans", "everything"):
                    c_res = brain.clear_bans()
                    suite.reset_safety_locks()
                    msg = f"🔓 **Cleared all active bans & reset safety locks** (Hours: {c_res['hours']}, Weekdays: {c_res['weekdays']}, Setups: {c_res['setups']})."
                elif target:
                    removed = brain.unban_setup(target)
                    msg = f"🔓 **Unbanned Setup(s):** `{', '.join(removed)}`" if removed else f"ℹ️ No banned setups matching '{target}' found."
                else:
                    msg = (
                        "ℹ️ **AI & Veto Unban Usage:**\n"
                        "• `/ai unban monday` (or `mon`, `tue`, etc.)\n"
                        "• `/ai unban <hour>` (e.g. `/ai unban 14`)\n"
                        "• `/ai unban all` or `/ai clearbans`"
                    )
                    await safe_reply(update, msg, parse_mode="Markdown")
                    return
            except Exception as e:
                msg = f"Unban error: {e}"

            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"{msg}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("mode", "control"):
            if len(args) < 2:
                m = "MANUAL (Trader Advisory)" if suite.is_manual_mode() else "AUTO (Strict Gating)"
                await safe_reply(
                    update,
                    f"Current AI control mode: `{m}`\nUsage: `/ai mode auto` or `/ai mode manual`",
                    parse_mode="Markdown",
                )
                return
            res = suite.set_control_mode(args[1])
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"🕹️ {res}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("manual", "supervised", "advisory"):
            res = suite.set_control_mode("manual")
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"🕹️ {res}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("auto", "strict", "autonomous"):
            res = suite.set_control_mode("auto")
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"🤖 {res}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("override", "allow", "bypass"):
            res = suite.set_override_next(True)
            await safe_reply(
                update,
                f"⚡ **{res}**\nThe next incoming trade signal will bypass AI gates. Use responsibly.",
                parse_mode="Markdown",
            )
            return

        if sub in ("reset", "unlock", "clear", "clearlock", "resetlock", "resetlocks"):
            res = suite.reset_safety_locks()
            text, markup = build_ai_dashboard(suite)
            await safe_reply(update, f"🔓 **{res}**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
            return

        if sub in ("persona", "risk", "riskpersona"):
            if len(args) < 2:
                await safe_reply(
                    update,
                    f"Current risk persona: `{suite.state.get('risk_persona', 'med')}`\n"
                    "Usage: `/ai persona low|med|high`",
                    parse_mode="Markdown",
                )
                return
            res = suite.set_persona(args[1])
            await safe_reply(update, f"🎭 {res}", parse_mode="Markdown")
            return

        if sub in ("bias", "daybias", "direction"):
            if len(args) < 2:
                b = suite.state.get("bias_day") or "none"
                await safe_reply(
                    update,
                    f"Current day bias: `{b}`\nUsage: `/ai bias long|short|clear`",
                    parse_mode="Markdown",
                )
                return
            res = suite.set_bias(args[1])
            await safe_reply(update, f"🧭 {res}", parse_mode="Markdown")
            return

        if sub in ("freeze", "lock", "pauselearn"):
            res = suite.set_learn_freeze(True)
            await safe_reply(update, f"❄️ {res} (Model weights frozen, observe-only)", parse_mode="Markdown")
            return

        if sub in ("unfreeze", "unlock", "resumelearn", "learn"):
            res = suite.set_learn_freeze(False)
            await safe_reply(update, f"🔥 {res} (Continuous learning & weight updates active)", parse_mode="Markdown")
            return

        if sub in ("scorecard", "minscore", "score"):
            if len(args) < 2:
                await safe_reply(
                    update,
                    f"Current minimum scorecard: `{suite.min_scorecard:.0f}`\n"
                    "Usage: `/ai scorecard <val>` (e.g. `/ai scorecard 60`)",
                    parse_mode="Markdown",
                )
                return
            try:
                val = float(args[1])
                res = suite.set_min_scorecard(val)
                await safe_reply(update, f"🎯 {res}", parse_mode="Markdown")
            except ValueError:
                await safe_reply(update, "Invalid number for scorecard. Example: `/ai scorecard 55`", parse_mode="Markdown")
            return

        if sub in ("scoreall", "balanceonly", "scoreprofile"):
            if len(args) < 2:
                st = "Balance Only" if suite.balance_mode_only_scorecard else "All Profiles"
                await safe_reply(
                    update,
                    f"Scorecard scope: `{st}`\nUsage: `/ai scoreall on` (all profiles) or `/ai scoreall off` (balance only)",
                    parse_mode="Markdown",
                )
                return
            enable_all = args[1] in ("on", "1", "all", "true")
            res = suite.set_scorecard_balance_only(not enable_all)
            await safe_reply(update, f"🎯 {res}", parse_mode="Markdown")
            return

        if sub in ("ceiling", "lotceiling", "maxlot"):
            if len(args) < 2:
                await safe_reply(
                    update,
                    f"Current lot ceiling: `{suite.lot_ceiling:.2f} lots`\nUsage: `/ai ceiling <lots>` (e.g. `/ai ceiling 2.0`)",
                    parse_mode="Markdown",
                )
                return
            try:
                val = float(args[1])
                res = suite.set_lot_ceiling(val)
                await safe_reply(update, f"📦 {res}", parse_mode="Markdown")
            except ValueError:
                await safe_reply(update, "Invalid number for lot ceiling. Example: `/ai ceiling 1.5`", parse_mode="Markdown")
            return

        if sub in ("health", "ok", "diag"):
            ok, detail = suite.health_ok()
            icon = "🟢" if ok else "🔴"
            ea_str = "Connected" if "not connected" not in detail else "Disconnected"
            msg = (
                f"❤️ **AI Advanced Suite Health Telemetry**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• **Overall Status:** {icon} `{detail.upper()}`\n"
                f"• **EA Bridge Status:** `{ea_str}`\n"
                f"• **Fill Rejections:** `{suite.state.get('fill_rejects', 0)}`\n"
                f"• **Consecutive Losses:** `{suite.state.get('consec_losses', 0)}/{suite.max_consec_losses}`\n"
                f"• **Live Health Active:** `{suite.live_health}`\n"
                f"• **Control Mode:** `{'MANUAL' if suite.is_manual_mode() else 'AUTO'}`"
            )
            await safe_reply(update, msg, parse_mode="Markdown")
            return

        if sub in ("critique", "weekly", "review"):
            prompt = suite.weekly_critique_prompt() if hasattr(suite, "weekly_critique_prompt") else "No critique data yet."
            try:
                from gemini_client import GeminiClient

                g = GeminiClient()
                if g.api_key:
                    reply = g.ask(chat_id=0, user_text=prompt, market_context="weekly AI critique")
                    await safe_reply(update, f"📋 **Weekly AI Trading Coach Critique**\n━━━━━━━━━━━━━━━━━━━━\n\n{reply}", parse_mode="Markdown")
                    return
            except Exception as _critique_err:
                logger.debug("Gemini critique fallback: %s", _critique_err)
            await safe_reply(
                update,
                "📋 **AI Weekly Critique Prompt (Set GEMINI_API_KEY for live answers):**\n\n```\n" + str(prompt)[:3500] + "\n```",
                parse_mode="Markdown",
            )
            return

        if sub in ("train", "training"):
            mode = args[1] if len(args) > 1 else "quick"
            await safe_reply(update, f"🧠 Launching Autonomous AI Learner training (`{mode}`)...", parse_mode="Markdown")
            try:
                from telegram_brain import train_command
                ctx.args = [mode]
                await train_command(update, ctx)
            except Exception as e:
                await safe_reply(update, f"Training trigger error: {e}", parse_mode="Markdown")
            return

        if sub in ("relearn", "bootstrap"):
            sym = args[1].upper() if len(args) > 1 else "XAUUSD"
            await safe_reply(update, f"🧠 Retraining AI model on `{sym}`...", parse_mode="Markdown")
            try:
                from telegram_brain import relearn_command
                ctx.args = [sym]
                await relearn_command(update, ctx)
            except Exception as e:
                await safe_reply(update, f"Relearn trigger error: {e}", parse_mode="Markdown")
            return

        # Fallback for unrecognized subcommand
        await safe_reply(
            update,
            f"❓ Unrecognized subcommand `/ai {sub}`.\n"
            "Try `/ai` for the interactive dashboard or `/ai help` for full documentation.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Error in /ai command: %s", e)
        await safe_reply(update, f"AI command error: `{type(e).__name__}: {e}`", parse_mode="Markdown")


async def handle_ai_callback(data: Any, update: Optional[Update] = None, ctx: Optional[ContextTypes.DEFAULT_TYPE] = None) -> bool:
    """Handle interactive inline buttons for the AI control dashboard."""
    if isinstance(data, Update):
        if update is not None and not isinstance(update, Update):
            ctx = update
        update = data
        data = str(getattr(getattr(update, "callback_query", None), "data", "") or "")
    else:
        data = str(data or "")

    query = getattr(update, "callback_query", None)
    if not query:
        return False
    try:
        await query.answer()
    except Exception:
        pass

    # Ensure update.message is available for handlers that reference it
    if not getattr(update, "message", None) and getattr(query, "message", None):
        update.message = query.message

    if not data.startswith("ai:"):
        return False

    from ai_advanced_suite import get_ai_suite
    suite = get_ai_suite()
    action = data.split(":", 1)[1]

    if action == "refresh":
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "toggle":
        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            new_st = not (suite.enabled or brain.is_enabled())
            suite.set_enabled(new_st)
            brain.set_enabled(new_st)
        except Exception:
            suite.set_enabled(not suite.enabled)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action.startswith("view:"):
        target_view = action.split(":", 1)[1]
        suite.set_ui_mode(target_view)
        text, markup = build_ai_dashboard(suite, mode=target_view)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "clearbans":
        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            brain.clear_bans()
        except Exception:
            pass
        suite.reset_safety_locks()
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action.startswith("mode:"):
        target_mode = action.split(":", 1)[1]
        suite.set_control_mode(target_mode)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "mode_toggle":
        new_mode = "auto" if suite.is_manual_mode() else "manual"
        suite.set_control_mode(new_mode)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action.startswith("persona:"):
        target_persona = action.split(":", 1)[1]
        suite.set_persona(target_persona)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action.startswith("bias:"):
        target_bias = action.split(":", 1)[1]
        suite.set_bias(target_bias)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action in ("freeze", "unfreeze"):
        suite.set_learn_freeze(action == "freeze")
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "reset_locks":
        suite.reset_safety_locks()
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "override":
        suite.set_override_next(True)
        text, markup = build_ai_dashboard(suite)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return True

    elif action == "health":
        ok, detail = suite.health_ok()
        icon = "🟢" if ok else "🔴"
        ea_str = "Connected" if "not connected" not in detail else "Disconnected"
        msg = (
            f"❤️ **AI Advanced Suite Health Telemetry**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Overall Status:** {icon} `{detail.upper()}`\n"
            f"• **EA Bridge Status:** `{ea_str}`\n"
            f"• **Fill Rejections:** `{suite.state.get('fill_rejects', 0)}`\n"
            f"• **Consecutive Losses:** `{suite.state.get('consec_losses', 0)}/{suite.max_consec_losses}`\n"
            f"• **Live Health Active:** `{suite.live_health}`\n"
            f"• **Control Mode:** `{'MANUAL' if suite.is_manual_mode() else 'AUTO'}`"
        )
        if ctx:
            await safe_reply(update, msg, parse_mode="Markdown")
        return True

    elif action == "critique":
        prompt = suite.weekly_critique_prompt() if hasattr(suite, "weekly_critique_prompt") else "No critique data yet."
        try:
            from gemini_client import GeminiClient
            g = GeminiClient()
            if g.api_key:
                reply = g.ask(chat_id=0, user_text=prompt, market_context="weekly AI critique")
                if ctx:
                    await safe_reply(update, f"📋 **Weekly AI Trading Coach Critique**\n━━━━━━━━━━━━━━━━━━━━\n\n{reply}", parse_mode="Markdown")
                return True
        except Exception:
            pass
        if ctx:
            await safe_reply(update, "📋 **AI Weekly Critique Prompt (Set GEMINI_API_KEY for live answers):**\n\n```\n" + str(prompt)[:3500] + "\n```", parse_mode="Markdown")
        return True

    elif action == "train":
        if ctx:
            await safe_reply(update, "🧠 Launching Autonomous AI Learner training (quick)...", parse_mode="Markdown")
            try:
                from telegram_brain import train_command
                ctx.args = ["quick"]
                await train_command(update, ctx)
            except Exception as e:
                await safe_reply(update, f"Training trigger error: {e}", parse_mode="Markdown")
        return True

    return False


def register_ai_commands(application) -> None:
    """Register /ai and /aisuite without conflicting with existing handlers."""
    existing = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing.update(h.commands)
    if "ai" not in existing:
        application.add_handler(CommandHandler(["ai", "aisuite"], ai_command))
        logger.info("Registered /ai command")
