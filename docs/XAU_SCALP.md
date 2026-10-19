# XAUUSD scalp optimization

## Goals
- Prefer **London / NY** liquidity (`allow_asian=False`)
- Favor **liquidity + MTF + fake-breakout** over mean-reversion spam
- TP $3 / SL $2.5 so winners can cover spread better than 2.5/3.0
- **1** gold position, max **2** opens
- Longer cooldowns (60s entry / 120s after loss)

## Profile (`scalp` / `xau_scalp`)

| Param | Value |
|-------|------|
| min_score | 1.20 |
| min_rr | 1.20 |
| TP / SL USD | 3.0 / 2.5 |
| max_open | 2 |
| per symbol | 1 |
| allow_asian | False |

## .env preset

```env
BOT_PROFILE=scalp
BOT_SCALP=true
BOT_SYMBOL=XAUUSD
BOT_AUTOTRADE_SYMBOLS=XAUUSD
BOT_MAX_OPEN_TRADES=2
BOT_SCALP_TP_USD=3.0
BOT_SCALP_SL_USD=2.5
BOT_SCALP_ENTRY_COOLDOWN_SEC=60
BOT_SCALP_LOSS_COOLDOWN_SEC=120
BOT_ACCURACY_MODE=true
BOT_ACCURACY_MIN_QUALITY=0.45
MT5_BACKEND=ea_bridge
BOT_AUTOTRADE=false
BOT_NEWS=true
```

## Telegram

```text
/profile scalp
/watchlist gold
/ai on
/ai persona med
/bridge ping
/autotrade on
```

Best sessions: London open through NY morning. Avoid dead mid-Asia for gold scalps.

```powershell
git pull origin main
python scripts/apply_xau_scalp.py
```
