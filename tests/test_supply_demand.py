#!/usr/bin/env python3
"""
Unit tests for Institutional Supply & Demand Strategy.
"""
import unittest
import pandas as pd
import numpy as np

from supply_demand_strategy import (
    SupplyDemandAgent,
    SDDir,
    SDVote,
    extract_supply_demand_zones,
    supply_demand_vote,
)


class TestSupplyDemandStrategy(unittest.TestCase):
    def test_edge_cases(self):
        agent = SupplyDemandAgent()
        v_none = agent.vote(None)
        self.assertEqual(v_none.direction, SDDir.FLAT)
        self.assertEqual(v_none.reason, "Need more bars")

        v_empty = agent.vote(pd.DataFrame())
        self.assertEqual(v_empty.direction, SDDir.FLAT)
        self.assertEqual(v_empty.reason, "Need more bars")

        v_missing = agent.vote(pd.DataFrame({"close": range(30)}))
        self.assertEqual(v_missing.direction, SDDir.FLAT)
        self.assertEqual(v_missing.reason, "Missing OHLC columns")

    def test_demand_zone_tap(self):
        # Construct DBR (Drop-Base-Rally):
        # 1. Drop candle: open 2655, close 2650
        # 2. Base candle: open 2650, close 2650.5, high 2651, low 2649
        # 3. Rally departure: open 2650.5, close 2658, high 2658.5, low 2650
        # 4. Continuation: 2658 to 2662
        # 5. Pullback retesting base (2651 to 2649.5) with lower wick
        data = []
        for i in range(10):
            data.append({"open": 2654.0, "high": 2655.0, "low": 2653.0, "close": 2654.0, "volume": 100})
        # Drop
        data.append({"open": 2655.0, "high": 2655.5, "low": 2649.8, "close": 2650.0, "volume": 250})
        # Base
        data.append({"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.5, "volume": 80})
        # Rally departure
        data.append({"open": 2650.5, "high": 2658.5, "low": 2650.0, "close": 2658.0, "volume": 450})
        # Peak
        data.append({"open": 2658.0, "high": 2661.0, "low": 2657.0, "close": 2660.0, "volume": 200})
        # Pullback into demand zone
        data.append({"open": 2660.0, "high": 2660.5, "low": 2654.0, "close": 2655.0, "volume": 150})
        # Tap demand zone [2649.0 - 2651.0] with lower wick rejection
        data.append({"open": 2655.0, "high": 2655.5, "low": 2650.2, "close": 2653.5, "volume": 300})
        # Bounce confirmation
        data.append({"open": 2653.5, "high": 2656.0, "low": 2653.0, "close": 2655.5, "volume": 280})

        df = pd.DataFrame(data)
        zones = extract_supply_demand_zones(df)
        self.assertGreater(len(zones), 0)

        agent = SupplyDemandAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "SUPPLY-DEMAND")
        self.assertIn(vote.direction, (SDDir.LONG, SDDir.FLAT))

    def test_supply_zone_tap(self):
        # Construct RBD (Rally-Base-Drop):
        data = []
        for i in range(10):
            data.append({"open": 2645.0, "high": 2646.0, "low": 2644.0, "close": 2645.0, "volume": 100})
        # Rally
        data.append({"open": 2645.0, "high": 2652.0, "low": 2644.5, "close": 2651.0, "volume": 250})
        # Base
        data.append({"open": 2651.0, "high": 2652.0, "low": 2650.0, "close": 2651.5, "volume": 80})
        # Drop departure
        data.append({"open": 2651.5, "high": 2652.0, "low": 2643.0, "close": 2644.0, "volume": 450})
        # Valley
        data.append({"open": 2644.0, "high": 2645.0, "low": 2640.0, "close": 2641.0, "volume": 200})
        # Retrace up toward supply zone
        data.append({"open": 2643.0, "high": 2648.0, "low": 2643.0, "close": 2647.5, "volume": 150})
        # Tap supply zone [2650.0 - 2652.0] with upper wick rejection
        data.append({"open": 2647.5, "high": 2651.5, "low": 2647.0, "close": 2648.0, "volume": 300})
        # Drop continuation confirming supply rejection
        data.append({"open": 2648.0, "high": 2648.5, "low": 2646.0, "close": 2646.5, "volume": 280})

        df = pd.DataFrame(data)
        zones = extract_supply_demand_zones(df)
        self.assertGreater(len(zones), 0)

        agent = SupplyDemandAgent()
        vote = agent.vote(df)
        self.assertEqual(vote.agent, "SUPPLY-DEMAND")
        self.assertIn(vote.direction, (SDDir.SHORT, SDDir.FLAT))

    def test_functional_wrapper(self):
        df = pd.DataFrame({
            "open": [2650.0 + i * 0.1 for i in range(25)],
            "high": [2650.5 + i * 0.1 for i in range(25)],
            "low": [2649.5 + i * 0.1 for i in range(25)],
            "close": [2650.2 + i * 0.1 for i in range(25)],
        })
        v = supply_demand_vote(df)
        self.assertIsInstance(v, SDVote)
        self.assertEqual(v.agent, "SUPPLY-DEMAND")


if __name__ == "__main__":
    unittest.main()
