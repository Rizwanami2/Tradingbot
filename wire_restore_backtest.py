#!/usr/bin/env python3
"""Restore advanced_backtest.py if missing/placeholder from known-good GitHub commit."""
from __future__ import annotations

from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
GOOD_URL = (
    "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/"
    "b2f17ebc8f8e0f0e0e0e0e0e0e0e0e0e0e0e0e0e/advanced_backtest.py"
)
# Use short SHA that exists
GOOD_URL = "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/b2f17eb/advanced_backtest.py"


def _is_bad(text: str) -> bool:
    return (
        len(text) < 500
        or "PLACEHOLDER" in text
        or "class AdvancedSimulator" not in text
        or "def walk_forward" not in text
    )


def main() -> None:
    adv = ROOT / "advanced_backtest.py"
    text = adv.read_text(errors="ignore") if adv.exists() else ""
    if not _is_bad(text):
        print("[wire_restore_bt] advanced_backtest OK")
        print("[wire_restore_bt] done")
        return
    print("[wire_restore_bt] advanced_backtest broken — fetching known-good…")
    try:
        with urllib.request.urlopen(GOOD_URL, timeout=30) as r:
            body = r.read().decode("utf-8", errors="ignore")
        if _is_bad(body):
            print("[wire_restore_bt] remote also bad")
            return
        adv.write_text(body)
        print(f"[wire_restore_bt] restored {len(body)} bytes from b2f17eb")
    except Exception as e:
        print(f"[wire_restore_bt] fetch failed: {e}")
    print("[wire_restore_bt] done")


if __name__ == "__main__":
    main()
