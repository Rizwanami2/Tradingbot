#!/usr/bin/env python3
"""Apply production fixes in-place (consensus, stops, realtime demo, autotrade). Safe to run every boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def patch_file(path: Path, replacements: list) -> list:
    if not path.exists():
        return [f"missing {path.name}"]
    t = path.read_text(errors="ignore")
    done = []
    for old, new in replacements:
        if old in t:
            t = t.replace(old, new, 1)
            done.append(f"applied:{old[:40]!r}")
        elif new.strip() and new in t:
            done.append(f"already:{old[:30]!r}")
        else:
            done.append(f"skip:{old[:30]!r}")
    path.write_text(t)
    return done


def main() -> None:
    logs = []
    logs += patch_file(ROOT / "multi_agent_trading_bot.py", [
        ("MIN_AGREEMENT = 3", "MIN_AGREEMENT = 2"),
        ("MIN_SCORE = 1.8", "MIN_SCORE = 1.2"),
        (
            '        if _ImprovedDataFeed is not None and source not in ("demo",):\n',
            "        if _ImprovedDataFeed is not None:\n",
        ),
        (
            "        entry = float(df1[\"close\"].iloc[-1])\n"
            "        atr_val = atr(df1) or entry * 0.002\n"
            "        if direction == Dir.LONG:\n"
            "            stop = entry - atr_val * 1.2\n"
            "        else:\n"
            "            stop = entry + atr_val * 1.2\n",
            "        entry = float(df1[\"close\"].iloc[-1])\n"
            "        atr_val = atr(df1) or entry * 0.002\n"
            "        min_stop_dist = entry * 0.003\n"
            "        stop_dist = max(atr_val * 1.5, min_stop_dist)\n"
            "        if direction == Dir.LONG:\n"
            "            stop = entry - stop_dist\n"
            "        else:\n"
            "            stop = entry + stop_dist\n",
        ),
        (
            "    def _demo(self, limit: int = 600, base_price: float = 2650.0) -> pd.DataFrame:\n"
            "        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq=\"1min\")\n"
            "        rng = np.random.default_rng(42)\n"
            "        rets = rng.normal(0, 0.3, limit)\n"
            "        p = base_price + np.cumsum(rets)\n"
            "        highs = p + rng.uniform(0.1, 1.5, limit)\n"
            "        lows = p - rng.uniform(0.1, 1.5, limit)\n",
            "    def _demo(self, limit: int = 600, base_price: float = None) -> pd.DataFrame:\n"
            "        if base_price is None:\n"
            "            base_price = 2650.0\n"
            "            try:\n"
            "                from realtime_prices import fetch_spot_price\n"
            "                rt = fetch_spot_price(self.symbol)\n"
            "                if rt.get(\"ok\") and float(rt.get(\"mark\") or 0) > 0:\n"
            "                    base_price = float(rt[\"mark\"])\n"
            "            except Exception:\n"
            "                sym = (self.symbol or \"\").upper()\n"
            "                if \"BTC\" in sym:\n"
            "                    base_price = 82000.0\n"
            "                elif \"ETH\" in sym:\n"
            "                    base_price = 3000.0\n"
            "                elif \"XAU\" in sym or \"GOLD\" in sym:\n"
            "                    base_price = 4100.0\n"
            "        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq=\"1min\")\n"
            "        rng = np.random.default_rng(42)\n"
            "        tick = max(base_price * 0.0002, 0.3)\n"
            "        rets = rng.normal(0, tick, limit)\n"
            "        p = base_price + np.cumsum(rets)\n"
            "        highs = p + rng.uniform(tick * 0.3, tick * 2, limit)\n"
            "        lows = p - rng.uniform(tick * 0.3, tick * 2, limit)\n",
        ),
    ])
    logs += patch_file(ROOT / "config.py", [
        ('DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUTUSD").upper()',
         'DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUUSD").upper()'),
        ('DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "tradingview").lower()',
         'DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()'),
        ('DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "demo").lower()',
         'DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()'),
        ('AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "false").lower() in ("true", "1", "yes", "on")',
         'AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "true").lower() in ("true", "1", "yes", "on")'),
    ])
    print("[wire_production_fixes]", "; ".join(logs))


if __name__ == "__main__":
    main()
