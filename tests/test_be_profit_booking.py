"""
Tests for Break-Even Profit Booking:
When Stop Loss is advanced to Break-Even, the system books partial profit (default 33%)
so that traders lock in cash and leave the remaining position running completely risk-free.
"""

import unittest
from unittest.mock import MagicMock, patch
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager, RiskConfig


class TestBreakEvenProfitBooking(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.rm.config.auto_be = True
        self.rm.config.auto_be_take_profit = True
        self.rm.config.be_close_pct = 0.33
        self.rm.config.be_close_stage = 1
        self.engine = ExecutionEngine(risk_manager=self.rm)

    def test_progressive_auto_be_books_profit(self):
        """Verify that when SL moves to Break-Even, partial profit is automatically booked."""
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.03,
            "size": 3.0,
            "leverage": 20.0,
            "margin_required": 75.0,
            "liquidation_price": 2620.0,
            "risk_amount": 30.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        # Execute setup
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        pos = self.engine.active_positions["XAUUSD"]
        self.assertEqual(pos.remaining_lots, 0.03)
        self.assertEqual(pos.realized_pnl, 0.0)
        self.assertFalse(pos.be_profit_booked)

        # Candle reaches +0.6R (2656.0), triggering Auto Break-Even (Stage 1 lock @ 2651.0)
        candle_be = {"open": 2650.0, "high": 2656.0, "low": 2650.0, "close": 2655.0}
        events = self.engine.update_market_candle("XAUUSD", candle_be)

        # Verify AUTO_BE_LOCKED and BE_PROFIT_BOOKED events emitted
        self.assertTrue(any(ev["event"] == "AUTO_BE_LOCKED" for ev in events))
        self.assertTrue(any(ev["event"] == "BE_PROFIT_BOOKED" for ev in events))

        # Stop loss moved past entry
        self.assertGreaterEqual(pos.stop_loss, 2651.0)
        self.assertTrue(pos.be_profit_booked)

        # 33% of 0.03 lots = 0.01 lots closed at 2655.0 (+5 pts profit)
        self.assertAlmostEqual(pos.remaining_lots, 0.02, places=3)
        self.assertGreater(pos.realized_pnl, 0.0)

        # Verify broker close was executed
        close_events = [ev for ev in events if ev["event"] == "BE_PROFIT_BOOKED"]
        self.assertEqual(len(close_events), 1)
        self.assertIn("Profit Booked on BE", close_events[0]["order"].close_reason or events[0]["message"])

    def test_be_profit_booked_runner_hits_tp1(self):
        """Verify that after booking BE profit, the remaining runner can still hit TP1."""
        sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2650.0,
            "stop": 2640.0,
            "tp1": 2665.0,
            "tp2": 2675.0,
            "lots": 0.04,
            "size": 4.0,
            "leverage": 20.0,
            "margin_required": 100.0,
            "liquidation_price": 2620.0,
            "risk_amount": 40.0,
            "rr": 1.5,
        }
        self.engine.register_setup(sig)
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.0})

        # 1. Price moves to +0.8R (2658.0) -> Books BE profit
        self.engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2658.0, "low": 2650.0, "close": 2657.0})
        pos = self.engine.active_positions["XAUUSD"]
        self.assertTrue(pos.be_profit_booked)
        be_pnl = pos.realized_pnl
        self.assertGreater(be_pnl, 0.0)
        rem_lots = pos.remaining_lots

        # 2. Later candle rallies to TP1 (2665.0)
        events_tp1 = self.engine.update_market_candle("XAUUSD", {"open": 2658.0, "high": 2666.0, "low": 2657.0, "close": 2664.0})
        self.assertTrue(any(ev["event"] == "TP1_PARTIAL_TAKEN" for ev in events_tp1))
        self.assertEqual(pos.partials_done, 1)
        self.assertGreater(pos.realized_pnl, be_pnl)
        self.assertLess(pos.remaining_lots, rem_lots)

    def test_adverse_profit_fade_be_snap_books_profit(self):
        """Verify that when Adverse Trade Manager snaps SL to BE on profit fade, it books profit."""
        from adverse_trade_manager import get_adverse_trade_manager, AdverseAction

        pos = TradeOrder(
            id="TEST-FADE-1",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2675.0,
            tp2=2685.0,
            tp3=None,
            lots=0.04,
            size=4.0,
            leverage=20.0,
            margin_required=100.0,
            liquidation_price=2620.0,
            risk_amount=40.0,
            rr_ratio=2.5,
            setup_reason="Adverse profit fade test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=4.0,
            remaining_lots=0.04,
            highest_price=2658.0,  # Peak was +0.80R
            lowest_price=2650.0,
        )
        pos.peak_r = 0.80  # Peak reached +0.80R
        self.engine.active_positions[pos.id] = pos

        # Price fades back to 2650.8 (+0.08R, below 0.12R threshold)
        candle = {"open": 2653.0, "high": 2653.0, "low": 2650.5, "close": 2650.8}
        ev = self.engine.evaluate_adverse_trade(pos, candle, curr_price=2650.8)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event"], "PROFIT_FADE_PROTECT")
        self.assertGreaterEqual(pos.stop_loss, 2650.0)
        self.assertTrue(pos.be_profit_booked)
        self.assertGreater(pos.realized_pnl, 0.0)
        self.assertLess(pos.remaining_lots, 0.04)


if __name__ == "__main__":
    unittest.main()
