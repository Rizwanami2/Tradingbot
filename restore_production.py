#!/usr/bin/env python3
"""Restore production modules from pinned commit + critical patches."""
from __future__ import annotations

import urllib.request
from pathlib import Path

GOOD = "67fa1214d01b82fa201685ffe8b27eee5fdb19ac"
BASE = f"https://raw.githubusercontent.com/Rizwanami2/Tradingbot/{GOOD}"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=45) as r:
        return r.read().decode("utf-8")


def patch_risk(data: str) -> str:
    if '"BTC":' not in data:
        data = data.replace(
            '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},',
            '"BTCUSDT": {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
            '    "BTCUSD":  {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},\n'
            '    "BTC":     {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},',
            1,
        )
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
    )
    if old_set in data:
        data = data.replace(old_set, new_set, 1)
    return data


def patch_ma(data: str) -> str:
    if "def _norm_trade_symbol" not in data:
        helper = (
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
            "\n\n"
        )
        data = data.replace("class MarketDataFeed:", helper + "class MarketDataFeed:", 1)
    data = data.replace(
        "self.symbol = symbol\n        self.source = source\n        self.exchange_id = exchange_id",
        "self.symbol = _norm_trade_symbol(symbol)\n        self.source = source\n        self.exchange_id = exchange_id",
        1,
    )
    tail = data.split("def get_latest_price", 1)[-1][:500] if "def get_latest_price" in data else ""
    if "from realtime_prices import fetch_spot_price" not in tail:
        data = data.replace(
            "    def get_latest_price(self) -> dict:\n        if self._improved is not None:\n",
            "    def get_latest_price(self) -> dict:\n"
            "        try:\n"
            "            from realtime_prices import fetch_spot_price\n"
            "            rt = fetch_spot_price(self.symbol)\n"
            '            if rt.get("ok") and float(rt.get("mark") or 0) > 0:\n'
            "                return rt\n"
            "        except Exception:\n"
            "            pass\n"
            "        if self._improved is not None:\n",
            1,
        )
    if "symbol = _norm_trade_symbol(symbol)" not in data:
        data = data.replace(
            'def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True):\n'
            "    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)",
            'def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True):\n'
            "    symbol = _norm_trade_symbol(symbol)\n"
            "    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)",
            1,
        )
    return data


def patch_main(data: str) -> str:
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
    if "Autotrade: no setup" not in data:
        data = data.replace(
            "                    if sig:\n                        order = engine.register_setup",
            '                    if not sig:\n                        logger.info("Autotrade: no setup — %s", why)\n'
            "                    if sig:\n                        order = engine.register_setup",
            1,
        )
    if "agent.report" not in data:
        old_news = (
            "async def news_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
            "    try:\n"
            "        agent = NewsAgent()\n"
            "        v = agent.vote()\n"
            '        await update.message.reply_text(f"📰 **News**\n{v.direction.name}: {v.reason}", parse_mode="Markdown")\n'
            "    except Exception as e:\n"
            '        await update.message.reply_text(f"News error: {e}")\n'
        )
        new_news = (
            "async def news_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
            "    try:\n"
            "        try:\n"
            "            from news_agent import NewsAgent as _NA\n"
            '            agent = _NA(symbol=getattr(config, "DEFAULT_SYMBOL", "XAUUSD"))\n'
            '            if hasattr(agent, "report"):\n'
            '                await update.message.reply_text(agent.report(), parse_mode="Markdown")\n'
            "                return\n"
            "        except Exception:\n"
            "            agent = NewsAgent()\n"
            "        v = agent.vote()\n"
            '        await update.message.reply_text(f"📰 **News**\n{v.direction.name}: {v.reason}", parse_mode="Markdown")\n'
            "    except Exception as e:\n"
            '        await update.message.reply_text(f"News error: {e}")\n'
        )
        if old_news in data:
            data = data.replace(old_news, new_news, 1)
    return data


def restore() -> None:
    root = Path(__file__).resolve().parent
    rm_path = root / "risk_manager.py"
    ma_path = root / "multi_agent_trading_bot.py"
    if (
        rm_path.exists() and "class RiskManager" in rm_path.read_text(errors="ignore") and
        ma_path.exists() and "class Arbiter" in ma_path.read_text(errors="ignore")
    ):
        print("[restore] Production files already present and verified.")
        return

    mapping = {
        "risk_manager.py": patch_risk,
        "multi_agent_trading_bot.py": patch_ma,
        "main.py": patch_main,
    }
    for name, patcher in mapping.items():
        try:
            data = patcher(fetch(name))
            (root / name).write_text(data)
            print(f"[restore] {name} {len(data)} bytes")
        except Exception as e:
            print(f"[restore] Failed to fetch {name}: {e}")
    print("[restore] DONE")


if __name__ == "__main__":
    restore()
