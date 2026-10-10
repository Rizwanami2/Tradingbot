# Strategy Research Toolkit (Phase 3)

This phase adds research utilities only; it does not connect strategy scores to order execution.

## Multi-timeframe analysis

`analyze_timeframes(frames, fast_ema=12, slow_ema=26, atr_period=14)` calculates per-timeframe EMA direction and ATR-normalized EMA separation, then reports a weighted alignment summary.

Example:

```python
from strategy_research import analyze_timeframes

report = analyze_timeframes({
    "5m": candles_5m,
    "15m": candles_15m,
    "1h": candles_1h,
})
print(report["direction"], report["quality_score"])
print(report["timeframe_details"])
```

The quality score is a heuristic from trend alignment, normalized EMA separation, and timeframe coverage. It is **not** a probability of profit, forecast, or trading recommendation. The caller must pass completed candles and must not include a still-forming candle in the analysis.

## Trade journal

`append_trade_journal(path, trade)` appends one JSONL record. Each record requires `strategy`, `symbol`, `timeframe`, `entry_time`, `exit_time`, and finite `net_pnl`.

`summarize_trade_journal(path)` groups closed trade records by strategy, symbol, and timeframe and reports trade count, net P&L, win rate, profit factor, and average P&L. Invalid rows fail with a row number instead of being silently skipped.

## Comparing backtests

`compare_backtest_results(results)` compares summary dictionaries and sorts by return divided by max drawdown (using a 1 percentage-point floor to avoid division by zero). Compare only results that use the same symbol, timeframe, test dates, fees, slippage, and sizing assumptions. Ranking on one sample is not proof of future performance; use a separate out-of-sample period and include realistic costs.

## Not included yet

- No Telegram command or dashboard integration in this phase.
- No strategy score automatically creates, modifies, or closes orders.
- No claim that this research score validates the existing five-agent consensus strategy.
