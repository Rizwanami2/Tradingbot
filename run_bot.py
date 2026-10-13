#!/usr/bin/env python3
"""Production entrypoint: restore modules, apply fixes, start Telegram bot."""
from __future__ import annotations

import runpy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _safe_wire(name: str) -> None:
    try:
        mod = __import__(name)
        if hasattr(mod, "main"):
            mod.main()
        elif hasattr(mod, "restore"):
            mod.restore()
    except Exception as e:
        print(f"[run_bot] {name}: {type(e).__name__}: {e}")


def main() -> None:
    sys.path.insert(0, str(ROOT))

    for name in (
        "restore_production",
        "wire_production_fixes",
        "wire_strategy_fixes",
        "wire_multitrade",
        "wire_gautam",
        "wire_micro_equity",
        "wire_signal_fix",
        "wire_railway_fix",
        "wire_positions_scalp",
    ):
        _safe_wire(name)

    try:
        from ai_historical_predictor import patch_into_indicators_module
        patch_into_indicators_module()
    except Exception as e:
        print("[run_bot] ai_historical:", e)

    main_py = ROOT / "main.py"
    if main_py.exists():
        t = main_py.read_text(errors="ignore")
        fixed = False
        if "application.add_handler" in t:
            t = t.replace("application.add_handler", "app.add_handler")
            fixed = True
            print("[run_bot] fixed application->app NameError")
        if "engine = ExecutionEngine(risk_manager=risk_mgr)" in t and "broker=_broker" not in t:
            t = t.replace(
                "engine = ExecutionEngine(risk_manager=risk_mgr)",
                "try:\n"
                "    from execution import create_broker\n"
                "    _broker = create_broker(\n"
                '        broker_mode=getattr(config, "BROKER_MODE", "paper"),\n'
                '        exchange_id=getattr(config, "DEFAULT_EXCHANGE", "binance"),\n'
                '        api_key=getattr(config, "EXCHANGE_API_KEY", ""),\n'
                '        api_secret=getattr(config, "EXCHANGE_API_SECRET", ""),\n'
                '        initial_balance=getattr(config, "INITIAL_EQUITY", 1000.0),\n'
                "    )\n"
                "except Exception as _broker_err:\n"
                '    print("[boot] broker:", _broker_err)\n'
                "    _broker = None\n"
                "engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)",
                1,
            )
            fixed = True
            print("[run_bot] paper broker wired")
        if fixed:
            main_py.write_text(t)

    if not main_py.exists():
        print("[run_bot] FATAL: main.py missing")
        sys.exit(1)

    print("[run_bot] starting main.py")
    sys.argv[0] = str(main_py)
    try:
        runpy.run_path(str(main_py), run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        print("[run_bot] FATAL crash in main.py:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
