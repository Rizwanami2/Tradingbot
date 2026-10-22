# COMBINED AGENT VETO — setup_blocked: consensus long score=...

## Why

Multi-agent builds a **reason** like:

```text
consensus long score=28.62
```

Autotrade passed that string into CombinedBotAgent as **setup**.

CombinedFIT only allowed names like `pullback` / `sweep` → **every trade vetoed** even with strong consensus + 150 swarm votes.

## Fix

1. Accept `consensus` / `score` / multi-agent reason text as valid setup
2. Autotrade passes `setup=consensus` instead of the long reason string

## Apply

```powershell
cd C:\Tradingbot
git stash push -m "local" 2>$null
git pull origin main
python scripts/fix_setup_blocked.py
```

Restart bot. Logs should stop showing `setup_blocked: consensus long score=...`.

Trades can still be blocked by real gates (risk, max open, m1, bridge) — that is normal.
