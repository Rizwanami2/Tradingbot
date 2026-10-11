#!/usr/bin/env python3
"""
One-shot patcher: registers /performance /kill /profile in main.py
and optionally upgrades /signal to use AdvancedEngine.

Run once from repo root:
    python wire_main.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

MARKER = "# === ADVANCED COMMANDS (auto-wired) ==="
SNIPPET = '''
    # === ADVANCED COMMANDS (auto-wired) ===
    try:
        from advanced_integration import register_advanced_commands
        register_advanced_commands(app)
        print("Advanced commands registered: /performance /kill /profile")
    except Exception as _adv_err:
        print(f"[main] Advanced commands not loaded: {_adv_err}")
'''

SIGNAL_UPGRADE = '''
async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run full advanced multi-agent evaluation (HTF, vol, CVD, portfolio, kill-switch)."""
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    await update.message.reply_text(f"⏳ Full advanced evaluation for **{sym}**...", parse_mode="Markdown")
    try:
        from advanced_integration import AdvancedEngine
        eng = AdvancedEngine(symbol=sym, profile="balanced")
        res = eng.evaluate(sym)
    except Exception as e:
        res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
        res["status"] = res.get("status", "") + f" (fallback: {e})"

    price_val = res.get("price", 0.0) or 0.0
    sig = res.get("signal")
    votes = res.get("votes", [])

    vote_lines = []
    for v in votes:
        d = v.get("direction", "FLAT")
        icon = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        vote_lines.append(f"{icon} **{v.get('agent')}:** {d} ({v.get('reason', '')})")

    status_str = f"⚡ **Status:** `{res.get('status', 'No consensus')}`"
    extra = []
    if res.get("session"):
        extra.append(f"Session: `{res['session']}`")
    if res.get("htf_bias"):
        extra.append(f"HTF: `{res['htf_bias']}`")
    if res.get("vol_reason"):
        extra.append(f"Vol: `{res['vol_reason']}`")
    extra_str = (" | ".join(extra) + "\n") if extra else ""

    if sig and not res.get("blocked"):
        direction = sig.get("direction")
        dir_name = direction.name if hasattr(direction, "name") else str(direction)
        dir_icon = "🟢 LONG" if "LONG" in dir_name.upper() else "🔴 SHORT"
        sig_box = (
            f"\n🎯 **TRADE SETUP ({dir_icon}):**\n"
            f"• **Entry:** `${sig.get('entry', 0):,.2f}`\n"
            f"• **Stop:** `${sig.get('stop', 0):,.2f}`\n"
            f"• **TP1 / TP2:** `${sig.get('tp1', 0):,.2f}` / `${sig.get('tp2', 0):,.2f}`\n"
            f"• **Lots:** `{sig.get('lots', 0)}` | **Lev:** `{sig.get('leverage', 1):.0f}x`\n"
            f"• **Margin:** `${sig.get('margin_required', 0):,.2f}` | **R:R:** `1:{sig.get('rr', 0):.1f}`\n"
            f"• **Profile:** `{sig.get('profile', 'balanced')}` | **Regime:** `{sig.get('regime', '?')}`\n"
        )
    else:
        sig_box = "\n🛡️ **Stay flat** — filters blocked or no consensus.\n"

    msg = (
        f"📊 **Advanced Evaluation — {sym}**\n"
        f"Mark: `${float(price_val):,.2f}`\n{extra_str}\n"
        f"**Votes:**\n" + "\n".join(vote_lines) + f"\n\n{status_str}\n{sig_box}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
'''


def main():
    if not MAIN.exists():
        print("main.py not found")
        sys.exit(1)
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    if MARKER not in text:
        # Insert before MessageHandler registration
        anchor = 'app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))'
        if anchor in text:
            text = text.replace(anchor, SNIPPET + "\n    " + anchor)
            changed = True
            print("Registered advanced commands in main.py")
        else:
            # fallback: before run_polling
            if "app.run_polling()" in text:
                text = text.replace("app.run_polling()", SNIPPET + "\n    app.run_polling()")
                changed = True
                print("Registered advanced commands (before run_polling)")
            else:
                print("Could not find insertion point for handlers")
    else:
        print("Advanced commands already wired")

    # Upgrade signal_command if still using plain evaluate_symbol only
    if "Full advanced evaluation" not in text and "async def signal_command" in text:
        text2, n = re.subn(
            r"async def signal_command\(update: Update, ctx: ContextTypes\.DEFAULT_TYPE\):.*?(?=\nasync def |\n# =====)",
            SIGNAL_UPGRADE.strip() + "\n\n\n",
            text,
            count=1,
            flags=re.S,
        )
        if n:
            text = text2
            changed = True
            print("Upgraded /signal to AdvancedEngine")
        else:
            print("Could not auto-upgrade signal_command (manual merge may be needed)")

    # Update docstring commands list
    if "/performance" not in text[:2500]:
        text = text.replace(
            "  /reset            - Clear conversation memory",
            "  /reset            - Clear conversation memory\n"
            "  /performance      - Expectancy, win rate, agent contribution\n"
            "  /kill [on|off]    - Activate/deactivate kill switch\n"
            "  /profile [name]   - Show strategy profile (balanced/scalp/swing/...)",
            1,
        )
        changed = True

    if changed:
        MAIN.write_text(text, encoding="utf-8")
        print("main.py updated successfully. Restart the bot.")
    else:
        print("No changes applied.")


if __name__ == "__main__":
    main()
