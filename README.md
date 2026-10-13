# TradingBot — Multi-Agent Institutional Trading Assistant

Modern Telegram bot and algorithmic trading engine for **Gold (XAUUSD)**, **Bitcoin (BTCUSDT)**, **Ethereum (ETHUSDT)**, **Solana (SOLUSDT)**, and **BNB (BNBUSDT)** powered by 7-agent consensus, best-entry zones, 2-year market structure, and **MetaTrader 5 (MT5) / Paper execution**.

## Features

| Area | Capability |
|------|------------|
| **Signals** | **7 Autonomous Agents**: Trend, Liquidity/SMC, Technical Indicators, AI-Learner, Macro News, Gautam Jha Price Action, and Mean-Reversion VWAP |
| **Entry Planning** | Dynamic best-entry *zones* synthesized from PDH/PDL, EMAs, market swings, ATR volatility, and 2-year macro S/R levels |
| **2Y History** | ~730 daily bars caching — 52-week ranges, EMA50/200, historical swing structures |
| **Autotrade** | Automated multi-symbol execution up to **5 concurrent active trade slots** |
| **Brokers** | **PaperBroker** (local ledger) & **MT5Broker** (**Paper/Demo simulation** and **Live MT5 account deal execution**) |
| **Safety & Risk** | Fail-closed security, 0.20% base risk per trade, 3.0% daily loss limit, 6.0% weekly loss limit, dynamic leverage ceilings, and emergency kill switch |
| **Scalping** | Optional instant profit harvesting on USD profit target or percentage moves |

## Environment Configuration

```ini
# Core Telegram & AI Assistant
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
GEMINI_API_KEY=your_gemini_api_key

# Execution & Broker Settings (paper | mt5 | mt5_paper | mt5_live | live)
BOT_BROKER_MODE=paper
BOT_EQUITY=1000
BOT_LEVERAGE=20
BOT_AUTOTRADE=true
BOT_AUTOTRADE_SYMBOLS=BTCUSDT,XAUUSD,ETHUSDT,SOLUSDT,BNBUSDT
BOT_MAX_OPEN_TRADES=5
BOT_DATA_SOURCE=ccxt

# MetaTrader 5 (MT5) Settings
MT5_ENABLED=false
MT5_MODE=paper              # paper | live
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=MetaQuotes-Demo
MT5_PATH=
MT5_MAGIC=112233
MT5_GATEWAY_URL=

# Fast Scalp TP Settings
BOT_SCALP=true
BOT_SCALP_TP_USD=5.0
BOT_SCALP_TP_PCT=0.15
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
- `/autotrade on|off` — Multi-symbol autotrading (up to 5 concurrent active trades)
- `/close [SYM|all]` — Instantly close position(s)
- `/cancel [SYM]` — Cancel pending order setup
- `/closed` — Recently closed trades history & realized PnL

### 🏦 MetaTrader 5 & Broker Accounts
- `/mt5` or `/mt5 status` — Check MT5 account telemetry & margin
- `/mt5 paper` — Switch to MT5 Paper simulation engine
- `/mt5 live [login] [pass] [server]` — Connect live MT5 account (fail-closed safe)
- `/mt5 positions` — Show active positions on MT5 broker terminal
- `/broker [paper|mt5|mt5_live|live]` — View or switch execution broker engine

### 🛡️ Risk & Safety Management
- `/status` — Portfolio equity, drawdown & active slot capacity
- `/health` — Real-time telemetry & background worker heartbeat
- `/risk` — Risk tier report, margin capacity & lot sizing
- `/leverage [1-20]` — Set maximum account leverage ceiling
- `/kill [on|off]` — Emergency execution stop circuit breaker

### 📈 Backtesting & Research
- `/backtest [bars] [SYM]` — Fast backtest engine
- `/abacktest [mode] [bars] [SYM]` — Advanced suite (WF / MC / sweep)
- `/performance` — Detailed trade metrics, win rate & Sharpe ratio
- `/profile [name]` — Switch strategy profile (scalp/swing/trend/balanced)

### 🧠 Autonomous AI & Standings
- `/brain` — Autonomous learner knowledge & win rates
- `/relearn [SYM]` — Force 5Y history retraining bootstrap
- `/board` — Strategy board & agent standings
- `/journal` — Trade journal analytics & history
- `/digest` — Daily market performance digest
- `/reset` — Clear conversation context

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

