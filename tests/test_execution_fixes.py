"""
Unit tests for execution safety helpers and fail-closed broker routing.
"""

import unittest
from unittest.mock import MagicMock

from execution import (
    BrokerConfigurationError,
    ExecutionError,
    ExecutionStatus,
    OrderResult,
    OrderStateUnknownError,
    PaperBroker,
    UnknownSymbolError,
    classify_order_response,
    close_order_params,
    create_broker,
    create_broker_fail_closed,
    fetch_positions_or_raise,
    require_confirmed_fill,
    resolve_ccxt_symbol,
)


class TestExecutionFixes(unittest.TestCase):
    def test_create_broker_fail_closed(self):
        # 1. Paper mode succeeds
        broker = create_broker_fail_closed(
            "paper",
            api_key=None,
            api_secret=None,
            exchange_id="binance",
            paper_factory=lambda: PaperBroker(),
            live_factory=lambda eid, k, s: None,
        )
        self.assertIsInstance(broker, PaperBroker)

        # 2. Live mode without credentials fails closed
        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed(
                "live",
                api_key=None,
                api_secret=None,
                exchange_id="binance",
                paper_factory=lambda: PaperBroker(),
                live_factory=lambda eid, k, s: None,
            )

        # 3. Live mode with initialization failure raises BrokerConfigurationError
        def failing_live_factory(eid, k, s):
            raise ConnectionError("Exchange offline")

        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed(
                "live",
                api_key="key123",
                api_secret="sec123",
                exchange_id="binance",
                paper_factory=lambda: PaperBroker(),
                live_factory=failing_live_factory,
            )

        # 4. Invalid mode raises BrokerConfigurationError
        with self.assertRaises(BrokerConfigurationError):
            create_broker_fail_closed(
                "sandbox_invalid",
                api_key=None,
                api_secret=None,
                exchange_id="binance",
                paper_factory=lambda: PaperBroker(),
                live_factory=lambda eid, k, s: None,
            )

    def test_create_broker_wrapper(self):
        # Default paper mode
        broker = create_broker(broker_mode="paper")
        self.assertIsInstance(broker, PaperBroker)

        # Live mode missing credentials fails closed
        with self.assertRaises(BrokerConfigurationError):
            create_broker(broker_mode="live", api_key="", api_secret="")

    def test_resolve_ccxt_symbol(self):
        mock_exchange = MagicMock()
        mock_exchange.markets = {
            "BTC/USDT": {"active": True, "symbol": "BTC/USDT"},
            "LTC/USDT": {"active": False, "symbol": "LTC/USDT"},
            "PAXG/USDT": {"active": True, "symbol": "PAXG/USDT"},
        }

        # Exact match
        self.assertEqual(resolve_ccxt_symbol(mock_exchange, "BTC/USDT"), "BTC/USDT")

        # Inferred candidate match
        self.assertEqual(resolve_ccxt_symbol(mock_exchange, "BTCUSDT"), "BTC/USDT")

        # Inactive market
        with self.assertRaises(UnknownSymbolError):
            resolve_ccxt_symbol(mock_exchange, "LTC/USDT")

        # Nonexistent market
        with self.assertRaises(UnknownSymbolError):
            resolve_ccxt_symbol(mock_exchange, "DOGE/USDT")

        # Empty symbol
        with self.assertRaises(UnknownSymbolError):
            resolve_ccxt_symbol(mock_exchange, "")

        # Unloaded markets
        mock_exchange_empty = MagicMock()
        mock_exchange_empty.markets = {}
        with self.assertRaises(BrokerConfigurationError):
            resolve_ccxt_symbol(mock_exchange_empty, "BTC/USDT")

    def test_classify_order_response(self):
        # 1. Closed and fully filled
        res_filled = classify_order_response(
            {"id": "ord-1", "status": "closed", "filled": 2.0, "average": 2650.0},
            symbol="BTC/USDT",
            side="buy",
            requested_qty=2.0,
        )
        self.assertEqual(res_filled.status, ExecutionStatus.FILLED)
        self.assertEqual(res_filled.filled_qty, 2.0)
        self.assertEqual(res_filled.average_price, 2650.0)

        # 2. Closed but partially filled
        res_partial = classify_order_response(
            {"id": "ord-2", "status": "closed", "filled": 1.0, "average": 2650.0},
            symbol="BTC/USDT",
            side="buy",
            requested_qty=2.0,
        )
        self.assertEqual(res_partial.status, ExecutionStatus.PARTIALLY_FILLED)

        # 3. Open / pending
        res_open = classify_order_response(
            {"id": "ord-3", "status": "open", "filled": 0.0},
            symbol="BTC/USDT",
            side="buy",
            requested_qty=2.0,
        )
        self.assertEqual(res_open.status, ExecutionStatus.PENDING)

        # 4. Cancelled / rejected
        res_cancel = classify_order_response(
            {"id": "ord-4", "status": "canceled"},
            symbol="BTC/USDT",
            side="sell",
            requested_qty=1.0,
        )
        self.assertEqual(res_cancel.status, ExecutionStatus.REJECTED)

        # 5. Missing / unknown status
        res_unk = classify_order_response(
            {"id": "ord-5"},
            symbol="BTC/USDT",
            side="buy",
            requested_qty=1.0,
        )
        self.assertEqual(res_unk.status, ExecutionStatus.UNKNOWN)

    def test_require_confirmed_fill(self):
        valid = OrderResult(
            order_id="123",
            status=ExecutionStatus.FILLED,
            symbol="BTC/USDT",
            side="buy",
            requested_qty=1.0,
            filled_qty=1.0,
            average_price=2650.0,
            raw={},
        )
        self.assertEqual(require_confirmed_fill(valid), valid)

        partial = OrderResult(
            order_id="124",
            status=ExecutionStatus.PARTIALLY_FILLED,
            symbol="BTC/USDT",
            side="buy",
            requested_qty=1.0,
            filled_qty=0.5,
            average_price=2650.0,
            raw={},
        )
        with self.assertRaises(OrderStateUnknownError):
            require_confirmed_fill(partial)

        no_price = OrderResult(
            order_id="125",
            status=ExecutionStatus.FILLED,
            symbol="BTC/USDT",
            side="buy",
            requested_qty=1.0,
            filled_qty=1.0,
            average_price=None,
            raw={},
        )
        with self.assertRaises(OrderStateUnknownError):
            require_confirmed_fill(no_price)

    def test_fetch_positions_or_raise(self):
        mock_ex = MagicMock()
        mock_ex.fetch_positions.return_value = [{"symbol": "BTC/USDT", "contracts": 1.0}]
        pos = fetch_positions_or_raise(mock_ex)
        self.assertEqual(len(pos), 1)

        mock_ex.fetch_positions.return_value = None
        with self.assertRaises(OrderStateUnknownError):
            fetch_positions_or_raise(mock_ex)

        mock_ex.fetch_positions.side_effect = RuntimeError("Network timeout")
        with self.assertRaises(OrderStateUnknownError):
            fetch_positions_or_raise(mock_ex)

    def test_close_order_params(self):
        mock_ex = MagicMock()
        mock_ex.features = {
            "future": {"createOrder": {"reduceOnly": True}},
        }
        params = close_order_params(mock_ex)
        self.assertEqual(params, {"reduceOnly": True})

        mock_ex_no_reduce = MagicMock()
        mock_ex_no_reduce.features = {"future": {"createOrder": {"reduceOnly": False}}}
        with self.assertRaises(BrokerConfigurationError):
            close_order_params(mock_ex_no_reduce)

    def test_paper_broker_position_management(self):
        broker = PaperBroker(initial_balance=10000.0)
        order = broker.place_order("XAUUSD", "buy", 1.0, price=2650.0)
        self.assertEqual(order.status, "filled")
        self.assertEqual(len(broker.get_positions()), 1)

        # Close position at 2660 (+10 pnl)
        closed = broker.close_position("XAUUSD", qty=1.0, price=2660.0)
        self.assertTrue(closed)
        self.assertEqual(len(broker.get_positions()), 0)
        self.assertGreater(broker.balance, 10005.0)


if __name__ == "__main__":
    unittest.main()
