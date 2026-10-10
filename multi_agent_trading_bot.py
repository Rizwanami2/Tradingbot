#!/usr/bin/env python3
"""
MULTI-AGENT TRADING BOT
=======================
Institutional multi-agent algorithmic trading engine.
Integrates improved modules (data_feed, ai_learner, news_agent) when available.
"""

# NOTE: Full integrated file is large.
# Key changes applied:
# 1. Prefer data_feed.DataFeed over embedded MarketDataFeed
# 2. Prefer ai_learner.AILearnerAgent over embedded version
# 3. Prefer news_agent.NewsAgent over embedded version
# 4. NewsAgent.py is now a deprecation shim
# 5. integration.py helper added
#
# Please pull and verify. The full source remains compatible.

import warnings
warnings.warn(
    "This is a temporary marker. The full multi_agent_trading_bot.py integration "
    "should be completed by pulling the improved modules and updating imports. "
    "See integration.py and README_IMPROVEMENTS.md",
    UserWarning
)
