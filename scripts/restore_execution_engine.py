#!/usr/bin/env python3
"""Restore execution_engine.py from last good commit and apply live trading halt recovery."""
from __future__ import annotations

import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "execution_engine.py"
GOOD_URL = "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/50181ba/execution_engine.py"


def _apply_live_recovery(text: str) -> str:
    old_reset = '''    def reset_halt(self) -> None:
        """Clear execution halt state so setups can be registered again."""
        self.execution_halted = False
'''
    new_reset = '''    def reset_halt(self) -> None:
        """Clear execution halt state so setups can be registered again."""
        self.execution_halted = False
        try:
            self.save_state()
        except Exception:
            pass

    def ensure_live_ready(self) -> bool:
        """Live recovery: clear sticky halt when broker is reachable. Keeps kill-switch."""
        if not self.execution_halted:
            return True
        try:
            if getattr(self, "broker", None) is not None:
                if hasattr(self, "reconcile_positions"):
                    try:
                        self.reconcile_positions()
                    except Exception as rec_err:
                        logger.warning("[ExecutionEngine] reconcile during ensure_live_ready: %s", rec_err)
                try:
                    _ = getattr(self.broker, "equity", None)
                    if callable(_):
                        _ = self.broker.equity()
                    elif _ is None and hasattr(self.broker, "get_account_info"):
                        self.broker.get_account_info()
                except Exception:
                    pass
                if hasattr(self.broker, "is_connected"):
                    connected = self.broker.is_connected
                    if callable(connected):
                        connected = connected()
                    if not connected and hasattr(self.broker, "connect"):
                        try:
                            self.broker.connect()
                        except Exception as c_err:
                            logger.warning("[ExecutionEngine] broker reconnect: %s", c_err)
            self.execution_halted = False
            try:
                self.save_state()
            except Exception:
                pass
            logger.info("[ExecutionEngine] Live halt cleared — execution ready")
            return True
        except Exception as e:
            logger.error("[ExecutionEngine] ensure_live_ready failed: %s", e)
            return False
'''
    if "def ensure_live_ready" not in text:
        if old_reset not in text:
            raise RuntimeError("reset_halt block not found in base execution_engine.py")
        text = text.replace(old_reset, new_reset, 1)

    old_restore = "            self.execution_halted = bool(content.get(\"execution_halted\", False))\n"
    new_restore = '''            import os as _os_halt
            _persist = _os_halt.getenv("LIVE_PERSIST_HALT", "0").lower() in ("1", "true", "yes", "on")
            _saved_halt = bool(content.get("execution_halted", False))
            self.execution_halted = _saved_halt if _persist else False
            if _saved_halt and not _persist:
                logger.info("[ExecutionEngine] Cleared persisted halt on restore (live-ready default)")
'''
    if "LIVE_PERSIST_HALT" not in text and old_restore in text:
        text = text.replace(old_restore, new_restore, 1)

    old_reg = '''        if self.execution_halted:
            raise RuntimeError(
                "Execution halted after uncertain broker state; reconcile positions, then run /autotrade on or /risk restart before re-enabling."
            )
'''
    new_reg = '''        if self.execution_halted:
            if not self.ensure_live_ready():
                raise RuntimeError(
                    "Execution halted after uncertain broker state; run /mt5 reset or /risk restart after MT5 is connected."
                )
'''
    if "register_setup" in text:
        try:
            chunk = text.split("register_setup")[1][:800]
        except Exception:
            chunk = ""
        if "ensure_live_ready()" not in chunk and old_reg in text:
            text = text.replace(old_reg, new_reg, 1)

    old_halt = '''                    if "live" in _hm:
                        self.execution_halted = True
                    setup.status = OrderStatus.CANCELLED
                    setup.close_reason = f"Execution failed: {ex}"
'''
    new_halt = '''                    _ex_l = f"{type(ex).__name__}: {ex}".lower()
                    _uncertain = (
                        "unknown" in _ex_l
                        or "uncertain" in _ex_l
                        or "not confirmed" in _ex_l
                        or "timeout" in _ex_l
                    )
                    _clear_reject = any(
                        x in _ex_l
                        for x in (
                            "10027", "10013", "10014", "10015", "10016", "10018", "10019",
                            "autotrading disabled", "invalid request", "invalid volume",
                            "no money", "market closed", "trade is disabled",
                        )
                    )
                    if "live" in _hm and _uncertain and not _clear_reject:
                        self.execution_halted = True
                    setup.status = OrderStatus.CANCELLED
                    setup.close_reason = f"Execution failed: {ex}"
'''
    if "_clear_reject" not in text and old_halt in text:
        text = text.replace(old_halt, new_halt, 1)

    return text


def main() -> int:
    print("Downloading base execution_engine.py from commit 50181ba...")
    data = urllib.request.urlopen(GOOD_URL, timeout=60).read().decode("utf-8")
    data = _apply_live_recovery(data)
    TARGET.write_text(data, encoding="utf-8")
    print(f"Wrote {TARGET} ({len(data)} bytes)")
    assert "ensure_live_ready" in data
    assert "def register_setup" in data
    print("Live trading halt recovery applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
