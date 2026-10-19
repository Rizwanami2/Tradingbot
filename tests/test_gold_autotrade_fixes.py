import unittest
from datetime import datetime, time, timezone
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

import config
from session_filter import get_session_info, is_tradeable_session
from strategy_profiles import (
    DEFAULT_PROFILES,
    StrategyProfile,
    apply_profile,
    get_profile,
)
from regime_auto_switcher import RegimeAutoSwitcher, RegimeSwitchResult
from mt5_broker import resolve_mt5_symbol
from tradingview_feed import fetch_tradingview_bars


class TestGoldAutotradeFixes(unittest.TestCase):
    def setUp(self):
        config.PROFILE_LOCKED = False
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)

    def test_gold_profiles_allow_asian_session(self):
        """Verify gold_only and balanced profiles allow Asian session trading."""
        gold_prof = get_profile("gold_only")
        self.assertTrue(gold_prof.allow_asian)
        self.assertFalse(gold_prof.require_killzone)
        self.assertLessEqual(gold_prof.min_score, 1.25)
        self.assertLessEqual(gold_prof.min_rr, 1.6)

        balanced_prof = get_profile("balanced")
        self.assertTrue(balanced_prof.allow_asian)
        self.assertFalse(balanced_prof.require_killzone)

        scalp_prof = get_profile("scalp")
        self.assertTrue(scalp_prof.allow_asian)
        self.assertFalse(scalp_prof.require_killzone)

    def test_asian_session_tradeable_for_gold(self):
        """Verify is_tradeable_session allows Gold during Asian session (00:00-07:00 UTC)."""
        # Tuesday 03:00 UTC (weekday 1)
        asian_ts = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)
        ok, reason = is_tradeable_session(asian_ts, allow_asian=True, symbol="XAUUSD")
        self.assertTrue(ok)
        self.assertIn("Asian session (allowed)", reason)

        # Tuesday 22:30 UTC (Sydney/Asia open transition)
        sydney_ts = datetime(2026, 10, 20, 22, 30, tzinfo=timezone.utc)
        ok2, reason2 = is_tradeable_session(sydney_ts, allow_asian=True, symbol="XAUUSD")
        self.assertTrue(ok2)
        self.assertIn("allowed", reason2)

    def test_regime_auto_switcher_preserves_scalp_for_gold(self):
        """Verify RegimeAutoSwitcher does not disrupt active scalp profile for Gold on high ADX."""
        switcher = RegimeAutoSwitcher(cooldown_seconds=0.0)
        apply_profile("scalp", config=config, lock=False)

        # High-trend expansion data that produces high ADX
        df_trend = pd.DataFrame({
            "open": [2700.0 + (i * 3.0) for i in range(40)],
            "high": [2706.0 + (i * 3.0) for i in range(40)],
            "low": [2698.0 + (i * 3.0) for i in range(40)],
            "close": [2705.0 + (i * 3.0) for i in range(40)],
        })
        res = switcher.evaluate_and_switch(df_trend, symbol="XAUUSD", config=config)
        self.assertEqual(res.current_profile, "scalp")
        self.assertIn("Micro-scalp mode preserved", res.reason)

    def test_multi_asset_watchlist_prevents_single_asset_thrashing(self):
        """Verify multi-asset watchlist (BTC + Gold) never auto-switches to single-asset profiles."""
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)
        switcher = RegimeAutoSwitcher(cooldown_seconds=0.0)
        apply_profile("scalp", config=config, lock=False)

        df_gold = pd.DataFrame({
            "open": [2700.0 + (i * 2.5) for i in range(40)],
            "high": [2705.0 + (i * 2.5) for i in range(40)],
            "low": [2695.0 + (i * 2.5) for i in range(40)],
            "close": [2704.0 + (i * 2.5) for i in range(40)],
        })
        res = switcher.evaluate_and_switch(df_gold, symbol="XAUUSD", config=config)
        self.assertNotEqual(res.current_profile, "gold_only")
        self.assertEqual(res.current_profile, "scalp")

    def test_resolve_mt5_symbol_reverse_aliases(self):
        """Verify MT5 symbol resolution handles broker naming variations (GOLD, GOLD.m, etc.)."""
        # When broker only offers "GOLD"
        resolved = resolve_mt5_symbol("XAUUSD", ["GOLD", "BTCUSD", "EURUSD"])
        self.assertEqual(resolved, "GOLD")

        # When broker offers "GOLD.raw" or "GOLD.m"
        resolved_suffix = resolve_mt5_symbol("XAUUSD", ["GOLD.RAW", "BTCUSD"])
        self.assertEqual(resolved_suffix, "GOLD.RAW")

        # When broker offers "XAUUSD.m"
        resolved_xau_suffix = resolve_mt5_symbol("XAUUSD", ["XAUUSD.M", "BTCUSD.M"])
        self.assertEqual(resolved_xau_suffix, "XAUUSD.M")

        # Bitcoin resolution
        resolved_btc = resolve_mt5_symbol("BTCUSDT", ["BTCUSD", "GOLD"])
        self.assertEqual(resolved_btc, "BTCUSD")

    def test_tradingview_feed_signatures(self):
        """Verify fetch_tradingview_bars supports 2-arg and 3-arg invocations without TypeError."""
        # 3-arg call (symbol, timeframe, limit)
        df3 = fetch_tradingview_bars("XAUUSD", "1m", 50)
        self.assertIsInstance(df3, pd.DataFrame)
        self.assertGreaterEqual(len(df3), 10)
        self.assertIn("close", df3.columns)

        # 2-arg call (symbol, limit)
        df2 = fetch_tradingview_bars("XAUUSD", 50)
        self.assertIsInstance(df2, pd.DataFrame)
        self.assertGreaterEqual(len(df2), 10)
        self.assertIn("close", df2.columns)

    @unittest.mock.patch("main.autotrade_active", False)
    def test_scalp_command_arguments(self):
        """Verify /scalp command arguments toggle autotrade and watchlist correctly."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from telegram_advanced import scalp_command
        import main

        update = MagicMock()
        ctx = MagicMock()

        with patch("telegram_advanced.safe_reply", new_callable=AsyncMock):
            # /scalp gold
            ctx.args = ["gold"]
            asyncio.run(scalp_command(update, ctx))
            self.assertEqual(config.get_autotrade_symbols(), ["XAUUSD"])
            self.assertTrue(main.autotrade_active)
            self.assertTrue(config.PROFILE_LOCKED)

            # /scalp both
            ctx.args = ["both"]
            asyncio.run(scalp_command(update, ctx))
            self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT", "XAUUSD"])
            self.assertTrue(main.autotrade_active)

            # /scalp off
            ctx.args = ["off"]
            asyncio.run(scalp_command(update, ctx))
            self.assertFalse(main.autotrade_active)


if __name__ == "__main__":
    unittest.main()
