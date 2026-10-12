#!/usr/bin/env python3
"""Bootstrap: load full bot from GitHub into memory, apply BTC fixes, exec. Works on read-only FS."""
from __future__ import annotations

import os
import re
import sys
import types
import urllib.request

GOOD_SHA = "67fa1214d01b82fa201685ffe8b27eee5fdb19ac"
BASE = f"https://raw.githubusercontent.com/Rizwanami2/Tradingbot/{GOOD_SHA}"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=45) as r:
        return r.read().decode("utf-8")


def patch_main(t: str) -> str:
    if 'if sym in ("BTC", "BITCOIN", "BTCUSD", "XBT"):' in t:
        return t
    old = (
        "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
        "    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL\n"
    )
    new = (
        "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
        "    sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()\n"
        '    if sym in ("BTC", "BITCOIN", "BTCUSD", "XBT"):\n'
        '        sym = "BTCUSDT"\n'
        '    if sym in ("ETH", "ETHEREUM", "ETHUSD"):\n'
        '        sym = "ETHUSDT"\n'
        '    if sym in ("GOLD", "XAU", "XAUT", "XAUTUSD"):\n'
        '        sym = "XAUUSD"\n'
    )
    if old in t:
        return t.replace(old, new, 1)
    t2, n = re.subn(
        r"async def signal_command\(update: Update, ctx: ContextTypes\.DEFAULT_TYPE\):\n"
        r"    sym = ctx\.args\[0\]\.upper\(\) if ctx\.args else config\.DEFAULT_SYMBOL\n",
        new,
        t,
        count=1,
    )
    return t2 if n else t


