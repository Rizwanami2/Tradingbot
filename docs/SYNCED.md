# Sync all pending scripts

## One command (server)

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/sync_all_pending.py
```

Then restart the bot.

## What it applies

| Patch | Effect |
|-------|--------|
| Anti-spam | Cooldowns = 0, no CD/proximity blocks |
| XAU scalp | Gold-optimized profile (TP 3 / SL 2.5) |
| Accuracy | Quality min + scorecard 60 |
| Risk | Max open 2, daily 2%, consec 3, risk_mgr gate |
| Config | SAFETY_* + ACCURACY_MODE |

## Skip / do not run

- Hybrid scripts (cancelled)
- Old restore_mt5_* unless mt5_broker is broken

## After sync

```text
/profile scalp
/watchlist gold
/bridge ping
/ai on
/autotrade on
```
