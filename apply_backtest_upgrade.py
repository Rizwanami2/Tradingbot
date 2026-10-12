#!/usr/bin/env python3
"""Hot-patch backtest defaults for better edge."""
from pathlib import Path
import ast
ROOT = Path(__file__).resolve().parent

def main():
    p = ROOT / "advanced_backtest.py"
    t = p.read_text(encoding="utf-8")
    orig = t
    t = t.replace("risk_per_trade: float = 0.0025", "risk_per_trade: float = 0.005")
    t = t.replace("tp_r: float = 1.8", "tp_r: float = 2.8")
    t = t.replace("atr_mult: float = 1.5", "atr_mult: float = 2.2")
    t = t.replace("atr * atr_mult * 1.8", "atr * atr_mult * 2.8")
    t = t.replace("commission_pct: float = 0.0004", "commission_pct: float = 0.00025")
    t = t.replace('timeframe="1m"', 'timeframe="15m"')
    if t != orig:
        ast.parse(t)
        p.write_text(t, encoding="utf-8")
        print("patched advanced_backtest.py")
    else:
        print("advanced_backtest: no simple replacements needed")
    p2 = ROOT / "auto_backtest.py"
    t2 = p2.read_text(encoding="utf-8")
    o2 = t2
    t2 = t2.replace("risk: float = 0.0025", "risk: float = 0.005")
    t2 = t2.replace("default=0.0025", "default=0.005")
    t2 = t2.replace("[1] EMA SINGLE", "[1] EMA CROSS TUNED")
    if t2 != o2:
        ast.parse(t2)
        p2.write_text(t2, encoding="utf-8")
        print("patched auto_backtest.py")
    print("Done")

if __name__ == "__main__":
    main()
