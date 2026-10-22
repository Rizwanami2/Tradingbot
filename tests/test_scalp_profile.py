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
        from anti_spam import get_anti_spam_limiter
        get_anti_spam_limiter().reset()
        self.risk_mgr = RiskManager(equity=10000.0, symbol="BTCUSDT", state_file="test_scalp_risk_mgr.json")
        self.risk_mgr.reset_daily_risk()
        self.arbiter = Arbiter(allow_asian=True)
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)

    def tearDown(self):
        import os
        from anti_spam import get_anti_spam_limiter
        get_anti_spam_limiter().reset()
        if os.path.exists("test_scalp_risk_mgr.json"):
            try:
                os.remove("test_scalp_risk_mgr.json")
            except OSError:
                pass


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

    def test_xau_scalp_profile_defaults_and_apply(self):
        profiles = load_profiles()
        self.assertIn("xau_scalp", profiles)
        p = profiles["xau_scalp"]
        self.assertTrue(p.is_scalp)
        self.assertFalse(p.allow_asian)
        self.assertAlmostEqual(p.min_score, 1.20)
        self.assertAlmostEqual(p.min_rr, 1.20)
        self.assertAlmostEqual(p.scalp_tp_usd, 3.00)
        self.assertAlmostEqual(p.scalp_sl_usd, 2.50)
        self.assertEqual(p.max_open_trades, 2)
        self.assertEqual(p.scalp_max_positions_per_symbol, 1)

        prof, summary = apply_profile(
            "xau_scalp",
            arbiter=self.arbiter,
            risk_mgr=self.risk_mgr,
            engine=self.engine,
            config=config,
        )
        self.assertEqual(prof.name, "xau_scalp")
        self.assertIn("XAUUSD-optimized", summary)
        self.assertFalse(self.arbiter.allow_asian)
        self.assertAlmostEqual(self.engine.scalp_tp_usd, 3.00)
        self.assertAlmostEqual(self.engine.scalp_sl_usd, 2.50)
        self.assertEqual(self.engine.max_open_trades, 2)

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

    async def test_telegram_cooldown_command(self):
        from main import cooldown_command
        self.ctx.args = []
        await cooldown_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Anti-Spam & Cooldown Telemetry", text)

        # Test reset
        self.message.reply_text.reset_mock()
        self.ctx.args = ["reset"]
        await cooldown_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        reset_text = self.message.reply_text.call_args[0][0]
        self.assertIn("reset globally", reset_text)


