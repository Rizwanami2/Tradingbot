# Market-regime research (Phase 5)

This phase adds a descriptive OHLCV classifier that labels the latest completed candle as:

- `trending`: ADX meets the configured trend threshold.
- `ranging`: ADX is at or below the configured range threshold.
- `transition`: ADX lies between those thresholds.
- `high_volatility`: ATR as a percentage of close exceeds a percentile threshold calculated from prior observations only.

The output also includes EMA-based trend direction, ADX, ATR, normalized ATR, and the timestamp of the analyzed candle. Normalizing ATR by close makes the volatility feature more comparable across assets with different price scales.

## Example

```python
from market_regime import classify_market_regime

# Supply a DataFrame with open/high/low/close and a DatetimeIndex.
# Remove any still-forming candle before calling this function.
result = classify_market_regime(completed_ohlcv)
print(result["regime"], result["trend_direction"])
```

## Safety and interpretation

- Research-only: this module does not generate buy/sell entries, place orders, or connect to an exchange.
- The label is a heuristic description, not a forecast, trade recommendation, or probability of profit.
- Feed it only completed candles from a consistent data source. In backtests, call it with each historical prefix rather than the full future dataset to avoid look-ahead leakage.
- Do not compare results across markets without accounting for differences in candle timeframe, liquidity, gaps, and trading costs.
- A regime label alone is not evidence that a strategy is profitable. Validate any proposed filter with walk-forward historical tests and paper-trading observations before considering any other deployment mode.

## Next planned research phases

1. Add a walk-forward harness that keeps tuning and evaluation periods separate.
2. Add signal calibration and trade analytics, including uncertainty and a no-trade state.
3. Add portfolio-level risk checks and paper-broker recovery tests.
4. Validate exchange adapter behavior, partial fills, timeouts, duplicate submissions, and restart reconciliation without sending live orders.
