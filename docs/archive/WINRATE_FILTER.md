# High-Conviction Filter ("max win-rate" mode)

**Important:** No filter can produce a true 100% win rate in live markets. This module only allows trades when many independent conditions agree, so the bot trades less and aims for a higher hit rate.

## Enable

Already wired into `AdvancedEngine` when `conviction_mode=True` (default on for profile `gold_only` / strict profiles, or set explicitly):

```python
from advanced_integration import AdvancedEngine
eng = AdvancedEngine(symbol="XAUUSD", profile="swing", conviction_mode=True)
print(eng.evaluate())
```

Telegram:
```
/asignal XAUUSD swing
```
(Uses engine defaults; conviction gate applied when enabled.)

## Checklist (STRICT)

1. ≥4 agents same direction with avg strength ≥ 0.55–0.60  
2. Weighted score ≥ 3.2–3.5  
3. HTF bias aligned  
4. Tradeable session (killzone preferred)  
5. SMC vote same direction  
6. CVD not opposing  
7. R:R ≥ 2.0–2.2  
8. Stop distance within ATR bounds  

All must pass in strict mode.

## Presets

| Preset | Behavior |
|--------|----------|
| `STRICT_100_STYLE` | Maximum selectivity |
| `BALANCED_QUALITY` | Soft mode, 5/8 checks |

Educational only. Past selectivity ≠ future 100% wins.
