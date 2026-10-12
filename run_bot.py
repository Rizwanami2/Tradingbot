#!/usr/bin/env python3
"""Production entrypoint: restore modules, apply fixes, start Telegram bot."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    try:
        import restore_production
        restore_production.restore()
    except Exception as e:
        print("[run_bot] restore:", e)

    try:
        import wire_production_fixes
        wire_production_fixes.main()
    except Exception as e:
        print("[run_bot] wire:", e)

    try:
        import wire_strategy_fixes
        wire_strategy_fixes.main()
    except Exception as e:
        print("[run_bot] strategy wire:", e)

    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[run_bot] FATAL: main.py missing")
        sys.exit(1)
    print("[run_bot] starting main.py")
    sys.argv[0] = str(main_py)
    runpy.run_path(str(main_py), run_name="__main__")


if __name__ == "__main__":
    main()
