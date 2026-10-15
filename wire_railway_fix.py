#!/usr/bin/env python3
"""Railway crash guards: NameError application, missing broker, bad imports."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[wire_railway] main missing")
        return
    t = main_py.read_text(encoding="utf-8", errors="ignore")
    changed = False

    # 1) NameError: application is not defined (crash on bot start)
    if "application.add_handler" in t:
        t = t.replace("application.add_handler", "app.add_handler")
        changed = True
        print("[wire_railway] fixed application->app")

    # 2) Ensure create_broker for paper trades
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
            '        initial_balance=getattr(config, "INITIAL_EQUITY", 100.0),\n'
            "    )\n"
            "except Exception as _broker_err:\n"
            '    print("[boot] broker:", _broker_err)\n'
            "    _broker = None\n"
            "engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)",
            1,
        )
        changed = True
        print("[wire_railway] paper broker")

    # 3) risk_manager syntax sanity
    rm = ROOT / "risk_manager.py"
    if rm.exists():
        try:
            compile(rm.read_text(encoding="utf-8", errors="ignore"), "risk_manager.py", "exec")
        except SyntaxError as e:
            print("[wire_railway] risk_manager SYNTAX", e)

    if changed:
        main_py.write_text(t, encoding="utf-8")
        print("[wire_railway] main.py patched")
    else:
        print("[wire_railway] main OK")
    print("[wire_railway] done")


if __name__ == "__main__":
    main()
