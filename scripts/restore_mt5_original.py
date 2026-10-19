#!/usr/bin/env python3
"""Restore original mt5_broker.py (EA bridge only, no hybrid)."""
import base64
from pathlib import Path
parts = []
for i in range(6):
    parts.append(Path(f"scripts/mt5_orig_b64_{i}.txt").read_text().strip())
raw = base64.b64decode("".join(parts))
Path("mt5_broker.py").write_bytes(raw)
assert b"class MT5Broker" in raw
assert b"Hybrid ON" not in raw
print("OK: restored original mt5_broker.py", len(raw), "bytes (no hybrid)")
