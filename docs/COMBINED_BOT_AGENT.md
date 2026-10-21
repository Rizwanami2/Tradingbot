# CombinedBotAgent

Implements FIT trading-skill rules inside Tradingbot autotrade:

- Session (London + London–NY UTC)
- Spread vs TP budget
- Risk: max open, daily halt, consec losses
- Structure: 1m align, no FOMO mid-range
- Exit geometry TP$3 / SL$2.5 on signal levels
- Journal on close
- Live blocked unless live opt-in flag

## Install on server

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/wire_combined_agent.py
python -m py_compile combined_bot_agent.py autotrade_worker.py
```

Restart bot.

## .env

```env
COMBINED_AGENT_ENABLED=true
BOT_AUTOTRADE_LIVE_OPT_IN=false
```

Disable agent without removing code:

```env
COMBINED_AGENT_ENABLED=false
```

## Logs

```text
COMBINED AGENT VETO — session_off
COMBINED AGENT VETO — spread>0.90
```

## Note

Still run `python scripts/fix_all_bugs.py` so AI suite gate is not stubbed.
