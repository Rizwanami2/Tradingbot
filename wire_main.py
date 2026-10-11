#!/usr/bin/env python3
"""
One-shot patcher: registers advanced Telegram commands + Feature Pack v3.

Run once from repo root:
    python wire_main.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

SNIPPET = '''
    # === ADVANCED + FEATURE PACK COMMANDS (auto-wired) ===
    try:
        from telegram_advanced import register_all_advanced
        register_all_advanced(app)
        print("Advanced commands registered: /performance /kill /profile /asignal /abacktest")
    except Exception as _adv_err:
        print(f"[main] Advanced commands not loaded: {_adv_err}")
    try:
        from telegram_feature_pack import register_feature_pack
        register_feature_pack(app)
        print("Feature Pack v3 registered: /board /journal /agents /tiers")
    except Exception as _fp_err:
        print(f"[main] Feature Pack not loaded: {_fp_err}")
'''


def main():
    if not MAIN.exists():
        print("main.py not found")
        sys.exit(1)
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    if "register_feature_pack" not in text:
        # Remove old advanced-only snippet if present, then inject full one
        if "register_all_advanced" in text and "register_feature_pack" not in text:
            # Append feature pack registration near existing advanced block
            old = "print(\"Advanced commands registered: /performance /kill /profile /asignal /abacktest\")"
            if old in text:
                text = text.replace(
                    old,
                    old + "\n    except Exception as _adv_err:\n"
                    "        print(f\"[main] Advanced commands not loaded: {_adv_err}\")\n"
                    "    try:\n"
                    "        from telegram_feature_pack import register_feature_pack\n"
                    "        register_feature_pack(app)\n"
                    "        print(\"Feature Pack v3 registered: /board /journal /agents /tiers\")",
                    1,
                )
                # fix double except if we created mess — cleaner path below
                changed = True

        anchor = "app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))"
        if "register_feature_pack" not in text:
            if anchor in text:
                text = text.replace(anchor, SNIPPET + "\n    " + anchor, 1)
                changed = True
                print("Registered advanced + feature pack commands")
            elif "app.run_polling()" in text:
                text = text.replace("app.run_polling()", SNIPPET + "\n    app.run_polling()", 1)
                changed = True
                print("Registered advanced + feature pack (before run_polling)")
            else:
                print("Could not find insertion point for handlers")
    else:
        print("Feature Pack already wired")

    # Fix stale import path if old wire used advanced_integration.register_advanced_commands
    if "from advanced_integration import register_advanced_commands" in text:
        text = text.replace(
            "from advanced_integration import register_advanced_commands",
            "from telegram_advanced import register_all_advanced",
        )
        text = text.replace("register_advanced_commands(app)", "register_all_advanced(app)")
        changed = True
        print("Fixed stale register import → telegram_advanced")

    help_block = (
        "  /board            - Full status board (equity, tiers, positions, agents)\n"
        "  /journal          - Trade journal + expectancy\n"
        "  /agents           - Per-agent performance\n"
        "  /tiers            - Risk tier status\n"
    )
    if "/board" not in text[:5000]:
        text = text.replace(
            "  /reset            - Clear conversation memory",
            "  /reset            - Clear conversation memory\n"
            "  /performance      - Expectancy, win rate, agent contribution\n"
            "  /kill [on|off]    - Activate/deactivate kill switch\n"
            "  /profile [name]   - Strategy profile\n"
            "  /asignal [SYM]    - Full advanced evaluation\n"
            "  /abacktest [bars] - Auto full backtest suite\n"
            + help_block,
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
