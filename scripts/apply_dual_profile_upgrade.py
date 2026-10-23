#!/usr/bin/env python3
"""Idempotent dual-profile + CombinedFIT setup fix for autotrade_worker."""
from pathlib import Path
import re
import ast

def main():
    w = Path("autotrade_worker.py")
    t = w.read_text(encoding="utf-8")
    t2 = t.replace(
        '"setup": str(sig.get("setup") or sig.get("reason") or "pullback"),',
        '"setup": str(sig.get("setup") or "consensus"),',
    )
    t2 = re.sub(
        r'"setup":\s*str\(sig\.get\("setup"\)\s*or\s*sig\.get\("reason"\)\s*or\s*"[^"]*"\),',
        '"setup": str(sig.get("setup") or "consensus"),',
        t2,
        count=5,
    )
    if t2 != t:
        w.write_text(t2, encoding="utf-8")
        print("patched autotrade_worker setup")
    else:
        print("autotrade_worker already OK or pattern differs")
    for f in ["combined_bot_agent.py", "anti_spam.py", "strategy_profiles.py", "autotrade_worker.py"]:
        if Path(f).exists():
            ast.parse(Path(f).read_text(encoding="utf-8"))
            print("syntax OK", f)

if __name__ == "__main__":
    main()
