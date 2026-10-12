#!/usr/bin/env python3
"""Strategy-layer fixes applied on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ma = ROOT / "multi_agent_trading_bot.py"
    if ma.exists():
        t = ma.read_text(errors="ignore")
        changed = False
        if "class Arbiter:" in t:
            head, _, rest = t.partition("class Arbiter:")
            rest2 = rest.replace("MIN_SCORE = 1.8", "MIN_SCORE = 1.2", 1)
            rest2 = rest2.replace("MIN_AGREEMENT = 3", "MIN_AGREEMENT = 2", 1)
            if rest2 != rest:
                t = head + "class Arbiter:" + rest2
                changed = True
        old_sig = "def vote(self, df: pd.DataFrame, daily: pd.DataFrame) -> Vote:"
        new_sig = "def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> Vote:"
        if old_sig in t:
            t = t.replace(old_sig, new_sig, 1)
            changed = True
            print("[wire_strategy] LiquidityAgent daily optional")
        if changed:
            ma.write_text(t)
            print("[wire_strategy] multi_agent patches applied")
        else:
            print("[wire_strategy] multi_agent already OK")

    ind = ROOT / "trading_strategy_indicators_pro.py"
    if ind.exists():
        it = ind.read_text(errors="ignore")
        if "def _safe_f" not in it:
            needle = "@dataclass\nclass IndicatorSnapshot:"
            helper = (
                "def _safe_f(x, default=0.0):\n"
                "    try:\n"
                "        v = float(x)\n"
                "        if v != v or v in (float('inf'), float('-inf')):\n"
                "            return default\n"
                "        return v\n"
                "    except Exception:\n"
                "        return default\n\n"
                "@dataclass\nclass IndicatorSnapshot:"
            )
            if needle in it:
                it = it.replace(needle, helper, 1)
                it = it.replace(
                    'snap.vol_osc = float(self.volume_oscillator(df_calc["volume"]).iloc[-1])',
                    'snap.vol_osc = _safe_f(self.volume_oscillator(df_calc["volume"]).iloc[-1])',
                )
                ind.write_text(it)
                print("[wire_strategy] indicators nan-safe injected")
            else:
                print("[wire_strategy] indicators pattern miss")
        else:
            print("[wire_strategy] indicators OK")
    print("[wire_strategy] done")


if __name__ == "__main__":
    main()
