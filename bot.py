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

import config

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


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
    parser.add_argument("--symbol", default=config.DEFAULT_SYMBOL)
    parser.add_argument("--source", default=config.DEFAULT_DATA_SOURCE)
    parser.add_argument("--exchange", default=config.DEFAULT_EXCHANGE)
    parser.add_argument("--poll", type=int, default=config.POLL_INTERVAL)
    parser.add_argument("--equity", type=float, default=config.INITIAL_EQUITY)
    parser.add_argument("--risk", type=float, default=config.DEFAULT_RISK_PCT)
    parser.add_argument("--leverage", type=float, default=config.DEFAULT_LEVERAGE, help="Leverage multiplier (1x to 500x)")
    parser.add_argument("--bars", type=int, default=2000)
    parser.add_argument("--news", action="store_true", default=config.NEWS_ENABLED)
    parser.add_argument("--news-window", type=int, default=config.NEWS_WINDOW_MIN)
    parser.add_argument(
        "--watchlist",
        "--symbols",
        default=None,
        help="Comma-separated autotrade selection (e.g. btc, gold, both, or BTCUSDT,XAUUSD)",
    )
    args = parser.parse_args()

    if args.watchlist:
        config.set_autotrade_symbols(args.watchlist)

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
