#!/usr/bin/env python3
"""Launcher: fetch known-good bootstrap from pinned commit, then run."""
import urllib.request
import sys

# Pinned commit with in-memory bootstrap (BTC fixes, read-only FS safe)
PINNED = [
    "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/55535401b56a4bb9d07ca5af5ae5a35198730c84/main.py",
    "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/67fa1214d01b82fa201685ffe8b27eee5fdb19ac/main.py",
]

def main():
    last_err = None
    for url in PINNED:
        try:
            code = urllib.request.urlopen(url, timeout=45).read().decode("utf-8")
            if len(code) < 500:
                raise RuntimeError(f"payload too small ({len(code)})")
            # Prefer in-memory bootstrap; if we got classic main, still runnable
            print(f"[launcher] loaded {len(code)} bytes from {url.split('/')[5][:7]}")
            # Inject autotrade on_demand=True if classic main
            if "live_monitoring_worker" in code and "on_demand=True" not in code:
                code = code.replace(
                    "risk_mgr, symbol=symbol\n                    )",
                    "risk_mgr, symbol=symbol, on_demand=True\n                    )",
                    1,
                )
                code = code.replace(
                    "risk_mgr, symbol=symbol)",
                    "risk_mgr, symbol=symbol, on_demand=True)",
                    1,
                )
            exec(compile(code, "main_boot.py", "exec"), {"__name__": "__main__", "__file__": "main.py"})
            return
        except SystemExit:
            raise
        except Exception as e:
            last_err = e
            print(f"[launcher] failed: {e}")
    print("[launcher] FATAL", last_err)
    sys.exit(1)

if __name__ == "__main__":
    main()
