#!/usr/bin/env python3
"""Polish Telegram start/help text for modern feature set."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[wire_polish] main missing")
        return
    t = main_py.read_text(errors="ignore")
    changed = False

    if "Institutional Multi-Agent Trading Assistant" in t:
        t = t.replace(
            "Institutional Multi-Agent Trading Assistant",
            "TradingBot — Multi-Agent Paper Assistant",
        )
        changed = True
        print("[wire_polish] start title")

    if "Welcome! Use /help for commands. /signal for 5-agent consensus." in t:
        t = t.replace(
            "Welcome! Use /help for commands. /signal for 5-agent consensus.\n"
            "Setup monitoring tracks levels locally; live exchange execution is separate.",
            "Gold + BTC · entry zones · 2Y structure · paper autotrade\n\n"
            "• `/signal btc` — full setup + best entry plan\n"
            "• `/autotrade on` — paper monitoring (Gold & BTC only)\n"
            "• `/positions` · `/close all` · `/help`",
        )
        changed = True
        print("[wire_polish] start body")

    if "Institutional Trading Assistant Guide" in t:
        t = t.replace("Institutional Trading Assistant Guide", "TradingBot Guide")
        changed = True

    if "`/close [ID]`" in t:
        t = t.replace(
            "• `/close [ID]` & `/cancel [ID]` — Manage trades\n",
            "• `/positions` — open / pending / recent closed\n"
            "• `/close all` · `/close BTCUSDT` — close trades\n"
            "• `/cancel [SYM]` — cancel pending setup\n",
        )
        changed = True
        print("[wire_polish] help close")

    if "`/autotrade on|off` — Toggle setup monitoring" in t:
        t = t.replace(
            "• `/autotrade on|off` — Toggle setup monitoring & trade tracking\n",
            "• `/autotrade on|off` — Paper autotrade (**Gold + BTC only**)\n",
        )
        changed = True

    if "5-Agent confluence" in t:
        t = t.replace(
            "• `/signal [SYM]` or `/single [SYM]` — 5-Agent confluence setup\n",
            "• `/signal [SYM]` · `/asignal [SYM]` — agents + entry zone + 2Y analysis\n",
        )
        changed = True

    if changed:
        main_py.write_text(t)
        print("[wire_polish] main updated")
    else:
        print("[wire_polish] main already OK")
    print("[wire_polish] done")


if __name__ == "__main__":
    main()
