#!/usr/bin/env python3
"""Remove obsolete hybrid/restore/one-shot patch scripts and changelog clutter."""
from pathlib import Path

REMOVE = [
    "scripts/apply_mt5_hybrid.py",
    "scripts/restore_mt5_hybrid.py",
    "scripts/mt5_hybrid_b64_0.txt",
    "scripts/mt5_hybrid_b64_1.txt",
    "scripts/RESTORE_MT5.txt",
    "scripts/RESTORE.txt",
    "scripts/restore_mt5_original.py",
    "scripts/restore_execution_engine.py",
    "scripts/restore_module_defaults.py",
    "scripts/apply_module_risk_fixes.py",
    "scripts/combine_ai_autotrade.py",
    "scripts/apply_ai_telegram_commands.py",
    "ADVANCED_FEATURES.md",
    "AUTO_LEARN.md",
    "BACKTEST_UPGRADE.md",
    "BEST.md",
    "BUGFIXES.md",
    "COMPLETE.md",
    "DATA_FEED_FIX.md",
    "FEATURES_V2.md",
    "FEATURE_PACK_V3.md",
    "FEATURE_PACK_V4.md",
    "GROWTH_PROFILE.md",
    "UPGRADES_1_2_4_5.md",
    "WINRATE_FILTER.md",
    "docs/MT5_HYBRID.md",
    "docs/PAPER_TRADING.md",
    "docs/NATIVE_BROKERS.md",
    "docs/MODULE_RISK_FIXES.md",
    "apply_upgrades.py",
    "apply_backtest_upgrade.py",
    "apply_test_fixes.py",
    "wire_restore_backtest.py",
    "restore_production.py",
]

def main():
    n = 0
    for f in REMOVE:
        path = Path(f)
        if path.is_file():
            path.unlink()
            print("removed", f)
            n += 1
    print(f"Done. Removed {n} unnecessary files. Core bot untouched.")

if __name__ == "__main__":
    main()
