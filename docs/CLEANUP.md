# Cleanup — unnecessary files

## Remove clutter on your PC

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/cleanup_unnecessary.py
```

## What gets removed

| Category | Examples |
|----------|----------|
| Hybrid leftovers | `apply_mt5_hybrid`, `mt5_hybrid_b64_*`, `MT5_HYBRID.md` |
| One-shot restore scripts | `restore_mt5_*`, `restore_module_defaults`, `RESTORE_*.txt` |
| Changelog spam at repo root | `FEATURES_V2.md`, `FEATURE_PACK_*`, `COMPLETE.md`, … |
| Old apply helpers | `apply_upgrades.py`, `combine_ai_autotrade.py`, … |
| Obsolete docs | paper trading, native brokers (MT5 exclusive) |

## What is kept (do not delete)

- `main.py`, `autotrade_worker.py`, `ai_advanced_suite.py`
- `mt5_broker.py`, `bridge/`, `mql5/EA_Bridge.mq5`
- `anti_spam.py`, `strategy_profiles.py`, `risk_manager.py`
- `docs/EA_BRIDGE.md`, `MT5_INTEGRATION.md`, `XAU_SCALP.md`, `ACCURACY.md`, `OPERATIONS.md`

Optional: after cleanup, commit locally:

```powershell
git add -A
git commit -m "chore: remove unnecessary clutter"
git push
```
