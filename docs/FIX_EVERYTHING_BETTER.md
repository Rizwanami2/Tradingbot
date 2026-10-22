# Fix everything for better (stability pass)

## What was wrong

| Issue | Effect |
|-------|--------|
| CombinedFIT session London-only | Most hours → no trades (`session_off`) |
| `/cooldown` min 5s | Could not clear cooldowns |
| ANTISPAM_MODE=off ignored time CD | Still blocked entries |
| Combined vs multi separate | Double gates / empty `/combined` metrics |
| Swarm silent | Hard to see if 100 agents ran |

## What this pass does

1. **Session filter OFF by default** (`COMBINED_AGENT_SESSION_FILTER=false`)
2. **Cooldown can be 0**; mode `off` skips time cooldowns
3. **CombinedFIT merged into Arbiter** after multi-agent + swarm votes
4. **Shared singleton** for autotrade + `/combined`
5. **Swarm vote count** logged each cycle
6. Autotrade **skips second FIT** if Arbiter already merged

## Pipeline

```text
Core multi-agents + agent_swarm
        ↓
   Arbiter consensus
        ↓
   CombinedFIT gate
        ↓
   autotrade → EA Bridge
```

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/fix_everything_better.py
python -m py_compile combined_bot_agent.py anti_spam.py multi_agent_trading_bot.py autotrade_worker.py
```

Restart bot.

## .env

```env
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
ANTISPAM_MODE=off
BOT_AUTOTRADE=false
BOT_PROFILE=scalp
BOT_AUTOTRADE_SYMBOLS=XAUUSD
```

## Telegram check

```text
/bridge ping
/cooldown 0
/combined
/signal
/status
/autotrade on
```

Look for: `Swarm votes for XAUUSD: N` and `CombinedFIT OK` in logs/status.
