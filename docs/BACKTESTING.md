# Historical Backtesting

The Telegram `/backtest` command runs an isolated EMA-crossover simulation over fetched historical OHLCV candles. It never calls the execution engine or submits exchange orders.

## Telegram

- `/backtest` — fetch up to 1,000 candles for the configured symbol on the default 1h timeframe.
- `/backtest 500 BTCUSDT 1h` — specify candle count, symbol, and timeframe.
- Supported timeframes: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`.

The command intentionally uses the real-data `DataFeed` path and fails clearly when historical data is unavailable; it must not silently backtest synthetic/demo candles.

## Local CSV

```bash
python backtest_engine.py --csv candles.csv --symbol BTCUSDT --timeframe 1h --equity 10000 --risk 0.01
```

CSV must include `timestamp` (or `datetime` / `date` / `time`) plus `open,high,low,close`. Timestamps are parsed as UTC.

## Model assumptions

- EMA crossover is calculated at candle close and enters no earlier than the next candle open.
- ATR determines the stop distance; target distance is a configurable risk/reward multiple.
- Quantity is risk-budget sized and capped by the configured leverage limit.
- Fees and slippage are charged on both entry and exit.
- If stop and target both touch within the same candle, the stop is assumed to fill first.
- Any remaining position closes at the final candle close.
- OHLC bars cannot reconstruct exact intrabar order flow. Results are research estimates, not promises of future performance.
