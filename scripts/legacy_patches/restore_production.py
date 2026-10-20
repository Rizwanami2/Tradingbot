#!/usr/bin/env python3
"""Restore production modules if needed, then always apply wire fixes."""
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
    rm_path = root / "risk_manager.py"
    ma_path = root / "multi_agent_trading_bot.py"
    needs = (
        not rm_path.exists()
        or "class RiskManager" not in rm_path.read_text(encoding="utf-8", errors="ignore")
        or not ma_path.exists()
        or "class Arbiter" not in ma_path.read_text(encoding="utf-8", errors="ignore")
        or rm_path.read_text(encoding="utf-8", errors="ignore").strip() in ("PLACEHOLDER", "")
        or ma_path.read_text(encoding="utf-8", errors="ignore").strip() in ("PLACEHOLDER", "")
    )
    if needs:
        for name in ("risk_manager.py", "multi_agent_trading_bot.py"):
            try:
                data = fetch(name)
                (root / name).write_text(data, encoding="utf-8")
                print(f"[restore] {name} {len(data)} bytes")
            except Exception as e:
                print(f"[restore] Failed {name}: {e}")
    else:
        print("[restore] core modules present")

    # Always apply production wire fixes (consensus, stops, prices, config)
    try:
        import wire_production_fixes
        wire_production_fixes.main()
    except Exception as e:
        print("[restore] wire_production_fixes:", e)
    print("[restore] DONE")


if __name__ == "__main__":
    restore()
