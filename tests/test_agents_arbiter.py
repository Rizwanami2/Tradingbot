"""
Unit tests for Multi-Agent Voting, Arbiter Consensus, and Symbol Evaluation.
"""

import unittest
from multi_agent_trading_bot import (
    Arbiter,
    TrendAgent,
    LiquidityAgent,
    IndicatorAgent,
    AILearnerAgent,
    NewsAgent,
    MarketDataFeed,
    evaluate_symbol,
    Dir,
)
from risk_manager import RiskManager


class TestAgentsAndArbiter(unittest.TestCase):
    def setUp(self):
        self.feed = MarketDataFeed(symbol="XAUUSD", source="demo")
        self.data = self.feed.multi_tf(limit_1m=200)
        self.df1 = self.data["1m"]
        self.daily = self.data["daily"]
        self.risk = RiskManager(equity=10000.0, symbol="XAUUSD")

    def test_trend_agent(self):
        agent = TrendAgent()
        vote = agent.vote(self.df1, self.data.get("5m"), self.data.get("15m"), self.data.get("1h"))
        self.assertEqual(vote.agent, "TREND")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))

    def test_liquidity_agent(self):
        agent = LiquidityAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "LIQUIDITY")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))

    def test_indicator_agent(self):
        agent = IndicatorAgent()
        vote = agent.vote(self.df1)
        self.assertEqual(vote.agent, "INDICATORS")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))

    def test_ai_learner_agent(self):
        agent = AILearnerAgent(path="test_temp_memory.json")
        vote = agent.vote(self.df1)
        self.assertEqual(vote.agent, "AI-LEARNER")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
        import os
        if os.path.exists("test_temp_memory.json"):
            os.remove("test_temp_memory.json")

    def test_news_agent(self):
        agent = NewsAgent(enabled=False)
        vote = agent.vote()
        self.assertEqual(vote.agent, "NEWS")
        self.assertEqual(vote.direction, Dir.FLAT)

    def test_evaluate_symbol(self):
        res = evaluate_symbol("XAUUSD", source="demo")
        self.assertEqual(res["symbol"], "XAUUSD")
        self.assertGreater(res["price"], 0.0)
        self.assertIn("votes", res)
        self.assertEqual(len(res["votes"]), 5)


if __name__ == "__main__":
    unittest.main()
