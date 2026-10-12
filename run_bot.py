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

    try:
        from ai_historical_predictor import patch_into_indicators_module
        patch_into_indicators_module()
    except Exception as e:
        print("[run_bot] ai_historical:", e)

    try:
        import wire_multitrade
        wire_multitrade.main()
    except Exception as e:
        print("[run_bot] multitrade:", e)

    try:
        import wire_gautam
        wire_gautam.main()
    except Exception as e:
        print("[run_bot] gautam:", e)

    try:
        import wire_micro_equity
        wire_micro_equity.main()
    except Exception as e:
        print("[run_bot] micro_equity:", e)

    try:
        import wire_signal_fix
        wire_signal_fix.main()
    except Exception as e:
        print("[run_bot] signal_fix:", e)

    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[run_bot] FATAL: main.py missing")
        sys.exit(1)
    print("[run_bot] starting main.py")
    sys.argv[0] = str(main_py)
    runpy.run_path(str(main_py), run_name="__main__")


if __name__ == "__main__":
    main()
