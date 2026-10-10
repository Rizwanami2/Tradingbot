# Improved Modules for Rizwanami2/Tradingbot

Production-ready improved modules that fix the main weaknesses of the original project.

## Files

| File | Purpose |
|------|---------|
| `data_feed.py` | Real multi-source data feed (CCXT → yfinance → demo) + Parquet caching |
| `execution.py` | PaperBroker + CCXTBroker with journal, position tracking, SL/TP support |
| `ai_learner.py` | Upgraded AI-Learner with feature vector + online weight updates |
| `news_agent.py` | Improved News Agent with better calendar filtering & sentiment |
| `backtest_engine.py` | Realistic backtester with commission, slippage, Sharpe, Sortino, PF |
| `requirements_improved.txt` | Updated dependencies |

## Quick Start

```bash
pip install -r requirements_improved.txt
python data_feed.py
python execution.py
python ai_learner.py
python news_agent.py
python backtest_engine.py --symbol XAUUSD --bars 2000 --source demo
```

## Integration

1. Place these files in the root of the repository.
2. In `multi_agent_trading_bot.py`:
   - `from data_feed import DataFeed`
   - `from ai_learner import AILearnerAgent`
   - `from news_agent import NewsAgent`
3. Use `from execution import create_broker` for paper/live trading.

Educational use only. Trading involves substantial risk of loss.
