"""
Unit tests for Multi-Agent Voting, Arbiter Consensus, and Symbol Evaluation.
"""

import unittest
from multi_agent_trading_bot import (
    TrendAgent,
    LiquidityAgent,
    IndicatorAgent,
    AILearnerAgent,
    NewsAgent,
    MT5ChartAgent,
    MarketDataFeed,
    evaluate_symbol,
    Dir,
)
from gautam_jha_strategy import GautamJhaAgent, GJDir
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

    def test_gautam_jha_agent(self):
        agent = GautamJhaAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "GAUTAM-JHA")
        self.assertIn(vote.direction, (GJDir.LONG, GJDir.SHORT, GJDir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

        # Edge cases: None, empty DataFrame, missing columns
        import pandas as pd
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, GJDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, GJDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"foo": range(40)}))
        self.assertEqual(v_missing.direction, GJDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_mean_reversion_agent(self):
        from mean_reversion_vwap_strategy import MeanReversionVWAPAgent, MRDir
        agent = MeanReversionVWAPAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "MEAN-REVERSION")
        self.assertIn(vote.direction, (MRDir.LONG, MRDir.SHORT, MRDir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

    def test_fake_breakout_agent(self):
        from fake_breakout_strategy import FakeBreakoutAgent, FBDir
        import pandas as pd
        agent = FakeBreakoutAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "FAKE-BREAKOUT")
        self.assertIn(vote.direction, (FBDir.LONG, FBDir.SHORT, FBDir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

        # Edge cases: None, empty DataFrame
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, FBDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, FBDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

    def test_mtf_confluence_agent(self):
        from mtf_fvg_ob import MTFConfluenceAgent, MTFDir
        import pandas as pd
        agent = MTFConfluenceAgent()
        vote = agent.vote(self.df1, df15=self.data.get("15m"), df1h=self.data.get("1h"))
        self.assertEqual(vote.agent, "MTF-CONFLUENCE")
        self.assertIn(vote.direction, (MTFDir.BULLISH, MTFDir.BEARISH, MTFDir.NEUTRAL))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

        # Edge cases: None, empty DataFrame
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, MTFDir.NEUTRAL)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, MTFDir.NEUTRAL)
        self.assertEqual(v_empty.reason, "Need more bars")

    def test_asian_breakout_agent(self):
        from asian_range_breakout import AsianBreakoutAgent, AsianDir
        import pandas as pd
        agent = AsianBreakoutAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "ASIAN-BREAKOUT")
        self.assertIn(vote.direction, (AsianDir.LONG, AsianDir.SHORT, AsianDir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

        # Edge cases: None, empty DataFrame
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, AsianDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, AsianDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        # Robustness: DataFrame with 'time' column instead of DatetimeIndex
        df_copy = self.df1.copy().reset_index()
        if "index" in df_copy.columns:
            df_copy.rename(columns={"index": "time"}, inplace=True)
        self.assertFalse(isinstance(df_copy.index, pd.DatetimeIndex))
        v_col_idx = agent.vote(df_copy)
        self.assertEqual(v_col_idx.agent, "ASIAN-BREAKOUT")
        self.assertIn(v_col_idx.direction, (AsianDir.LONG, AsianDir.SHORT, AsianDir.FLAT))

    def test_mt5_chart_agent(self):
        agent = MT5ChartAgent()
        vote = agent.vote(self.df1, symbol="XAUUSD")
        self.assertEqual(vote.agent, "MT5-CHART")
        self.assertIn(vote.direction, (Dir.LONG, Dir.SHORT, Dir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

    def test_orr_agent(self):
        from orr_strategy import ORRAgent, ORRDir
        agent = ORRAgent()
        vote = agent.vote(self.df1, self.daily)
        self.assertEqual(vote.agent, "ORR-STRATEGY")
        self.assertIn(vote.direction, (ORRDir.LONG, ORRDir.SHORT, ORRDir.FLAT))
        self.assertGreaterEqual(vote.strength, 0.0)
        self.assertLessEqual(vote.strength, 1.0)

        # Edge cases: None, empty DataFrame
        import pandas as pd
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, ORRDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, ORRDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

    def test_evaluate_symbol(self):
        res = evaluate_symbol("XAUUSD", source="demo")
        self.assertEqual(res["symbol"], "XAUUSD")
        self.assertGreater(res["price"], 0.0)
        self.assertIn("votes", res)
        self.assertIn(len(res["votes"]), (5, 6, 7, 8, 9, 10, 11, 12))
        self.assertIn("mt5_chart", res)

    def test_evaluate_symbol_btc_normalization(self):
        res = evaluate_symbol("BTC", source="demo", on_demand=True)
        self.assertEqual(res["symbol"], "BTCUSDT")
        self.assertGreater(res["price"], 0.0)
        self.assertIn(len(res["votes"]), (5, 6, 7, 8, 9, 10, 11, 12))
        self.assertIn("mt5_chart", res)


if __name__ == "__main__":
    unittest.main()
