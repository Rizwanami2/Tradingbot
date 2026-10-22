#!/usr/bin/env python3
"""Speed up Telegram replies: no menu purge by default, optional swarm/chart skip."""
from pathlib import Path
import re
import ast

def fix_sync():
    p = Path("main.py")
    t = p.read_text(encoding="utf-8")
    if "TELEGRAM_MENU_PURGE" in t:
        print("main sync already gated")
    else:
        old = "        # 1. Purge all existing/stale commands first so old commands are permanently deleted\n        await delete_all_stale_telegram_commands(bot, chat_id=chat_id)\n"
        new = (
            "        # 1. Optional purge (slow). Default OFF for speed — set TELEGRAM_MENU_PURGE=true to force\n"
            "        import os as _os_menu\n"
            "        if _os_menu.getenv(\"TELEGRAM_MENU_PURGE\", \"false\").lower() in (\"true\", \"1\", \"yes\", \"on\"):\n"
            "            await delete_all_stale_telegram_commands(bot, chat_id=chat_id)\n"
        )
        if old in t:
            t = t.replace(old, new, 1)
            print("menu purge gated")
        else:
            print("WARN: purge block not found")
    p.write_text(t, encoding="utf-8")
    print("OK main.py")

def fix_arbiter_speed():
    p = Path("multi_agent_trading_bot.py")
    t = p.read_text(encoding="utf-8")
    # Skip chart when BOT_FAST_MODE
    if "BOT_FAST_MODE" not in t:
        t = t.replace(
            "        if not os.getenv(\"PYTEST_CURRENT_TEST\"):\n"
            "            try:\n"
            "                from mt5_chart import get_mt5_live_chart\n"
            "                ch_data = get_mt5_live_chart(symbol=symbol, timeframe=\"1m\")\n",
            "        if not os.getenv(\"PYTEST_CURRENT_TEST\") and os.getenv(\"BOT_FAST_MODE\", \"false\").lower() not in (\"true\", \"1\", \"yes\", \"on\"):\n"
            "            try:\n"
            "                from mt5_chart import get_mt5_live_chart\n"
            "                ch_data = get_mt5_live_chart(symbol=symbol, timeframe=\"1m\")\n",
            1,
        )
        print("chart gated by BOT_FAST_MODE")
    # Swarm gated by SWARM_ENABLED (default true for compat; user sets false for speed)
    if "SWARM_ENABLED" not in t:
        t = t.replace(
            "        if include_swarm and getattr(self, \"swarm\", None) is not None and build_market_context is not None:\n",
            "        _swarm_on = os.getenv(\"SWARM_ENABLED\", \"true\").lower() in (\"true\", \"1\", \"yes\", \"on\")\n"
            "        if include_swarm and _swarm_on and getattr(self, \"swarm\", None) is not None and build_market_context is not None:\n",
            1,
        )
        print("swarm gated by SWARM_ENABLED")
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("OK multi_agent_trading_bot.py")

def main():
    fix_sync()
    fix_arbiter_speed()
    print("DONE — set in .env:")
    print("  BOT_FAST_MODE=true")
    print("  SWARM_ENABLED=false")
    print("  TELEGRAM_MENU_PURGE=false")
    print("Restart bot")

if __name__ == "__main__":
    main()
