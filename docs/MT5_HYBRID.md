# MT5 Hybrid Mode — CANCELLED

Hybrid mode (native quotes + EA orders) was requested and then **removed**.

Use normal mode:

```env
MT5_BACKEND=ea_bridge
```

All execution via EA Bridge. For quotes, the bot may still use whatever data source is configured (EA ticks, feeds, etc.).

To restore the broker module if it is a stub:

```powershell
git show 575aba1aa62a9e9ae6b48d212438a956b8ae6e62:mt5_broker.py > mt5_broker.py
```
