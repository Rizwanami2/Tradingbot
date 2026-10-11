# Advanced Backtesting

Module: `advanced_backtest.py`

## Modes

| Mode | Command | Description |
|------|---------|-------------|
| Single | `--mode single` | Full metrics + MAE/MFE on EMA cross |
| Multi-agent proxy | `--mode multiagent` | Trend + BOS + RSI proxy signal |
| Walk-forward | `--mode walkforward` | Rolling IS/OOS windows |
| Monte Carlo | `--mode montecarlo` | Trade reshuffle stress test |
| Parameter sweep | `--mode sweep` | Grid over EMA/ATR params |

## Examples

```bash
# Standard run with enhanced metrics
python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode single --source demo

# Walk-forward (5 windows, 70% IS / 30% OOS)
python advanced_backtest.py --bars 4000 --mode walkforward --windows 5

# Monte Carlo 1000 paths
python advanced_backtest.py --bars 3000 --mode montecarlo --mc-runs 1000

# Parameter sweep
python advanced_backtest.py --bars 2500 --mode sweep

# Multi-agent style signal
python advanced_backtest.py --bars 3000 --mode multiagent

# Real CSV
python advanced_backtest.py --csv candles.csv --symbol BTCUSDT --mode walkforward
```

## Metrics included

- Total return, max drawdown
- Sharpe, Sortino, **Calmar**
- **SQN** (System Quality Number)
- Profit factor, win rate, avg R, expectancy
- **MAE / MFE** (max adverse / favorable excursion in R)

## Monte Carlo outputs

- Median / 5th / 95th percentile returns
- Median and 95th percentile max drawdown
- Probability of profit
- Probability of drawdown > 20%

## Walk-forward

Each window is split into in-sample (optimization observation) and out-of-sample (true test). Combined OOS equity path and metrics are reported.

## Notes

- Same-bar stop and target → stop assumed first
- Fees + slippage on entry and exit
- Synthetic/demo data is for research only; prefer real CSV or live DataFeed
- Results are estimates, not guarantees of future performance

Educational use only.
