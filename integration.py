#!/usr/bin/env python3
"""
Integration helpers - prefer improved modules when available.
Import this early in multi_agent_trading_bot.py or main.py.
"""

# Prefer improved data feed
try:
    from data_feed import DataFeed as MarketDataFeed
    print("[Integration] Using improved data_feed.DataFeed")
except ImportError:
    MarketDataFeed = None  # fall back to embedded class

# Prefer improved AI Learner
try:
    from ai_learner import AILearnerAgent
    print("[Integration] Using improved ai_learner.AILearnerAgent")
except ImportError:
    AILearnerAgent = None

# Prefer improved News Agent
try:
    from news_agent import NewsAgent
    print("[Integration] Using improved news_agent.NewsAgent")
except ImportError:
    NewsAgent = None

# Prefer execution layer
try:
    from execution import create_broker, PaperBroker, CCXTBroker
except ImportError:
    create_broker = PaperBroker = CCXTBroker = None

__all__ = [
    "MarketDataFeed",
    "AILearnerAgent",
    "NewsAgent",
    "create_broker",
    "PaperBroker",
    "CCXTBroker",
]
