#!/usr/bin/env python3
"""Restore original mt5_broker.py (no hybrid). Run: python scripts/restore_mt5_original.py"""
import base64, gzip, urllib.request
from pathlib import Path

# Prefer git history if available; else embedded payload below after git show fails
def main():
    target = Path("mt5_broker.py")
    # 1) Try git show from known good commit
    import subprocess
    try:
        r = subprocess.run(
            ["git", "show", "575aba1aa62a9e9ae6b48d212438a956b8ae6e62:mt5_broker.py"],
            capture_output=True,
            check=True,
        )
        if b"class MT5Broker" in r.stdout and b"Hybrid ON" not in r.stdout:
            target.write_bytes(r.stdout)
            print("OK: restored from git history", len(r.stdout), "bytes (no hybrid)")
            return
    except Exception as e:
        print("git show skipped:", e)

    # 2) Embedded gzip+base64 of original (no hybrid)
    b64_path = Path("scripts/mt5_broker_orig.gz.b64")
    if b64_path.exists():
        raw = gzip.decompress(base64.b64decode(b64_path.read_text().strip()))
        target.write_bytes(raw)
        print("OK: restored from scripts/mt5_broker_orig.gz.b64", len(raw), "bytes")
        return

    raise SystemExit(
        "Could not restore. Run:\n"
        "  git fetch origin\n"
        "  git show 575aba1aa62a9e9ae6b48d212438a956b8ae6e62:mt5_broker.py > mt5_broker.py\n"
    )

if __name__ == "__main__":
    main()
