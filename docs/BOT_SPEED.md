# Bot response too slow

## Main causes

1. **Menu sync** deletes commands on every scope/language then re-sets (many Telegram API calls)
2. **100+ agent swarm** on every signal
3. **MT5 live chart** attached inside every `Arbiter.decide`
4. Heavy help/status text

## Fast mode (.env)

```env
BOT_FAST_MODE=true
SWARM_ENABLED=false
TELEGRAM_MENU_PURGE=false
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
ANTISPAM_MODE=off
```

| Flag | Effect |
|------|--------|
| `BOT_FAST_MODE=true` | Skip chart in decide; lighter path |
| `SWARM_ENABLED=false` | Core multi-agents only (~18), not 100+ |
| `TELEGRAM_MENU_PURGE=false` | `/updatemenu` only sets commands, no mass delete |

## Apply

```powershell
cd C:\Tradingbot
git stash push -m "local" 
git pull origin main
python scripts/fix_bot_speed.py
```

Add the env lines above to `.env`, restart bot.

## Telegram

- Prefer `/status` over huge `/help`
- `/signal` is slower than `/status` (runs agents)
- After menu works once, avoid spam `/updatemenu`

## Expect

| Mode | Typical reply |
|------|----------------|
| Fast (no swarm, no chart) | 1–3s |
| Full swarm + chart | 5–20s+ |
