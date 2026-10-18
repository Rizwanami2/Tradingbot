"""Telegram /ai commands for AI Advanced Suite control."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from telegram_brain import safe_reply

logger = logging.getLogger("TelegramAI")


async def ai_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """AI Advanced Suite: /ai [status|persona|bias|freeze|on|off|health|critique|help]"""
    try:
        from ai_advanced_suite import get_ai_suite

        suite = get_ai_suite()
        args = [a.lower() for a in (ctx.args or [])]
        sub = args[0] if args else "status"

        if sub in ("help", "commands", "?"):
            msg = (
                "AI Advanced Suite\n\n"
                "• /ai or /ai status — gates, persona, bias, daily R, learn freeze\n"
                "• /ai persona low|med|high — risk persona\n"
                "• /ai bias long|short|clear — day bias filter\n"
                "• /ai freeze — stop weight updates (observe only)\n"
                "• /ai unfreeze — resume learning\n"
                "• /ai on / /ai off — enable / disable suite gates\n"
                "• /ai health — EA/tick health + fill rejects\n"
                "• /ai critique — weekly self-critique (Gemini if key set)\n\n"
                "Scorecard applies in balance profile only by default."
            )
            await safe_reply(update, msg, parse_mode=None)
            return

        if sub in ("status", "stat", "info", "show"):
            await safe_reply(update, suite.status_text(), parse_mode=None)
            return

        if sub in ("on", "enable", "1", "true"):
            if hasattr(suite, "set_enabled"):
                msg = suite.set_enabled(True)
            else:
                suite.enabled = True
                msg = "AI suite ON"
            await safe_reply(update, msg, parse_mode=None)
            return

        if sub in ("off", "disable", "0", "false"):
            if hasattr(suite, "set_enabled"):
                msg = suite.set_enabled(False)
            else:
                suite.enabled = False
                msg = "AI suite OFF"
            await safe_reply(update, msg, parse_mode=None)
            return

        if sub in ("persona", "risk", "riskpersona"):
            if len(args) < 2:
                await safe_reply(
                    update,
                    f"Current persona: {suite.state.get('risk_persona', 'med')}\n"
                    "Usage: /ai persona low|med|high",
                    parse_mode=None,
                )
                return
            await safe_reply(update, suite.set_persona(args[1]), parse_mode=None)
            return

        if sub in ("bias", "daybias", "direction"):
            if len(args) < 2:
                b = suite.state.get("bias_day") or "none"
                await safe_reply(
                    update,
                    f"Day bias: {b}\nUsage: /ai bias long|short|clear",
                    parse_mode=None,
                )
                return
            await safe_reply(update, suite.set_bias(args[1]), parse_mode=None)
            return

        if sub in ("freeze", "lock", "pauselearn"):
            await safe_reply(update, suite.set_learn_freeze(True), parse_mode=None)
            return

        if sub in ("unfreeze", "unlock", "resumelearn", "learn"):
            await safe_reply(update, suite.set_learn_freeze(False), parse_mode=None)
            return

        if sub in ("health", "ok"):
            ok, detail = suite.health_ok(ea_ok=True, ticks_ok=True)
            await safe_reply(
                update,
                f"AI Health\n- Status: {'ok' if ok else 'issues'}\n- Detail: {detail}\n"
                f"- Fill rejects: {suite.state.get('fill_rejects', 0)}\n"
                f"- Live checks: {suite.live_health}",
                parse_mode=None,
            )
            return

        if sub in ("critique", "weekly", "review"):
            prompt = suite.weekly_critique_prompt() if hasattr(suite, "weekly_critique_prompt") else "No critique data yet."
            try:
                from gemini_client import GeminiClient

                g = GeminiClient()
                if g.api_key:
                    reply = g.ask(chat_id=0, user_text=prompt, market_context="weekly AI critique")
                    await safe_reply(update, f"Weekly AI Critique\n\n{reply}", parse_mode=None)
                    return
            except Exception:
                pass
            await safe_reply(
                update,
                "Critique prompt (set GEMINI_API_KEY for auto answer):\n\n" + str(prompt)[:3500],
                parse_mode=None,
            )
            return

        await safe_reply(update, "Unknown /ai subcommand. Try /ai help", parse_mode=None)
    except Exception as e:
        logger.exception("Error in /ai command: %s", e)
        await safe_reply(update, f"AI command error: {e}", parse_mode=None)


def register_ai_commands(application) -> None:
    """Register /ai without conflicting with existing handlers."""
    existing = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing.update(h.commands)
    if "ai" not in existing:
        application.add_handler(CommandHandler("ai", ai_command))
        logger.info("Registered /ai command")
