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
    """Inform user that secondary AI Suite & Veto filtering have been permanently removed."""
    msg = (
        "ℹ️ **AI Suite & Veto Removed**\n\n"
        "• **Execution Engine:** Pure Multi-Agent Consensus (`multi_agent_trading_bot.py`)\n"
        "  _Consensus signals flow directly to execution without secondary AI or veto blocks._\n\n"
        "• **Learning Engine:** Autonomous Brain (`autonomous_learner.py`)\n"
        "  _Background win-rate logging and regime sizing remain active without blocking trades._\n\n"
        "• Use `/brain`, `/agents`, `/signal`, or `/autotrade` for active controls."
    )
    await safe_reply(update, msg, parse_mode="Markdown")
    return


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
    """AI commands removed; no-op registration."""
    pass
