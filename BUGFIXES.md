# Bug fixes & polish (2026-10-11)

| Issue | Fix |
|-------|-----|
| `portfolio_risk.remove()` case mismatch | Symbols normalized to upper on add/remove |
| Structure trail could place stop on wrong side of entry | LONG stop must stay `< entry`; SHORT `> entry` |
| CVD crashed / weak without volume | Falls back to range proxy volume |
| `wire_main` imported removed `register_advanced_commands` | Now uses `telegram_advanced.register_all_advanced` |
| Performance JSON load broke on extra keys | `_safe_trade` filters unknown fields |
| Empty agent list on record | Guarded with `or []` |

Run after pull:
```bash
git pull origin main
python wire_main.py
python apply_upgrades.py
PYTHONPATH=. pytest tests/test_advanced_modules.py -q
```
