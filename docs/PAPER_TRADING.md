# Paper Trading Accounting (DEPRECATED & REMOVED)

> [!WARNING]
> **Paper trading and PaperBroker have been completely removed.**
> MetaTrader 5 Live (`mt5_live`) is now the sole, exclusive execution broker across the entire platform. Attempting to instantiate `PaperBroker` or set `BROKER_MODE=paper` fails closed with a `BrokerConfigurationError`.
> For live execution setup, refer to [MT5_INTEGRATION.md](MT5_INTEGRATION.md).

The historical documentation below is kept for archival reference only.

## Accounting behavior

- Entry and exit commissions are deducted from the paper balance.
- Closing orders realize P&L only for the quantity closed.
- Partial closes retain the remaining position.
- Opposite-side orders larger than the open quantity are rejected; the simulator will not silently reverse a position.
- `mark_price(symbol, price)` updates unrealized P&L and equity without changing balance.
- `get_account_summary()` returns balance, equity, unrealized P&L, net realized balance change after fees, and open position counts.

## Optional restart persistence

Pass `state_file="path/to/paper-state.json"` to `PaperBroker`. State is written via a temporary file and atomic replace, and includes balance, positions, mark prices, and orders. On restart, malformed or unsupported state raises an error instead of silently starting a fresh account. Keep this state file private and back it up if the paper ledger matters.

Example:

```python
from execution import PaperBroker

broker = PaperBroker(
    initial_balance=10_000,
    commission_pct=0.0004,
    state_file="paper-state.json",
    journal_file="paper-journal.jsonl",
)
broker.place_order("BTC/USDT", "buy", 0.01, price=60000)
broker.mark_price("BTC/USDT", 60500)
print(broker.get_account_summary())
```

## Limitations

- This is a paper ledger, not an exchange simulator: no order book, partial-fill model, latency, funding, liquidation, margin, precision, or exchange-specific stop handling.
- A provided fill price is trusted as a simulation input; it is not verified against a live feed.
- Persistence does not yet coordinate with external broker state or provide multi-process locking.
- Live trading remains separate and should remain disabled until live order lifecycle, protective orders, reconciliation, and restart recovery are independently verified.
