#!/usr/bin/env python3
"""Self-restoring main: downloads baseline + applies /levels & realtime patches, then re-exec."""
from __future__ import annotations
import os
import sys
import pathlib
import urllib.request

HERE = pathlib.Path(__file__).resolve()


def run() -> None:
    url = (
        "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/"
        "3c38ff9098bc859e78ff01f95427a22e9ee542e7/main.py"
    )
    print("[bootstrap] Downloading known-good main.py...")
    with urllib.request.urlopen(url, timeout=45) as resp:
        base = resp.read().decode("utf-8")

    old = (
        'def get_price_info(symbol: str = "XAUTUSD") -> Dict:\n'
        "    from tradingview_feed import fetch_tradingview_quote\n"
        "    tv_info = fetch_tradingview_quote(symbol)\n"
        '    if tv_info.get("ok"):\n'
        "        return tv_info"
    )
    new = (
        'def get_price_info(symbol: str = "XAUTUSD") -> Dict:\n'
        "    try:\n"
        "        from realtime_prices import fetch_spot_price\n"
        "        rt = fetch_spot_price(symbol)\n"
        '        if rt.get("ok") and float(rt.get("mark") or 0) > 0:\n'
        "            return rt\n"
        "    except Exception as e:\n"
        '        logger.warning("realtime_prices failed for %s: %s", symbol, e)\n'
        "    from tradingview_feed import fetch_tradingview_quote\n"
        "    tv_info = fetch_tradingview_quote(symbol)\n"
        '    if tv_info.get("ok"):\n'
        "        return tv_info"
    )
    if old in base:
        base = base.replace(old, new, 1)
        print("[bootstrap] Patched get_price_info")

    levels = (
        "\nasync def levels_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
        '    """Real-time key levels for BTC or Gold (PDH/PDL, pivots, swings)."""\n'
        "    sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()\n"
        '    if sym in ("BTC", "BITCOIN"):\n'
        '        sym = "BTCUSDT"\n'
        '    if sym in ("GOLD", "XAU", "XAUT"):\n'
        '        sym = "XAUUSD"\n'
        '    await update.message.reply_text(f"⏳ Fetching **{sym}** levels…", parse_mode="Markdown")\n'
        "    try:\n"
        "        from realtime_prices import compute_levels, format_levels_message\n"
        "        data = compute_levels(sym)\n"
        '        await update.message.reply_text(format_levels_message(data), parse_mode="Markdown")\n'
        "    except Exception as e:\n"
        '        logger.exception("levels failed")\n'
        '        await update.message.reply_text(f"Levels error: `{e}`", parse_mode="Markdown")\n'
        "\n\n"
    )
    if "async def levels_command" not in base:
        base = base.replace("async def price_command", levels + "async def price_command", 1)
        print("[bootstrap] Inserted levels_command")

    if 'CommandHandler("levels"' not in base:
        base = base.replace(
            'app.add_handler(CommandHandler("price", price_command))',
            'app.add_handler(CommandHandler("levels", levels_command))\n'
            '    app.add_handler(CommandHandler("price", price_command))',
            1,
        )
        print("[bootstrap] Registered /levels handler")

    if "/levels [SYM]" not in base:
        base = base.replace(
            "• `/price [SYM]` — Live mark price & quote\\n",
            "• `/price [SYM]` — Live mark price & quote\\n"
            "• `/levels [SYM]` — Key levels (PDH/PDL, pivots, swings)\\n",
            1,
        )

    if 'name == "levels"' not in base:
        base = base.replace(
            '        elif name == "price":\n',
            '        elif name == "levels":\n'
            "            sym = parts[1] if len(parts) > 1 else config.DEFAULT_SYMBOL\n"
            "            from realtime_prices import compute_levels, format_levels_message\n"
            "            print(format_levels_message(compute_levels(sym)))\n"
            '        elif name == "price":\n',
            1,
        )

    HERE.write_text(base, encoding="utf-8")
    print("[bootstrap] Wrote full main.py. Re-executing...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    run()
