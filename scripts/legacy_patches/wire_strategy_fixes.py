#!/usr/bin/env python3
"""Strategy-layer fixes applied on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ma = ROOT / "multi_agent_trading_bot.py"
    if ma.exists():
        t = ma.read_text(encoding="utf-8", errors="ignore")
        changed = False
        if "class Arbiter:" in t:
            head, _, rest = t.partition("class Arbiter:")
            rest2 = rest.replace("MIN_SCORE = 1.8", "MIN_SCORE = 1.2", 1)
            rest2 = rest2.replace("MIN_AGREEMENT = 3", "MIN_AGREEMENT = 2", 1)
            if rest2 != rest:
                t = head + "class Arbiter:" + rest2
                changed = True
        if "class IndicatorAgent:" in t:
            ih, _, ir = t.partition("class IndicatorAgent:")
            ir2 = ir.replace("MIN_SCORE = 1.8", "MIN_SCORE = 1.2", 1)
            if ir2 != ir:
                t = ih + "class IndicatorAgent:" + ir2
                changed = True
        old_sig = "def vote(self, df: pd.DataFrame, daily: pd.DataFrame) -> Vote:"
        new_sig = "def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> Vote:"
        if old_sig in t:
            t = t.replace(old_sig, new_sig, 1)
            changed = True
            print("[wire_strategy] LiquidityAgent daily optional")
        if changed:
            ma.write_text(t, encoding="utf-8")
            print("[wire_strategy] multi_agent patches applied")
        else:
            print("[wire_strategy] multi_agent already OK")

    ind = ROOT / "trading_strategy_indicators_pro.py"
    if ind.exists():
        it = ind.read_text(encoding="utf-8", errors="ignore")
        ichanged = False
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
                ichanged = True
                print("[wire_strategy] indicators nan-safe injected")
        if 'def mom(n): return (c[-1]-c[-n-1])/c[-n-1] if len(c)>n else 0' in it:
            it = it.replace(
                'def mom(n): return (c[-1]-c[-n-1])/c[-n-1] if len(c)>n else 0',
                'def mom(n):\n'
                '            if len(c) <= n or abs(float(c[-n-1])) < 1e-12: return 0.0\n'
                '            return float((c[-1]-c[-n-1])/c[-n-1])',
            )
            it = it.replace(
                'if len(df) < 60:\n            return AIPrediction(Direction.FLAT, 0.0, 0.0, horizon, "No data")\n        c = df["close"].values\n',
                'if df is None or len(df) < 60:\n'
                '            return AIPrediction(Direction.FLAT, 0.0, 0.0, horizon, "No data")\n'
                '        c = df["close"].values.astype(float)\n'
                '        if not np.isfinite(c[-1]) or abs(float(c[-1])) < 1e-12:\n'
                '            return AIPrediction(Direction.FLAT, 0.0, 0.0, horizon, "Invalid price")\n',
            )
            it = it.replace(
                'slope = np.polyfit(np.arange(25), c[-25:], 1)[0]/c[-1]',
                'slope = float(np.polyfit(np.arange(25), c[-25:], 1)[0] / c[-1]) if abs(float(c[-1])) > 1e-12 else 0.0\n'
                '        if slope != slope: slope = 0.0',
            )
            it = it.replace(
                'return AIPrediction(d, float(conf), float(exp), horizon, f"AI{horizon}c {d.name} {conf:.0%}")',
                'conf = float(conf) if conf == conf else 0.0\n'
                '        exp = float(exp) if exp == exp else 0.0\n'
                '        return AIPrediction(d, conf, exp, horizon, f"AI{horizon}c {d.name} {conf:.0%}")',
            )
            ichanged = True
            print("[wire_strategy] AIPredictor zero-price guard")
        if ichanged:
            ind.write_text(it, encoding="utf-8")
        else:
            print("[wire_strategy] indicators OK")
    print("[wire_strategy] done")


if __name__ == "__main__":
    main()
