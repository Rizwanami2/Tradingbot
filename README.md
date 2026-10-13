# TradingBot — Multi-Agent Paper Trading Assistant

Modern Telegram bot for **Gold (XAUUSD)** and **BTC (BTCUSDT)** with multi-agent consensus, best-entry zones, 2-year structure analysis, and safe **paper** execution.

## Features

| Area | Capability |
|------|------------|
| **Signals** | Trend, Liquidity/SMC, Indicators, AI-Learner, News, Gautam Jha |
| **Entry plan** | Best entry *zone* from PDH/PDL, EMAs, swings, ATR, 2Y S/R |
| **2Y history** | ~730 daily bars — 52w range, EMA50/200, major S/R |
| **Autotrade** | Paper only, **Gold + BTC** watchlist, max concurrent slots |
| **Scalp** | Optional auto-close on USD or % profit target |
| **Risk** | Leverage-aware sizing, micro lots, $1000 paper default |

## Railway deploy

**Start command**

```text
python run_bot.py
```

**Environment**

```text
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
BOT_BROKER_MODE=paper
BOT_EQUITY=1000
BOT_LEVERAGE=20
BOT_AUTOTRADE=true
BOT_AUTOTRADE_SYMBOLS=BTCUSDT,XAUUSD
BOT_SCALP=true
BOT_SCALP_TP_USD=5
BOT_DATA_SOURCE=ccxt
```

## Telegram commands

| Command | Description |
|---------|-------------|
| `/signal btc` · `/asignal gold` | Agents + entry plan + 2Y context |
| `/price` · `/levels` | Live mark and key levels |
| `/autotrade on\|off` | Paper autotrade (Gold + BTC only) |
| `/positions` | Open / pending / recent closed |
| `/close all` · `/close BTCUSDT` | Close all or one symbol |
| `/leverage` · `/status` · `/risk` | Risk controls |
| `/news` · `/backtest` | Sentiment and research |

## Architecture

```text
run_bot.py
  ├─ wire_* patches (safe on every boot)
  ├─ signal_pipeline.py
  ├─ entry_level_planner.py
  ├─ historical_2y.py
  ├─ autotrade_worker.py (Gold + BTC)
  ├─ multi_agent_trading_bot.py
  ├─ execution_engine.py + paper broker
  └─ main.py (Telegram UI)
```

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env   # fill TELEGRAM_TOKEN
python run_bot.py
```

## Safety

Default mode is **paper**. Not financial advice. Educational / research use.
