#!/usr/bin/env python3
"""Entry: restore production modules if placeholders, then run full bot."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _needs_restore(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        t = path.read_text(errors="ignore")
    except Exception:
        return True
    if t.strip() in ("PLACEHOLDER", "") or len(t) < 500:
        return True
    if path.name == "multi_agent_trading_bot.py" and "class Arbiter" not in t:
        return True
    if path.name == "risk_manager.py" and "class RiskManager" not in t:
        return True
    return False

def main() -> None:
    critical = [ROOT / "risk_manager.py", ROOT / "multi_agent_trading_bot.py"]
    if any(_needs_restore(p) for p in critical) or _needs_restore(ROOT / "main.py") and "Entry: restore" in (ROOT / "main.py").read_text(errors="ignore")[:80]:
        print("[boot] restoring production modules…")
        try:
            import restore_production
            restore_production.restore()
        except Exception as e:
            print("[boot] restore failed:", e)
            # fallback: fetch classic main only
            import urllib.request
            url = "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/67fa1214d01b82fa201685ffe8b27eee5fdb19ac/main.py"
            code = urllib.request.urlopen(url, timeout=45).read().decode()
            # force on_demand
            code = code.replace("risk_mgr, symbol=symbol)", "risk_mgr, symbol=symbol, on_demand=True)", 1)
            print("[boot] running classic main fallback", len(code))
            exec(compile(code, "main.py", "exec"), {"__name__": "__main__", "__file__": str(ROOT / "main.py")})
            return

    # After restore, main.py was overwritten with full bot — re-exec
    full = (ROOT / "main.py").read_text(errors="ignore")
    if "Entry: restore" in full[:120]:
        # restore did not replace us; run fallback
        import urllib.request
        url = "https://raw.githubusercontent.com/Rizwanami2/Tradingbot/67fa1214d01b82fa201685ffe8b27eee5fdb19ac/main.py"
        code = urllib.request.urlopen(url, timeout=45).read().decode()
        code = code.replace("risk_mgr, symbol=symbol)", "risk_mgr, symbol=symbol, on_demand=True)", 1)
        print("[boot] re-exec classic main", len(code))
        exec(compile(code, "main.py", "exec"), {"__name__": "__main__", "__file__": str(ROOT / "main.py")})
        return

    print("[boot] starting restored main.py")
    os.execv(sys.executable, [sys.executable, str(ROOT / "main.py")] + sys.argv[1:])

if __name__ == "__main__":
    main()
