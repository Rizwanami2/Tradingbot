#!/usr/bin/env python3
"""
UNIFIED TRADING BOT RUNNER
==========================
Master launcher for the Trading Bot system.

Usage:
  python bot.py telegram      - Run the interactive Telegram Bot (main.py)
  python bot.py cli           - Run interactive terminal mode without Telegram
  python bot.py live          - Run live multi-agent trading engine
  python bot.py backtest      - Run multi-agent strategy backtest
  python bot.py all           - Run Telegram bot + live background trader concurrently
"""

import argparse
import os
import subprocess
import sys


def run_telegram():
    import main
    main.main()


def run_cli():
    import main
    main.run_cli_interactive()


def run_live(args):
    import multi_agent_trading_bot
    multi_agent_trading_bot.run_live(args)


def run_backtest(args):
    import multi_agent_trading_bot
    multi_agent_trading_bot.run_backtest(args)


def run_all(args):
    """Run both the Telegram bot and live multi-agent engine."""
    import threading
    import multi_agent_trading_bot
    import main

    print("\n[Launcher] Starting Multi-Agent Live Trader in background thread...")
    t = threading.Thread(target=multi_agent_trading_bot.run_live, args=(args,), daemon=True)
    t.start()

    print("[Launcher] Starting Telegram Bot on main thread...")
    main.main()


def main_launcher():
    parser = argparse.ArgumentParser(description="Unified Trading Bot Master Launcher")
    parser.add_argument(
        "mode",
        choices=["telegram", "cli", "live", "backtest", "all"],
        default="telegram",
        nargs="?",
        help="Run mode (default: telegram)",
    )
    parser.add_argument("--symbol", default=os.getenv("BOT_SYMBOL", "XAUTUSD"))
    parser.add_argument("--source", default=os.getenv("BOT_DATA_SOURCE", "demo"))
    parser.add_argument("--exchange", default=os.getenv("BOT_EXCHANGE", "binance"))
    parser.add_argument("--poll", type=int, default=int(os.getenv("BOT_POLL", "30")))
    parser.add_argument("--equity", type=float, default=float(os.getenv("BOT_EQUITY", "10000")))
    parser.add_argument("--risk", type=float, default=float(os.getenv("BOT_RISK", "0.18")))
    parser.add_argument("--leverage", type=float, default=float(os.getenv("BOT_LEVERAGE", "100.0")), help="Leverage multiplier (1x to 500x)")
    parser.add_argument("--bars", type=int, default=2000)
    parser.add_argument("--news", action="store_true", default=True)
    parser.add_argument("--news-window", type=int, default=30)
    args = parser.parse_args()

    if args.mode == "telegram":
        run_telegram()
    elif args.mode == "cli":
        run_cli()
    elif args.mode == "live":
        run_live(args)
    elif args.mode == "backtest":
        run_backtest(args)
    elif args.mode == "all":
        run_all(args)


if __name__ == "__main__":
    main_launcher()
