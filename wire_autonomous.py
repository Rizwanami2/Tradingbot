#!/usr/bin/env python3
"""
Wire autonomous learner into main.py (startup + optional /brain).
Run once: python wire_autonomous.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

STARTUP = '''
    # === AUTONOMOUS LEARNER (auto, no commands required) ===
    try:
        from autonomous_learner import auto_start
        _brain = auto_start()
        print("[main] Autonomous learner started:", _brain.knowledge.last_bootstrap or "bootstrapping")
    except Exception as _al_err:
        print(f"[main] Autonomous learner not started: {_al_err}")
'''

BRAIN_CMD = '''
async def brain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Read-only view of autonomous self-learning state."""
    try:
        from autonomous_learner import get_brain
        from telegram_brain import safe_reply
        text = get_brain(auto_bootstrap=False).summary()
        await safe_reply(update, text, parse_mode="Markdown")
    except Exception as e:
        from telegram_brain import safe_reply
        await safe_reply(update, f"Brain error: {e}", parse_mode=None)
'''


def main():
    if not MAIN.exists():
        print("main.py not found")
        sys.exit(1)
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    if "auto_start" not in text:
        if "app.run_polling()" in text:
            text = text.replace("app.run_polling()", STARTUP + "\n    app.run_polling()", 1)
            changed = True
            print("Inserted auto_start before run_polling")
        elif "register_all_advanced" in text:
            text = text.replace(
                "register_all_advanced(app)",
                "register_all_advanced(app)\n" + STARTUP,
                1,
            )
            changed = True
            print("Inserted auto_start near advanced registration")
        else:
            print("Could not find insertion point for auto_start")

    if "brain_command" not in text:
        # append handler registration lightly
        if "application.add_handler" in text or "app.add_handler" in text:
            inject = (
                "\n    try:\n"
                "        from telegram.ext import CommandHandler as _CH\n"
                "        async def brain_command(update, ctx):\n"
                "            from autonomous_learner import get_brain\n"
                "            from telegram_brain import safe_reply\n"
                "            await safe_reply(update, get_brain(auto_bootstrap=False).summary(), parse_mode=\"Markdown\")\n"
                "        app.add_handler(_CH(\"brain\", brain_command))\n"
                "        print(\"Registered /brain\")\n"
                "    except Exception as _be:\n"
                "        print(\"brain cmd skip\", _be)\n"
            )
            if "app.run_polling()" in text:
                text = text.replace("app.run_polling()", inject + "\n    app.run_polling()", 1)
                changed = True
                print("Registered /brain")

    if changed:
        MAIN.write_text(text, encoding="utf-8")
        print("main.py updated. Restart the bot.")
    else:
        print("No changes (already wired or anchors missing).")


if __name__ == "__main__":
    main()
