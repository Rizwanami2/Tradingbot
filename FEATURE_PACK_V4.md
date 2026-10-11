# Feature Pack v4 — Suggestions 47–70

Pushed to `main` 2026-10-11.

## Modules

| File | Covers |
|------|--------|
| `signal_quality.py` | 47–51 liquidity/imbalance, volume node, spread/slippage veto, RS rank, candle quality |
| `risk_advanced.py` | 52–56 pyramid gate, correlation block, equity-curve sizing, overnight/weekend cut, max daily trades |
| `learning_edge.py` | 57–61 setup tagging, time-of-day map, negative ban-list, calibration, agent veto helper |
| `ops_advanced.py` | 62–66 daily digest, health self-heal, data-feed fallback order, trade replay, CSV export |
| `research_helpers.py` | 67–70 A/B paper books, stress scenarios, symbol rotation, param-lock suggestion |
| `feature_pack_v4.py` | Unified `full_gate` + lifecycle wiring with v3 |
| `telegram_feature_pack_v4.py` | `/digest /replay /export /setups /abooks /quality /heal` |

## Quick use

```python
from feature_pack_v4 import FeaturePackV4
pack = FeaturePackV4(equity=10000)

ok, reasons, size_mult = pack.full_gate("XAUUSD", "LONG", df=df1, spread=0.25)
if ok:
    tid = pack.on_open(symbol="XAUUSD", direction="LONG", entry=..., stop=..., tp1=..., size=0.1*size_mult, risk_amount=50)
# on close
pack.on_close(tid, exit_price=..., conf=0.7, signal_meta={"regime": "trend", "session": "London"})
print(pack.digest())
```

## Telegram

```
/digest
/replay <id>
/export
/setups
/abooks
/quality [SYMBOL]
/heal [reset]
```

After pull:

```bash
git pull origin main
# ensure wire registers v4 — or add to main:
# from telegram_feature_pack_v4 import register_feature_pack_v4
# register_feature_pack_v4(app)
```

## Notes

- Spread veto needs a live spread value from your broker/feed (pass into `full_gate`).
- A/B books record outcomes for research; branch at entry time in live if you want true isolation.
- Ban-list auto-clears a pattern after a win.
- Health self-heal is a counter — wire `record_error` / `record_ok` in your monitoring worker.
"
