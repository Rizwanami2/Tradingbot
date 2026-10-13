# MetaTrader 5 (MT5) Integration Guide

The MetaTrader 5 (MT5) integration enables institutional trading execution in two distinct modes:
1. **Paper / Demo Simulation Mode**: High-fidelity local simulation featuring MT5 ticket generation, leverage-aware margin calculation, realistic spread & commission deductions, dynamic SL/TP modification, and floating unrealized mark-to-market PnL tracking.
2. **Live MT5 Account Mode**: Direct order routing via MetaTrader 5 terminal (`TRADE_ACTION_DEAL`) or REST gateway bridge with broker-side stop loss and take profit protection.

---

## 1. Architectural Highlights

- **Adapter**: `mt5_broker.py` implements the standardized `Broker` interface (`place_order`, `get_positions`, `close_position`, `modify_position`, `mark_price`, `get_account_info`, `get_account_summary`).
- **Fail-Closed Protection**: If live MT5 execution is requested (`BOT_BROKER_MODE=mt5_live` or `/mt5 live`), missing credentials (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) or an unreachable terminal immediately raise a `BrokerConfigurationError`. The system will **never silently fall back to paper mode** when live trading is explicitly commanded.
- **Cross-Platform Compatibility**:
  - On **Windows** or **Wine/Proot** with native `MetaTrader5` installed, live orders execute directly into the local terminal.
  - On **Headless Linux / Containers**, high-fidelity MT5 paper simulation runs natively with zero external dependencies. Optional live execution can be routed via `MT5_GATEWAY_URL`.

---

## 2. Symbol Resolution

Brokers often append custom suffixes or use alternative naming conventions. `resolve_mt5_symbol` resolves these automatically:

| TradingBot Unified Symbol | Standard MT5 Symbol | Example Broker Variations Handled |
|---------------------------|---------------------|-----------------------------------|
| `XAUUSD` / `GOLD` / `XAU` | `XAUUSD`            | `XAUUSD.m`, `XAUUSD.raw`, `XAUUSD_i`, `GOLD` |
| `BTCUSDT` / `BTC`         | `BTCUSD`            | `BTCUSD.m`, `BTCUSD.raw`, `BTCUSD_i` |
| `ETHUSDT` / `ETH`         | `ETHUSD`            | `ETHUSD.m`, `ETHUSD.raw`, `ETHUSD_i` |
| `SOLUSDT` / `SOL`         | `SOLUSD`            | `SOLUSD.m`, `SOLUSD.raw` |
| `BNBUSDT` / `BNB`         | `BNBUSD`            | `BNBUSD.m`, `BNBUSD.raw` |

---

## 3. Configuration & Environment Variables

Configure these settings in `.env`:

```ini
# Enable MT5 mode
BOT_BROKER_MODE=mt5          # Options: paper | mt5 | mt5_paper | mt5_live | live

# MT5 Account Credentials
MT5_ENABLED=true
MT5_MODE=paper               # "paper" for simulation, "live" for real execution
MT5_LOGIN=12345678           # Account number (integer)
MT5_PASSWORD=YourPassword    # Account trading password
MT5_SERVER=YourBroker-Live   # Broker server name (e.g., Exness-MT5Live, IC-Markets, etc.)
MT5_PATH=                    # Optional path to terminal64.exe (Windows/Wine)
MT5_MAGIC=112233             # EA Magic identifier for order tagging
MT5_GATEWAY_URL=             # Optional HTTP gateway for remote Linux execution
```

---

## 4. Telegram & CLI Commands

### Checking MT5 Account Telemetry
```text
/mt5
/mt5 status
```
Outputs:
```text
🏦 MT5 Account Info (🟢 LIVE or ⚪ PAPER)
• Status: 📶 Connected
• Login: 12345678 | Server: Broker-Live
• Balance: $10,000.00 | Equity: $10,240.50
• Margin: $250.00 | Free Margin: $9,990.50
• Margin Level: 4096.2% | Leverage: 20x
• Currency: USD | Broker: Broker Financial Ltd.
```

### Switching to MT5 Paper Simulation
```text
/mt5 paper
```
Activates paper simulation mode with $0 capital risk.

### Connecting to Live MT5 Account
```text
/mt5 live <LOGIN> <PASSWORD> <SERVER> [GATEWAY_URL]
```
Validates credentials and connects live. Fails closed safely if connection cannot be authorized.

Examples:
- Direct Windows: `/mt5 live 10054330 MyPassword Mtrading-live`
- Linux via Gateway: `/mt5 live 10054330 MyPassword Mtrading-live http://192.168.1.50:5000`

### Viewing Active MT5 Positions
```text
/mt5 positions
```
Lists open positions, lots, entry prices, and floating PnL on MT5 terminal.

### Switching Broker Engine
```text
/broker paper
/broker mt5
/broker mt5_live
```

---

## 5. Risk & Execution Safety

1. **Server-Side Protection**: When placing live MT5 orders, Stop Loss and Take Profit levels are attached directly to the `MqlTradeRequest` (`TRADE_ACTION_DEAL`). Protection is active immediately on the broker's matching engine.
2. **Dynamic Trailing Stops**: As the price advances through Break-Even thresholds (+0.5R, +1.0R), `modify_position` updates the stop loss level on the server (`TRADE_ACTION_SLTP`).
3. **Concurrent Trade Ceiling**: Capped at 5 active positions (`BOT_MAX_OPEN_TRADES=5`) across the active watchlist (`BTCUSDT,XAUUSD,ETHUSDT,SOLUSDT,BNBUSDT`).

---

## 6. Linux & Cloud Deployment via MT5 REST Gateway

### Why is the Gateway Bridge needed?
The official `MetaTrader5` Python package from MetaQuotes is **Windows-only**. It relies on proprietary Windows C/C++ DLLs and requires an active desktop MT5 terminal (`terminal64.exe`) to execute trades. It does not run natively on standard Linux or cloud containers.

To trade live with MT5 when your bot runs on Linux or in a cloud environment:
1. **On your Windows PC or Windows VPS** (where MetaTrader 5 is installed):
   - Install dependencies: `pip install MetaTrader5`
   - Run the turnkey gateway bridge:
     ```bash
     python mt5_gateway_server.py --port 5000
     ```
2. **On your Linux TradingBot**:
   - Set the gateway URL in `.env`:
     ```ini
     MT5_GATEWAY_URL=http://<YOUR_WINDOWS_IP>:5000
     ```
   - Or connect dynamically from Telegram:
     ```text
     /mt5 live 10054330 <PASSWORD> Mtrading-live http://<YOUR_WINDOWS_IP>:5000
     ```

### Gateway REST API Reference
- `GET  /health`    — Verifies bridge responsiveness and terminal status
- `POST /connect`   — Authorizes MT5 account with login, password, and server
- `GET  /account`   — Returns balance, equity, margin, free margin, and leverage
- `POST /order`     — Dispatches live deal order with SL/TP
- `GET  /positions` — Lists open positions with unrealized PnL
- `POST /modify`    — Updates server-side SL and TP
- `POST /close`     — Closes open positions

