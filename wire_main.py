#!/usr/bin/env python3
"""
One-shot patcher: registers advanced Telegram commands via telegram_advanced.

Run once from repo root:
    python wire_main.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

MARKER = "# === ADVANCED COMMANDS (auto-wired) ==="
SNIPPET = '''
    # === ADVANCED COMMANDS (auto-wired) ===
    try:
        from telegram_advanced import register_all_advanced
        register_all_advanced(app)
        print("Advanced commands registered: /performance /kill /profile /asignal /abacktest")
    except Exception as _adv_err:
        print(f"[main] Advanced commands not loaded: {_adv_err}")
'''


def main():
    if not MAIN.exists():
        print("main.py not found")
        sys.exit(1)
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    # Prefer telegram_advanced; remove stale advanced_integration register if present
    if "register_all_advanced" not in text:
        anchor = "app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))"
        if anchor in text:
            text = text.replace(anchor, SNIPPET + "\n    " + anchor, 1)
            changed = True
            print("Registered advanced commands via telegram_advanced")
        elif "app.run_polling()" in text:
            text = text.replace("app.run_polling()", SNIPPET + "\n    app.run_polling()", 1)
            changed = True
            print("Registered advanced commands (before run_polling)")
        else:
            print("Could not find insertion point for handlers")
    else:
        print("Advanced commands already wired")

    # Fix stale import path if old wire used advanced_integration.register_advanced_commands
    if "from advanced_integration import register_advanced_commands" in text:
        text = text.replace(
            "from advanced_integration import register_advanced_commands",
            "from telegram_advanced import register_all_advanced",
        )
        text = text.replace("register_advanced_commands(app)", "register_all_advanced(app)")
        changed = True
        print("Fixed stale register import → telegram_advanced")

    if "/abacktest" not in text[:4000] and "/performance" not in text[:4000]:
        text = text.replace(
            "  /reset            - Clear conversation memory",
            "  /reset            - Clear conversation memory\n"
            "  /performance      - Expectancy, win rate, agent contribution\n"
            "  /kill [on|off]    - Activate/deactivate kill switch\n"
            "  /profile [name]   - Strategy profile\n"
            "  /asignal [SYM]    - Full advanced evaluation\n"
            "  /abacktest [bars] - Auto full backtest suite",
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
