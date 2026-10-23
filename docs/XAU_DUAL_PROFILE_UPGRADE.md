# XAU dual profile upgrade (balanced + scalp)

## Apply on your machine

```powershell
cd C:\Tradingbot
git pull origin main
python scripts\apply_dual_profile_upgrade.py
python -m py_compile combined_bot_agent.py autotrade_worker.py anti_spam.py strategy_profiles.py
```

Restart the bot.

## Shared fixes (script applies these)
- CombinedFIT: `setup=consensus` (not reason) — stops `setup_blocked: consensus long score=...`
- Session filter default off; `get_combined_agent` singleton
- Cooldown `max(0,...)`; `ANTISPAM_MODE=off` skips time cooldowns

## Balanced
min_score=1.35, min_rr=1.9, require_killzone=True, allow_asian=False, strict_htf=True, max_open=3, is_scalp=False

## Scalp / xau_scalp
min_score~1.1–1.15, min_rr~1.15–1.20, is_scalp=True

## Env
```
BOT_PROFILE=balanced
# or BOT_PROFILE=scalp
BOT_AUTOTRADE_SYMBOLS=XAUUSD
SWARM_ENABLED=false
BOT_FAST_MODE=true
ANTISPAM_MODE=off
MT5_BACKEND=ea_bridge
```

## Telegram
`/profile balanced` or `/profile scalp` → `/bridge` → `/status` → `/cooldown 0` → `/autotrade on`
