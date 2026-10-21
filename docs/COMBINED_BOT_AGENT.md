# CombinedBotAgent & Unified Architecture

The Combined Bot & Agent system implements institutional trading rules (FIT trading skills) and unifies the execution pipeline inside Nimble Darwin.

It is accessible both as the standalone module [`combined_bot_agent.py`](file:///workspace/nimble-darwin/combined_bot_agent.py) and via the master runner [`artifacts/combined_bot_agent.py`](file:///workspace/nimble-darwin/artifacts/combined_bot_agent.py).

## FIT Skills Implemented
- **Session Filter**: London (07:00–11:00 UTC) + London–NY Overlap (13:00–17:00 UTC)
- **Spread vs TP budget**: Rejects when spread consumes >30% of target
- **Risk Protection**: Max concurrent trades, daily halt, consecutive loss limits
- **Single-Direction & Cooldown**: No opposing positions (simultaneous BUY & SELL) on the same symbol, active cooldowns for auto & manual
- **Structure Validation**: 1m trend alignment, no FOMO mid-range entries
- **Exit Geometry**: TP $3.00 / SL $2.50 on signal levels with auto break-even
- **Journal & Audit**: Records trade details and outcomes on close

## Environment Variables (.env)

```env
COMBINED_AGENT_ENABLED=true
BOT_AUTOTRADE_LIVE_OPT_IN=false
```

Disable agent without removing code:
```env
COMBINED_AGENT_ENABLED=false
```

## Production Runner Commands

```bash
# Run full combined autonomous system (24/7)
python artifacts/combined_bot_agent.py

# Run comprehensive diagnostic check across all subsystems
python artifacts/combined_bot_agent.py --check

# Run single-pass consensus & risk evaluation across watchlist
python artifacts/combined_bot_agent.py --once

# Run single-pass dry run simulation (no live broker execution)
python artifacts/combined_bot_agent.py --once --dry-run

# Run with custom watchlist
python artifacts/combined_bot_agent.py --symbols XAUUSD,BTCUSDT

# Explicit headless or telegram modes
python artifacts/combined_bot_agent.py --headless
python artifacts/combined_bot_agent.py --telegram
```

## Logs & Audit

```text
COMBINED AGENT VETO — session_off
COMBINED AGENT VETO — spread>0.90
COMBINED AGENT VETO — Opposing direction: active BUY position exists on GOLD
```
