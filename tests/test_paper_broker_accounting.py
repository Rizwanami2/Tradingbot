"""Regression tests for paper-broker accounting, validation, and restart recovery."""

import json
import tempfile
import unittest
from pathlib import Path

from execution import PaperBroker


class TestPaperBrokerAccounting(unittest.TestCase):
    def make_broker(self, balance=10000.0, fee=0.001):
        return PaperBroker(initial_balance=balance, commission_pct=fee, journal_file=None)

    def test_order_requires_explicit_positive_price_and_quantity(self):
        broker = self.make_broker()
        for kwargs in (
            {"qty": 1, "price": None},
            {"qty": 0, "price": 100},
            {"qty": -1, "price": 100},
            {"qty": float("nan"), "price": 100},
            {"qty": 1, "price": float("inf")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                broker.place_order("BTC/USDT", "buy", kwargs["qty"], price=kwargs["price"])

    def test_open_close_accounts_for_pnl_and_both_side_fees(self):
        broker = self.make_broker(balance=10000, fee=0.001)
        broker.place_order("BTC/USDT", "buy", 2, price=100)
        # Entry fee: 2 * 100 * 0.1% = 0.20
        self.assertAlmostEqual(broker.balance, 9999.8)
        self.assertAlmostEqual(broker.equity, 9999.8)
        self.assertTrue(broker.close_position("BTC/USDT", price=110))
        # Gross realized P&L = 20; exit fee = 0.22.
        self.assertAlmostEqual(broker.balance, 10019.58)
        self.assertAlmostEqual(broker.equity, 10019.58)
        self.assertEqual(broker.get_positions(), [])
        self.assertEqual(broker.get_account_summary()["open_positions"], 0)

    def test_partial_close_realizes_only_closed_quantity(self):
        broker = self.make_broker(fee=0)
        broker.place_order("PAXG/USDT", "buy", 4, price=50)
        self.assertTrue(broker.close_position("PAXG/USDT", qty=1, price=60))
        position = broker.positions["PAXG/USDT"]
        self.assertAlmostEqual(position.qty, 3)
        self.assertAlmostEqual(broker.balance, 10010)
        # Remaining 3 units are marked at 60: +30 unrealized.
        self.assertAlmostEqual(broker.equity, 10040)

    def test_opposite_order_cannot_flip_position(self):
        broker = self.make_broker(fee=0)
        broker.place_order("BTC/USDT", "buy", 1, price=100)
        with self.assertRaisesRegex(ValueError, "position flips are disabled"):
            broker.place_order("BTC/USDT", "sell", 1.1, price=101)
        self.assertEqual(broker.positions["BTC/USDT"].side, "buy")
        self.assertAlmostEqual(broker.positions["BTC/USDT"].qty, 1)

    def test_mark_price_updates_equity_without_realizing_pnl(self):
        broker = self.make_broker(fee=0)
        broker.place_order("BTC/USDT", "buy", 2, price=100)
        equity = broker.mark_price("BTC/USDT", 107)
        self.assertAlmostEqual(equity, 10014)
        self.assertAlmostEqual(broker.balance, 10000)
        self.assertAlmostEqual(broker.positions["BTC/USDT"].unrealized_pnl, 14)

    def test_state_survives_restart_with_positions_and_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "paper-state.json")
            first = PaperBroker(initial_balance=1234, commission_pct=0.0005,
                                state_file=state, journal_file=None)
            first.place_order("BTC/USDT", "buy", 0.5, price=200)
            first.mark_price("BTC/USDT", 210)

            restored = PaperBroker(initial_balance=999, state_file=state, journal_file=None)
            self.assertAlmostEqual(restored.balance, first.balance)
            self.assertAlmostEqual(restored.equity, first.equity)
            self.assertEqual(restored.positions["BTC/USDT"].qty, 0.5)
            self.assertEqual(len(restored.orders), 1)
            self.assertAlmostEqual(restored.mark_prices["BTC/USDT"], 210)
            self.assertAlmostEqual(restored.get_account_summary()["realized_pnl_net_fees"],
                                   first.balance - 1234)

    def test_corrupt_persisted_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "paper-state.json"
            state.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not safely load"):
                PaperBroker(state_file=str(state), journal_file=None)

    def test_invalid_initial_balance_and_fee_are_rejected(self):
        with self.assertRaises(ValueError):
            PaperBroker(initial_balance=float("nan"))
        with self.assertRaises(ValueError):
            PaperBroker(commission_pct=-0.1)


if __name__ == "__main__":
    unittest.main()
