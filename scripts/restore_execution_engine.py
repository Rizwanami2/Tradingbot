#!/usr/bin/env python3
"""Restore execution_engine.py if empty/corrupt. Run: python scripts/restore_execution_engine.py"""
import base64, gzip, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "execution_engine.py"

# Prefer recovering from previous good commit on GitHub if local empty
GOOD_URL = "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/50181ba/execution_engine.py"

def main():
    if TARGET.exists() and TARGET.stat().st_size > 1000:
        text = TARGET.read_text(encoding="utf-8", errors="replace")
        if "class ExecutionEngine" in text or "def register_setup" in text:
            print("execution_engine.py looks OK, size", TARGET.stat().st_size)
            return 0
    print("Downloading good execution_engine.py from commit 50181ba...")
    data = urllib.request.urlopen(GOOD_URL, timeout=60).read()
    TARGET.write_bytes(data)
    print("Wrote", TARGET, "size", len(data))
    print("Now pull latest patches or re-apply live halt recovery if needed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
