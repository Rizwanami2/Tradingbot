#!/usr/bin/env python3
"""Strategy-layer fixes applied on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ma = ROOT / "multi_agent_trading_bot.py"
    if ma.exists():
        t = ma.read_text(errors="ignore")
        if "class Arbiter:" in t:
            head, _, rest = t.partition("class Arbiter:")
            rest2 = rest.replace("MIN_SCORE = 1.8", "MIN_SCORE = 1.2", 1)
            rest2 = rest2.replace("MIN_AGREEMENT = 3", "MIN_AGREEMENT = 2", 1)
            if rest2 != rest:
                ma.write_text(head + "class Arbiter:" + rest2)
                print("[wire_strategy] Arbiter thresholds fixed")
            else:
                print("[wire_strategy] Arbiter already OK")
    print("[wire_strategy] done")


if __name__ == "__main__":
    main()
