"""Pytest configuration and environment fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure root and strategies directories are in sys.path
root = Path(__file__).resolve().parent
strat = root / "strategies"
for p in (str(strat), str(root)):
    if p not in sys.path:
        sys.path.insert(0, p)
