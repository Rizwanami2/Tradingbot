# Feature Pack v3 — All Suggested Upgrades

Implemented and pushed to `main` on 2026-10-11.

## Modules added

| File | What it does |
|------|----------------|
| `trade_journal.py` | Persistent trade log + **auto AI-Learner.learn() on close** + agent stats feed |
| `position_manager.py` | Time-stop, max hold, **scale-out at 1R/2R**, BE move, MFE-fade tighten, MAE/MFE tracking |
| `risk_tiers.py` | Multi-tier daily/weekly loss circuits + **max concurrent open R** (GREEN→YELLOW→ORANGE→RED) |
| `agent_stats.py` | Per-agent win-rate / expectancy → **suggested Arbiter weight adaptation** |
| `state_persistence.py` | Equity + open positions snapshot → survives Railway restart |
| `feature_pack.py` | One import that wires everything together |

## Quick usage

```python
from feature_pack import FeaturePack

pack = FeaturePack(equity=10000, paper_mode=True)

# Before any new signal
allowed, reason, size_mult = pack.gate_new_entry()
if not allowed:
    print("Blocked:", reason)
else:
    # size *= size_mult
    tid = pack.on_open(
        symbol="XAUUSD", direction="LONG",
        entry=2650, stop=2640, tp1=2670,
        size=0.1, risk_amount=50,
        agents=["Trend", "Liquidity", "AI-LEARNER"],
        session="London", score=4.1, conviction=0.78,
    )

# Every bar / poll
actions = pack.on_bar(tid, price=2662)
if actions["close"]:
    pack.on_close(tid, exit_price=2662)
elif actions["partial"]:
    print("Partial exit", actions["partial"], actions["reason"])
elif actions["new_stop"]:
    print("Move stop to", actions["new_stop"])

# Status board (Telegram /board)
print(pack.board())
```

## Telegram commands (after wire)

- `/board` — full status board (equity, risk tier, open trades, journal, agents)
- `/journal` — recent closed trades + expectancy
- `/agents` — per-agent performance
- Existing: `/asignal` `/abacktest` `/kill` `/performance` `/profile`

## Wiring into live loop

1. On signal accepted → `pack.on_open(...)`
2. On every monitoring tick → `pack.on_bar(tid, price)` and honour close/partial/stop actions
3. On final close → `pack.on_close(...)` (auto-learns)
4. Before new entry → `pack.gate_new_entry()` and multiply size by returned multiplier

## What this covers from the suggestion lists

- Trade journal + learn-on-close
- Multi-tier daily/weekly loss circuit
- Max concurrent risk in R
- Time-based exit / max hold
- Scale-in/out (scale-out at 1R & 2R + BE)
- MAE/MFE live tracking + fade exit logic
- Agent performance dashboard + weight adaptation
- Status board
- Equity / position state persistence (Railway safe)
- Paper mode flag in state store

## Notes

- Soft risk tiers sit **in front of** the hard KillSwitch.
- Scale-out percentages and max-hold bars are configurable in `PositionManager`.
- Agent weight adaptation only kicks in after ≥12 samples per agent.
"
