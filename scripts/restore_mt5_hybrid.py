#!/usr/bin/env python3
import base64
from pathlib import Path
parts = []
for i in range(5):
    parts.append(Path(f"scripts/mt5_hybrid_b64_{i}.txt").read_text().strip())
raw = base64.b64decode("".join(parts))
Path("mt5_broker.py").write_bytes(raw)
print("Restored mt5_broker.py", len(raw), "bytes; Hybrid=", b"Hybrid ON" in raw)