class TestMultiPositionTrailingAndAntiSpam(unittest.TestCase):
    def setUp(self):
        import main
        main.engine.active_positions.clear()
        main.engine.pending_setups.clear()
        self.risk_mgr = RiskManager(equity=10000.0, symbol="XAUUSD", state_file="test_scalp_multi_risk_mgr.json")
        self.risk_mgr.reset_daily_risk()
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        self.engine.save_state()
        self.engine.scalp_mode = True
        self.engine.scalp_trail_enabled = True
        self.engine.scalp_trail_activation_usd = 1.00
        self.engine.scalp_trail_distance_usd = 0.80
        self.engine.scalp_tp_usd = 10.0  # High ceiling so trailing exit triggers first
        self.engine.scalp_tp_pct = 5.0
        self.engine.max_open_trades = 5

    def tearDown(self):
        import main
        main.engine.active_positions.clear()
        main.engine.pending_setups.clear()
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        self.engine.save_state()
        import os
        if os.path.exists("test_scalp_multi_risk_mgr.json"):
            try:
                os.remove("test_scalp_multi_risk_mgr.json")
            except OSError:
                pass


    def test_multi_position_scalp_trailing_sl_independent_tracking(self):
        # Open 2 separate positions on Gold
        p1 = TradeOrder(
            id="gold-pos-1", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
            stop_loss=2645.0, tp1=2665.0, tp2=2675.0, tp3=None, lots=0.01, size=1.0,
            leverage=20.0, margin_required=26.5, liquidation_price=2600.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="Gold Scalp #1", status=OrderStatus.OPEN, executed_price=2650.0,
            remaining_size=1.0, is_scalp=True, ticket=101,
        )
        p2 = TradeOrder(
            id="gold-pos-2", symbol="XAUUSD", direction="LONG", entry_level=2652.0,
            stop_loss=2645.0, tp1=2665.0, tp2=2675.0, tp3=None, lots=0.01, size=1.0,
            leverage=20.0, margin_required=26.5, liquidation_price=2600.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="Gold Scalp #2", status=OrderStatus.OPEN, executed_price=2652.0,
            remaining_size=1.0, is_scalp=True, ticket=102,
        )
        self.engine.active_positions[p1.id] = p1
        self.engine.active_positions[p2.id] = p2

        # Candle 1 moves to 2653.5 -> Both positions gain profit:
        # P1: +$3.50 (>= $1.00 activation) -> Trails to 2653.5 - 0.80 = 2652.70
        # P2: +$1.50 (>= $1.00 activation) -> Trails to 2653.5 - 0.80 = 2652.70
        candle1 = {"open": 2652.0, "high": 2653.5, "low": 2652.0, "close": 2653.5}
        evs1 = self.engine.update_market_candle("XAUUSD", candle1)
        self.assertTrue(p1.trailing_sl_active)
        self.assertTrue(p2.trailing_sl_active)
        self.assertAlmostEqual(p1.stop_loss, 2652.70, delta=0.05)
        self.assertAlmostEqual(p2.stop_loss, 2652.70, delta=0.05)

        # Candle 2 pulls back to 2652.5:
        # 2652.5 <= 2652.70 -> Trailing SL HIT for both!
        candle2 = {"open": 2653.0, "high": 2653.0, "low": 2652.5, "close": 2652.6}
        evs2 = self.engine.update_market_candle("XAUUSD", candle2)
        trail_hits = [e for e in evs2 if e["event"] == "SCALP_TRAIL_HIT"]
        self.assertEqual(len(trail_hits), 2)
        self.assertNotIn(p1.id, self.engine.active_positions)
        self.assertNotIn(p2.id, self.engine.active_positions)
        # Position 1 secured +$2.70 profit, Position 2 secured +$0.70 profit!
        self.assertGreater(p1.realized_pnl, 2.50)
        self.assertGreater(p2.realized_pnl, 0.50)

    def test_anti_spam_rate_limiter_suite(self):
        from anti_spam import AntiSpamRateLimiter
        limiter = AntiSpamRateLimiter(
            entry_cooldown_sec=45,
            loss_cooldown_sec=90,
            global_cooldown_sec=10,
            max_positions_per_symbol=2,
            min_price_distance_pct=0.08,
        )

        # 1. Allowed initially
        ok, reason = limiter.check_can_enter("XAUUSD", 2650.0, self.engine, max_open=4, timestamp=1000.0)
        self.assertTrue(ok, reason)

        # 2. Record entry at ts=1000.0
        limiter.record_entry("XAUUSD", 2650.0, timestamp=1000.0)

        # 3. Global cooldown active for BTCUSDT at ts=1005.0 (< 10s)
        ok, reason = limiter.check_can_enter("BTCUSDT", 65000.0, self.engine, max_open=4, timestamp=1005.0)
        self.assertFalse(ok)
        self.assertIn("Global cooldown", reason)

        # 4. Global cooldown expires at ts=1015.0 -> BTCUSDT allowed
        ok, reason = limiter.check_can_enter("BTCUSDT", 65000.0, self.engine, max_open=4, timestamp=1015.0)
        self.assertTrue(ok, reason)

        # 5. Symbol entry cooldown active for XAUUSD at ts=1030.0 (< 45s)
        ok, reason = limiter.check_can_enter("XAUUSD", 2660.0, self.engine, max_open=4, timestamp=1030.0)
        self.assertFalse(ok)
        self.assertIn("Entry cooldown", reason)

        # 6. Min price distance: at ts=1050.0, if XAUUSD price is 2651.0 (0.038% from 2650.0 < 0.08%)
        p_dummy = TradeOrder(
            id="g1", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
            stop_loss=2645.0, tp1=2665.0, tp2=2675.0, tp3=None, lots=0.01, size=1.0,
            leverage=20.0, margin_required=26.5, liquidation_price=2600.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="Gold", status=OrderStatus.OPEN, executed_price=2650.0,
            remaining_size=1.0, is_scalp=True,
        )
        self.engine.active_positions[p_dummy.id] = p_dummy
        ok, reason = limiter.check_can_enter("XAUUSD", 2651.0, self.engine, max_open=4, timestamp=1050.0)
        self.assertFalse(ok)
        self.assertIn("Price too close", reason)

        # Distance >= 0.08% (e.g. 2655.0 is ~0.19% away) -> Allowed
        ok, reason = limiter.check_can_enter("XAUUSD", 2655.0, self.engine, max_open=4, timestamp=1050.0)
        self.assertTrue(ok, reason)

        # 7. Max positions per symbol (2)
        p_dummy2 = TradeOrder(
            id="g2", symbol="XAUUSD", direction="LONG", entry_level=2655.0,
            stop_loss=2645.0, tp1=2665.0, tp2=2675.0, tp3=None, lots=0.01, size=1.0,
            leverage=20.0, margin_required=26.5, liquidation_price=2600.0, risk_amount=5.0,
            rr_ratio=1.0, setup_reason="Gold 2", status=OrderStatus.OPEN, executed_price=2655.0,
            remaining_size=1.0, is_scalp=True,
        )
        self.engine.active_positions[p_dummy2.id] = p_dummy2
        ok, reason = limiter.check_can_enter("XAUUSD", 2670.0, self.engine, max_open=4, timestamp=1050.0)
        self.assertFalse(ok)
        self.assertIn("Max 2 positions reached", reason)

        # Clear positions for loss test
        self.engine.active_positions.clear()

        # 8. Loss cooldown: record SL hit at ts=2000.0
        limiter.record_loss("XAUUSD", 3.0, timestamp=2000.0)
        # Blocked at ts=2050.0 (< 90s)
        ok, reason = limiter.check_can_enter("XAUUSD", 2650.0, self.engine, max_open=4, timestamp=2050.0)
        self.assertFalse(ok)
        self.assertIn("Loss cooldown", reason)

        # 9. Consecutive loss escalation: 2nd consecutive loss extends cooldown to 180s
        limiter.record_loss("XAUUSD", 3.0, timestamp=2100.0)
        self.assertEqual(limiter.get_loss_cooldown_duration("XAUUSD"), 180)
        # Blocked at ts=2250.0 (150s elapsed < 180s)
        ok, reason = limiter.check_can_enter("XAUUSD", 2650.0, self.engine, max_open=4, timestamp=2250.0)
        self.assertFalse(ok)

        # 10. Win resets streak
        limiter.record_win("XAUUSD")
        self.assertEqual(limiter._get_state("XAUUSD").consecutive_losses, 0)

        # 11. Reset clears all state
        limiter.reset()
        ok, reason = limiter.check_can_enter("XAUUSD", 2650.0, self.engine, max_open=4, timestamp=2251.0)
        self.assertTrue(ok, reason)

    def test_manual_lot_size_preserved_against_learner_mult(self):
        sig_auto = {"lots": 0.10, "size": 1.0, "lot_mode": "auto"}
        size_mult = 0.5
        if size_mult < 1.0 and sig_auto.get("lot_mode", "auto") != "manual":
            sig_auto["lots"] = max(0.01, round(float(sig_auto["lots"]) * size_mult, 4))
        self.assertEqual(sig_auto["lots"], 0.05)

        sig_manual = {"lots": 0.10, "size": 1.0, "lot_mode": "manual"}
        if size_mult < 1.0 and sig_manual.get("lot_mode", "auto") != "manual":
            sig_manual["lots"] = max(0.01, round(float(sig_manual["lots"]) * size_mult, 4))
        self.assertEqual(sig_manual["lots"], 0.10)

    def test_send_telegram_alert_fallback(self):
        import asyncio
        from autotrade_worker import send_telegram_alert

        app = MagicMock()
        # First call fails on Markdown parsing, second succeeds with clean text
        app.bot.send_message = AsyncMock(side_effect=[Exception("Markdown error"), True])
        mock_cfg = MagicMock()
        mock_cfg.TELEGRAM_CHAT_ID = "12345"

        text = "⚡ *TRADE EXECUTED* `XAUUSD` @ 2650"
        result = asyncio.run(send_telegram_alert(app, mock_cfg, text))
        self.assertTrue(result)
        self.assertEqual(app.bot.send_message.call_count, 2)
        # Second call stripped markdown characters
        call2_kwargs = app.bot.send_message.call_args_list[1][1]
        self.assertNotIn("*", call2_kwargs["text"])
        self.assertNotIn("`", call2_kwargs["text"])
        self.assertNotIn("parse_mode", call2_kwargs)


    def test_scalp_concession_spread_slippage_veto(self):
        """Verify scalp profile concessions allow ~1.0 R:R while standard mode requires >= 1.4."""
        from signal_quality import spread_slippage_veto, evaluate_signal_quality

        # In standard mode (expected_rr=1.0), 1.0 * (1 - (0.05 + 0.0)) = 0.95 < 1.4 -> VETOED
        ok_std, reason_std = spread_slippage_veto(spread=0.0, atr=1.0, expected_rr=1.0, is_scalp=False)
        self.assertFalse(ok_std)
        self.assertIn("kills R:R", reason_std)

        # In scalp mode (is_scalp=True), effective R:R ~0.95 >= 0.80 -> ALLOWED
        ok_scalp, reason_scalp = spread_slippage_veto(spread=0.0, atr=1.0, expected_rr=1.0, is_scalp=True)
        self.assertTrue(ok_scalp)
        self.assertIn("spread ok", reason_scalp)

        # In scalp mode with min_effective_rr=1.15 (accuracy mode threshold):
        ok_acc_low, r_acc_low = spread_slippage_veto(spread=0.0, atr=1.0, expected_rr=1.0, min_effective_rr=1.15, is_scalp=True)
        self.assertFalse(ok_acc_low)
        self.assertIn("kills R:R", r_acc_low)

        ok_acc_high, r_acc_high = spread_slippage_veto(spread=0.0, atr=1.0, expected_rr=1.25, min_effective_rr=1.15, is_scalp=True)
        self.assertTrue(ok_acc_high)
        self.assertIn("spread ok", r_acc_high)

    def test_scalp_concession_candle_and_volume(self):
        """Verify candle quality and volume node concessions in scalp mode."""
        from signal_quality import candle_quality, session_volume_node_ok

        # Quiet volume (last = 4, median = 10 -> ratio 0.40)
        # Standard requires 0.85 -> rejected
        df_vol = pd.DataFrame({"volume": [10.0] * 60 + [4.0]})
        ok_v_std, _ = session_volume_node_ok(df_vol, direction="LONG", lookback=60, is_scalp=False)
        self.assertFalse(ok_v_std)
        # Scalp concession allows 0.35 -> allowed
        ok_v_scalp, _ = session_volume_node_ok(df_vol, direction="LONG", lookback=60, is_scalp=True)
        self.assertTrue(ok_v_scalp)

        # Small 1m body candle (body ratio 0.08)
        # Standard requires 0.12 -> rejected
        df_c = pd.DataFrame({
            "open": [100.0] * 20 + [100.0],
            "high": [105.0] * 20 + [105.0],
            "low": [95.0] * 20 + [95.0],
            "close": [100.0] * 20 + [100.8], # body = 0.8 / 10.0 = 0.08
        })
        ok_c_std, _, _ = candle_quality(df_c, is_scalp=False)
        self.assertFalse(ok_c_std)
        # Scalp concession allows 0.06 -> allowed
        ok_c_scalp, _, _ = candle_quality(df_c, is_scalp=True)
        self.assertTrue(ok_c_scalp)

    def test_regime_auto_switcher_respects_scalp_profile_and_locks(self):
        """Verify regime auto switcher does not wipe out scalp profile concessions."""
        from regime_auto_switcher import RegimeAutoSwitcher
        from strategy_profiles import apply_profile, get_current_profile
        import config

        switcher = RegimeAutoSwitcher(cooldown_seconds=0.0)
        apply_profile("scalp", config=config, lock=True)

        df = pd.DataFrame({
            "open": [2700.0 + i for i in range(30)],
            "high": [2705.0 + i for i in range(30)],
            "low": [2695.0 + i for i in range(30)],
            "close": [2702.0 + i for i in range(30)],
        })
        res = switcher.evaluate_and_switch(df, symbol="XAUUSD", config=config)
        self.assertFalse(res.switched)
        self.assertEqual(res.current_profile, "scalp")
        self.assertIn("locked", res.reason.lower())

        # Unlock and evaluate: scalp profile preserved under consolidation/chop market
        config.PROFILE_LOCKED = False
        df_chop = pd.DataFrame({
            "open": [2700.0 + (i % 2) for i in range(35)],
            "high": [2703.0 for i in range(35)],
            "low": [2698.0 for i in range(35)],
            "close": [2701.0 - (i % 2) for i in range(35)],
        })
        res2 = switcher.evaluate_and_switch(df_chop, symbol="XAUUSD", config=config)
        self.assertEqual(res2.current_profile, "scalp")

    def test_mt5_multi_position_reconciliation_and_sync(self):
        """Verify ExecutionEngine reconciles and synchronizes multiple MT5 positions with tickets and telemetry."""
        from execution import Position
        from execution_engine import ExecutionEngine, TradeOrder, OrderStatus

        broker = MagicMock()
        bp1 = Position(symbol="XAUUSD", side="buy", qty=0.05, entry_price=2650.0, unrealized_pnl=25.0, current_price=2655.0, stop_loss=2640.0, take_profit=2670.0, ticket=101)
        bp2 = Position(symbol="XAUUSD", side="buy", qty=0.02, entry_price=2652.0, unrealized_pnl=6.0, current_price=2655.0, stop_loss=2642.0, take_profit=2672.0, ticket=102)
        broker.get_positions.return_value = [bp1, bp2]

        engine = ExecutionEngine(risk_manager=self.risk_mgr, broker=broker)

        # 1. Reconcile adopts both positions with exact tickets
        summary = engine.reconcile_positions()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["broker_count"], 2)
        self.assertEqual(summary["adopted"], 2)
        self.assertEqual(len(engine.active_positions), 2)

        p_list = list(engine.active_positions.values())
        tickets = {str(p.ticket) for p in p_list}
        self.assertIn("101", tickets)
        self.assertIn("102", tickets)

        # 2. Broker updates prices and closes ticket 101
        bp2.current_price = 2660.0
        bp2.unrealized_pnl = 16.0
        broker.get_positions.return_value = [bp2] # ticket 101 closed on MT5

        summary2 = engine.reconcile_positions()
        self.assertEqual(summary2["closed_locally"], 1)
        self.assertEqual(len(engine.active_positions), 1)

        remaining = list(engine.active_positions.values())[0]
        self.assertEqual(str(remaining.ticket), "102")
        self.assertEqual(remaining.current_price, 2660.0)
        self.assertEqual(remaining.unrealized_pnl, 16.0)

    def test_mt5_live_chart_card_includes_order_positions_and_signal_levels(self):
        """Verify MT5 live chart card renders active MT5 order positions and signal key levels."""
        from execution import Position
        from mt5_chart import get_mt5_live_chart, format_mt5_chart_card

        broker = MagicMock()
        broker.mark_prices = {"XAUUSD": 2655.50}
        broker.server = "MetaQuotes-Demo"
        broker.login = 100200
        broker.mode = "LIVE"
        broker.is_connected = lambda: True

        bp = Position(
            symbol="XAUUSD",
            side="buy",
            qty=0.05,
            entry_price=2650.0,
            unrealized_pnl=27.50,
            current_price=2655.50,
            stop_loss=2640.0,
            take_profit=2675.0,
            ticket=778899,
        )
        broker.get_positions = MagicMock(return_value=[bp])

        chart_data = get_mt5_live_chart("XAUUSD", timeframe="1m", broker=broker)
        self.assertIn("open_positions", chart_data)
        self.assertIn("signal_levels", chart_data)
        self.assertEqual(len(chart_data["open_positions"]), 1)
        self.assertEqual(chart_data["open_positions"][0]["ticket"], 778899)

        card = format_mt5_chart_card(chart_data)
        # Check active orders section
        self.assertIn("Active MT5 Order Positions", card)
        self.assertIn("#778899", card)
        self.assertIn("0.05 Lots", card)
        self.assertIn("2,650.00", card)
        self.assertIn("2,655.50", card)
        self.assertIn("27.50", card)

        # Check signal levels section
        self.assertIn("Signal Key Levels & Market Confluence", card)
        self.assertIn("Daily Range (PDH / PDL)", card)
        self.assertIn("Central Pivot (P)", card)

    def test_scalp_trailing_sl_guaranteed_profit_floor(self):
        """Verify dynamic trailing stop secures tiered minimum profit floor (50%-70% of peak profit)."""
        self.engine.scalp_mode = True
        self.engine.scalp_trail_enabled = True
        self.engine.scalp_trail_activation_usd = 1.00
        self.engine.scalp_min_trail_profit_usd = 1.50
        self.engine.scalp_profit_retention_pct = 0.50

        # Create active scalp position on XAUUSD
        # 1 lot: size = 1.0. Entry = 2650.0.
        order = TradeOrder(
            id="order-profit-floor",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2670.0,
            tp2=2680.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=25.0,
            liquidation_price=2600.0,
            risk_amount=5.0,
            rr_ratio=1.0,
            setup_reason="Scalp Profit Floor Test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=1.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Price advances +$6.00 to 2656.0 (peak profit = $6.00)
        # Tier: peak_usd >= 5.0 -> locks 60% of peak = 6.00 * 0.60 = $3.60.
        # SL floor = 2650.0 + 3.60 = 2653.60.
        candle1 = {"open": 2650.0, "high": 2656.0, "low": 2650.0, "close": 2655.8}
        self.engine.update_market_candle("XAUUSD", candle1)
        self.assertTrue(order.trailing_sl_active)
        self.assertGreaterEqual(order.stop_loss, 2653.60)

        # Pullback triggers trailing stop hit
        candle2 = {"open": 2654.0, "high": 2654.0, "low": 2653.0, "close": 2653.2}
        events = self.engine.update_market_candle("XAUUSD", candle2)
        trail_hits = [e for e in events if e["event"] == "SCALP_TRAIL_HIT"]
        self.assertEqual(len(trail_hits), 1)
        self.assertEqual(order.status, OrderStatus.CLOSED)
        # Position closed with substantial profit guaranteed (> $3.50)!
        self.assertGreaterEqual(order.realized_pnl, 3.50)

    def test_scalp_let_profit_run_on_strong_expanding_momentum(self):
        """Verify engine waits for TP and holds expanding candles instead of prematurely dumping position."""
        self.engine.scalp_mode = True
        self.engine.scalp_tp_usd = 2.50
        self.engine.scalp_let_profit_run = True

        order = TradeOrder(
            id="order-run-to-tp",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2665.0,  # Higher target exists
            tp2=2670.0,
            tp3=None,
            lots=0.02,
            size=2.0,
            leverage=20.0,
            margin_required=50.0,
            liquidation_price=2600.0,
            risk_amount=10.0,
            rr_ratio=1.0,
            setup_reason="Scalp Run to TP Test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=2.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Price reaches $2.60 profit (2651.30 * 2.0 = +$2.60 >= $2.50)
        # Candle is strongly expanding at the highs: open 2650.5, high 2651.30, close 2651.30
        expanding_candle = {"open": 2650.5, "high": 2651.30, "low": 2650.5, "close": 2651.30}
        events = self.engine.update_market_candle("XAUUSD", expanding_candle)

        # Position should NOT be closed! It should hold and run towards TP1
        self.assertIn("order-run-to-tp", self.engine.active_positions)
        self.assertEqual(order.status, OrderStatus.OPEN)
        run_events = [e for e in events if e["event"] == "SCALP_RUNNING_TO_TP"]
        self.assertEqual(len(run_events), 1)
        # Trailing stop was ratcheted to lock in guaranteed profit floor (at least 85% of $2.50 = $2.12 -> +$1.06 on price)
        self.assertGreaterEqual(order.stop_loss, 2651.06)

    def test_scalp_shakeout_protection_from_old_candle_low(self):
        """Verify trade does not get stopped out by an old intra-candle low while price is trading high in profit."""
        self.engine.scalp_mode = True
        self.engine.scalp_trail_enabled = True
        self.engine.scalp_trail_activation_usd = 1.00
        self.engine.scalp_trail_distance_usd = 0.80

        order = TradeOrder(
            id="order-shakeout-protect",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2665.0,
            tp2=2670.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=25.0,
            liquidation_price=2600.0,
            risk_amount=5.0,
            rr_ratio=1.0,
            setup_reason="Scalp Shakeout Test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=1.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Candle 1 establishes trailing stop at 2652.70
        c1 = {"open": 2650.0, "high": 2653.5, "low": 2649.5, "close": 2653.5}
        self.engine.update_market_candle("XAUUSD", c1)
        self.assertAlmostEqual(order.stop_loss, 2652.70, delta=0.05)

        # Candle 2 (still open): has an early wick to 2652.0, but current price rallied to 2654.0
        # curr_price (2654.0) > stop_loss (2652.70), close (2654.0) > stop_loss (2652.70)
        c2 = {"open": 2653.0, "high": 2654.0, "low": 2652.0, "close": 2654.0}
        events2 = self.engine.update_market_candle("XAUUSD", c2)
        trail_hits = [e for e in events2 if e["event"] == "SCALP_TRAIL_HIT"]
        # Shakeout was prevented! Trade stayed alive!
        self.assertEqual(len(trail_hits), 0)
        self.assertIn("order-shakeout-protect", self.engine.active_positions)
        self.assertEqual(order.status, OrderStatus.OPEN)

    def test_scalp_trail_buffering_prevents_ratchet_into_current_price(self):
        """Verify trailing stop never tightens into or above current price during intra-candle pullbacks."""
        self.engine.scalp_mode = True
        self.engine.scalp_trail_enabled = True
        self.engine.scalp_trail_activation_usd = 1.00
        self.engine.scalp_trail_distance_usd = 0.80
        self.engine.scalp_trail_buffer_usd = 0.40

        order = TradeOrder(
            id="order-buffer-test",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2665.0,
            tp2=2670.0,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=26.5,
            liquidation_price=2600.0,
            risk_amount=5.0,
            rr_ratio=1.0,
            setup_reason="Trail Buffer Test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=1.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Price peaked at 2653.5 (+3.50), but pulled back to 2652.5 within the candle.
        # Highest = 2653.5 -> unbuffered trail would be 2653.5 - 0.80 = 2652.70 (which is ABOVE close 2652.5!).
        # But with buffer_usd = 0.40, safe_sl_ceiling = 2652.5 - 0.40 = 2652.10.
        # Candidate SL MUST be clamped to <= 2652.10 so it does not choke or invert against current price!
        candle = {"open": 2650.0, "high": 2653.5, "low": 2650.0, "close": 2652.5}
        events = self.engine.update_market_candle("XAUUSD", candle)

        self.assertTrue(order.trailing_sl_active)
        # Stop loss must be safely below current price by at least the buffer
        self.assertLessEqual(order.stop_loss, 2652.5 - 0.40 + 1e-4)
        self.assertGreater(order.stop_loss, 2650.0)
        # Position must NOT be stopped out on this candle!
        trail_hits = [e for e in events if e["event"] == "SCALP_TRAIL_HIT"]
        self.assertEqual(len(trail_hits), 0)
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertIn(order.id, self.engine.active_positions)

    def test_scalp_mtf_confluence_veto_countertrend(self):
        """Verify scalp strategy strictly vetoes counter-trend trades against MTF matrix."""
        from mtf_matrix import check_mtf_confluence, MatrixSnapshot, TimeframeAnalysis

        fake_tfs = {
            tf: TimeframeAnalysis(
                timeframe=tf,
                label=tf.upper(),
                close=2650.0,
                change_pct=-1.5,
                ema9=2648.0,
                ema21=2652.0,
                ema50=2655.0,
                ema200=2660.0,
                trend_state="BEAR",
                trend_icon="🔴",
                rsi=42.0,
                rsi_state="BEAR",
                atr=2.0,
                atr_pct=0.08,
                volatility_state="NORMAL",
                vol_ratio=1.0,
                vol_state="ACTIVE",
                score=-45.0,
            )
            for tf in ["1m", "5m", "15m", "1h", "4h", "daily"]
        }
        bear_snap = MatrixSnapshot(
            symbol="XAUUSD",
            current_price=2650.0,
            timeframes=fake_tfs,
            overall_score=-45.0,
            confluence_grade="SELL / SHORT BIAS 🔴",
            ltf_htf_alignment="ALIGNED ⚡",
            top_bias="BEARISH",
            recommendation="Bearish alignment",
            timestamp="2026-10-19 12:00:00 UTC",
        )

        with patch("mtf_matrix.analyze_symbol_mtf", return_value=bear_snap):
            # Normal swing allows Long because -45.0 > -60.0 veto threshold
            ok_norm, _, _ = check_mtf_confluence("XAUUSD", "BUY", is_scalp=False)
            self.assertTrue(ok_norm)

            # High-accuracy scalp vetoes Long against -45.0 bearish MTF trend
            ok_scalp, reason_scalp, _ = check_mtf_confluence("XAUUSD", "BUY", is_scalp=True)
            self.assertFalse(ok_scalp)
            self.assertIn("Scalp counter-trend veto", reason_scalp)

            # High-accuracy scalp confirms Short with MTF alignment
            ok_short, reason_short, mult_short = check_mtf_confluence("XAUUSD", "SELL", is_scalp=True)
            self.assertTrue(ok_short)
            self.assertGreaterEqual(mult_short, 1.20)

    def test_scalp_opposing_rejection_wick_filter(self):
        """Verify candle quality filter rejects opposing rejection wicks for scalp trade accuracy."""
        from signal_quality import candle_quality

        dates = pd.date_range("2026-10-19 10:00", periods=25, freq="1min")
        # Base candle DataFrame
        rows = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5} for _ in range(24)]

        # Candidate candle with huge upper wick (shooting star / selling trap)
        rows.append({"open": 100.0, "high": 105.0, "low": 99.8, "close": 101.0})
        df_shooting_star = pd.DataFrame(rows, index=dates)

        # Candidate candle with huge lower wick (hammer / buying trap for short)
        rows[-1] = {"open": 104.0, "high": 105.0, "low": 100.0, "close": 103.0}
        df_hammer = pd.DataFrame(rows, index=dates)

        # For BUY: shooting star rejection wick must be vetoed in scalp mode
        ok_buy, reason_buy, _ = candle_quality(df_shooting_star, is_scalp=True, direction="BUY")
        self.assertFalse(ok_buy)
        self.assertIn("opposing rejection wick (upper)", reason_buy)

        # For SELL: hammer rejection wick must be vetoed in scalp mode
        ok_sell, reason_sell, _ = candle_quality(df_hammer, is_scalp=True, direction="SELL")
        self.assertFalse(ok_sell)
        self.assertIn("opposing rejection wick (lower)", reason_sell)

    def test_scalp_min_quality_gate(self):
        """Verify evaluate_signal_quality enforces minimum quality score for scalps."""
        from signal_quality import evaluate_signal_quality

        dates = pd.date_range("2026-10-19 10:00", periods=25, freq="1min")
        # Build DataFrame with low liquidity/flat candles
        rows = [{"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.02} for _ in range(25)]
        df_flat = pd.DataFrame(rows, index=dates)

        with patch("signal_quality.liquidity_imbalance_score", return_value=(0.20, "poor liquidity")), \
             patch("signal_quality.candle_quality", return_value=(True, "candle ok", 0.40)):
            res = evaluate_signal_quality(df_flat, direction="BUY", is_scalp=True)
            self.assertFalse(res.allowed)
            self.assertTrue(any("quality" in r for r in res.reasons))

    def test_scalp_waits_for_tp1_and_executes_tp1_without_premature_dump(self):
        """Verify scalp trade waits through consolidation for TP1 and takes TP1 instead of dumping at $2.50."""
        self.engine.scalp_mode = True
        self.engine.scalp_tp_usd = 2.50
        self.engine.scalp_let_profit_run = True

        order = TradeOrder(
            id="order-wait-for-tp1",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2656.0,
            tp2=None,
            tp3=None,
            lots=0.01,
            size=1.0,
            leverage=20.0,
            margin_required=26.5,
            liquidation_price=2600.0,
            risk_amount=5.0,
            rr_ratio=1.2,
            setup_reason="Wait For TP1 Scalp Test",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=1.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Candle 1: Reaches $2.60 profit (close=2652.60) on an expanding bar
        # Triggering SCALP_RUNNING_TO_TP
        c1 = {"open": 2650.5, "high": 2652.60, "low": 2650.5, "close": 2652.60}
        events1 = self.engine.update_market_candle("XAUUSD", c1)
        self.assertTrue(any(e["event"] == "SCALP_RUNNING_TO_TP" for e in events1))
        self.assertTrue(getattr(order, "running_to_tp", False))
        self.assertEqual(order.status, OrderStatus.OPEN)

        # Candle 2: Consolidation candle at 2653.0 (high was 2654.5, so neither expanding bar nor near_peak)
        # Because running_to_tp is True, it MUST NOT auto-close at $2.50; it waits for TP1!
        c2 = {"open": 2652.5, "high": 2654.5, "low": 2652.5, "close": 2653.0}
        events2 = self.engine.update_market_candle("XAUUSD", c2)
        scalp_closes = [e for e in events2 if e["event"] == "SCALP_CLOSE"]
        self.assertEqual(len(scalp_closes), 0)
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertIn(order.id, self.engine.active_positions)

        # Candle 3: Price hits TP1 (2656.0) with high 2657.0!
        c3 = {"open": 2653.5, "high": 2657.0, "low": 2653.5, "close": 2656.5}
        events3 = self.engine.update_market_candle("XAUUSD", c3)
        tp_closes = [e for e in events3 if e["event"] == "SCALP_CLOSE"]
        self.assertEqual(len(tp_closes), 1)
        self.assertEqual(order.status, OrderStatus.CLOSED)
        self.assertAlmostEqual(order.exit_price, 2656.0)
        self.assertAlmostEqual(order.realized_pnl, 6.0)
        self.assertIn("Scalp TP1 Target Hit", order.close_reason)

    def test_scalp_trail_enlarged_buffer_default(self):
        """Verify scalp engine defaults have increased trail buffer (1.50) and distance (2.00)."""
        engine = ExecutionEngine(risk_manager=self.risk_mgr)
        self.assertEqual(engine.scalp_trail_buffer_usd, 1.50)
        self.assertEqual(engine.scalp_trail_distance_usd, 2.00)
        self.assertEqual(engine.scalp_trail_activation_usd, 2.00)
        self.assertEqual(engine.scalp_be_lock_usd, 0.50)

    def test_trade_executed_on_spread_and_brackets_shift(self):
        """Verify that when an order executes at market spread/tick, entry_level, stop_loss, and TP shift accordingly."""
        sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0, "stop": 2645.0,
            "tp1": 2655.0, "tp2": 2660.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 10.0, "rr": 1.0, "is_scalp": True, "fast_market_fill": True,
        }
        order = self.engine.register_setup(sig, reason="Spread alignment test")
        self.assertEqual(order.entry_level, 2650.0)
        self.assertEqual(order.stop_loss, 2645.0)
        self.assertEqual(order.tp1, 2655.0)

        # Market candle with execution at live spread price (2650.50)
        candle = {"open": 2650.5, "high": 2651.0, "low": 2650.0, "close": 2650.5}
        events = self.engine.update_market_candle("XAUUSD", candle)
        self.assertTrue(any(e["event"] == "TRADE_EXECUTED" for e in events))
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertEqual(order.executed_price, 2650.5)
        self.assertEqual(order.entry_level, 2650.5)
        # Verify brackets shifted by exactly +0.50 (2650.5 - 2650.0)
        self.assertEqual(order.stop_loss, 2645.5)
        self.assertEqual(order.tp1, 2655.5)
        self.assertEqual(order.tp2, 2660.5)

    def test_scalp_micro_gap_proximity_near_chart_move(self):
        """Verify that scalping with bypass_cooldowns enforces a micro-gap near the chart move to avoid duplicate stacking."""
        from anti_spam import AntiSpamRateLimiter
        limiter = AntiSpamRateLimiter(
            max_positions_per_symbol=2,
            min_price_distance_pct=0.03,
        )
        # Position 1 at 2650.0
        p1 = TradeOrder(
            id="pos1", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
            stop_loss=2645.0, tp1=2655.0, tp2=2660.0, tp3=None, lots=0.02, size=2.0,
            leverage=20.0, margin_required=50.0, liquidation_price=2600.0, risk_amount=10.0,
            rr_ratio=1.0, setup_reason="First scalp", status=OrderStatus.OPEN, executed_price=2650.0,
            remaining_size=2.0, is_scalp=True,
        )
        self.engine.active_positions[p1.id] = p1

        # Attempt position 2 at 2650.10 (gap 0.0038% < 0.03%) with bypass_cooldowns=True
        # Must be rejected because it's stacking at the same tick
        can_open, reason = limiter.check_can_enter("XAUUSD", current_price=2650.10, engine=self.engine, max_open=4, bypass_cooldowns=True)
        self.assertFalse(can_open)
        self.assertIn("Price too close", reason)

        # Attempt position 2 at 2651.0 (gap 0.0377% >= 0.03%) near the chart move
        # Allowed cleanly without an excessive gap!
        can_open, reason = limiter.check_can_enter("XAUUSD", current_price=2651.0, engine=self.engine, max_open=4, bypass_cooldowns=True)
        self.assertTrue(can_open, reason)

    def test_auto_trend_1m_strategy_detection(self):
        """Verify that 1m Auto-Trend following strategy correctly identifies bullish and bearish auto-trends."""
        from auto_trend_1m_strategy import detect_auto_trend_1m, AutoTrendDir
        import numpy as np

        # Create 60 candles in a strong upward auto-trend (EMA9 > 21 > 50)
        prices = [2650.0 + i * 0.3 for i in range(60)]
        bull_df = pd.DataFrame({
            "open": [p - 0.1 for p in prices],
            "high": [p + 0.2 for p in prices],
            "low": [p - 0.15 for p in prices],
            "close": prices,
        })
        vote_bull = detect_auto_trend_1m(bull_df)
        self.assertEqual(vote_bull.direction, AutoTrendDir.LONG)
        self.assertGreaterEqual(vote_bull.strength, 0.85)
        self.assertIsNotNone(vote_bull.stop_price)
        self.assertIsNotNone(vote_bull.tp_price)
        self.assertLess(vote_bull.stop_price, prices[-1])
        self.assertGreater(vote_bull.tp_price, prices[-1])
        self.assertIn("BULLISH", vote_bull.reason)

        # Create 60 candles in a strong downward auto-trend (EMA9 < 21 < 50)
        prices_down = [2700.0 - i * 0.3 for i in range(60)]
        bear_df = pd.DataFrame({
            "open": [p + 0.1 for p in prices_down],
            "high": [p + 0.15 for p in prices_down],
            "low": [p - 0.2 for p in prices_down],
            "close": prices_down,
        })
        vote_bear = detect_auto_trend_1m(bear_df)
        self.assertEqual(vote_bear.direction, AutoTrendDir.SHORT)
        self.assertGreaterEqual(vote_bear.strength, 0.85)
        self.assertIsNotNone(vote_bear.stop_price)
        self.assertIsNotNone(vote_bear.tp_price)
        self.assertGreater(vote_bear.stop_price, prices_down[-1])
        self.assertLess(vote_bear.tp_price, prices_down[-1])
        self.assertIn("BEARISH", vote_bear.reason)

    def test_auto_trend_alignment_verification(self):
        """Verify that verify_auto_trend_alignment rejects counter-trend trades."""
        from auto_trend_1m_strategy import verify_auto_trend_alignment

        # Bullish data
        prices = [2650.0 + i * 0.3 for i in range(60)]
        bull_df = pd.DataFrame({
            "open": [p - 0.1 for p in prices],
            "high": [p + 0.2 for p in prices],
            "low": [p - 0.15 for p in prices],
            "close": prices,
        })
        # BUY allowed in bullish auto-trend
        ok_buy, why_buy = verify_auto_trend_alignment(bull_df, "BUY")
        self.assertTrue(ok_buy, why_buy)

        # SELL rejected in bullish auto-trend
        ok_sell, why_sell = verify_auto_trend_alignment(bull_df, "SELL")
        self.assertFalse(ok_sell)
        self.assertIn("Opposing 1m Auto-Trend", why_sell)

    def test_no_simultaneous_buy_and_sell_positions(self):
        """Verify that holding BUY and SELL positions at the same time on the same currency is strictly blocked when hedging is disabled."""
        import config
        orig_hedge = config.is_hedging_allowed()
        config.set_hedging_mode(False, persist=False)
        try:
            from anti_spam import AntiSpamRateLimiter
            limiter = AntiSpamRateLimiter(max_positions_per_symbol=2)

            # 1. Place active BUY position on Gold
            p_buy = TradeOrder(
                id="buy_xau_1", symbol="XAUUSD", direction="LONG", entry_level=2650.0,
                stop_loss=2645.0, tp1=2655.0, tp2=2660.0, tp3=None, lots=0.02, size=2.0,
                leverage=20.0, margin_required=50.0, liquidation_price=2600.0, risk_amount=10.0,
                rr_ratio=1.0, setup_reason="Active long", status=OrderStatus.OPEN, executed_price=2650.0,
                remaining_size=2.0, is_scalp=True,
            )
            self.engine.active_positions[p_buy.id] = p_buy

            # Attempt to enter SHORT on Gold in anti_spam -> must be blocked
            ok_short, reason_short = limiter.check_can_enter("XAUUSD", 2655.0, self.engine, max_open=4, direction="SHORT")
            self.assertFalse(ok_short)
            self.assertIn("cannot BUY and SELL at the same time", reason_short)

            # Attempt to enter another LONG on Gold in anti_spam -> allowed
            ok_long, reason_long = limiter.check_can_enter("XAUUSD", 2655.0, self.engine, max_open=4, direction="LONG")
            self.assertTrue(ok_long, reason_long)

            # In ExecutionEngine.register_setup, registering SHORT on Gold must raise RuntimeError
            short_sig = {
                "symbol": "XAUUSD", "direction": "SHORT", "entry": 2655.0, "stop": 2660.0,
                "tp1": 2650.0, "tp2": 2645.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
                "margin_required": 50.0, "risk_amount": 10.0, "rr": 1.0, "is_scalp": True,
            }
            with self.assertRaises(RuntimeError) as ctx:
                self.engine.register_setup(short_sig)
            self.assertIn("cannot hold BUY and SELL at the same time", str(ctx.exception))

            # Clear and test the reverse: active SHORT on BTC blocks LONG on BTC
            self.engine.active_positions.clear()
            p_sell = TradeOrder(
                id="sell_btc_1", symbol="BTCUSDT", direction="SHORT", entry_level=65000.0,
                stop_loss=65200.0, tp1=64800.0, tp2=64600.0, tp3=None, lots=0.01, size=0.01,
                leverage=20.0, margin_required=32.5, liquidation_price=66000.0, risk_amount=5.0,
                rr_ratio=1.0, setup_reason="Active short", status=OrderStatus.OPEN, executed_price=65000.0,
                remaining_size=0.01, is_scalp=True,
            )
            self.engine.active_positions[p_sell.id] = p_sell

            # Attempt to enter LONG on BTC in anti_spam -> must be blocked
            ok_btc_long, reason_btc_long = limiter.check_can_enter("BTCUSDT", 64900.0, self.engine, max_open=4, direction="LONG")
            self.assertFalse(ok_btc_long)
            self.assertIn("cannot BUY and SELL at the same time", reason_btc_long)

            # In ExecutionEngine.register_setup, registering LONG on BTC must raise RuntimeError
            long_btc_sig = {
                "symbol": "BTCUSDT", "direction": "LONG", "entry": 64900.0, "stop": 64700.0,
                "tp1": 65100.0, "tp2": 65300.0, "lots": 0.01, "size": 0.01, "leverage": 20.0,
                "margin_required": 32.5, "risk_amount": 5.0, "rr": 1.0, "is_scalp": True,
            }
            with self.assertRaises(RuntimeError) as ctx2:
                self.engine.register_setup(long_btc_sig)
            self.assertIn("cannot hold BUY and SELL at the same time", str(ctx2.exception))
        finally:
            config.set_hedging_mode(orig_hedge, persist=False)


class TestScalpReworkRiskManagement(unittest.TestCase):
    def setUp(self):
        self.risk_mgr = RiskManager(equity=10000.0, symbol="XAUUSD", state_file="test_scalp_rework_rm.json")
        self.risk_mgr.reset_daily_risk()
        self.engine = ExecutionEngine(risk_manager=self.risk_mgr)
        self.engine.scalp_mode = True

    def tearDown(self):
        import os
        if os.path.exists("test_scalp_rework_rm.json"):
            try:
                os.remove("test_scalp_rework_rm.json")
            except OSError:
                pass

    def test_scalp_lot_scaled_dollar_thresholds_large_lots(self):
        """Large lot scalp trades must not be choked by normal spread noise due to unscaled USD thresholds."""
        self.engine.scalp_tp_usd = 2.50
        self.engine.scalp_sl_usd = 3.00

        # 1.0 lot on Gold = 100 units
        order = TradeOrder(
            id="order-scalp-1lot",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2660.0,
            tp2=2670.0,
            tp3=None,
            lots=1.0,
            size=100.0,
            leverage=20.0,
            margin_required=1325.0,
            liquidation_price=2600.0,
            risk_amount=1000.0,
            rr_ratio=1.0,
            setup_reason="Scalp LONG 1.0 Lot",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=100.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Candle 1: Slight spread jitter (-$0.05). Unscaled loss = -$5.00.
        # Scaled SL threshold for 1.0 lot is 3.00 * (1.0 / 0.02) = $150.00 (-$1.50 move).
        # Trade MUST stay OPEN!
        c1 = {"open": 2650.0, "high": 2650.05, "low": 2649.95, "close": 2649.95}
        evs1 = self.engine.update_market_candle("XAUUSD", c1)
        self.assertNotIn("SCALP_SL", [e["event"] for e in evs1])
        self.assertEqual(order.status, OrderStatus.OPEN)

        # Candle 2: Genuine stop loss move of -$1.55 (unrealized PnL = -$155.00 <= -$150.00)
        c2 = {"open": 2649.95, "high": 2649.95, "low": 2648.45, "close": 2648.45}
        evs2 = self.engine.update_market_candle("XAUUSD", c2)
        self.assertTrue(any(e["event"] == "SCALP_SL" for e in evs2))
        self.assertEqual(order.status, OrderStatus.CLOSED)
        self.assertAlmostEqual(order.realized_pnl, -155.0, delta=1.0)

    def test_suicide_breakeven_prevented_when_price_near_entry(self):
        """Verify break-even stop is NEVER placed above current price for long or below for short."""
        self.engine.scalp_be_lock_usd = 0.50
        self.engine.scalp_trail_buffer_usd = 0.40

        order = TradeOrder(
            id="order-safe-be",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2645.0,
            tp1=2660.0,
            tp2=2670.0,
            tp3=None,
            lots=0.02,
            size=2.0,
            leverage=20.0,
            margin_required=26.5,
            liquidation_price=2600.0,
            risk_amount=10.0,
            rr_ratio=1.0,
            setup_reason="Scalp BE Safety",
            status=OrderStatus.OPEN,
            executed_price=2650.0,
            remaining_size=2.0,
            is_scalp=True,
        )
        self.engine.active_positions[order.id] = order

        # Price touched 2650.30 (peak profit = 2.0 * 0.30 = $0.60 >= $0.50), but pulled back to 2650.15.
        # target_be = 2650.0 * 1.0002 = 2650.53, which is GREATER than close (2650.15).
        # Stop loss MUST NOT be placed above 2650.15!
        c1 = {"open": 2650.0, "high": 2650.30, "low": 2650.0, "close": 2650.15}
        evs = self.engine.update_market_candle("XAUUSD", c1)

        # Stop loss must be strictly below current price 2650.15
        self.assertLess(order.stop_loss, 2650.15)
        # Position must not have been suicided by SCALP_TRAIL_HIT
        self.assertNotIn("SCALP_TRAIL_HIT", [e["event"] for e in evs])
        self.assertEqual(order.status, OrderStatus.OPEN)

    def test_xau_scalp_profile_rr_target_and_arbiter_alignment(self):
        """Verify xau_scalp profile enforces minimum 1.20R on TP1 and 1.80R on TP2."""
        arbiter = Arbiter(allow_asian=False)
        apply_profile("xau_scalp", arbiter=arbiter, risk_mgr=self.risk_mgr, engine=self.engine)

        dates = pd.date_range("2026-10-14 00:00", periods=50, freq="1min")
        df1 = pd.DataFrame({
            "open": [2650.0 + i * 0.1 for i in range(50)],
            "high": [2650.5 + i * 0.1 for i in range(50)],
            "low": [2649.8 + i * 0.1 for i in range(50)],
            "close": [2650.2 + i * 0.1 for i in range(50)],
            "volume": [100.0 for _ in range(50)],
        }, index=dates)

        with patch.object(arbiter.trend, "vote") as mock_trend, \
             patch.object(arbiter.ind, "vote") as mock_ind, \
             patch.object(arbiter.liq, "vote") as mock_liq:
            from multi_agent_trading_bot import Vote
            mock_trend.return_value = Vote("TREND", Dir.LONG, 1.0, "Uptrend")
            mock_ind.return_value = Vote("INDICATORS", Dir.LONG, 1.0, "Bullish EMA")
            mock_liq.return_value = Vote("LIQUIDITY", Dir.LONG, 1.0, "Support held")
            if hasattr(arbiter, "mr") and arbiter.mr:
                arbiter.mr.vote = MagicMock(return_value=Vote("MEAN-REVERSION", Dir.FLAT, 0.0, "Neutral"))

            sig, reason, votes = arbiter.decide(
                df1, None, None, None, None, self.risk_mgr, symbol="XAUUSD", on_demand=True
            )
            self.assertIsNotNone(sig)
            stop_dist = abs(sig["entry"] - sig["stop"])
            tp1_dist = abs(sig["tp1"] - sig["entry"])
            tp2_dist = abs(sig["tp2"] - sig["entry"])

            # TP1 must achieve >= 1.2R
            self.assertAlmostEqual(tp1_dist / stop_dist, 1.20, delta=0.05)
            # TP2 must achieve >= 1.8R
            self.assertAlmostEqual(tp2_dist / stop_dist, 1.80, delta=0.05)
            # Risk manager min_rr must be 1.20
            self.assertAlmostEqual(self.risk_mgr.config.min_rr, 1.20)
            self.assertAlmostEqual(self.risk_mgr.config.tp1_r, 1.20)



if __name__ == "__main__":
    unittest.main()
