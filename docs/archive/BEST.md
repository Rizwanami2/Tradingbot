# Best-Tier Upgrade Pack

## Activate everything

```bash
git pull origin main
pip install -r requirements.txt

# Register advanced Telegram commands (once)
python -c "
from pathlib import Path
p = Path('main.py')
t = p.read_text()
if 'telegram_advanced' not in t:
    t = t.replace(
        'app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))',
        '''try:\n        from telegram_advanced import register_all_advanced\n        register_all_advanced(app)\n    except Exception as e:\n        print('Advanced cmds:', e)\n    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))'''
    )
    p.write_text(t)
    print('Patched main.py')
else:
    print('Already patched')
"

# Tests
PYTHONPATH=. pytest tests/test_advanced_modules.py -v

# Advanced backtest
python advanced_backtest.py --mode walkforward --bars 3000
python advanced_backtest.py --mode montecarlo --mc-runs 500
```

## New Telegram commands

| Command | Purpose |
|---------|---------|
| `/asignal [SYM] [profile]` | Full AdvancedEngine (kill, session, HTF, SMC, CVD, vol, portfolio) |
| `/abacktest [bars] [mode] [SYM]` | single / walkforward / montecarlo / multiagent / sweep |
| `/performance` | Expectancy & agent stats |
| `/kill on\|off` | Kill switch |
| `/profile [name]` | Strategy profile info |

Examples:
```
/asignal XAUUSD gold_only
/abacktest 2000 walkforward XAUUSD
/abacktest 2000 montecarlo
/abacktest 1500 multiagent BTCUSDT
```

## Structure trailing

```python
from execution_enhancements import apply_structure_trail
event = apply_structure_trail(position, df_window, atr_value=2.0)
```

Call after TP1 inside `execution_engine.update_market_candle`.

## Safety defaults (best practice)

- Instrument leverage caps enforced
- Kill switch on DD / loss streak
- HTF alignment required on strict profiles
- Vol shrinks size in high ATR
- Portfolio sector limits
- Live exchange still fail-closed until SL/TP verification exists

Educational use only.
