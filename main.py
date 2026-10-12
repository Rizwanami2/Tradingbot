#!/usr/bin/env python3
"""Thin launcher: load full bootstrap from bootstrap_runtime.py on this repo."""
import urllib.request
import sys
import os

URLS = [
    "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/main/bootstrap_runtime.py",
]

def main():
    last_err = None
    for url in URLS:
        try:
            code = urllib.request.urlopen(url, timeout=45).read().decode("utf-8")
            if len(code) < 500 or "def main" not in code:
                raise RuntimeError(f"bad bootstrap payload from {url} ({len(code)} bytes)")
            print(f"[launcher] loaded bootstrap ({len(code)} bytes)")
            exec(compile(code, "bootstrap_runtime.py", "exec"), {"__name__": "__main__", "__file__": "main.py"})
            return
        except Exception as e:
            last_err = e
            print(f"[launcher] {url} failed: {e}")
    print("[launcher] FATAL: could not load bootstrap_runtime.py", last_err)
    sys.exit(1)

if __name__ == "__main__":
    main()
