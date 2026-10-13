#!/usr/bin/env python3
"""Ensure max concurrent autotrade slots + Gold/BTC-only defaults."""
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
                "_AUTOTRADE_SYMBOLS_RAW = os.getenv(\"BOT_AUTOTRADE_SYMBOLS\", \"BTCUSDT,XAUUSD\")",
                "AUTOTRADE_SYMBOLS = [s.strip().upper() for s in _AUTOTRADE_SYMBOLS_RAW.split(\",\") if s.strip()] or [\"BTCUSDT\", \"XAUUSD\"]",
                "",
            ])
            cfg.write_text(t + append)
            print("[wire_multitrade] config defaults added")
        elif "ETHUSDT" in t or "SOLUSDT" in t or "BNBUSDT" in t:
            t = t.replace("BTCUSDT,ETHUSDT,XAUUSD,SOLUSDT,BNBUSDT", "BTCUSDT,XAUUSD")
            t = t.replace("BTCUSDT,ETHUSDT,XAUUSD", "BTCUSDT,XAUUSD")
            cfg.write_text(t)
            print("[wire_multitrade] symbols -> Gold+BTC only")
        else:
            print("[wire_multitrade] config OK")

    ee = ROOT / "execution_engine.py"
    if ee.exists():
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
        if changed:
            ee.write_text(t)
            print("[wire_multitrade] execution_engine capacity")
        else:
            print("[wire_multitrade] execution_engine OK")

    main_py = ROOT / "main.py"
    if main_py.exists():
        mt = main_py.read_text(errors="ignore")
        if "from autotrade_worker import live_monitoring_worker" not in mt:
            marker = "async def live_monitoring_worker(app):\n"
            if marker in mt:
                inject = (
                    "async def live_monitoring_worker(app):\n"
                    "    try:\n"
                    "        from autotrade_worker import live_monitoring_worker as _multi_worker\n"
                    "        return await _multi_worker(\n"
                    "            app,\n"
                    "            config=config,\n"
                    "            engine=engine,\n"
                    "            risk_mgr=risk_mgr,\n"
                    "            arbiter=arbiter,\n"
                    "            MarketDataFeed=MarketDataFeed,\n"
                    "            autotrade_active_getter=lambda: autotrade_active,\n"
                    "            write_runtime_status=_write_runtime_status,\n"
                    "            monitoring_health=monitoring_health,\n"
                    "            now_iso=_now_iso,\n"
                    "        )\n"
                    "    except Exception as _aw_err:\n"
                    "        print(\"[autotrade_worker] using legacy worker:\", _aw_err)\n"
                )
                mt = mt.replace(marker, inject, 1)
                main_py.write_text(mt)
                print("[wire_multitrade] main.py -> autotrade_worker")
            else:
                print("[wire_multitrade] main worker marker miss")
        else:
            print("[wire_multitrade] main already delegates")
    print("[wire_multitrade] done")


if __name__ == "__main__":
    main()
