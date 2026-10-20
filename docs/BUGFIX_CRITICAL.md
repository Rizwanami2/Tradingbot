# Critical bug check & fix

## Bugs found on main

| Bug | Impact |
|-----|--------|
| AI gate stub `return True, sig` | Suite never vetoed bad trades |
| No Telegram on learn close | No live analysis visibility |
| Risk/AI gates not in entry path | Oversized risk when filters off |
| Learn size-only (no veto) | Banned setups could still open |
| Cooldowns still active in anti_spam | Unexpected blocks (optional zero) |

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/fix_all_bugs.py
# restart bot
```

## After fix expect

- Logs: `AI VETO`, `RISK VETO`, `LEARN VETO`
- Telegram: `Learn+Close`, veto alerts
- File: `data/live_trade_journal.csv`

## .env

```env
AI_ADVANCED_ENABLED=true
BOT_SAFETY_REQUIRE_RISK=true
BOT_LEARN_LIVE_VETO=true
ANTISPAM_MODE=off
```
