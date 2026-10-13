#!/usr/bin/env python3
"""Wire Mean Reversion & VWAP Volatility Band agent into multi-agent Arbiter on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ma = ROOT / "multi_agent_trading_bot.py"
    if not ma.exists():
        print("[wire_mean_reversion] multi_agent missing")
        return
    t = ma.read_text(errors="ignore")
    changed = False

    if "mean_reversion_vwap_strategy" not in t:
        needle = "classify_market_regime = None\n"
        insert = (
            "classify_market_regime = None\n\n"
            "try:\n"
            "    from mean_reversion_vwap_strategy import MeanReversionVWAPAgent, MRDir\n"
            "except ImportError:\n"
            "    MeanReversionVWAPAgent = None\n"
            "    MRDir = None\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_mean_reversion] import added")

    if '"MEAN-REVERSION"' not in t and "'MEAN-REVERSION'" not in t:
        needle = '"GAUTAM-JHA": 1.55,\n    }'
        insert = '"GAUTAM-JHA": 1.55,\n        "MEAN-REVERSION": 1.35,\n    }'
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_mean_reversion] weight added")

    if "self.mr" not in t:
        needle = "self.gautam = GautamJhaAgent() if GautamJhaAgent is not None else None\n"
        insert = (
            "self.gautam = GautamJhaAgent() if GautamJhaAgent is not None else None\n"
            "        self.mr = MeanReversionVWAPAgent() if MeanReversionVWAPAgent is not None else None\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_mean_reversion] init added")

    if "self.mr is not None" not in t and "mr.vote" not in t:
        needle = "votes.append(Vote(gv.agent, direction, float(gv.strength), gv.reason))\n            except Exception:\n                pass\n"
        insert = (
            "votes.append(Vote(gv.agent, direction, float(gv.strength), gv.reason))\n"
            "            except Exception:\n"
            "                pass\n\n"
            "        if getattr(self, \"mr\", None) is not None:\n"
            "            try:\n"
            "                mv = self.mr.vote(df1, daily if daily is not None else pd.DataFrame())\n"
            "                dname = getattr(mv.direction, \"name\", str(mv.direction))\n"
            "                direction = Dir.LONG if dname == \"LONG\" else (Dir.SHORT if dname == \"SHORT\" else Dir.FLAT)\n"
            "                votes.append(Vote(mv.agent, direction, float(mv.strength), mv.reason))\n"
            "            except Exception:\n"
            "                pass\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_mean_reversion] vote hook added")

    if changed:
        ma.write_text(t)
        print("[wire_mean_reversion] multi_agent patched")
    else:
        print("[wire_mean_reversion] multi_agent already OK")
    print("[wire_mean_reversion] done")


if __name__ == "__main__":
    main()
