# More accuracy (fewer, better setups)

## What changed

| Lever | Before | After (accuracy mode) |
|-------|--------|------------------------|
| Scalp min_score | 1.0 | **1.25** |
| Scalp min_rr | 1.0 | **1.25** |
| Signal quality min | none | **0.45** composite |
| AI scorecard min | 55 | **60** (balance mode) |
| Scalp min effective RR | 0.85 | **1.15** if accuracy on |

## .env

```env
BOT_ACCURACY_MODE=true
BOT_ACCURACY_MIN_QUALITY=0.45
AI_MIN_SCORECARD=60
BOT_PROFILE=scalp
```

Stricter (even fewer trades):

```env
BOT_ACCURACY_MIN_QUALITY=0.55
AI_MIN_SCORECARD=65
```

Looser (more trades, less accuracy):

```env
BOT_ACCURACY_MODE=false
BOT_ACCURACY_MIN_QUALITY=0.30
```

## Expected effect

- **Fewer** Telegram alerts / setups
- Higher average quality of those that pass
- Not a guarantee of profit — spread and session still matter

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/apply_accuracy_mode.py
# restart bot
```
