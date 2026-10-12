#!/usr/bin/env python3
"""Restore production modules from pinned commit + patches."""
from __future__ import annotations
import urllib.request
from pathlib import Path

GOOD = "67fa1214d01b82fa201685ffe8b27eee5fdb19ac"
BASE = f"https://raw.githubusercontent.com/Rizwanami2/Tradingbot/{GOOD}"

def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=45) as r:
        return r.read().decode("utf-8")

def restore() -> None:
    root = Path(__file__).resolve().parent
    for name in ("risk_manager.py", "multi_agent_trading_bot.py", "main.py"):
        data = fetch(name)
        if name == "risk_manager.py" and '"BTC":' not in data:
            data = data.replace(
                '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},',
                '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
                '    "BTCUSD":  {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
                '    "BTC":     {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},',
                1,
            )
        if name == "multi_agent_trading_bot.py" and "def _norm_trade_symbol" not in data:
            helper = (
                "\ndef _norm_trade_symbol(symbol: str) -> str:\n"
                '    s = (symbol or "").upper().replace("/", "").replace("-", "").replace(":", "").strip()\n'
                '    if s in ("BTC", "BTCUSD", "XBT", "XBTUSD", "BITCOIN"):\n'
                '        return "BTCUSDT"\n'
                '    if s in ("ETH", "ETHUSD", "ETHEREUM"):\n'
                '        return "ETHUSDT"\n'
                '    if s in ("GOLD", "XAU", "XAUT", "XAUTUSD", "XAUUSD"):\n'
                '        return "XAUUSD"\n'
                '    if s.endswith("USD") and not s.endswith("USDT") and any(x in s for x in ("BTC", "ETH", "SOL", "BNB", "XRP")):\n'
                '        return s[:-3] + "USDT"\n'
                '    return s or "XAUUSD"\n\n\n'
            )
            data = data.replace("class MarketDataFeed:", helper + "class MarketDataFeed:", 1)
            data = data.replace(
                "self.symbol = symbol\n        self.source = source\n        self.exchange_id = exchange_id",
                "self.symbol = _norm_trade_symbol(symbol)\n        self.source = source\n        self.exchange_id = exchange_id",
                1,
            )
        if name == "main.py":
            if 'if sym in ("BTC", "BITCOIN", "BTCUSD", "XBT"):' not in data:
                data = data.replace(
                    "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
                    "    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL\n",
                    "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
                    "    sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()\n"
                    '    if sym in ("BTC", "BITCOIN", "BTCUSD", "XBT"):\n'
                    '        sym = "BTCUSDT"\n'
                    '    if sym in ("ETH", "ETHEREUM", "ETHUSD"):\n'
                    '        sym = "ETHUSDT"\n'
                    '    if sym in ("GOLD", "XAU", "XAUT", "XAUTUSD"):\n'
                    '        sym = "XAUUSD"\n',
                    1,
                )
            if "on_demand=True" not in data:
                data = data.replace("risk_mgr, symbol=symbol)", "risk_mgr, symbol=symbol, on_demand=True)", 1)
                data = data.replace(
                    "risk_mgr, symbol=symbol\n                    )",
                    "risk_mgr, symbol=symbol, on_demand=True\n                    )",
                    1,
                )
        path = root / name
        path.write_text(data)
        print(f"[restore] {name} {len(data)} bytes")
    print("[restore] DONE")

if __name__ == "__main__":
    restore()
