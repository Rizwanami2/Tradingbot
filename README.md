# TradingBot — Multi-Agent Institutional Trading Assistant

Modern Telegram bot and algorithmic trading engine for **Gold (XAUUSD)** and **Bitcoin (BTCUSDT)** powered by 7-agent consensus and multi-timeframe market structure. MT5 and Delta broker adapters are present; validate only with demo/testnet and keep live execution disabled.

## Features

| Area | Capability |
|------|------------|
| **Signals** | **7 Autonomous Agents**: Trend, Liquidity/SMC, Technical Indicators, AI-Learner, Macro News, Gautam Jha Price Action, and Mean-Reversion VWAP |
| **Entry Planning** | Dynamic best-entry *zones* synthesized from PDH/PDL, EMAs, market swings, ATR volatility, and 2-year macro S/R levels |
| **2Y History** | ~730 daily bars caching — 52-week ranges, EMA50/200, historical swing structures |
| **Autotrade** | Automated multi-symbol execution up to **5 concurrent active trade slots** |
| **Brokers** | MT5 and Delta broker adapters; use MT5 demo / Delta testnet for validation. Live execution is not enabled by this deployment baseline. |
| **Safety & Risk** | Fail-closed security, 0.20% base risk per trade, 3.0% daily loss limit, 6.0% weekly loss limit, dynamic leverage ceilings, and emergency kill switch |
| **Scalping** | Optional instant profit harvesting on USD profit target or percentage moves |

## Environment Configuration

```ini
# Core Telegram & AI Assistant
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
GEMINI_API_KEY=your_gemini_api_key

# Testnet-first broker settings; do not use live credentials during validation.
BOT_BROKER_MODE=delta_testnet
TELEGRAM_ALLOWED_USER_IDS=123456789 # comma-separated numeric Telegram user IDs
BOT_EQUITY=1000
BOT_LEVERAGE=1
BOT_AUTOTRADE=false
BOT_AUTOTRADE_LIVE_OPT_IN=false
BOT_AUTOTRADE_SYMBOLS=BTCUSDT,XAUUSD
BOT_MAX_OPEN_TRADES=5
BOT_DATA_SOURCE=ccxt

# MT5 remains disabled until a demo terminal is explicitly configured.
MT5_ENABLED=false
MT5_MODE=demo
MT5_LOGIN=                   # Demo account number only
MT5_PASSWORD=               # Demo account password only
MT5_SERVER=MetaQuotes-Demo  # Demo server only
MT5_PATH=                   # Optional path to terminal64.exe (Windows/Wine)
MT5_MAGIC=112233            # EA Magic identifier for order tagging
MT5_GATEWAY_URL=            # Keep unset during testnet validation

# Delta Exchange testnet (no live keys)
DELTA_EXCHANGE_API_KEY=
DELTA_EXCHANGE_API_SECRET=
DELTA_EXCHANGE_BASE_URL=https://cdn-ind.testnet.deltaex.org
DELTA_EXCHANGE_TESTNET=true
DELTA_EXCHANGE_DEFAULT_LEVERAGE=1.0
DELTA_EXCHANGE_AUTO_LEVERAGE=false
DELTA_EXCHANGE_MAX_LEVERAGE=1.0

# Strategy Profile & Micro-Scalping Settings
BOT_PROFILE=scalp
BOT_SCALP=true
BOT_SCALP_TP_USD=2.5
BOT_SCALP_TP_PCT=0.10
BOT_SCALP_SL_USD=3.0
BOT_SCALP_SL_PCT=0.12
BOT_NEWS=true
```

## Telegram Commands Catalog

### 📊 Market Data & Live Quotes
- `/price [SYM]` — Live mark price, 24h change & volatility stats
- `/levels [SYM]` — Key technical levels (PDH/PDL, pivots, swings, 2Y S/R)
- `/news [SYM]` — Macroeconomic calendar & market sentiment

### 🎯 Signals & Strategy Confluence
- `/signal [SYM]` — 7-Agent consensus analysis & optimal entry zone
- `/asignal [SYM] [profile]` — Advanced SMC + CVD orderflow setup
- `/quality [SYM]` — Comprehensive signal quality score & breakdown

### ⚡ Execution & Positions (Active Limit: 5)
- `/positions` — Open active trades, pending setups & real-time PnL
- `/autotrade on|off|scalp` — Multi-symbol autotrading (standard or rapid micro-scalping up to 5 concurrent active trades)
- `/close [SYM|all]` — Instantly close position(s)
- `/cancel [SYM]` — Cancel pending order setup
- `/closed` — Recently closed trades history & realized PnL

### 🏦 Exclusive MT5 Live Execution
- `/mt5 [status|live|accounts|add]` — MetaTrader 5 live account dashboard & management ([Guide](docs/MT5_INTEGRATION.md))
- `/live` — Connect and inspect MT5 Live account telemetry
- `/switch <alias|login>` — Switch active MT5 Live account credentials
- `/broker` — Display exclusive MT5 Live broker status and telemetry

### 🛡️ Risk & Safety Management
- `/status` — Portfolio equity, drawdown, active slot capacity & strategy profile
- `/health` — Real-time telemetry & background worker heartbeat
- `/risk` — Risk tier report, margin capacity & lot sizing
- `/leverage [1-20]` — Set maximum account leverage ceiling
- `/kill [on|off]` — Emergency execution stop circuit breaker

### 📈 Backtesting & Research
- `/backtest [bars] [SYM]` — Fast backtest engine
- `/abacktest [mode] [bars] [SYM]` — Advanced suite (WF / MC / sweep)
- `/performance` — Detailed trade metrics, win rate & Sharpe ratio
- `/profile [name]` — View or activate strategy profile (`scalp`, `swing`, `balanced`, `gold_only`, `crypto`)

### 🧠 Autonomous AI & Standings
- `/brain` — Autonomous learner knowledge & win rates
- `/relearn [SYM]` — Force 5Y history retraining bootstrap
- `/board` — Strategy board & agent standings
- `/journal` — Trade journal analytics & history
- `/digest` — Daily market performance digest
- `/reset` — Clear conversation context

## Hybrid Python + MT5 Demo Bridge

See [docs/MT5_HYBRID_DEMO.md](docs/MT5_HYBRID_DEMO.md) for the opt-in Python-to-MQL5 signal-file adapter. The EA is disabled by default and blocks non-demo accounts. Python historical backtesting remains separate; the external wall-clock signal bridge is not replayed in MT5 Strategy Tester.

## Quick Start & Deploy

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env

# 3. Start trading bot (applies self-healing wire patches on boot)
python run_bot.py

# Or launch directly in interactive CLI mode:
python main.py --cli
```

## Safety & Disclaimer

Default mode is **paper**. Fail-closed mechanisms strictly prevent unauthorized live trading without validated credentials and explicit configuration. Not financial advice. Built for institutional research and algorithmic execution.

