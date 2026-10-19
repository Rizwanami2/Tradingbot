#!/usr/bin/env python3
"""Undo module risk patches — restore previous defaults."""
from pathlib import Path

def restore_config():
    p = Path("config.py")
    t = p.read_text(encoding="utf-8")
    # Remove safety constants if present
    lines = []
    for line in t.splitlines(True):
        if "SAFETY_MAX_OPEN_TRADES" in line or "SAFETY_REQUIRE_RISK_CAN_TRADE" in line:
            continue
        lines.append(line)
    t = "".join(lines)
    t = t.replace(
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 2)  # safety default',
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 6)',
    )
    t = t.replace(
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 2)',
        'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 6)',
    )
    t = t.replace(
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 1)',
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 3)',
    )
    p.write_text(t, encoding="utf-8")
    print("config: restored")

def restore_risk():
    p = Path("risk_manager.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace("max_daily_loss_pct: float = 2.0  # tightened from 3.0", "max_daily_loss_pct: float = 3.0")
    t = t.replace("max_daily_loss_pct: float = 2.0", "max_daily_loss_pct: float = 3.0")
    t = t.replace("max_consecutive_losses: int = 3  # tightened from 4", "max_consecutive_losses: int = 4")
    t = t.replace("max_consecutive_losses: int = 3", "max_consecutive_losses: int = 4")
    t = t.replace("base_risk_pct: float = 0.15  # tightened from 0.20", "base_risk_pct: float = 0.20")
    t = t.replace("base_risk_pct: float = 0.15", "base_risk_pct: float = 0.20")
    p.write_text(t, encoding="utf-8")
    print("risk_manager: restored")

def restore_ai():
    p = Path("ai_advanced_suite.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace('_env_int("AI_MAX_DAILY_TRADES", 6)', '_env_int("AI_MAX_DAILY_TRADES", 12)')
    t = t.replace('_env_int("AI_MAX_CONSEC_LOSSES", 3)', '_env_int("AI_MAX_CONSEC_LOSSES", 4)')
    p.write_text(t, encoding="utf-8")
    print("ai_advanced_suite: restored")

def restore_autotrade():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    # Remove risk halt block
    start = t.find("                    # Safety: risk_manager daily loss")
    if start >= 0:
        end = t.find("\n                    if can_open:", start)
        if end > start:
            t = t[:start] + t[end + 1:]  # keep newline before if can_open via end
            # fix: end points to newline before if can_open
            t = t[:start] + t[end:]
            print("autotrade: removed risk halt block")
    # Remove _safety_cap blocks (simple line filter)
    out = []
    skip_next_related = False
    for line in t.splitlines(True):
        if "_safety_cap" in line or "max_open = max(1, min(int(max_open)" in line:
            continue
        if "Risk halt" in line or "_ok_risk" in line or "_risk_why" in line:
            continue
        if "SAFETY_REQUIRE_RISK_CAN_TRADE" in line:
            continue
        if "blocked by risk_mgr" in line:
            continue
        if "risk_mgr.can_trade error" in line:
            continue
        out.append(line)
    t = "".join(out)
    t = t.replace(
        'getattr(config, "MAX_OPEN_TRADES", 2)',
        'getattr(config, "MAX_OPEN_TRADES", 6)',
    )
    p.write_text(t, encoding="utf-8")
    print("autotrade_worker: restored")

def main():
    restore_config()
    restore_risk()
    restore_ai()
    restore_autotrade()
    print("Done — defaults restored (max open 6, daily 3%, AI daily 12, no extra risk gate).")

if __name__ == "__main__":
    main()
