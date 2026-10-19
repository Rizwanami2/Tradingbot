# MT5 Hybrid Mode

When `MT5_BACKEND=ea_bridge`:

| Function | Channel |
|----------|---------|
| Buy / Sell / Close / Modify | **EA Bridge** (`EA_Bridge.mq5`) |
| Candles / ticks / marks | **Native** `MetaTrader5` Python package (when available) |
| Fallback data | EA rates/ticks, then last mark |

## Setup

```env
MT5_BACKEND=ea_bridge
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=...
MT5_PATH=C:\\Program Files\\MetaTrader 5\\terminal64.exe
EA_BRIDGE_DIR=
```

1. `pip install MetaTrader5`
2. MT5 terminal open and logged in
3. EA_Bridge on chart, AutoTrading green
4. Restart bot — look for: `Hybrid ON: native quotes/candles + EA Bridge orders`

If native init fails, **orders still work via EA**; quotes fall back to EA files.

## Apply on existing clone

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/apply_mt5_hybrid.py
# or pull if mt5_broker.py already includes Hybrid ON
```
