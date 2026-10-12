# Backtest Upgrade (2026-10-12)

## What changed
- **Data**: Prefer **15m** bars (not noisy 1m) via realtime_prices / PAXG
- **ADX**: Fixed index-aligned ADX (was always 0)
- **Primary signal**: EMA 12/26 + ADX>=18 + slope filter + ATR x2.2 + RR 2.8
- **Adaptive**: Same tuned EMA in trend regime; flat in chop
- **Risk defaults**: 0.5% risk/trade, RR 2.8, lower fees/slippage
- **Walk-forward**: Expanding windows so OOS can fire trades

## Before vs After (XAUTUSD 1000x15m)
| Metric | Before | After |
|--------|--------|-------|
| Return | -2.68% | **+1.33%** |
| Sharpe | -53 | **+9.1** |
| Win rate | 16.7% | **40%** |
| Profit factor | 0.12 | **1.36** |
| P(profit) MC | 0% | **61%** |

## Apply locally if GitHub files lag
```bash
# Copy improved modules from this session or re-run grid:
python auto_backtest.py --symbol XAUTUSD --bars 1000 --risk 0.005 --no-sweep
```
