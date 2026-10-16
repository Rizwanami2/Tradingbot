# Native MQL5 Expert Advisor (demo/tester only)

## Files
- `mt5/TradingBotDemoEA.mq5`: native Expert Advisor with previous completed D1 high/low sweep and M1 confirmation logic.
- `tests/test_mql5_ea_safety.py`: static safety checks for the source. These do **not** replace MetaEditor compilation or Strategy Tester validation.

## Safety defaults
- `EnableTrading=false` by default.
- The EA refuses to initialize with order submission enabled on a live MT5 account. Order submission is permitted only in Strategy Tester or a DEMO account.
- Risk is capped at 1% per trade by input validation; the default is 0.20%.
- A daily equity-loss guard halts new entries.
- If risk sizing would require less than the broker's minimum volume, the EA skips the trade rather than rounding risk upward.
- No credentials are embedded in source.

## Strategy behavior
On each new signal-timeframe bar, it inspects the previous completed bar. A bearish candle that swept above the previous completed D1 high and closed back below it can generate a short signal; a bullish candle that swept below the previous completed D1 low and closed back above it can generate a long signal. Stop loss is beyond the signal candle with a configurable point buffer; target is based on the configured reward/risk multiple.

This is a simplified native implementation, not a claim that every Python strategy agent has been ported. Differences in symbol naming, tick size/value, spread, session times, contract specifications and data feeds require instrument-specific validation.

## Validation checklist
1. Open `mt5/TradingBotDemoEA.mq5` in MetaEditor and compile with the broker's current MT5 build.
2. Run Strategy Tester using historical data, visual mode first, and inspect Experts/Journal logs.
3. Test only on an MT5 demo account after the tester run; keep `EnableTrading=false` until ready to test the signal path without order submission.
4. Compare EA signals with Python strategy outputs and record mismatches.
5. Do not treat backtest results as a guarantee of future performance.

No live trading or deployment is enabled by this repository change.
