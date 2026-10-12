#!/usr/bin/env python3
"""Apply critical /signal BTC fixes in-place on deployed tree."""
from __future__ import annotations

import re
from pathlib import Path


def patch_main(p: Path) -> bool:
    t = p.read_text()
    if 'if sym in ("BTC", "BITCOIN", "BTCUSD", "XBT"):' in t:
        return False
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
        p.write_text(t.replace(old, new, 1))
        return True
    t2, n = re.subn(
        r"async def signal_command\(update: Update, ctx: ContextTypes\.DEFAULT_TYPE\):\n"
        r"    sym = ctx\.args\[0\]\.upper\(\) if ctx\.args else config\.DEFAULT_SYMBOL\n",
        new,
        t,
        count=1,
    )
    if n:
        p.write_text(t2)
        return True
    return False


def patch_risk(p: Path) -> bool:
    t = p.read_text()
    changed = False
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
            changed = True
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
        changed = True
    if changed:
        p.write_text(t)
    return changed


def patch_ma(p: Path) -> bool:
    t = p.read_text()
    changed = False
    if "def _norm_trade_symbol" not in t:
        helper = '''
def _norm_trade_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace("/", "").replace("-", "").replace(":", "").strip()
    if s in ("BTC", "BTCUSD", "XBT", "XBTUSD", "BITCOIN"):
        return "BTCUSDT"
    if s in ("ETH", "ETHUSD", "ETHEREUM"):
        return "ETHUSDT"
    if s in ("GOLD", "XAU", "XAUT", "XAUTUSD", "XAUUSD"):
        return "XAUUSD"
    if s.endswith("USD") and not s.endswith("USDT") and any(x in s for x in ("BTC", "ETH", "SOL", "BNB", "XRP")):
        return s[:-3] + "USDT"
    return s or "XAUUSD"


'''
        t = t.replace("class MarketDataFeed:", helper + "class MarketDataFeed:", 1)
        changed = True
    if "self.symbol = _norm_trade_symbol(symbol)" not in t:
        t = t.replace(
            "self.symbol = symbol\n        self.source = source\n        self.exchange_id = exchange_id",
            "self.symbol = _norm_trade_symbol(symbol)\n        self.source = source\n        self.exchange_id = exchange_id",
            1,
        )
        changed = True
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
    if old_glp in t and "from realtime_prices import fetch_spot_price" not in t.split("def get_latest_price")[1][:500]:
        t = t.replace(old_glp, new_glp, 1)
        changed = True
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
        changed = True
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
            changed = True
    if changed:
        p.write_text(t)
    return changed


def main():
    root = Path(__file__).resolve().parent
    results = []
    for name, fn in [
        ("main.py", patch_main),
        ("main_full.py", patch_main),
        ("risk_manager.py", patch_risk),
        ("multi_agent_trading_bot.py", patch_ma),
    ]:
        p = root / name
        if not p.exists():
            results.append(f"{name}: missing")
            continue
        try:
            ok = fn(p)
            results.append(f"{name}: {'patched' if ok else 'already ok'}")
        except Exception as e:
            results.append(f"{name}: ERROR {e}")
    print("[wire_signal_btc_fix]", " | ".join(results))


if __name__ == "__main__":
    main()
