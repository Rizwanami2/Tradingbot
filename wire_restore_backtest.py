#!/usr/bin/env python3
"""If advanced_backtest.py was corrupted (placeholder), restore from improved_backtest_signals."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    adv = ROOT / "advanced_backtest.py"
    improved = ROOT / "improved_backtest_signals.py"
    if not adv.exists():
        print("[wire_restore_bt] advanced missing")
        return
    t = adv.read_text(errors="ignore")
    bad = (
        len(t) < 500
        or "PLACEHOLDER" in t
        or "class AdvancedSimulator" not in t
        or "def ema_cross_signal" not in t
    )
    if bad and improved.exists() and "class AdvancedSimulator" in improved.read_text(errors="ignore"):
        adv.write_text(improved.read_text(errors="ignore"))
        print("[wire_restore_bt] restored advanced_backtest from improved_backtest_signals")
    elif bad:
        print("[wire_restore_bt] advanced looks broken and no improved backup")
    else:
        print("[wire_restore_bt] advanced_backtest OK")
    print("[wire_restore_bt] done")


if __name__ == "__main__":
    main()
