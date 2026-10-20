#!/usr/bin/env python3
"""Wire 2-year daily analysis into signal_pipeline on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    sp = ROOT / "signal_pipeline.py"
    if not sp.exists():
        print("[wire_2y] signal_pipeline missing")
        return
    t = sp.read_text(encoding="utf-8", errors="ignore")
    changed = False

    if "historical_2y" not in t:
        # Inject 2y block before final return of run_signal
        anchor = '    res["symbol"] = sym\n    res["equity"] = eq\n    res["leverage"] = risk.get_leverage()\n    return res'
        block = (
            "    try:\n"
            "        from historical_2y import analyze_2y\n"
            "        h2 = analyze_2y(sym)\n"
            "        res[\"history_2y\"] = h2.to_dict()\n"
            "        if res.get(\"signal\") and h2.bars >= 60:\n"
            "            d = res[\"signal\"].get(\"direction\")\n"
            "            dname = d.name if hasattr(d, \"name\") else str(d)\n"
            "            if \"LONG\" in dname.upper() and h2.bias_score < -0.35:\n"
            "                res[\"status\"] = str(res.get(\"status\", \"\")) + \" | 2Y bias caution (bearish structure)\"\n"
            "            elif \"SHORT\" in dname.upper() and h2.bias_score > 0.35:\n"
            "                res[\"status\"] = str(res.get(\"status\", \"\")) + \" | 2Y bias caution (bullish structure)\"\n"
            "    except Exception as _h2_err:\n"
            "        res[\"history_2y_error\"] = str(_h2_err)\n\n"
            + anchor
        )
        if anchor in t:
            t = t.replace(anchor, block, 1)
            changed = True
            print("[wire_2y] run_signal history attached")

    if "format_history_2y" not in t and "def format_signal_message" in t:
        vt = t.find("    votes_txt")
        if vt > 0:
            inject = (
                "    hist_txt = \"\"\n"
                "    try:\n"
                "        from historical_2y import History2Y, format_history_2y\n"
                "        hraw = res.get(\"history_2y\")\n"
                "        if hraw:\n"
                "            h2 = History2Y(\n"
                "                symbol=str(hraw.get(\"symbol\", sym)),\n"
                "                bars=int(hraw.get(\"bars\") or 0),\n"
                "                years_span=float(hraw.get(\"years_span\") or 0),\n"
                "                mark=float(hraw.get(\"mark\") or 0),\n"
                "                high_2y=float(hraw.get(\"high_2y\") or 0),\n"
                "                low_2y=float(hraw.get(\"low_2y\") or 0),\n"
                "                high_52w=float(hraw.get(\"high_52w\") or 0),\n"
                "                low_52w=float(hraw.get(\"low_52w\") or 0),\n"
                "                ema50=float(hraw.get(\"ema50\") or 0),\n"
                "                ema200=float(hraw.get(\"ema200\") or 0),\n"
                "                trend=str(hraw.get(\"trend\") or \"RANGE\"),\n"
                "                support=list(hraw.get(\"support\") or []),\n"
                "                resistance=list(hraw.get(\"resistance\") or []),\n"
                "                bias_score=float(hraw.get(\"bias_score\") or 0),\n"
                "                summary=str(hraw.get(\"summary\") or \"\"),\n"
                "            )\n"
                "            hist_txt = format_history_2y(h2)\n"
                "    except Exception:\n"
                "        hist_txt = \"\"\n"
            )
            t = t[:vt] + inject + t[vt:]
            if 'f"{plan_txt}"' in t and "hist_txt" not in t.split('f"{plan_txt}"')[1][:80]:
                t = t.replace('f"{plan_txt}"', 'f"{plan_txt}"\n        f"{hist_txt}"', 1)
            elif 'f"{plan_txt}"\n    )' in t:
                t = t.replace('f"{plan_txt}"\n    )', 'f"{plan_txt}"\n        f"{hist_txt}"\n    )', 1)
            changed = True
            print("[wire_2y] format history")

    if changed:
        sp.write_text(t, encoding="utf-8")
        print("[wire_2y] signal_pipeline updated")
    else:
        print("[wire_2y] signal_pipeline OK")

    ep = ROOT / "entry_level_planner.py"
    if ep.exists():
        et = ep.read_text(encoding="utf-8", errors="ignore")
        if "historical_2y" not in et and "all_levels.update(emas)" in et:
            et = et.replace(
                "    all_levels.update(emas)\n",
                "    all_levels.update(emas)\n"
                "    try:\n"
                "        from historical_2y import analyze_2y, history_levels_for_planner\n"
                "        h2 = analyze_2y(symbol)\n"
                "        all_levels.update(history_levels_for_planner(h2))\n"
                "    except Exception:\n"
                "        pass\n",
                1,
            )
            ep.write_text(et, encoding="utf-8")
            print("[wire_2y] entry planner levels")
    print("[wire_2y] done")


if __name__ == "__main__":
    main()
