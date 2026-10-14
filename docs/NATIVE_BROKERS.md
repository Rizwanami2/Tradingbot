# Native Broker Environments for Live Trading (Linux & Cloud)

This guide documents the native execution broker environments available in TradingBot. Unlike desktop-bound software (such as MetaTrader 5 which requires Windows or Wine), these native broker environments run **100% natively on standard Linux, Docker containers, macOS, and cloud VPS instances with zero external desktop dependencies.**

---

## 1. Summary of Supported Broker Environments

| Broker Engine | Type | Primary Markets | Linux-Native? | Free Demo / Paper? | File Reference |
|---|---|---|---|---|---|
| **OANDA v20** | Institutional REST | Forex, Gold (XAU/USD), Silver, Oil, Indices | ✅ Yes (Pure REST) | ✅ Yes ($100k practice) | `oanda_broker.py` |
| **CCXT** | Unified Exchange API | Crypto Spot & Perpetuals (100+ exchanges) | ✅ Yes (CCXT library) | ✅ Yes (Sandbox/Testnet) | `execution.py` |
| **Alpaca** | Institutional REST | US Equities (Stocks), ETFs, Crypto | ✅ Yes (Pure REST) | ✅ Yes (Paper trading) | `alpaca_broker.py` |
| **Paper Engine** | Deterministic Sim | Multi-Asset (BTC, Gold, Forex, Stocks) | ✅ Yes (Built-in) | ✅ Yes (Zero capital risk) | `execution.py` |
| **MetaTrader 5** | Desktop / Gateway | Forex, CFDs, Metals, Futures | ⚠️ Requires Windows/Gateway | ✅ Yes (Paper simulation) | `mt5_broker.py` |

---

## 2. OANDA v20 REST Broker (Forex & Gold)

OANDA provides institutional-grade trading execution for currency pairs and commodities directly over an official HTTP REST v20 interface.

### Key Features:
- **Zero Desktop Requirement**: Operates over pure JSON HTTP requests (`requests`).
- **Full Asset Coverage**:
  - Gold (`XAU_USD`), Silver (`XAG_USD`), Crude Oil (`WTICO_USD`).
  - All Forex Majors & Minors (`EUR_USD`, `GBP_USD`, `USD_JPY`, `AUD_USD`, `USD_CAD`, etc.).
  - Global Indices (`SPX500_USD`, `NAS100_USD`, `US30_USD`).
- **Free Demo / Practice Accounts**: Instant registration with $100,000 virtual balance and personal API tokens at [oanda.com](https://www.oanda.com).
- **Server-Side Protection**: Real-time Stop Loss (`stopLossOnFill`) and Take Profit (`takeProfitOnFill`) attached to market deals.

### Connecting via Telegram:
```text
/oanda live <ACCOUNT_ID> <API_TOKEN> practice
/oanda live <ACCOUNT_ID> <API_TOKEN> live
```

### Checking Telemetry:
```text
/oanda status
/oanda positions
```

### Configuration in `.env`:
```ini
BOT_BROKER_MODE=oanda
OANDA_ACCOUNT_ID=101-001-12345678-001
OANDA_API_TOKEN=your_oanda_personal_access_token
OANDA_ENVIRONMENT=practice   # "practice" or "live"
```

---

## 3. CCXT Live Crypto Broker (100+ Exchanges)

`CCXTBroker` executes directly against cryptocurrency spot and perpetual futures markets on over 100 global exchanges.

### Supported Exchanges:
- **Binance** (Spot & USDⓈ-M Futures)
- **Bybit** (Spot & Linear USDT Perpetuals)
- **OKX** (Spot & Perpetual Swaps)
- **Bitget** / **BingX** / **KuCoin** / **Gate.io** / **Kraken**
- **Delta Exchange India**

### Connecting via Telegram:
```text
/ccxt live binance <API_KEY> <API_SECRET>
/ccxt live bybit <API_KEY> <API_SECRET>
/ccxt live okx <API_KEY> <API_SECRET> <PASSPHRASE>
```

### Checking Telemetry:
```text
/ccxt status
/ccxt positions
```

### Configuration in `.env`:
```ini
BOT_BROKER_MODE=live
BOT_EXCHANGE=binance
EXCHANGE_API_KEY=your_api_key
EXCHANGE_API_SECRET=your_api_secret
EXCHANGE_PASSWORD=              # Optional (required by OKX, KuCoin, Bitget)
EXCHANGE_TESTNET=false          # Set true for testnet sandbox
```

---

## 4. Alpaca REST Broker (US Stocks, ETFs & Crypto)

Alpaca provides commission-free US stock and ETF trading with instant API access.

### Key Features:
- Stocks & ETFs: `AAPL`, `TSLA`, `NVDA`, `SPY`, `QQQ`, etc.
- Crypto: `BTC/USD`.
- Built-in bracket orders with stop-loss and take-profit bounds.

### Connecting via Telegram:
```text
/alpaca live <API_KEY> <API_SECRET> paper
/alpaca live <API_KEY> <API_SECRET> live
```

### Configuration in `.env`:
```ini
BOT_BROKER_MODE=alpaca
ALPACA_API_KEY=your_alpaca_key
ALPACA_API_SECRET=your_alpaca_secret
ALPACA_PAPER=true
```

---

## 5. Universal Broker Switching

You can view active telemetry and switch execution engines on the fly:
```text
/broker
```
Outputs:
```text
🏦 Active Execution Broker Environment
• Class: OANDABroker
• Mode: PRACTICE

🌐 OANDA v20 Account Info (⚪ PRACTICE)
• Status: 📶 Connected
• Account ID: 101-001-99887766-001
• Balance: $100,000.00 | NAV / Equity: $100,250.00
• Margin Used: $500.00 | Free Margin: $99,750.00
• Margin Level: 20050.0% | Margin Rate: 2.0%
• Unrealized PnL: +$250.00 | Open Positions: 1
```

Switch to another broker instantly:
```text
/broker paper
/broker oanda
/broker ccxt
/broker alpaca
/broker mt5
```

---

## 6. Fail-Closed Safety Principle

Every native live broker strictly enforces fail-closed safety:
- If API credentials are missing, invalid, or expired, initialization **fails closed** immediately.
- The engine will **never silently execute paper trades** when live trading was explicitly requested.
- Funds and current broker state remain completely protected.
