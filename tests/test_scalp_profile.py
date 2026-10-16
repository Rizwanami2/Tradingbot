import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd

import config
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from multi_agent_trading_bot import Arbiter, Dir
from risk_manager import RiskManager
from strategy_profiles import load_profiles, apply_profile


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
             patch.object(self.arbiter.ind, "vote") as mock_ind, \
             patch.object(self.arbiter.liq, "vote") as mock_liq:
            from multi_agent_trading_bot import Vote
            mock_trend.return_value = Vote("TREND", Dir.LONG, 1.0, "Uptrend")
            mock_ind.return_value = Vote("INDICATORS", Dir.LONG, 1.0, "Bullish EMA")
            mock_liq.return_value = Vote("LIQUIDITY", Dir.LONG, 1.0, "Support held")
            if hasattr(self.arbiter, "mr") and self.arbiter.mr:
                self.arbiter.mr.vote = MagicMock(return_value=Vote("MEAN-REVERSION", Dir.FLAT, 0.0, "Neutral"))

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
        self.assertIsNotNone(order)
        self.assertIn("BTCUSDT", self.engine.pending_setups)

        # Even if candle does not cross 83450.0, fast_market_fill triggers immediately at curr_price
        candle = {"open": 83480.0, "high": 83485.0, "low": 83475.0, "close": 83480.0}
        events = self.engine.update_market_candle("BTCUSDT", candle)
        self.assertTrue(any(ev["event"] == "TRADE_EXECUTED" for ev in events))
        self.assertIn("BTCUSDT", self.engine.active_positions)
        self.assertNotIn("BTCUSDT", self.engine.pending_setups)
        self.assertEqual(self.engine.active_positions["BTCUSDT"].status, OrderStatus.OPEN)

    def test_scalp_not_blocked_by_autonomous_learner_min_rr(self):
        from autonomous_learner import AutonomousLearner
        import tempfile
        import os
        from signal_pipeline import try_paper_execute

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "test_scalp_learner.json")
            brain = AutonomousLearner(memory_path=mem_path)
            # Learned min_rr is tightened to 1.90
            brain.knowledge.min_rr = 1.90

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
                "score": 1.5,
                "is_scalp": True,
                "fast_market_fill": True,
            }

            # Scalp signal passes learner
            ok, reason, mult = brain.advise_signal(
                direction="LONG",
                score=sig["score"],
                rr=sig["rr"],
                agreement=2,
                conviction=0.6,
                is_scalp=sig["is_scalp"],
            )
            self.assertTrue(ok)
            self.assertIn("pass learned filters", reason)

            # Paper execution succeeds
            self.engine.scalp_mode = True
            ok_exec, note = try_paper_execute(self.engine, sig)
            self.assertTrue(ok_exec)
            self.assertNotIn("blocked by Autonomous Learner", note)

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

    def test_multi_position_scalp_gold_and_btc_up_to_two(self):
        self.engine.scalp_mode = True
        self.engine.fast_market_fill = True
        self.engine.max_open_trades = 2

        self.assertEqual(self.engine.open_trade_count(), 0)
        self.assertEqual(self.engine.slots_available(), 2)

        # Register 1 Gold position and 1 BTC position (2 total slots filled)
        g_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0, "stop": 2646.0,
            "tp1": 2655.0, "tp2": 2660.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 8.0, "rr": 1.0, "is_scalp": True, "fast_market_fill": True,
        }
        order1 = self.engine.register_setup(g_sig, reason="Gold scalp #1")
        self.assertIsNotNone(order1)

        b_sig = {
            "symbol": "BTCUSDT", "direction": "LONG", "entry": 65000.0, "stop": 64800.0,
            "tp1": 65200.0, "tp2": 65400.0, "lots": 0.01, "size": 0.01, "leverage": 20.0,
            "margin_required": 32.5, "risk_amount": 5.0, "rr": 1.0, "is_scalp": True, "fast_market_fill": True,
        }
        order2 = self.engine.register_setup(b_sig, reason="BTC scalp #1")
        self.assertIsNotNone(order2)

        self.assertEqual(self.engine.open_trade_count(), 2)
        self.assertEqual(self.engine.slots_available(), 0)

        # 3rd order must be blocked by the 2 slots limit
        extra_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2652.0, "stop": 2648.0,
            "tp1": 2657.0, "tp2": 2662.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 8.0, "rr": 1.0, "is_scalp": True,
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(extra_sig)
        self.assertIn("Max concurrent trades reached (2)", str(ctx.exception))

        # Fill orders with market candles
        gcandle = {"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}
        self.engine.update_market_candle("XAUUSD", gcandle)
        bcandle = {"open": 65000.0, "high": 65050.0, "low": 64990.0, "close": 65010.0}
        self.engine.update_market_candle("BTCUSDT", bcandle)

        self.assertEqual(len(self.engine.active_positions), 2)
        detail = self.engine.format_positions_detail()
        self.assertIn("2/2 active", detail)
        self.assertIn("0 slots free", detail)

        # Close 1 position and verify slot is freed
        self.engine.close_position("XAUUSD", current_price=2652.0)
        self.assertEqual(self.engine.open_trade_count(), 1)
        self.assertEqual(self.engine.slots_available(), 1)

        # Now registering a 2nd position succeeds again
        order3 = self.engine.register_setup(extra_sig, reason="Gold scalp #2")
        self.assertIsNotNone(order3)
        self.assertEqual(self.engine.open_trade_count(), 2)

    def test_multi_position_scalp_gold_and_btc_up_to_ten(self):
        self.engine.scalp_mode = True
        self.engine.fast_market_fill = True
        self.engine.max_open_trades = 10

        self.assertEqual(self.engine.open_trade_count(), 0)
        self.assertEqual(self.engine.slots_available(), 10)

        # Register 5 Gold positions and 5 BTC positions (10 total slots filled)
        gold_orders = []
        btc_orders = []

        for i in range(5):
            g_sig = {
                "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0 + i, "stop": 2646.0 + i,
                "tp1": 2655.0 + i, "tp2": 2660.0 + i, "lots": 0.02, "size": 2.0, "leverage": 20.0,
                "margin_required": 50.0, "risk_amount": 8.0, "rr": 1.0, "is_scalp": True, "fast_market_fill": True,
            }
            order = self.engine.register_setup(g_sig, reason=f"Gold scalp #{i+1}")
            gold_orders.append(order)

            b_sig = {
                "symbol": "BTCUSDT", "direction": "LONG", "entry": 65000.0 + i * 10, "stop": 64800.0 + i * 10,
                "tp1": 65200.0 + i * 10, "tp2": 65400.0 + i * 10, "lots": 0.01, "size": 0.01, "leverage": 20.0,
                "margin_required": 32.5, "risk_amount": 5.0, "rr": 1.0, "is_scalp": True, "fast_market_fill": True,
            }
            border = self.engine.register_setup(b_sig, reason=f"BTC scalp #{i+1}")
            btc_orders.append(border)

        self.assertEqual(self.engine.open_trade_count(), 10)
        self.assertEqual(self.engine.slots_available(), 0)

        # 11th order must be blocked by the 10 slots limit
        extra_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2660.0, "stop": 2656.0,
            "tp1": 2665.0, "tp2": 2670.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 8.0, "rr": 1.0, "is_scalp": True,
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.engine.register_setup(extra_sig)
        self.assertIn("Max concurrent trades reached (10)", str(ctx.exception))

        # Fill all 5 Gold orders with a market candle
        gcandle = {"open": 2652.0, "high": 2654.0, "low": 2650.0, "close": 2652.0}
        gevents = self.engine.update_market_candle("XAUUSD", gcandle)
        self.assertEqual(sum(1 for e in gevents if e["event"] == "TRADE_EXECUTED"), 5)

        # Fill all 5 BTC orders with a market candle
        bcandle = {"open": 65020.0, "high": 65050.0, "low": 65000.0, "close": 65020.0}
        bevents = self.engine.update_market_candle("BTCUSDT", bcandle)
        self.assertEqual(sum(1 for e in bevents if e["event"] == "TRADE_EXECUTED"), 5)

        self.assertEqual(len(self.engine.active_positions), 10)
        self.assertEqual(self.engine.active_positions.count_for_symbol("XAUUSD"), 5)
        self.assertEqual(self.engine.active_positions.count_for_symbol("BTCUSDT"), 5)

        # Verify format_positions_detail contains 10 active trades
        detail = self.engine.format_positions_detail()
        self.assertIn("10/10 active", detail)
        self.assertIn("0 slots free", detail)

    def test_multi_position_independent_scalp_tp_and_sl(self):
        self.engine.scalp_mode = True
        self.engine.scalp_tp_usd = 2.50
        self.engine.scalp_sl_usd = 3.00
        self.engine.max_open_trades = 10

        # Create 2 active Gold positions at different entry levels
        order1 = TradeOrder(
            id="order-gold-1", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
            stop_loss=2640.0, tp1=2660.0, tp2=2670.0, tp3=None, lots=0.02, size=2.0,
            leverage=20.0, margin_required=50.0, liquidation_price=2600.0, risk_amount=20.0,
            rr_ratio=1.0, setup_reason="Scalp #1", status=OrderStatus.OPEN, executed_price=2650.0,
            remaining_size=2.0, is_scalp=True,
        )
        order2 = TradeOrder(
            id="order-gold-2", symbol="XAUUSD", direction="LONG", entry_level=2652.0,
            stop_loss=2640.0, tp1=2660.0, tp2=2670.0, tp3=None, lots=0.02, size=2.0,
            leverage=20.0, margin_required=50.0, liquidation_price=2600.0, risk_amount=20.0,
            rr_ratio=1.0, setup_reason="Scalp #2", status=OrderStatus.OPEN, executed_price=2652.0,
            remaining_size=2.0, is_scalp=True,
        )
        self.engine.active_positions[order1.id] = order1
        self.engine.active_positions[order2.id] = order2
        self.assertEqual(len(self.engine.active_positions), 2)

        # Candle @ 2651.5:
        # Order 1: (2651.5 - 2650.0) * 2.0 = +$3.00 >= $2.50 -> Hits Scalp TP!
        # Order 2: (2651.5 - 2652.0) * 2.0 = -$1.00 > -$3.00 -> Small loss within SL limit -> stays open!
        candle = {"open": 2651.0, "high": 2652.0, "low": 2651.0, "close": 2651.5}
        events = self.engine.update_market_candle("XAUUSD", candle)

        # Order 1 should have auto-closed on Scalp TP
        self.assertTrue(any(e["event"] == "SCALP_CLOSE" and e["order"].id == "order-gold-1" for e in events))
        self.assertEqual(order1.status, OrderStatus.CLOSED)
        self.assertNotIn("order-gold-1", self.engine.active_positions)

        # Order 2 should still be open
        self.assertIn("order-gold-2", self.engine.active_positions)
        self.assertEqual(len(self.engine.active_positions), 1)

        # Next candle drops price to 2648.0:
        # Order 2: (2648.0 - 2652.0) * 2.0 = -$8.00 <= -$3.00 -> Hits Scalp SL cut!
        candle_sl = {"open": 2650.0, "high": 2650.0, "low": 2648.0, "close": 2648.0}
        events_sl = self.engine.update_market_candle("XAUUSD", candle_sl)
        self.assertTrue(any(e["event"] == "SCALP_SL" and e["order"].id == "order-gold-2" for e in events_sl))
        self.assertEqual(order2.status, OrderStatus.CLOSED)
        self.assertNotIn("order-gold-2", self.engine.active_positions)
        self.assertEqual(len(self.engine.active_positions), 0)

    def test_multi_position_close_by_order_id_and_symbol(self):
        self.engine.scalp_mode = True
        self.engine.max_open_trades = 10

        o1 = TradeOrder(
            id="btc-scalp-1", symbol="BTCUSDT", direction="LONG", entry_level=65000.0,
            stop_loss=64500.0, tp1=66000.0, tp2=67000.0, tp3=None, lots=0.01, size=0.01,
            leverage=20.0, margin_required=32.5, liquidation_price=60000.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="BTC Scalp 1", status=OrderStatus.OPEN, executed_price=65000.0,
            remaining_size=0.01, is_scalp=True,
        )
        o2 = TradeOrder(
            id="btc-scalp-2", symbol="BTCUSDT", direction="LONG", entry_level=65100.0,
            stop_loss=64500.0, tp1=66000.0, tp2=67000.0, tp3=None, lots=0.01, size=0.01,
            leverage=20.0, margin_required=32.5, liquidation_price=60000.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="BTC Scalp 2", status=OrderStatus.OPEN, executed_price=65100.0,
            remaining_size=0.01, is_scalp=True,
        )
        o3 = TradeOrder(
            id="btc-scalp-3", symbol="BTCUSDT", direction="LONG", entry_level=65200.0,
            stop_loss=64500.0, tp1=66000.0, tp2=67000.0, tp3=None, lots=0.01, size=0.01,
            leverage=20.0, margin_required=32.5, liquidation_price=60000.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="BTC Scalp 3", status=OrderStatus.OPEN, executed_price=65200.0,
            remaining_size=0.01, is_scalp=True,
        )
        self.engine.active_positions[o1.id] = o1
        self.engine.active_positions[o2.id] = o2
        self.engine.active_positions[o3.id] = o3
        self.assertEqual(len(self.engine.active_positions), 3)

        # 1. Close specific position by order ID
        closed1 = self.engine.close_position("btc-scalp-1", current_price=65100.0)
        self.assertIsNotNone(closed1)
        self.assertEqual(closed1.id, "btc-scalp-1")
        self.assertEqual(len(self.engine.active_positions), 2)
        self.assertNotIn("btc-scalp-1", self.engine.active_positions)

        # 2. Close remaining positions by symbol "BTCUSDT"
        closed_rem = self.engine.close_position("BTCUSDT", current_price=65250.0)
        self.assertIsNotNone(closed_rem)
        self.assertEqual(len(self.engine.active_positions), 0)
        self.assertNotIn("BTCUSDT", self.engine.active_positions)


class TestTelegramScalpCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telegram import Chat, Message, Update
        from telegram.ext import ContextTypes
        import config

        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.user = MagicMock()
        self.user.id = 12345
        self.update.effective_user = self.user
        self._orig_optin = config.AUTOTRADE_LIVE_OPT_IN
        config.AUTOTRADE_LIVE_OPT_IN = True

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def asyncTearDown(self):
        import config
        config.AUTOTRADE_LIVE_OPT_IN = self._orig_optin

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
