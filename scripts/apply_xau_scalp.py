#!/usr/bin/env python3
"""Patch scalp profile for XAUUSD optimization."""
from pathlib import Path

BLOCK = '''    "scalp": StrategyProfile(
        name="scalp",
        description="XAUUSD-optimized micro-scalp: liquidity+MTF focus, London/NY friendly, tight risk",
        weights={
            "TREND": 1.25,
            "LIQUIDITY": 1.55,
            "INDICATORS": 1.25,
            "AI-LEARNER": 1.05,
            "NEWS": 0.85,
            "GAUTAM-JHA": 1.40,
            "MEAN-REVERSION": 1.15,
            "FAKE-BREAKOUT": 1.45,
            "MTF-CONFLUENCE": 1.50,
            "ASIAN-BREAKOUT": 0.90,
            "MT5-CHART": 1.40,
        },
        min_agreement=2,
        min_score=1.20,
        min_rr=1.20,
        require_killzone=False,
        allow_asian=False,
        base_risk_pct=0.15,
        max_leverage=15.0,
        strict_htf=False,
        is_scalp=True,
        fast_market_fill=True,
        scalp_tp_pct=0.08,
        scalp_sl_pct=0.10,
        scalp_tp_usd=3.00,
        scalp_sl_usd=2.50,
        poll_interval=2,
        fast_poll_interval=1,
        max_open_trades=2,
        scalp_trail_enabled=True,
        scalp_trail_activation_usd=1.20,
        scalp_trail_distance_usd=0.70,
        scalp_trail_activation_pct=0.04,
        scalp_trail_distance_pct=0.025,
        scalp_be_lock_usd=0.60,
        scalp_min_trail_profit_usd=1.20,
        scalp_profit_retention_pct=0.55,
        scalp_let_profit_run=True,
        scalp_entry_cooldown_sec=60.0,
        scalp_loss_cooldown_sec=120.0,
        scalp_global_cooldown_sec=15.0,
        scalp_max_positions_per_symbol=1,
        scalp_min_price_distance_pct=0.10,
    ),
'''

def main():
    p = Path("strategy_profiles.py")
    t = p.read_text(encoding="utf-8")
    if "XAUUSD-optimized micro-scalp" in t:
        print("Already optimized")
        return
    i = t.find('"scalp": StrategyProfile')
    j = t.find('"swing": StrategyProfile')
    if i < 0 or j < 0:
        raise SystemExit("markers not found")
    i = t.rfind("\n", 0, i) + 1
    t = t[:i] + BLOCK + t[j:]
    p.write_text(t, encoding="utf-8")
    print("scalp profile -> XAUUSD optimized")
    cpath = Path("config.py")
    c = cpath.read_text(encoding="utf-8")
    c = c.replace(
        'SCALP_TAKE_PROFIT_USD = _safe_float(os.getenv("BOT_SCALP_TP_USD"), 2.5)',
        'SCALP_TAKE_PROFIT_USD = _safe_float(os.getenv("BOT_SCALP_TP_USD"), 3.0)',
    )
    c = c.replace(
        'SCALP_STOP_LOSS_USD = _safe_float(os.getenv("BOT_SCALP_SL_USD"), 3.0)',
        'SCALP_STOP_LOSS_USD = _safe_float(os.getenv("BOT_SCALP_SL_USD"), 2.5)',
    )
    cpath.write_text(c, encoding="utf-8")
    print("config TP/SL defaults 3.0 / 2.5")
    print("Use: BOT_AUTOTRADE_SYMBOLS=XAUUSD and /watchlist gold")

if __name__ == "__main__":
    main()
