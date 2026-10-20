# Pending scripts synced (2026-10-20)

Applied into **source** (not only scripts):

| Script / change | Result |
|-----------------|--------|
| remove_antispam_cooldowns | Cooldown getters = 0; enter checks return OK |
| apply_xau_scalp | Scalp profile XAU-optimized (TP 3 / SL 2.5, no Asian) |
| apply_accuracy_mode | ACCURACY_MODE, scorecard 60, quality min |
| module risk | Max open 2, daily loss 2%, consec 3, risk_mgr gate |
| cleanup_unnecessary | Hybrid/restore/changelog clutter removed |

## Pull on server

```powershell
cd C:\Tradingbot
git pull origin main
# restart bot
```

## .env

See `.env.example` (XAU scalp + accuracy + safety).
