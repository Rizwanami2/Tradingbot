#!/usr/bin/env python3
"""Expand Telegram '/' menu and harden command sync."""
from pathlib import Path
import re

NEW_MENU = '''def get_clean_telegram_commands() -> list:
    """Primary Telegram '/' menu — must stay under 100 commands (Telegram limit)."""
    from telegram import BotCommand
    return [
        BotCommand("start", "Start bot & overview"),
        BotCommand("help", "Full command list"),
        BotCommand("status", "System health & equity"),
        BotCommand("bridge", "EA Bridge ping / MT5 link"),
        BotCommand("mt5", "MetaTrader 5 dashboard"),
        BotCommand("signal", "Multi-agent consensus signal"),
        BotCommand("combined", "CombinedFIT + multi-agent status"),
        BotCommand("autotrade", "Toggle auto trading on/off"),
        BotCommand("positions", "Open positions & live PnL"),
        BotCommand("close", "Close position(s) or all"),
        BotCommand("balance", "Balance & equity"),
        BotCommand("cooldown", "Cooldowns & anti-spam settings"),
        BotCommand("spam", "Spam/burst mode on/off"),
        BotCommand("watchlist", "Symbols to trade"),
        BotCommand("profile", "scalp / balanced profile"),
        BotCommand("ai", "AI suite status & controls"),
        BotCommand("risk", "Risk limits & capacity"),
        BotCommand("price", "Live price"),
        BotCommand("chart", "Live chart"),
        BotCommand("settings", "Config dashboard"),
        BotCommand("updatemenu", "Refresh this command menu"),
        BotCommand("menu", "Interactive command hub"),
    ]
'''

def main():
    p = Path("main.py")
    t = p.read_text(encoding="utf-8")
    t2, n = re.subn(
        r"def get_clean_telegram_commands\(\) -> list:.*?return \[.*?\n    \]\n",
        NEW_MENU + "\n",
        t,
        count=1,
        flags=re.S,
    )
    if not n:
        if 'BotCommand("bridge"' in t and 'BotCommand("cooldown"' in t:
            print("menu already expanded")
        else:
            raise SystemExit("could not replace get_clean_telegram_commands")
    else:
        t = t2
        print("menu expanded")

    if "Syncing Telegram menu commands:" not in t:
        t = t.replace(
            "        cmds = get_clean_telegram_commands()\n\n        # 2. Sync default scope\n        try:\n            await bot.set_my_commands(cmds, scope=BotCommandScopeDefault())\n",
            "        cmds = get_clean_telegram_commands()\n"
            "        logger.info(\"Syncing Telegram menu commands: %s\", [c.command for c in cmds])\n\n"
            "        # 2. Sync default scope\n        try:\n            await bot.set_my_commands(cmds, scope=BotCommandScopeDefault())\n"
            "            try:\n                await bot.set_my_commands(cmds, scope=BotCommandScopeDefault(), language_code=\"\")\n            except Exception:\n                pass\n",
            1,
        )
        print("sync logging added")

    p.write_text(t, encoding="utf-8")
    print("DONE — restart bot, then send /updatemenu in Telegram")
    print("If still empty: @BotFather → Edit Commands → /empty → /updatemenu again")

if __name__ == "__main__":
    main()
