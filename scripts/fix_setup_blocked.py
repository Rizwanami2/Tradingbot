#!/usr/bin/env python3
"""Allow multi-agent consensus reasons through CombinedFIT structure gate."""
from pathlib import Path
import re
import ast

NEW_BLOCK = '''        su = (setup or "pullback").lower().strip()
        # Multi-agent reasons look like "consensus long score=28" — treat as valid setup
        ok_keywords = (
            "pull", "sweep", "liquid", "trend", "scalp", "consensus", "score",
            "long", "short", "agent", "arbiter", "swarm", "break", "fvg", "ob",
            "ote", "bos", "choch", "smc", "ict", "pivot", "fib",
        )
        allowed = {
            "pullback", "sweep_reclaim", "sweep", "liquidity", "consensus",
            "multi_agent", "swarm", "scalp", "break_retest", "breakout", "",
        }
        if self.cfg.allow_break_retest:
            allowed |= {"break_retest", "breakout"}
        if not su or su in allowed:
            return True, "structure_ok"
        if any(k in su for k in ok_keywords):
            return True, "structure_ok"
        if "score" in su or "=" in su or len(su) > 24:
            return True, "structure_ok"
        return False, f"setup_blocked:{su[:40]}"
'''

def main():
    p = Path("combined_bot_agent.py")
    t = p.read_text(encoding="utf-8")
    t2, n = re.subn(
        r"        su = \(setup or \"pullback\"\)\.lower\(\).*?return True, \"structure_ok\"\n",
        NEW_BLOCK,
        t,
        count=1,
        flags=re.S,
    )
    if not n:
        if "consensus" in t and "ok_keywords" in t:
            print("combined already fixed")
        else:
            raise SystemExit("could not patch structure_ok")
    else:
        p.write_text(t2, encoding="utf-8")
        print("OK combined_bot_agent")
    ast.parse(p.read_text(encoding="utf-8"))

    w = Path("autotrade_worker.py")
    wt = w.read_text(encoding="utf-8")
    wt2 = wt.replace(
        '"setup": str(sig.get("setup") or sig.get("reason") or "pullback"),',
        '"setup": str(sig.get("setup") or "consensus"),',
    )
    if wt2 != wt:
        w.write_text(wt2, encoding="utf-8")
        print("OK autotrade_worker")
    else:
        print("worker setup line skip/already fixed")
    ast.parse(w.read_text(encoding="utf-8"))
    print("DONE — restart bot")

if __name__ == "__main__":
    main()
