# Hybrid Python + MT5 Demo Bridge

This feature is an integration scaffold, not a claim that the whole strategy has been
ported or that the EA has been compiled. It connects Python analysis output to an MT5
Expert Advisor through a short-lived file in MetaTrader's **Common Files** directory.

## Safety defaults

- **InpEnableDemoExecution=false** in the EA. It does not submit orders until explicitly enabled.
- The EA blocks accounts unless **ACCOUNT_TRADE_MODE_DEMO** is active. External signal files are not executed in Strategy Tester.
- Python publishing is an explicit function call; importing the module does not publish anything.
- No MT5 terminal, broker credentials, account, or AWS instance is accessed by these files.
- A single pending signal is allowed. The writer refuses to overwrite it.
- Signals expire after at most five minutes and must include positive volume, entry, stop, and target with valid directional ordering.
- Before using demo execution, compile the EA in MetaEditor and validate it against the broker's symbol names, volume steps, stop-distance rules, and terminal permissions.

## Files

- **mt5_signal_bridge.py** validates signals and atomically publishes one pipe-delimited row.
- **mt5_ea/TradingBotHybridEA.mq5** polls for a signal and enforces demo-only execution.
- **tests/test_mt5_signal_bridge.py** covers Python-side validation and pending-file behavior.

## Python usage

```python
import time
from mt5_signal_bridge import TradeSignal, publish_signal

now = int(time.time())
signal = TradeSignal(
    signal_id="example_001",
    symbol="XAUUSD",       # Must match an available MT5 symbol or be mapped by the broker
    side="BUY",
    volume=0.01,           # MT5 lots, must match symbol volume step
    entry_price=2350.0,
    stop_loss=2340.0,
    take_profit=2370.0,
    created_at=now,
    expires_at=now + 90,
)

# Pass the MT5 Common Files directory explicitly, or set MT5_COMMON_FILES_DIR.
publish_signal(signal, common_files_dir=r"C:\path\to\MetaQuotes\Terminal\Common\Files")
```

The Python function only writes a signal file; it does not connect to MT5 or place orders.
Do not call it from unattended automation until the signal generation, symbol mapping,
and demo test cases have been reviewed.

## MT5 setup for demo validation

1. Open the .mq5 file in MetaEditor and compile it. The repository CI cannot compile MQL5
   because MetaEditor is not available on the Linux runner.
2. In MT5, open a **demo** account and attach TradingBotHybridEA to a chart.
3. Keep InpEnableDemoExecution=false while validating file visibility and logs.
4. Only for a controlled demo test, enable InpEnableDemoExecution=true; the EA itself
   blocks real accounts, rejects stale/invalid signals, checks volume limits, and does not
   retry uncertain order results automatically.
5. Inspect the Experts and Journal logs. Reconcile any uncertain result before publishing
   another signal.

The EA deliberately refuses to replay wall-clock signal files in Strategy Tester. Use the
project's Python historical backtest engine for strategy backtests; use MT5 demo only to
validate terminal-side signal handling and order semantics.

## Protocol

The file TradingBot/signal.csv contains one row:

**schema|signal_id|symbol|side|volume|entry|stop_loss|take_profit|created_at|expires_at**

Timestamps are Unix seconds. The writer publishes atomically and fails if a pending row
already exists. The EA deletes accepted, rejected, expired, or duplicate rows. If an order
result is uncertain, it logs the retcode and does not automatically retry.
