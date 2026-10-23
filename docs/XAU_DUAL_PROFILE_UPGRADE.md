# XAU dual profile upgrade (balanced + scalp)

## Shared fixes
- CombinedFIT: setup=consensus (not reason) — stops setup_blocked on consensus score text
- Session filter default off; singleton get_combined_agent
- Cooldown max(0); ANTISPAM_MODE=off skips time cooldowns

## Balanced
min_score=1.35, min_rr=1.9, require_killzone=True, allow_asian=False, strict_htf=True, max_open=3, is_scalp=False

## Scalp / xau_scalp
min_score~1.1–1.15, min_rr~1.15–1.20, is_scalp=True

## Env
BOT_PROFILE=balanced|scalp
BOT_AUTOTRADE_SYMBOLS=XAUUSD
SWARM_ENABLED=false
BOT_FAST_MODE=true
ANTISPAM_MODE=off
MT5_BACKEND=ea_bridge

## Telegram
/profile balanced|scalp → /bridge → /status → /cooldown 0 → /autotrade on

## Apply worker one-liner if needed
```powershell
python scripts/apply_dual_profile_upgrade.py
```
