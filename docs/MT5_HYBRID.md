# MT5 Hybrid Mode

When `MT5_BACKEND=ea_bridge` and `MT5_HYBRID=true`:

| Function | Channel |
|----------|---------|
| Buy / Sell / Close / Modify | **EA Bridge** |
| Candles / ticks (when available) | **Native** MetaTrader5 Python |
| Fallback data | EA files / other feeds |

## .env

```env
MT5_BACKEND=ea_bridge
MT5_HYBRID=true
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=...
MT5_PATH=C:\\Program Files\\MetaTrader 5\\terminal64.exe
EA_BRIDGE_DIR=
MT5_MAGIC=112233
```

## Setup

1. `pip install MetaTrader5`
2. MT5 terminal open + logged in
3. EA_Bridge on chart, AutoTrading green
4. Apply code if needed:

```powershell
git pull origin main
python scripts/apply_mt5_hybrid.py
```

5. Restart bot — look for: `Hybrid ON: native quotes/candles + EA Bridge orders`

If native fails, orders still work via EA.

Disable: `MT5_HYBRID=false`
