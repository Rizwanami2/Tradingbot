# MT5 Hybrid — fresh features

## Channels

| Function | Channel |
|----------|---------|
| Orders (buy/sell/close/modify) | **EA Bridge** |
| Quotes / candles | **Native** MetaTrader5 (preferred) |
| Fallback quotes | EA ticks/rates → other feeds |

## Enable

```env
MT5_BACKEND=ea_bridge
# or: MT5_BACKEND=hybrid
MT5_HYBRID=true
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=...
MT5_PATH=C:\\Program Files\\MetaTrader 5\\terminal64.exe
EA_BRIDGE_DIR=
MT5_MAGIC=112233
```

```powershell
pip install MetaTrader5
git pull origin main
python scripts/apply_mt5_hybrid.py
# restart bot
```

Log lines:
- `Hybrid ON: native quotes + EA Bridge orders`
- `EA ORDERS connected (... hybrid=True, quotes=native)`

## Fresh features

1. **`MT5_BACKEND=hybrid`** alias (= ea_bridge + hybrid on)
2. **Native init** when EA connects (login optional)
3. **Market Watch preselect** XAUUSD / BTCUSD
4. **`broker.hybrid_status()`** → ea_ok, native_ok, quotes_channel
5. **Graceful fallback** if native fails — orders still via EA
6. **Native quote path** not blocked when EA is primary backend

## Disable

```env
MT5_HYBRID=false
```

## Checklist

- [ ] `pip install MetaTrader5`
- [ ] MT5 open + logged in
- [ ] EA_Bridge on chart, Algo Trading green
- [ ] Magic matches `.env`
- [ ] `/bridge ping` OK
