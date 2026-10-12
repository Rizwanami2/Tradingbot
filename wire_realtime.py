#!/usr/bin/env python3
"""Patch main.py for realtime prices + /levels. Run: python wire_realtime.py"""
from pathlib import Path

MAIN = Path("main.py")
t = MAIN.read_text(encoding="utf-8")
changed = False

# 1) get_price_info uses realtime_prices first
needle = 'def get_price_info(symbol: str = "XAUTUSD") -> Dict:\n    from tradingview_feed import fetch_tradingview_quote'
if "from realtime_prices import fetch_spot_price" not in t and "from tradingview_feed import fetch_tradingview_quote" in t:
    old = (
        'def get_price_info(symbol: str = "XAUTUSD") -> Dict:\n'
        '    from tradingview_feed import fetch_tradingview_quote\n'
        '    tv_info = fetch_tradingview_quote(symbol)\n'
        '    if tv_info.get("ok"):\n'
        '        return tv_info'
    )
    new = (
        'def get_price_info(symbol: str = "XAUTUSD") -> Dict:\n'
        '    try:\n'
        '        from realtime_prices import fetch_spot_price\n'
        '        rt = fetch_spot_price(symbol)\n'
        '        if rt.get("ok") and float(rt.get("mark") or 0) > 0:\n'
        '            return rt\n'
        '    except Exception as e:\n'
        '        logger.warning("realtime_prices failed for %s: %s", symbol, e)\n'
        '    from tradingview_feed import fetch_tradingview_quote\n'
        '    tv_info = fetch_tradingview_quote(symbol)\n'
        '    if tv_info.get("ok"):\n'
        '        return tv_info'
    )
    if old in t:
        t = t.replace(old, new, 1)
        changed = True
        print("Patched get_price_info")
    else:
        print("get_price_info pattern not found — may already be patched")
elif "from realtime_prices import fetch_spot_price" in t:
    print("get_price_info already uses realtime_prices")

# 2) levels_command
if "async def levels_command" not in t:
    fn = (
        '\nasync def levels_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n'
        '    """Real-time key levels for BTC or Gold (PDH/PDL, pivots, swings)."""\n'
        '    sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()\n'
        '    if sym in ("BTC", "BITCOIN"):\n'
        '        sym = "BTCUSDT"\n'
        '    if sym in ("GOLD", "XAU", "XAUT"):\n'
        '        sym = "XAUUSD"\n'
        '    await update.message.reply_text(f"⏳ Fetching **{sym}** levels…", parse_mode="Markdown")\n'
        '    try:\n'
        '        from realtime_prices import compute_levels, format_levels_message\n'
        '        data = compute_levels(sym)\n'
        '        await update.message.reply_text(format_levels_message(data), parse_mode="Markdown")\n'
        '    except Exception as e:\n'
        '        logger.exception("levels failed")\n'
        '        await update.message.reply_text(f"Levels error: `{e}`", parse_mode="Markdown")\n\n\n'
    )
    if "async def price_command" in t:
        t = t.replace("async def price_command", fn + "async def price_command", 1)
        changed = True
        print("Inserted levels_command")

# 3) register handler
if 'CommandHandler("levels"' not in t and "levels_command" in t:
    if 'app.add_handler(CommandHandler("price", price_command))' in t:
        t = t.replace(
            'app.add_handler(CommandHandler("price", price_command))',
            'app.add_handler(CommandHandler("levels", levels_command))\n'
            '    app.add_handler(CommandHandler("price", price_command))',
            1,
        )
    elif 'app.add_handler(CommandHandler("start", start_command))' in t:
        t = t.replace(
            'app.add_handler(CommandHandler("start", start_command))',
            'app.add_handler(CommandHandler("start", start_command))\n'
            '    app.add_handler(CommandHandler("levels", levels_command))',
            1,
        )
    changed = True
    print("Registered /levels")

if changed:
    MAIN.write_text(t, encoding="utf-8")
    print("main.py updated — restart bot")
else:
    print("No changes needed")
