#!/usr/bin/env python3
"""Self-healing bootstrap: restore full bot from GitHub raw if placeholder, apply BTC signal fix, then run."""
from __future__ import annotations
import os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOOD_SHA = "67fa1214d01b82fa201685ffe8b27eee5fdb19ac"
FILES = [
    "main.py",
    "multi_agent_trading_bot.py",
    "risk_manager.py",
]

def fetch(path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/Rizwanami2/Tradingbot/{GOOD_SHA}/{path}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()

def needs_restore(p: Path) -> bool:
    if not p.exists():
        return True
    t = p.read_text(errors="ignore")
    if t.strip() in ("PLACEHOLDER", "") or len(t) < 500:
        return True
    if p.name == "main.py" and "async def signal_command" not in t:
        return True
    return False

def apply_wire():
    wire = ROOT / "wire_signal_btc_fix.py"
    if wire.exists():
        import runpy
        runpy.run_path(str(wire), run_name="__main__")

def restore():
    for name in FILES:
        p = ROOT / name
        if needs_restore(p) or name != "main.py":
            try:
                data = fetch(name)
                p.write_bytes(data)
                print(f"[bootstrap] restored {name} from {GOOD_SHA[:7]} ({len(data)} bytes)")
            except Exception as e:
                print(f"[bootstrap] restore {name} failed: {e}")
    apply_wire()

if __name__ == "__main__":
    if Path(__file__).name == "main.py" and "Self-healing bootstrap" in Path(__file__).read_text(errors="ignore")[:200]:
        restore()
        real = (ROOT / "main.py").read_text(errors="ignore")
        if "Self-healing bootstrap" in real[:300]:
            print("[bootstrap] still stub after restore — abort")
            sys.exit(1)
        os.execv(sys.executable, [sys.executable, str(ROOT / "main.py")] + sys.argv[1:])
    else:
        restore()
        print("[bootstrap] done")
