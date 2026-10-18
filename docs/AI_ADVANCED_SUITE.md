# AI Advanced Suite

Modular filters enabled via `ai_advanced_suite.py` and `AI_*` env vars.

## Implemented (selected roadmap)

| ID | Feature |
|----|---------|
| 1 | Entry feature passthrough to learner on close |
| 2 | Gemini rationale helper |
| 3 | Regime chaos/shock veto |
| 5 | News risk filter / size cut |
| 6 | Scorecard gate (**balance mode only** by default) |
| 8 | Narrative log, mistake tags, weekly critique prompt |
| 11 | Live health checks (`AI_HEALTH_LIVE`) |
| 12 | Stale signal timeout |
| 13 | Advisory exits (partial / time-stop / news flatten) |
| 14 | Agent conflict veto + debate helper + trust decay |
| 15 | Daily-open distance metadata |
| 16 | Portfolio DD recovery sizing |
| 19 | Learn freeze flag |
| 20 | Spread vs ATR veto |
| 22 | Revenge lock, max daily trades, consec losses, FOMO |
| 24 | Structure + HTF bias sizing |
| 25 | State hooks for nightly tooling |
| 28/38 | Research left as offline flags (not live) |
| 33 | Gold session / BTC weekend specialists |
| 34 | Status / explain helpers |
| 35 | Equity milestone tighten |
| 37 | JSON schema preference, fail-closed |

## Env

See `.env.example` section `AI Advanced Suite`.

## Autotrade

`autotrade_worker` calls `evaluate_signal_dict` after AutonomousLearner advise.

## API

```python
from ai_advanced_suite import get_ai_suite, SignalContext
suite = get_ai_suite()
print(suite.status_text())
suite.set_persona("low")
suite.set_bias("long")
suite.set_learn_freeze(True)
```
