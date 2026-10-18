#!/usr/bin/env python3
"""Optional local patch if main.py bot menu lacks /ai (handlers already via telegram_advanced)."""
from pathlib import Path

def main():
    p = Path("main.py")
    t = p.read_text(encoding="utf-8")
    changed = False
    if 'BotCommand("ai"' not in t and 'BotCommand("ask"' in t:
        t = t.replace(
            'BotCommand("ask", "AI trading assistant with live MT5 market context"),',
            'BotCommand("ai", "AI suite status, persona, bias, freeze"),\n'
            '                BotCommand("ask", "AI trading assistant with live MT5 market context"),',
            1,
        )
        changed = True
    if 'CommandHandler("ai"' not in t and 'CommandHandler(["ask", "chat"]' in t:
        t = t.replace(
            'app.add_handler(CommandHandler(["ask", "chat"], ask_command))',
            'app.add_handler(CommandHandler(["ask", "chat"], ask_command))\n'
            '    try:\n'
            '        from telegram_advanced import ai_command\n'
            '        app.add_handler(CommandHandler("ai", ai_command))\n'
            '    except Exception:\n'
            '        pass',
            1,
        )
        changed = True
    if changed:
        p.write_text(t, encoding="utf-8")
        print("main.py patched for /ai menu + handler")
    else:
        print("main.py already has /ai hooks or patterns not found")

if __name__ == "__main__":
    main()
