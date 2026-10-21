#!/usr/bin/env python3
"""
Standalone Telegram Bot Command Menu Synchronizer.
Connects directly to Telegram Bot API and sets clean, curated commands across all scopes.
Usage:
  python3 scripts/sync_telegram_menu.py           # Purges stale commands and syncs clean commands
  python3 scripts/sync_telegram_menu.py --clear   # Completely deletes all commands across all scopes
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

# Ensure repo root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))


async def run_sync():
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token or token == "your_telegram_bot_token_here":
        print("❌ Error: TELEGRAM_TOKEN not configured in .env or environment.")
        print("Please configure TELEGRAM_TOKEN in /workspace/nimble-darwin/.env to sync with Telegram Bot API.")
        sys.exit(1)

    action = "clear" if any(arg in sys.argv for arg in ("--clear", "--delete", "-d")) else "sync"

    print(f"🔄 Connecting to Telegram Bot API with token: {token[:8]}***...")
    try:
        from telegram import Bot
        from main import (
            sync_telegram_bot_commands,
            delete_all_stale_telegram_commands,
            get_clean_telegram_commands,
        )

        bot = Bot(token=token)
        async with bot:
            if action == "clear":
                print("🗑️ Purging all command registrations across all scopes...")
                ok = await delete_all_stale_telegram_commands(bot)
                if ok:
                    print("\n✅ Successfully deleted all commands across Default, Private, and Group scopes!")
                else:
                    print("\n⚠️ Failed to delete some commands.")
            else:
                cmds = get_clean_telegram_commands()
                print(f"📋 Curated {len(cmds)} clean primary commands:")
                for c in cmds:
                    print(f"   /{c.command:<12} — {c.description}")

                print("\n1️⃣ Step 1: Purging all old/stale command scopes...")
                await delete_all_stale_telegram_commands(bot)

                print("2️⃣ Step 2: Registering clean commands across all scopes...")
                ok = await sync_telegram_bot_commands(bot)
                if ok:
                    print("\n✅ Successfully synchronized clean commands with Telegram API across all scopes!")
                    print("✨ Scopes updated: BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats")
                    print("✨ Chat Menu Button set to: MenuButtonCommands")
                    print("💡 Telegram client menu button and '/' autocomplete are now clean and up to date.")
                else:
                    print("\n⚠️ Failed to synchronize commands. Check logs above.")
                    sys.exit(1)

            print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("💡 TIP: If old commands still appear in Telegram:")
            print("1. If commands were added via @BotFather in Telegram:")
            print("   • Open @BotFather -> send /setcommands -> select your bot -> send /empty")
            print("2. Force-restart the Telegram app on your device to refresh its cache.")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    except Exception as e:
        print(f"\n❌ Error during Telegram command operation: {e}")
        sys.exit(1)


def main():
    asyncio.run(run_sync())


if __name__ == "__main__":
    main()
