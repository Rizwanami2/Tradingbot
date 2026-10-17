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

- `status(magic=112233)` — EA / account ping & open position telemetry
- `buy(symbol, volume, sl=0, tp=0, magic=112233)` — Execute BUY deal
- `sell(symbol, volume, sl=0, tp=0, magic=112233)` — Execute SELL deal
- `close(symbol="", ticket=0, magic=112233)` — Close specific position or symbol
- `close_all(magic=112233)` — Close all positions with matching magic number
- `modify(symbol="", ticket=0, sl=0, tp=0, magic=112233)` — Dynamic SL/TP trailing adjustment
- `set_bridge_dir(path)` — Dynamic custom MT5 common files path override

Volume is rounded inside the EA to symbol min/step. Filling mode is auto-detected (IOC/FOK/RETURN).

## Integration with MT5Broker

The EA Bridge is directly integrated as a selectable backend for `MT5Broker`:

```ini
# .env
BOT_BROKER_MODE=mt5_live
MT5_BACKEND=ea_bridge
```

Or programmatically:
```python
from mt5_broker import MT5Broker

broker = MT5Broker(mode="live", backend="ea_bridge")
order = broker.place_order("XAUUSD", "buy", 0.05, stop_loss=2640.0, take_profit=2680.0)
broker.modify_position("XAUUSD", stop_loss=2645.0, ticket=order.id)
broker.close_position("XAUUSD", ticket=order.id)
```
