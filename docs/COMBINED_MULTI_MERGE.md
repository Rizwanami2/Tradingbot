# Combined agents merged with multi-agents

## How it works now

```text
Multi-agent votes (trend, liq, AI, news, Gautam, swarm, ...)
        ↓
   Arbiter consensus → signal
        ↓
   CombinedFIT gate (session optional, spread, risk, structure)
        ↓
   Vote tag: CombinedFIT + sig["combined_merged"]=True
        ↓
   autotrade_worker (skips second FIT if already merged)
        ↓
   EA Bridge order
```

## Status messages

- Success: `... | CombinedFIT OK`
- Blocked: `Combined+Multi FIT veto: ...`

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/merge_combined_multi.py
python -m py_compile multi_agent_trading_bot.py autotrade_worker.py combined_bot_agent.py
```

Restart bot.

## .env

```env
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
```

## Telegram

`/combined` — shared metrics  
`/signal` — consensus includes CombinedFIT when merged  
`/autotrade on` — uses merged path  
