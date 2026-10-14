import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd

import config
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from multi_agent_trading_bot import Arbiter, Dir
from risk_manager import RiskManager
from strategy_profiles import StrategyProfile, load_profiles, get_profile, apply_profile, CURRENT_PROFILE


class TestScalpProfileAndSpamTrading(unittest.TestCase):
    def setUp(self):
        self.risk_mgr = RiskManager(equity=1000.0, symbol="BTCUSDT")
        self.arbiter = Arbiter(allow_asian=True)
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)

    def test_scalp_profile_defaults(self):
        profiles = load_profiles()
        self.assertIn("scalp", profiles)
        p = profiles["scalp"]
        self.assertTrue(p.is_scalp)
        self.assertTrue(p.fast_market_fill)
        self.assertFalse(p.require_killzone)
        self.assertTrue(p.allow_asian)
        self.assertEqual(p.min_agreement, 2)
        self.assertAlmostEqual(p.min_score, 1.0)
        self.assertAlmostEqual(p.min_rr, 1.0)
        self.assertAlmostEqual(p.scalp_tp_pct, 0.10)
        self.assertAlmostEqual(p.scalp_sl_pct, 0.12)
        self.assertAlmostEqual(p.scalp_tp_usd, 2.50)
        self.assertAlmostEqual(p.scalp_sl_usd, 3.00)
        self.assertEqual(p.poll_interval, 2)
        self.assertEqual(p.fast_poll_interval, 1)

    def test_apply_profile_scalp_updates_components(self):
        prof, summary = apply_profile(
            "scalp",
            arbiter=self.arbiter,
            risk_mgr=self.risk_mgr,
            engine=self.engine,
            config=config,
        )
        self.assertEqual(prof.name, "scalp")
        self.assertIn("STRATEGY PROFILE ACTIVATED: SCALP", summary)
        self.assertIn("Rapid Micro-Scalping Mode", summary)

        # Check arbiter
        self.assertTrue(self.arbiter.is_scalp)
        self.assertTrue(self.arbiter.fast_market_fill)
        self.assertEqual(self.arbiter.MIN_AGREEMENT, 2)
        self.assertAlmostEqual(self.arbiter.MIN_SCORE, 1.0)
        self.assertFalse(self.arbiter.require_killzone)
        self.assertTrue(self.arbiter.allow_asian)

        # Check risk manager
        self.assertAlmostEqual(self.risk_mgr.config.min_rr, 1.0)
        self.assertAlmostEqual(self.risk_mgr.config.tp1_r, 1.0)
        self.assertAlmostEqual(self.risk_mgr.config.tp2_r, 1.5)

        # Check execution engine
        self.assertTrue(self.engine.scalp_mode)
        self.assertTrue(self.engine.fast_market_fill)
        self.assertAlmostEqual(self.engine.scalp_tp_usd, 2.50)
        self.assertAlmostEqual(self.engine.scalp_sl_usd, 3.00)

        # Check config
        self.assertEqual(config.CURRENT_PROFILE, "scalp")
        self.assertTrue(config.SCALP_ENABLED)
        self.assertAlmostEqual(config.SCALP_TAKE_PROFIT_USD, 2.50)
        self.assertAlmostEqual(config.SCALP_STOP_LOSS_USD, 3.00)

    def test_arbiter_decide_scalp_micro_levels(self):
        apply_profile("scalp", arbiter=self.arbiter, risk_mgr=self.risk_mgr, engine=self.engine, config=config)

        # Create mock 1m candle dataframe with slight upward drift
        dates = pd.date_range("2026-10-14 00:00", periods=50, freq="1min")
        df1 = pd.DataFrame({
            "open": [83000.0 + i * 2 for i in range(50)],
            "high": [83005.0 + i * 2 for i in range(50)],
            "low": [82998.0 + i * 2 for i in range(50)],
            "close": [83002.0 + i * 2 for i in range(50)],
            "volume": [100.0 for _ in range(50)],
        }, index=dates)

        # Mock votes to ensure agreement
        with patch.object(self.arbiter.trend, "vote") as mock_trend, \
             patch.object(self.arbiter.ind, "vote") as mock_ind:
            from multi_agent_trading_bot import Vote
            mock_trend.return_value = Vote("TREND", Dir.LONG, 1.0, "Uptrend")
            mock_ind.return_value = Vote("INDICATORS", Dir.LONG, 1.0, "Bullish EMA")

            sig, why, votes = self.arbiter.decide(
                df1, None, None, None, None, self.risk_mgr, symbol="BTCUSDT", on_demand=True
            )

        self.assertIsNotNone(sig)
        self.assertEqual(sig["direction"], Dir.LONG)
        self.assertTrue(sig["is_scalp"])
        self.assertTrue(sig["fast_market_fill"])
        self.assertIn("micro SL/TP", sig["reason"])

        entry = sig["entry"]
        stop = sig["stop"]
        tp1 = sig["tp1"]
        # Micro stop distance should be tight (around 0.12% move on BTC, ~$100)
        stop_dist = abs(entry - stop)
        self.assertLess(stop_dist, entry * 0.003)
        self.assertGreater(stop_dist, 0.0)
        # TP1 should be tightly placed at ~1.0R
        tp_dist = abs(tp1 - entry)
        self.assertAlmostEqual(tp_dist / stop_dist, 1.0, delta=0.1)

    def test_instant_market_trigger_for_scalp_setup(self):
        self.engine.scalp_mode = True
        self.engine.fast_market_fill = True

        sig = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 83450.0,
            "stop": 83350.0,
            "tp1": 83550.0,
            "tp2": 83600.0,
            "lots": 0.05,
            "size": 0.05,
            "leverage": 20.0,
            "margin_required": 208.6,
            "risk_amount": 5.0,
            "rr": 1.0,
            "is_scalp": True,
            "fast_market_fill": True,
        }
        order = self.engine.register_setup(sig, reason="Scalp LONG")
        self.assertIn("BTCUSDT", self.engine.pending_setups)

        # Even if candle does not cross 83450.0, fast_market_fill triggers immediately at curr_price
        candle = {"open": 83480.0, "high": 83485.0, "low": 83475.0, "close": 83480.0}
        events = self.engine.update_market_candle("BTCUSDT", candle)
        self.assertTrue(any(ev["event"] == "TRADE_EXECUTED" for ev in events))
        self.assertIn("BTCUSDT", self.engine.active_positions)
        self.assertNotIn("BTCUSDT", self.engine.pending_setups)
        self.assertEqual(self.engine.active_positions["BTCUSDT"].status, OrderStatus.OPEN)

    def test_scalp_auto_close_on_small_profit(self):
        self.engine.scalp_mode = True
        self.engine.scalp_tp_usd = 2.50
        self.engine.scalp_tp_pct = 0.10

        # Create active scalp position on XAUUSD
        order = TradeOrder(
            id="order-scalp-profit",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2646.0,
            tp1=2654.0,
            tp2=2658.0,
            tp3=None,
            lots=0.02,
            size=2.0,
            leverage=20.0,
            margin_required=53.0,
            liquidation_price=2600.0,
            risk_amount=8.0,
            rr_ratio=1.0,
            setup_reason="Scalp LONG micro",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=2.0,
            is_scalp=True,
        )
        self.engine.active_positions["XAUUSD"] = order

        # Advance price by +$1.50 -> PnL = 2.0 * $1.50 = +$3.00 >= $2.50
        candle = {"open": 2650.5, "high": 2652.0, "low": 2650.5, "close": 2651.5}
        events = self.engine.update_market_candle("XAUUSD", candle)

        self.assertTrue(any(ev["event"] == "SCALP_CLOSE" for ev in events))
        self.assertNotIn("XAUUSD", self.engine.active_positions)
        self.assertEqual(order.status, OrderStatus.CLOSED)
        self.assertGreaterEqual(order.realized_pnl, 2.50)

    def test_scalp_tight_stop_loss_cut(self):
        self.engine.scalp_mode = True
        self.engine.scalp_sl_usd = 3.00
        self.engine.scalp_sl_pct = 0.12

        # Create active scalp position on XAUUSD
        order = TradeOrder(
            id="order-scalp-loss",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,  # Wide SL on order, but scalp SL cuts much tighter!
            tp1=2660.0,
            tp2=2670.0,
            tp3=None,
            lots=0.02,
            size=2.0,
            leverage=20.0,
            margin_required=53.0,
            liquidation_price=2600.0,
            risk_amount=20.0,
            rr_ratio=1.0,
            setup_reason="Scalp LONG micro",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=2.0,
            is_scalp=True,
        )
        self.engine.active_positions["XAUUSD"] = order

        # Drop price by -$1.80 -> PnL = 2.0 * (-$1.80) = -$3.60 <= -$3.00
        candle = {"open": 2649.5, "high": 2649.8, "low": 2648.0, "close": 2648.2}
        events = self.engine.update_market_candle("XAUUSD", candle)

        self.assertTrue(any(ev["event"] == "SCALP_SL" for ev in events))
        self.assertNotIn("XAUUSD", self.engine.active_positions)
        self.assertEqual(order.status, OrderStatus.CLOSED)
        self.assertIn("Scalp SL", order.close_reason)


class TestTelegramScalpCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telegram import Chat, Message, Update
        from telegram.ext import ContextTypes

        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def test_telegram_profile_command_activates_scalp(self):
        from telegram_advanced import profile_command
        self.ctx.args = ["scalp"]
        await profile_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("STRATEGY PROFILE ACTIVATED: SCALP", text)
        self.assertIn("Rapid Micro-Scalping Mode", text)

    async def test_telegram_autotrade_scalp_command(self):
        from main import autotrade_command
        self.ctx.args = ["scalp"]
        await autotrade_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("RAPID SCALP AUTOTRADE ACTIVATED", text)
        self.assertIn("SCALP", text)


if __name__ == "__main__":
    unittest.main()
