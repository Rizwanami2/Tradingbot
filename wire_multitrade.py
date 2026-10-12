#!/usr/bin/env python3
"""Ensure max 5 concurrent autotrade slots + multi-symbol defaults."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg = ROOT / "config.py"
    if cfg.exists():
        t = cfg.read_text(errors="ignore")
        if "MAX_OPEN_TRADES" not in t:
            append = "\n".join([
                "",
                "MAX_OPEN_TRADES = _safe_int(os.getenv(\"BOT_MAX_OPEN_TRADES\"), 5)",
                "_AUTOTRADE_SYMBOLS_RAW = os.getenv(\"BOT_AUTOTRADE_SYMBOLS\", \"BTCUSDT,ETHUSDT,XAUUSD,SOLUSDT,BNBUSDT\")",
                "AUTOTRADE_SYMBOLS = [s.strip().upper() for s in _AUTOTRADE_SYMBOLS_RAW.split(\",\") if s.strip()] or [DEFAULT_SYMBOL]",
                "",
            ])
            cfg.write_text(t + append)
            print("[wire_multitrade] config defaults added")

    ee = ROOT / "execution_engine.py"
    if not ee.exists():
        print("[wire_multitrade] done")
        return
    t = ee.read_text(errors="ignore")
    changed = False
    if "max_open_trades" not in t:
        needle = "self.execution_halted: bool = False\n"
        insert = (
            "self.execution_halted: bool = False\n"
            "        try:\n"
            "            import config as _cfg_max\n"
            "            self.max_open_trades = int(getattr(_cfg_max, \"MAX_OPEN_TRADES\", 5) or 5)\n"
            "        except Exception:\n"
            "            self.max_open_trades = 5\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_multitrade] max_open_trades attr")
    if "def open_trade_count" not in t:
        needle = "        self.execution_halted = False\n"
        insert = (
            "        self.execution_halted = False\n\n"
            "    def open_trade_count(self) -> int:\n"
            "        return len(self.active_positions) + len(self.pending_setups)\n\n"
            "    def slots_available(self) -> int:\n"
            "        return max(0, int(getattr(self, \"max_open_trades\", 5)) - self.open_trade_count())\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_multitrade] open_trade_count")
    if "Max concurrent trades reached" not in t:
        needle = '        symbol = sig["symbol"].upper()\n'
        insert = (
            '        symbol = sig["symbol"].upper()\n'
            "        already = symbol in self.active_positions or symbol in self.pending_setups\n"
            "        if not already and self.open_trade_count() >= int(getattr(self, \"max_open_trades\", 5)):\n"
            "            raise RuntimeError(\n"
            "                f\"Max concurrent trades reached ({getattr(self, 'max_open_trades', 5)}).\"\n"
            "            )\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_multitrade] capacity guard")
    if changed:
        ee.write_text(t)
    else:
        print("[wire_multitrade] execution_engine OK")
    print("[wire_multitrade] done")


if __name__ == "__main__":
    main()
