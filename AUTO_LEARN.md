# Autonomous Self-Learning (no commands required)

## What it does

1. **On startup** — loads up to **~5 years** of market data (yfinance / CCXT / synthetic fallback).
2. **Replays** a trend strategy across that history.
3. **Mines flaws** — bad setups, hours, weekdays, regimes.
4. **Self-corrects** — bans weak patterns, tightens score/R:R/conviction, adjusts size by regime, reweights agents.
5. **Live** — every closed trade updates the same brain; after 3 losses on one setup → auto-ban.
6. **Every signal** — `advise_signal()` can veto or shrink size using learned rules. **No Telegram command needed.**

## Files

| File | Role |
|------|------|
| `autonomous_learner.py` | Core brain |
| `autonomous_memory.json` | Created at runtime — knowledge + trade log |
| `wire_autonomous.py` | One-shot patch into `main.py` |

## Wire once

```bash
git pull origin main
python wire_autonomous.py
# restart bot / Railway
```

Or in code:

```python
from autonomous_learner import auto_start, get_brain

brain = auto_start()  # bootstrap if memory older than 24h

# before taking a trade
ok, reason, size_mult = brain.advise_signal(
    score=sig_score, rr=rr, agreement=n_agents,
    conviction=conv, regime=regime, setup=setup_tag,
)
if not ok:
    # skip trade — brain already corrected itself
    ...
else:
    lots *= size_mult

# after close
brain.on_trade_closed(r_multiple=r, won=won, setup=setup_tag, regime=regime, agents=[...])
```

## Telegram Controls & On/Off Toggle

While learning runs automatically, you can control signal vetoing anytime:

- `/learner on` or `/veto on` — Activate learned signal filtering & vetoing
- `/learner off` or `/veto off` — Pause signal filtering (all trades execute unblocked)
- `/learner unban <hour>` — Unban a specific UTC hour (e.g. `/learner unban 14`)
- `/learner clearhours` — Unban all hours
- `/learner clearbans` — Clear all hour, weekday, and setup bans
- `/learner rules` — View active thresholds, bans, and agent weights
- `/learner flaws` — View detected statistical flaws & sanctions
- `/autotrade veto on|off` — Toggle veto filtering directly via Autotrade
- `/brain [status|on|off|unban|rules|relearn|reset]` — Complete brain console

Interactive buttons (`[🔴 Turn Veto OFF]`, `[🔓 Unban Hour XX]`) are also attached directly to trade block notifications and the watchlist dashboard.
