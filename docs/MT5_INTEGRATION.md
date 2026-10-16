# MetaTrader 5 (MT5) Exclusive Live Integration Guide

MetaTrader 5 (MT5) Live (`mt5_live`) is the sole, exclusive broker execution engine across the entire trading bot platform. All paper trading and alternative brokers have been completely removed.

Orders are directly routed via MetaTrader 5 terminal (`TRADE_ACTION_DEAL`) or REST gateway bridge with broker-side stop loss and take profit protection.

---

## 1. Architectural Highlights

- **Exclusive Adapter**: `mt5_broker.py` implements the standardized `Broker` interface (`place_order`, `get_positions`, `close_position`, `modify_position`, `mark_price`, `get_account_info`, `get_account_summary`).
- **Fail-Closed Protection**: Live MT5 execution is required (`BOT_BROKER_MODE=mt5_live` or `/mt5 live`). Missing credentials (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) or an unreachable terminal immediately raise a `BrokerConfigurationError`. The system fails closed.
- **Cross-Platform Compatibility**:
  - On **Windows** with native `MetaTrader5` installed, live orders execute directly into the local terminal.
  - On **Linux / Cloud VPS / Containers**, live execution is routed seamlessly via the REST gateway bridge (`MT5_GATEWAY_URL` or `mt5_gateway_server.py`).

---

## 2. Symbol Resolution

Brokers often append custom suffixes or use alternative naming conventions. `resolve_mt5_symbol` resolves these automatically:

| TradingBot Unified Symbol | Standard MT5 Symbol | Example Broker Variations Handled |
|---------------------------|---------------------|-----------------------------------|
| `XAUUSD` / `GOLD` / `XAU` | `XAUUSD`            | `XAUUSD.m`, `XAUUSD.raw`, `XAUUSD_i`, `GOLD` |
| `BTCUSDT` / `BTC`         | `BTCUSD`            | `BTCUSD.m`, `BTCUSD.raw`, `BTCUSD_i` |

---

## 3. Configuration & Environment Variables

Configure these settings in `.env`:

```ini
# Exclusive MT5 Live Broker Mode
BOT_BROKER_MODE=mt5_live

# MT5 Live Account Credentials
MT5_ENABLED=true
MT5_MODE=live                # Exclusive Live Execution
MT5_LOGIN=12345678           # Account number (integer)
MT5_PASSWORD=YourPassword    # Account trading password
MT5_SERVER=YourBroker-Live   # Broker server name (e.g., Exness-MT5Live, IC-Markets, etc.)
MT5_PATH=                    # Optional path to terminal64.exe (Windows)
MT5_MAGIC=112233             # EA Magic identifier for order tagging
MT5_GATEWAY_URL=             # Optional HTTP gateway for Linux/cloud execution
```

---

## 4. Telegram & CLI Commands

### MT5 Dashboard & Interactive Inline Controls
```text
/mt5
/mt5 status
```
Renders the real-time MT5 account dashboard with live telemetry and interactive inline buttons:
- `[ 🟢 Connected (Exclusive Live) ]` — Shows active MT5 Live connection state.
- `[ ➕ Add Account ]` — Shows instructions and examples for registering accounts.
- `[ 📋 My Accounts ]` — Lists all saved MT5 accounts with masked passwords.
- `[ 📊 Positions ]` — Shows open MT5 positions and unrealized PnL.
- `[ 🔄 Refresh Telemetry ]` — Updates balance, equity, and margin levels.

### Adding & Registering Live Accounts
```text
/mt5 add <LOGIN> <PASSWORD> <SERVER> [ALIAS] [GATEWAY_URL]
```
Saves the MT5 account into `.runtime/mt5_accounts.json` with masked passwords (`P@••••23`), verifies connection credentials immediately, and sets it as the active account.

Examples:
- Direct Windows / AWS EC2: `/mt5 add 10054330 Secret123 Exness-Real primary`
- Remote Linux via Gateway: `/mt5 add 10054330 Secret123 Exness-Real primary http://192.168.1.50:5000`
- Prop Firm / FTMO: `/mt5 add 2098711 Pass123 FTMO-Server prop1`

### Switching MT5 Live Accounts
```text
/switch <alias|login>    # Switch active live account by alias or login number
/mt5 switch <login>      # Switch to specific live MT5 account
/broker                  # Display current MT5 Live broker status
```

### Quick Live Mode Control
```text
/live on                 # Connect/verify MT5 Live trading immediately
/live off                # Pause live order generation (suggests /autotrade off or /kill on)
/live <login> <pass> <srv> # Save account and switch to live in one command
```

### Managing Registered Accounts
```text
/mt5 accounts            # View list of saved accounts, servers, and active flag
/mt5 remove <alias|login> # Remove a saved account from disk
```

### Viewing Active MT5 Positions
```text
/mt5 positions
```
Lists open positions, lots, entry prices, mark prices, and floating PnL on the MT5 broker.

### Terminal Interactive CLI
The interactive CLI (`python main.py --cli`) provides equivalent commands:
- `mt5 add <login> <pass> <server> [alias] [gateway_url]`
- `mt5 accounts`
- `mt5 switch [live|paper|<alias>]`
- `mt5 remove <alias|login>`
- `switch [live|paper|<alias>]`
- `live [on|off|<login> <pass> <server>]`

---

## 5. Risk & Execution Safety

1. **Server-Side Protection**: When placing live MT5 orders, Stop Loss and Take Profit levels are attached directly to the `MqlTradeRequest` (`TRADE_ACTION_DEAL`). Protection is active immediately on the broker's matching engine.
2. **Dynamic Trailing Stops**: As the price advances through Break-Even thresholds (+0.5R, +1.0R), `modify_position` updates the stop loss level on the server (`TRADE_ACTION_SLTP`).
3. **Concurrent Trade Ceiling**: Capped at 5 active positions (`BOT_MAX_OPEN_TRADES=5`) across the active watchlist (`BTCUSDT,XAUUSD`).

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

