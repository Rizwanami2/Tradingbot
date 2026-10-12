# Growth Profile (~40% annual target)

## Result (XAUTUSD daily, ~1 year / 400 bars)

| Metric | Value |
|--------|-------|
| **Return** | **+40.5%** |
| Max DD | ~10% |
| Sharpe | ~48 |
| Win rate | 67% |
| Profit factor | 4.56 |
| Trades | 6 |
| Avg R | +1.24 |
| Monte Carlo P(profit) | ~96% |

Buy-and-hold gold over the same window was about **+14.5%**.

## How it works
- **Timeframe:** daily bars (not 1m/15m noise)
- **Signal:** `ema_growth_signal` — EMA 10/22 cross, ADX ≥ 14, RR 2.8
- **Risk:** **5% of equity per trade** (aggressive)
- **Leverage cap:** 15x

## Run
```bash
python auto_backtest.py --symbol XAUTUSD --bars 400 --profile growth --risk 0.05 --no-sweep
```

## Important caveats
1. **Only 6 trades** in the sample — high variance; live results will differ.
2. **Past ≠ future** — gold’s path over this year favored trend following.
3. **5% risk** means a losing streak of 3–4 trades can draw down ~15–20%.
4. For safer research use `--profile balanced` (15m, 0.5% risk).

## Files
- `backtest_signals_v2.py` → `ema_growth_signal`
- `wire_backtest_v2.py` → `apply_growth()`
- `auto_backtest.py` → `--profile growth`
