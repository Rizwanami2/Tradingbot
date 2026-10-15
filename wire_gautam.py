#!/usr/bin/env python3
"""Wire Gautam Jha agent into multi-agent Arbiter on boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ma = ROOT / "multi_agent_trading_bot.py"
    if not ma.exists():
        print("[wire_gautam] multi_agent missing")
        return
    t = ma.read_text(encoding="utf-8", errors="ignore")
    changed = False

    if "gautam_jha_strategy" not in t:
        needle = "classify_market_regime = None\n"
        insert = (
            "classify_market_regime = None\n\n"
            "try:\n"
            "    from gautam_jha_strategy import GautamJhaAgent, GJDir\n"
            "except ImportError:\n"
            "    GautamJhaAgent = None\n"
            "    GJDir = None\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_gautam] import")

    if '"GAUTAM-JHA"' not in t and "'GAUTAM-JHA'" not in t:
        needle = '"NEWS": 0.70,\n    }'
        insert = '"NEWS": 0.70,\n        "GAUTAM-JHA": 1.55,\n    }'
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_gautam] weight")

    if "self.gautam" not in t:
        needle = "self.news = NewsAgent()\n"
        insert = (
            "self.news = NewsAgent()\n"
            "        self.gautam = GautamJhaAgent() if GautamJhaAgent is not None else None\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_gautam] init")

    if "self.gautam is not None" not in t and "gautam.vote" not in t:
        needle = "            self.news.vote(),\n        ]\n"
        insert = (
            "            self.news.vote(),\n"
            "        ]\n"
            "        if getattr(self, \"gautam\", None) is not None:\n"
            "            try:\n"
            "                gv = self.gautam.vote(df1, daily if daily is not None else pd.DataFrame())\n"
            "                dname = getattr(gv.direction, \"name\", str(gv.direction))\n"
            "                direction = Dir.LONG if dname == \"LONG\" else (Dir.SHORT if dname == \"SHORT\" else Dir.FLAT)\n"
            "                votes.append(Vote(gv.agent, direction, float(gv.strength), gv.reason))\n"
            "            except Exception:\n"
            "                pass\n"
        )
        if needle in t:
            t = t.replace(needle, insert, 1)
            changed = True
            print("[wire_gautam] vote hook")

    if changed:
        ma.write_text(t, encoding="utf-8")
        print("[wire_gautam] multi_agent patched")
    else:
        print("[wire_gautam] multi_agent already OK")
    print("[wire_gautam] done")


if __name__ == "__main__":
    main()
