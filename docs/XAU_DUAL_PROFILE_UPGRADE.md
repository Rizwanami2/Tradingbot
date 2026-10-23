# XAU dual profile upgrade (balanced + scalp)

## Apply (required once after pull)

```powershell
cd C:\Tradingbot
git pull origin main
python scripts\apply_dual_profile_upgrade.py
python -m py_compile combined_bot_agent.py autotrade_worker.py anti_spam.py strategy_profiles.py
```

Restart the bot. The apply script is **idempotent** (safe to re-run).

## What it upgrades
- CombinedFIT: setup=`consensus` (not reason) — stops `setup_blocked: consensus long score=...`
- Session filter default off; `get_combined_agent` singleton; spread 0 does not block
- Cooldown `max(0,...)`; `ANTISPAM_MODE=off` skips time cooldowns
- **balanced**: min_score=1.35, min_rr=1.9, killzone on, asian off, max_open=3
- **scalp / xau_scalp**: min_score~1.1–1.15, min_rr~1.15–1.20, is_scalp=True

## Env
```
BOT_PROFILE=balanced
BOT_AUTOTRADE_SYMBOLS=XAUUSD
SWARM_ENABLED=false
BOT_FAST_MODE=true
ANTISPAM_MODE=off
MT5_BACKEND=ea_bridge
```

## Telegram
`/profile balanced` or `/profile scalp` → `/bridge` → `/status` → `/cooldown 0` → `/autotrade on`
