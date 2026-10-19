# Hybrid mode — CANCELLED

Native-quotes + EA-orders hybrid was reverted.

Use normal EA bridge:

```env
MT5_BACKEND=ea_bridge
```

Restore broker file if needed:

```powershell
python scripts/restore_mt5_original.py
```