def patch_risk(t: str) -> str:
    if '"BTC":' not in t and '"BTCUSDT":' in t:
        needle = (
            '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, '
            '"pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},'
        )
        insert = (
            '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, '
            '"pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
            '    "BTCUSD":  {"contract_size": 1, "pip_size": 0.1, '
            '"pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
            '    "BTC":     {"contract_size": 1, "pip_size": 0.1, '
            '"pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},'
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
    old_set = (
        "    def set_symbol(self, symbol: str):\n"
        "        self.symbol = symbol.upper()\n"
        '        self.profile = INSTRUMENTS.get(self.symbol, INSTRUMENTS["DEFAULT"])\n'
    )
    new_set = (
        "    def set_symbol(self, symbol: str):\n"
        '        s = (symbol or "").upper().replace("/", "").replace("-", "").strip()\n'
        '        if s in ("BTC", "BTCUSD", "XBT", "XBTUSD"):\n'
        '            s = "BTCUSDT"\n'
        '        elif s in ("ETH", "ETHUSD"):\n'
        '            s = "ETHUSDT"\n'
        '        elif s in ("GOLD", "XAU", "XAUT", "XAUTUSD"):\n'
        '            s = "XAUUSD"\n'
        "        self.symbol = s\n"
        '        self.profile = INSTRUMENTS.get(self.symbol, INSTRUMENTS["DEFAULT"])\n'
        "        if self.symbol not in INSTRUMENTS:\n"
        '            if any(x in self.symbol for x in ("BTC", "ETH", "SOL", "BNB", "XRP")):\n'
        '                self.profile = INSTRUMENTS.get("BTCUSDT", INSTRUMENTS["DEFAULT"])\n'
        '            elif "XAU" in self.symbol or "GOLD" in self.symbol or "PAXG" in self.symbol:\n'
        '                self.profile = INSTRUMENTS.get("XAUUSD", INSTRUMENTS["DEFAULT"])\n'
    )
    if old_set in t:
        t = t.replace(old_set, new_set, 1)
    return t


_NORM_HELPER = (
    "\n"
    "def _norm_trade_symbol(symbol: str) -> str:\n"
    '    s = (symbol or "").upper().replace("/", "").replace("-", "").replace(":", "").strip()\n'
    '    if s in ("BTC", "BTCUSD", "XBT", "XBTUSD", "BITCOIN"):\n'
    '        return "BTCUSDT"\n'
    '    if s in ("ETH", "ETHUSD", "ETHEREUM"):\n'
    '        return "ETHUSDT"\n'
    '    if s in ("GOLD", "XAU", "XAUT", "XAUTUSD", "XAUUSD"):\n'
    '        return "XAUUSD"\n'
    '    if s.endswith("USD") and not s.endswith("USDT") and any(x in s for x in ("BTC", "ETH", "SOL", "BNB", "XRP")):\n'
    '        return s[:-3] + "USDT"\n'
    '    return s or "XAUUSD"\n'
    "\n"
    "\n"
)


def patch_ma(t: str) -> str:
    if "def _norm_trade_symbol" not in t:
        t = t.replace("class MarketDataFeed:", _NORM_HELPER + "class MarketDataFeed:", 1)
    if "self.symbol = _norm_trade_symbol(symbol)" not in t:
        t = t.replace(
            "self.symbol = symbol\n        self.source = source\n        self.exchange_id = exchange_id",
            "self.symbol = _norm_trade_symbol(symbol)\n        self.source = source\n        self.exchange_id = exchange_id",
            1,
        )
    old_glp = "    def get_latest_price(self) -> dict:\n        if self._improved is not None:\n"
    new_glp = (
        "    def get_latest_price(self) -> dict:\n"
        "        try:\n"
        "            from realtime_prices import fetch_spot_price\n"
        "            rt = fetch_spot_price(self.symbol)\n"
        '            if rt.get("ok") and float(rt.get("mark") or 0) > 0:\n'
        "                return rt\n"
        "        except Exception:\n"
        "            pass\n"
        "        if self._improved is not None:\n"
    )
    parts = t.split("def get_latest_price", 1)
    if old_glp in t and (len(parts) < 2 or "from realtime_prices import fetch_spot_price" not in parts[1][:500]):
        t = t.replace(old_glp, new_glp, 1)
    old_eval = (
        'def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True):\n'
        "    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)"
    )
    new_eval = (
        'def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True):\n'
        "    symbol = _norm_trade_symbol(symbol)\n"
        "    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)"
    )
    if old_eval in t:
        t = t.replace(old_eval, new_eval, 1)
    if 'if "BTC" in symbol and 0 < mark < 1000:' not in t:
        old_price = (
            "    quote = feed.get_latest_price()\n"
            "    votes_out = [\n"
            '        {"agent": v.agent, "direction": v.direction.name, "strength": round(v.strength, 2), "reason": v.reason}\n'
            "        for v in votes\n"
            "    ]\n"
            "    return {\n"
            '        "symbol": symbol,\n'
            '        "price": quote.get("mark") or float(data["1m"]["close"].iloc[-1]),\n'
        )
        new_price = (
            "    quote = feed.get_latest_price()\n"
            "    mark = 0.0\n"
            "    try:\n"
            '        mark = float(quote.get("mark") or 0)\n'
            "    except Exception:\n"
            "        mark = 0.0\n"
            "    if mark <= 0:\n"
            "        try:\n"
            '            mark = float(data["1m"]["close"].iloc[-1])\n'
            "        except Exception:\n"
            "            mark = 0.0\n"
            '    if "BTC" in symbol and 0 < mark < 1000:\n'
            "        try:\n"
            "            from realtime_prices import fetch_spot_price\n"
            '            rt = fetch_spot_price("BTCUSDT")\n'
            '            if rt.get("ok") and float(rt.get("mark") or 0) > 1000:\n'
            '                mark = float(rt["mark"])\n'
            "        except Exception:\n"
            "            pass\n"
            "    votes_out = [\n"
            '        {"agent": v.agent, "direction": v.direction.name, "strength": round(v.strength, 2), "reason": v.reason}\n'
            "        for v in votes\n"
            "    ]\n"
            "    return {\n"
            '        "symbol": symbol,\n'
            '        "price": mark,\n'
        )
        if old_price in t:
            t = t.replace(old_price, new_price, 1)
    return t


def try_write(name: str, content: str) -> bool:
    try:
        with open(name, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError as e:
        print(f"[bootstrap] write {name} failed (read-only?): {e}")
        return False


def load_module(name: str, source: str):
    mod = types.ModuleType(name)
    mod.__file__ = name + ".py"
    exec(compile(source, name + ".py", "exec"), mod.__dict__)
    sys.modules[name] = mod
    return mod


def main():
    print("[bootstrap] loading full bot from", GOOD_SHA[:7], "…")
    main_src = patch_main(fetch("main.py"))
    risk_src = patch_risk(fetch("risk_manager.py"))
    ma_src = patch_ma(fetch("multi_agent_trading_bot.py"))

    wrote_risk = try_write("risk_manager.py", risk_src)
    wrote_ma = try_write("multi_agent_trading_bot.py", ma_src)

    if not wrote_risk:
        load_module("risk_manager", risk_src)
        print("[bootstrap] risk_manager injected into sys.modules")
    if not wrote_ma:
        load_module("multi_agent_trading_bot", ma_src)
        print("[bootstrap] multi_agent_trading_bot injected into sys.modules")

    print("[bootstrap] starting bot (", len(main_src), "chars )")
    code = compile(main_src, "main.py", "exec")
    g = {"__name__": "__main__", "__file__": os.path.abspath("main.py")}
    exec(code, g)


if __name__ == "__main__":
    main()
