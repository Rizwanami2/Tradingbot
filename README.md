# Institutional Multi-Agent Algorithmic Trading Bot

An institutional-grade algorithmic trading bot combining a **5-Agent Democratic Consensus Engine**, an **Institutional Risk Manager**, **Google Gemini AI Assistant**, and an **Interactive Telegram Interface**.

Designed for Gold (`XAUTUSD`, `XAUUSD`), Crypto (`BTCUSD`, `ETHUSD`, `SOLUSD`), and Forex pairs.

---

## Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │          Market Data Feeds             │
                      │ (TradingView / CCXT / yfinance / Demo) │
                      └──────────────────┬─────────────────────┘
                                         │
        ┌───────────────┬────────────────┼───────────────┬────────────────┐
        ▼               ▼                ▼               ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Trend Agent  │ │Liquidity SMC │ │Indicator Pro │ │  AI-Learner  │ │  News Agent  │
│ (1m/5m/15m/1h│ │(Sweeps & BOS)│ │(10 Indicators│ │ (Momentum +  │ │(ForexFactory │
│  Alignment)  │ │              │ │  Confluence) │ │ Self-Learning│ │Calendar+RSS) │
└───────┬──────┘ └───────┬──────┘ └───────┬──────┘ └───────┬──────┘ └───────┬──────┘
        │                │                │                │                │
        └────────────────┴───────┬────────┴────────────────┴────────────────┘
                                 │ Democratic Votes
                                 ▼
                     ┌───────────────────────┐      Veto Check
                     │        Arbiter        │ ◄───────────────────┐
                     │ (Consensus >= 3 Agree │                     │
                     │  Score >= 1.8 | RR>=2)│          ┌──────────┴──────────┐
                     └───────────┬───────────┘          │    Risk Manager     │
                                 │                      │ • Auto Break-Even   │
                                 ▼ Trade Signal         │ • Progressive Trail │
                     ┌───────────────────────┐          │ • Dynamic Lot Size  │
                     │  Execution & Alerts   │          │ • Drawdown Breakers │
                     │ • Telegram Bot Alerts │          └─────────────────────┘
                     │ • Live Order Tracking │
                     │ • Gemini AI Assistant │
                     └───────────────────────┘
```

---

## Core Components

### 1. Specialized Agents
* **Trend Agent** (`TrendAgent`): Analyzes market structure across multiple timeframes (`1m`, `5m`, `15m`, `1h`) using EMA alignment and swing highs/lows.
* **Liquidity Agent** (`LiquidityAgent`): Implements Smart Money Concepts (SMC/ICT), identifying Previous Day High (PDH) and Low (PDL) sweeps, rejection pinbars, and Break of Structure (BOS) continuation.
* **Indicator Agent** (`IndicatorAgent`): Computes a 10-indicator composite confirmation score from `trading_strategy_indicators_pro.py`:
  * RSI (14) + Regular & Hidden Divergence Detection
  * EMA Structure (9, 21, 50)
  * MACD (12, 26, 9) Histogram & Signal
  * ADX (14) & Directional Movement (+DI / -DI) Trend Strength
  * Bollinger Bands (20, 2) Volatility & Squeeze
  * Stochastic Oscillator (14, 3, 3)
  * VWAP Distance
  * Supertrend (ATR-based trend direction)
  * Commodity Channel Index (CCI 20)
  * Volume Oscillator
* **AI-Learner Agent** (`AILearnerAgent`): Adaptive polynomial slope and momentum predictor with persistent memory (`multi_agent_memory.json`). Self-calibrates confidence and weights after each trade outcome.
* **News Agent** (`NewsAgent`):
  * **Macro Calendar:** Real-time ForexFactory economic calendar tracking high-impact USD events (FOMC, CPI, NFP, GDP, Fed speeches). Enforces a hard **blackout veto** $\pm 30$ minutes around events.
  * **Crypto RSS:** Sentiment extraction from top crypto feeds (CoinDesk, Cointelegraph) with emergency shock detection (hacks, bans, SEC charges).

### 2. Institutional Risk Manager (`risk_manager.py`)
* **Progressive Auto Break-Even:**
  * At **+0.5R**: Locks in +0.1R early profit to eliminate downside risk.
  * At **+1.0R**: Advances stop loss to full Break-Even (+0.25R buffer).
* **Dynamic Trailing Stops:** Trails profitable runners after TP1 using ATR or R-multiple bands.
* **Risk Factor (RF) Auto-Scaling:** Auto-adjusts position risk based on current win/loss streaks and statistical expectancy (scales between 0.50x and 1.50x).
* **Precision Lot Sizing:** Instrument-aware lot calculations factoring in contract sizes, pip values, and broker lot steps across:
  * Metals: `XAUUSD`, `GOLD`, `GC=F`
  * Crypto: `BTCUSD`, `BTCUSDT`, `ETHUSDT`, `XAUUSDT`
  * Forex: `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCAD`, `USDCHF`
* **Circuit Breakers:** Daily loss limit cap (default 1.5%), weekly loss cap (4.0%), and a 75-minute cooldown following 3 consecutive losses. State is saved to `risk_state.json`.

### 3. Gemini AI Assistant (`gemini_client.py`)
* Connects directly to Google's official Gemini REST API (`gemini-2.5-flash`, `gemini-1.5-flash`).
* Zero SDK dependency issues across Python versions.
* Injects real-time live prices, market context, and indicator scores into conversational turns.
* **Intelligent Fallback:** If `GEMINI_API_KEY` is not provided, the bot uses a built-in rule-based technical analysis engine to provide trade planning, stop calculations, and risk advice.

---

## Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome dashboard, bot status, and quick menu |
| `/help` | Detailed command reference and system guide |
| `/price [SYMBOL]` | Live price, 24h change & technical rating from TradingView (e.g. `/price XAUTUSD`, `/price BTCUSD`) |
| `/signal [SYMBOL]` | Runs real-time 5-agent evaluation and prints consensus + individual agent votes |
| `/status` | Current equity, peak equity, active mode (normal/recovery/aggressive), and RF |
| `/risk` | Risk management report, drawdown metrics, and recommended lot sizing |
| `/news` | High-impact economic calendar events and crypto headline sentiment |
| `/positions` | Current open trades, trailing stop levels, and partial take-profits |
| `/autotrade on` | Activates background live candle monitoring and automated trade alerts |
| `/autotrade off` | Pauses background automated alerts |
| `/backtest [bars]` | Runs an on-demand historical simulation (e.g. `/backtest 2000`) |
| `/reset` | Clears conversation memory |
| *Any text* | Natural chat with the Gemini AI trading assistant |

---

## Installation & Setup

### 1. Clone & Dependencies
Ensure Python 3.8+ is installed:
```bash
pip install -r requirements.txt # or install: requests pandas numpy ccxt python-telegram-bot
```

### 2. Configure Environment (`.env`)
Copy the sample environment file:
```bash
cp .env.example .env
```

Edit `.env` with your tokens:
```ini
# Telegram Token from @BotFather
TELEGRAM_TOKEN=123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ

# Telegram Chat ID (for automated alert dispatch)
TELEGRAM_CHAT_ID=123456789

# Google Gemini API Key from aistudio.google.com
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-2.5-flash

# Default Trading Symbol & Settings
BOT_SYMBOL=XAUTUSD
BOT_DATA_SOURCE=tradingview
BOT_EQUITY=10000.0
BOT_RISK=0.18
```

---

## Usage

### Option 1: Run Telegram Bot
```bash
python bot.py telegram
# or directly:
python main.py
```

### Option 2: Run Terminal Interactive CLI (No Telegram token required)
Test all bot commands (`/price`, `/signal`, `/risk`, `/status`, `/backtest`, chat) directly in the console:
```bash
python bot.py cli
# or:
python main.py --cli
```

### Option 3: Run Multi-Agent Live Trader (Standalone CLI)
```bash
python bot.py live --symbol XAUTUSD --source tradingview --poll 30
# or:
python multi_agent_trading_bot.py live --symbol XAUTUSD --source tradingview
```

### Option 4: Run Strategy Backtest
```bash
python bot.py backtest --bars 3000 --symbol XAUTUSD
# or:
python multi_agent_trading_bot.py backtest --bars 3000
```

### Option 5: Run Everything Concurrently
Runs the Telegram Bot and background live trader simultaneously:
```bash
python bot.py all
```

---

## Running Unit Tests

Run the full automated test suite covering risk calculations, indicator formulas, agent voting, arbiter consensus, and Gemini AI:
```bash
PYTHONPATH=. pytest tests/ -v
```

All 18 unit tests pass out-of-the-box:
* `tests/test_risk_manager.py` — Lot sizing, auto break-even (+0.5R/+1.0R), state persistence, risk limits
* `tests/test_indicators.py` — RSI, divergence, EMAs, MACD, ADX, Bollinger Bands, Supertrend, AI predictor
* `tests/test_agents_arbiter.py` — Trend, Liquidity, Indicator, AI-Learner, News agents, and Arbiter decision
* `tests/test_gemini_client.py` — Chat session management, trade setup generation, fallback intelligence

---

## File Structure

```
├── .env.example                    # Configuration template
├── .gitignore                      # Git ignore rules
├── README.md                       # Documentation
├── bot.py                          # Unified master CLI launcher
├── config.py                       # Environment and .env loader
├── gemini_client.py                # Gemini AI REST client & fallback intelligence
├── main.py                         # Interactive Telegram bot & CLI shell
├── multi_agent_trading_bot.py      # Multi-agent engine & arbiter
├── risk_manager.py                 # Advanced risk manager & lot sizing
├── trading_strategy_indicators_pro.py # 10-indicator technical engine & SMC setups
└── tests/
    ├── test_agents_arbiter.py      # Tests for agents and arbiter
    ├── test_gemini_client.py       # Tests for Gemini assistant
    ├── test_indicators.py          # Tests for technical indicators
    └── test_risk_manager.py        # Tests for risk management module
```

---

*Disclaimer: Educational and algorithmic trading software. Trading financial instruments carries risk. Never risk capital you cannot afford to lose.*