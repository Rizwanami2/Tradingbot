#!/usr/bin/env python3
"""Apply module risk fixes (safer defaults + risk_mgr gate on autotrade)."""
from pathlib import Path

def patch_config():
    p = Path("config.py")
    t = p.read_text(encoding="utf-8")
    if "SAFETY_MAX_OPEN_TRADES" in t:
        print("config: already patched")
        return
    t = t.replace(
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 6)',
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 2)  # safety default\n'
        'SAFETY_MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_SAFETY_MAX_OPEN"), 3)\n'
        'SAFETY_REQUIRE_RISK_CAN_TRADE = os.getenv("BOT_SAFETY_REQUIRE_RISK", "true").lower() in ("true", "1", "yes", "on")',
    )
    t = t.replace(
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 3)',
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 1)',
    )
    p.write_text(t, encoding="utf-8")
    print("config: OK")

def patch_risk():
    p = Path("risk_manager.py")
    t = p.read_text(encoding="utf-8")
    if "max_daily_loss_pct: float = 2.0" in t:
        print("risk_manager: already patched")
        return
    t = t.replace("max_daily_loss_pct: float = 3.0", "max_daily_loss_pct: float = 2.0  # tightened from 3.0")
    t = t.replace("max_consecutive_losses: int = 4", "max_consecutive_losses: int = 3  # tightened from 4")
    t = t.replace("base_risk_pct: float = 0.20", "base_risk_pct: float = 0.15  # tightened from 0.20")
    p.write_text(t, encoding="utf-8")
    print("risk_manager: OK")

def patch_ai():
    p = Path("ai_advanced_suite.py")
    t = p.read_text(encoding="utf-8")
    t2 = t.replace('_env_int("AI_MAX_DAILY_TRADES", 12)', '_env_int("AI_MAX_DAILY_TRADES", 6)')
    t2 = t2.replace('_env_int("AI_MAX_CONSEC_LOSSES", 4)', '_env_int("AI_MAX_CONSEC_LOSSES", 3)')
    if t2 == t:
        print("ai_advanced_suite: no change or already done")
    else:
        p.write_text(t2, encoding="utf-8")
        print("ai_advanced_suite: OK")

def patch_autotrade():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "Risk halt:" in t:
        print("autotrade_worker: already patched")
        return
    t = t.replace(
        'getattr(config, "MAX_OPEN_TRADES", 6)',
        'getattr(config, "MAX_OPEN_TRADES", 2)',
    )
    if "_safety_cap" not in t:
        old = 'max_open = int(getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2)) or 2)'
        new = (
            old
            + "\n    _safety_cap = int(getattr(config, \"SAFETY_MAX_OPEN_TRADES\", 3) or 3)"
            + "\n    max_open = max(1, min(int(max_open), _safety_cap))"
        )
        if old in t:
            t = t.replace(old, new, 1)
    if "Risk halt:" not in t:
        needle = "\n                    if can_open:"
        insert = (
            "\n                    # Safety: risk_manager daily loss / consecutive loss halt"
            "\n                    if can_open and getattr(config, \"SAFETY_REQUIRE_RISK_CAN_TRADE\", True):"
            "\n                        try:"
            "\n                            _ok_risk, _risk_why = risk_mgr.can_trade()"
            "\n                        except Exception as _re:"
            "\n                            _ok_risk, _risk_why = True, \"\""
            "\n                            logger.debug(\"risk_mgr.can_trade error: %s\", _re)"
            "\n                        if not _ok_risk:"
            "\n                            can_open = False"
            "\n                            monitoring_health[f\"{symbol}_eval\"] = f\"Risk halt: {_risk_why}\""
            "\n                            logger.info(\"Autotrade %s blocked by risk_mgr: %s\", symbol, _risk_why)"
        )
        if needle in t:
            t = t.replace(needle, insert + needle, 1)
        else:
            print("autotrade_worker: could not find if can_open marker")
            return
    p.write_text(t, encoding="utf-8")
    print("autotrade_worker: OK")

def main():
    patch_config()
    patch_risk()
    patch_ai()
    patch_autotrade()
    print("Done.")
    print("Suggested .env:")
    print("  BOT_MAX_OPEN_TRADES=2")
    print("  BOT_SAFETY_MAX_OPEN=3")
    print("  BOT_SAFETY_REQUIRE_RISK=true")
    print("  BOT_AUTOTRADE=false")
    print("  AI_MAX_DAILY_TRADES=6")
    print("  AI_MAX_CONSEC_LOSSES=3")
    print("  AI_DAILY_LOSS_R_LIMIT=2.0")

if __name__ == "__main__":
    main()
