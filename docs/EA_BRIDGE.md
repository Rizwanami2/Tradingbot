# EA Bridge (Python ↔ MetaTrader 5)

File-based bridge so the Python Tradingbot can execute orders through an MT5 Expert Advisor.

## Files

| Path | Role |
|------|------|
| `bridge/ea_client.py` | Python client (writes commands, reads responses) |
| `mql5/EA_Bridge.mq5` | Expert Advisor (reads commands, calls `OrderSend`) |

## Setup

1. Copy `mql5/EA_Bridge.mq5` into MetaEditor → compile (F7).
2. Attach **EA_Bridge** to a chart (e.g. BTCUSD).
3. Enable **AutoTrading** (green).
4. From the project root:

```powershell
python bridge/ea_client.py
python -c "from bridge.ea_client import buy, close_all; print(buy('BTCUSD', 0.01)); print(close_all())"
```

Commands use MT5 **Common\\Files**:

`%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\ea_commands.txt`

## API

- `status()` — EA / account ping
- `buy(symbol, volume, sl=0, tp=0)`
- `sell(symbol, volume, sl=0, tp=0)`
- `close_all(magic=112233)`

Volume is rounded inside the EA to symbol min/step. Filling mode is auto-detected (IOC/FOK/RETURN).
