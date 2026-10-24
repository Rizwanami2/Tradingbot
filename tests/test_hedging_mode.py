"""
Unit tests verifying Bi-Directional Hedging Mode:
Allowing simultaneous BUY and SELL positions on the same asset (Gold and Bitcoin)
when hedging is enabled, and preserving strict single-direction protection when disabled.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import main
from anti_spam import AntiSpamRateLimiter, get_anti_spam_limiter
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from risk_manager import RiskManager


class TestHedgingMode(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.risk_mgr = RiskManager(equity=10000.0, symbol="XAUUSD")
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        self.limiter = AntiSpamRateLimiter()
        self.limiter.reset()

        # Save original hedging setting
        self.orig_hedge = config.is_hedging_allowed()

    def tearDown(self):
        config.set_hedging_mode(self.orig_hedge, persist=False)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        self.limiter.reset()

    def test_config_hedging_mode_toggle_and_query(self):
        """Test enabling and disabling hedging mode via config functions."""
        config.set_hedging_mode(True, persist=False)
        self.assertTrue(config.is_hedging_allowed())

        config.set_hedging_mode(False, persist=False)
        self.assertFalse(config.is_hedging_allowed())

    def test_engine_allows_simultaneous_buy_and_sell_when_hedging_enabled(self):
        """When hedging is enabled, ExecutionEngine permits both BUY and SELL on XAUUSD."""
        config.set_hedging_mode(True, persist=False)

        # 1. Register and open a LONG on Gold
        buy_sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2720.0,
            "stop": 2710.0,
            "tp1": 2735.0,
            "tp2": 2740.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 10.0,
            "rr": 2.0,
            "is_scalp": False,
        }
        order_buy = self.engine.register_setup(buy_sig, reason="Long setup")
        candle_buy = {"open": 2720.0, "high": 2720.5, "low": 2719.5, "close": 2720.1}
        self.engine.update_market_candle("XAUUSD", candle_buy)
        self.assertEqual(order_buy.status, OrderStatus.OPEN)

        # 2. Register and open a simultaneous SHORT on Gold
        sell_sig = {
            "symbol": "XAUUSD",
            "direction": "SHORT",
            "entry": 2720.0,
            "stop": 2730.0,
            "tp1": 2705.0,
            "tp2": 2700.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 10.0,
            "rr": 2.0,
            "is_scalp": False,
        }
        order_sell = self.engine.register_setup(sell_sig, reason="Hedged short setup")
        candle_sell = {"open": 2720.0, "high": 2720.5, "low": 2719.5, "close": 2720.1}
        self.engine.update_market_candle("XAUUSD", candle_sell)
        self.assertEqual(order_sell.status, OrderStatus.OPEN)

        # Verify BOTH orders exist in active positions simultaneously
        active_orders = list(self.engine.active_positions.values())
        self.assertEqual(len(active_orders), 2)
        directions = {getattr(o, "direction", "").upper() for o in active_orders}
        self.assertIn("LONG", directions)
        self.assertIn("SHORT", directions)

    def test_engine_blocks_opposing_when_hedging_disabled(self):
        """When hedging is disabled, ExecutionEngine blocks opposing SELL when BUY is active."""
        config.set_hedging_mode(False, persist=False)

        buy_sig = {
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry": 2720.0,
            "stop": 2715.0,
            "tp1": 2730.0,
            "tp2": 2735.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 10.0,
            "rr": 2.0,
            "is_scalp": True,
        }
        self.engine.register_setup(buy_sig)
        self.engine.update_market_candle("XAUUSD", {"open": 2720.0, "high": 2722.0, "low": 2719.0, "close": 2720.5})

        sell_sig = {
            "symbol": "XAUUSD",
            "direction": "SHORT",
            "entry": 2725.0,
            "stop": 2730.0,
            "tp1": 2715.0,
            "tp2": 2710.0,
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "risk_amount": 10.0,
            "rr": 2.0,
            "is_scalp": True,
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(sell_sig)
        self.assertIn("cannot hold BUY and SELL at the same time", str(ctx.exception))

    def test_antispam_check_can_enter_hedging_mode(self):
        """AntiSpam rate limiter allows opposite direction under hedging mode."""
        # 1. Setup active LONG on BTC
        p_long = TradeOrder.from_dict({
            "id": "test_btc_long",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_level": 65000.0,
            "stop_loss": 64500.0,
            "tp1": 66000.0,
            "tp2": 67000.0,
            "tp3": None,
            "status": "OPEN",
            "lots": 0.01,
            "size": 0.01,
            "leverage": 20.0,
            "margin_required": 32.5,
            "liquidation_price": 60000.0,
            "risk_amount": 5.0,
            "rr_ratio": 1.0,
            "setup_reason": "Active long",
        })
        self.engine.active_positions[p_long.id] = p_long

        # Hedging OFF: incoming SHORT rejected
        config.set_hedging_mode(False, persist=False)
        can_enter_off, reason_off = self.limiter.check_can_enter(
            symbol="BTCUSDT", current_price=65000.0, engine=self.engine, max_open=6, direction="SHORT"
        )
        self.assertFalse(can_enter_off)
        self.assertIn("cannot BUY and SELL at the same time", reason_off)
        self.assertIn("Strict Single-Direction", self.limiter.get_status_report())

        # Hedging ON: incoming SHORT allowed
        config.set_hedging_mode(True, persist=False)
        can_enter_on, reason_on = self.limiter.check_can_enter(
            symbol="BTCUSDT", current_price=65000.0, engine=self.engine, max_open=6, direction="SHORT"
        )
        self.assertTrue(can_enter_on, reason_on)
        self.assertIn("Bi-Directional Hedging", self.limiter.get_status_report())

    def test_arbiter_decide_allows_hedging_consensus(self):
        """MultiAgentTradingBot Arbiter does not veto opposite direction when hedging is enabled."""
        import pandas as pd
        from multi_agent_trading_bot import Arbiter, Dir, Vote

        df = pd.DataFrame({
            "open": [2720.0] * 50,
            "high": [2725.0] * 50,
            "low": [2715.0] * 50,
            "close": [2722.0] * 50,
            "volume": [100.0] * 50,
        })

        # Add active LONG on Gold
        p_buy = TradeOrder.from_dict({
            "id": "gold_long_1",
            "symbol": "XAUUSD",
            "direction": "LONG",
            "entry_level": 2720.0,
            "stop_loss": 2710.0,
            "tp1": 2735.0,
            "tp2": 2740.0,
            "tp3": None,
            "status": "OPEN",
            "lots": 0.02,
            "size": 2.0,
            "leverage": 20.0,
            "margin_required": 50.0,
            "liquidation_price": 2600.0,
            "risk_amount": 10.0,
            "rr_ratio": 1.5,
            "setup_reason": "Active gold buy",
        })
        self.engine.active_positions[p_buy.id] = p_buy

        arb = Arbiter()
        # Mock agents returning strong SHORT consensus
        class MockBearAgent:
            def vote(self, *a, **k):
                return Vote("BEAR_AGENT", Dir.SHORT, 0.95, "Strong bearish cascade")

        arb.trend = MockBearAgent()
        arb.liq = MockBearAgent()
        arb.ind = MockBearAgent()
        arb.ai = MockBearAgent()
        arb.news = MockBearAgent()
        arb.WEIGHTS = {"BEAR_AGENT": 1.0}

        # 1. When hedging is OFF, decide returns None because opposing LONG exists
        config.set_hedging_mode(False, persist=False)
        sig_off, reason_off, _ = arb.decide(df, df, df, df, None, self.risk_mgr, symbol="XAUUSD", engine=self.engine, include_swarm=False, on_demand=True)
        self.assertIsNone(sig_off)
        self.assertIn("Opposing direction", reason_off)

        # 2. When hedging is ON, decide successfully generates SHORT setup despite open LONG
        config.set_hedging_mode(True, persist=False)
        sig_on, reason_on, _ = arb.decide(df, df, df, df, None, self.risk_mgr, symbol="XAUUSD", engine=self.engine, include_swarm=False, on_demand=True)
        self.assertIsNotNone(sig_on)
        self.assertEqual(sig_on["direction"], Dir.SHORT)

    async def test_telegram_hedge_command(self):
        """Verify Telegram /hedge on, /hedge off, and /hedge status commands."""
        mock_msg = AsyncMock()
        mock_update = MagicMock()
        mock_update.message = mock_msg
        mock_ctx = MagicMock()

        # 1. /hedge on
        mock_ctx.args = ["on"]
        await main.hedge_command(mock_update, mock_ctx)
        self.assertTrue(config.is_hedging_allowed())
        reply_on = mock_msg.reply_text.call_args[0][0]
        self.assertIn("Bi-Directional Hedging Mode: ACTIVATED", reply_on)

        # 2. /hedge status
        mock_ctx.args = []
        await main.hedge_command(mock_update, mock_ctx)
        reply_status = mock_msg.reply_text.call_args[0][0]
        self.assertIn("Hedging Mode Status:", reply_status)
        self.assertIn("ACTIVE", reply_status)

        # 3. /hedge off
        mock_ctx.args = ["off"]
        await main.hedge_command(mock_update, mock_ctx)
        self.assertFalse(config.is_hedging_allowed())
        reply_off = mock_msg.reply_text.call_args[0][0]
        self.assertIn("Bi-Directional Hedging Mode: DEACTIVATED", reply_off)


if __name__ == "__main__":
    unittest.main()
