"""
Unit tests for Autotrade selection and BTC / Gold Watchlist.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
from autotrade_worker import live_monitoring_worker
import main


class TestWatchlistAutotrade(unittest.TestCase):
    def setUp(self):
        # Reset symbols to default
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)

    def tearDown(self):
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)
        p = Path(config._WATCHLIST_PERSIST_FILE)
        if p.is_file():
            try:
                p.unlink()
            except Exception:
                pass

    def test_normalization_and_selection(self):
        # Bitcoin only
        self.assertEqual(config.set_autotrade_symbols("btc", persist=False), ["BTCUSDT"])
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT"])

        self.assertEqual(config.set_autotrade_symbols("bitcoin", persist=False), ["BTCUSDT"])
        self.assertEqual(config.set_autotrade_symbols("btcusd", persist=False), ["BTCUSDT"])

        # Gold only
        self.assertEqual(config.set_autotrade_symbols("gold", persist=False), ["XAUUSD"])
        self.assertEqual(config.get_autotrade_symbols(), ["XAUUSD"])

        self.assertEqual(config.set_autotrade_symbols("xau", persist=False), ["XAUUSD"])
        self.assertEqual(config.set_autotrade_symbols("xauusd", persist=False), ["XAUUSD"])
        self.assertEqual(config.set_autotrade_symbols("paxg", persist=False), ["XAUUSD"])

        # Both
        self.assertEqual(config.set_autotrade_symbols("both", persist=False), ["BTCUSDT", "XAUUSD"])
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT", "XAUUSD"])

        self.assertEqual(config.set_autotrade_symbols("all", persist=False), ["BTCUSDT", "XAUUSD"])
        self.assertEqual(config.set_autotrade_symbols("btc,gold", persist=False), ["BTCUSDT", "XAUUSD"])
        self.assertEqual(config.set_autotrade_symbols("gold,btc", persist=False), ["BTCUSDT", "XAUUSD"])

    def test_disallowed_symbols_filtered(self):
        # Disallowed symbols like EURUSD, AAPL must be filtered out
        res = config.set_autotrade_symbols("EURUSD,AAPL,BTC", persist=False)
        self.assertEqual(res, ["BTCUSDT"])

        # If all disallowed, falls back to default ["BTCUSDT", "XAUUSD"]
        res_empty = config.set_autotrade_symbols("EURUSD,GBPUSD", persist=False)
        self.assertEqual(res_empty, ["BTCUSDT", "XAUUSD"])

    def test_watchlist_persistence(self):
        tmp_runtime = Path(config._WATCHLIST_PERSIST_FILE)
        orig_content = tmp_runtime.read_text(encoding="utf-8") if tmp_runtime.is_file() else None
        try:
            config.set_autotrade_symbols("btc", persist=True)
            self.assertTrue(tmp_runtime.is_file())
            data = json.loads(tmp_runtime.read_text(encoding="utf-8"))
            self.assertEqual(data.get("symbols"), ["BTCUSDT"])

            # Test loader
            loaded = config._load_persisted_watchlist()
            self.assertEqual(loaded, ["BTCUSDT"])
        finally:
            if orig_content is not None:
                tmp_runtime.write_text(orig_content, encoding="utf-8")
            elif tmp_runtime.is_file():
                tmp_runtime.unlink()

    def test_gold_market_status(self):
        status, desc = main.get_gold_market_status()
        self.assertIsInstance(status, bool)
        self.assertIsInstance(desc, str)

    def test_build_watchlist_dashboard(self):
        text, markup = main.build_watchlist_dashboard()
        self.assertIn("Gold & Bitcoin Trading Watchlist", text)
        self.assertIn("BTCUSDT", text)
        self.assertIn("XAUUSD", text)
        self.assertIsNotNone(markup)
        # Verify inline keyboard rows
        inline_buttons = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("wl:btc", inline_buttons)
        self.assertIn("wl:gold", inline_buttons)
        self.assertIn("wl:both", inline_buttons)
        self.assertIn("auto:on", inline_buttons)
        self.assertIn("auto:off", inline_buttons)
        self.assertIn("auto:scalp", inline_buttons)


class TestWatchlistAsyncHandlers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)
        main.autotrade_active = False

    def tearDown(self):
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"], persist=False)
        main.autotrade_active = False
        p = Path(config._WATCHLIST_PERSIST_FILE)
        if p.is_file():
            try:
                p.unlink()
            except Exception:
                pass

    async def test_watchlist_command_btc(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["btc"]

        await main.watchlist_command(update, ctx)
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT"])
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Bitcoin Only", call_text)

    async def test_watchlist_command_gold(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["gold"]

        await main.watchlist_command(update, ctx)
        self.assertEqual(config.get_autotrade_symbols(), ["XAUUSD"])
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Gold Only", call_text)

    async def test_watchlist_command_both(self):
        config.set_autotrade_symbols(["BTCUSDT"], persist=False)
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["both"]

        await main.watchlist_command(update, ctx)
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT", "XAUUSD"])
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Both (BTC + Gold)", call_text)

    async def test_autotrade_command_btc_selection(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["btc"]

        await main.autotrade_command(update, ctx)
        self.assertTrue(main.autotrade_active)
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT"])
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Autotrade ACTIVATED", call_text)
        self.assertIn("BTCUSDT", call_text)

    async def test_autotrade_command_scalp_gold(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["scalp", "gold"]

        await main.autotrade_command(update, ctx)
        self.assertTrue(main.autotrade_active)
        self.assertEqual(config.get_autotrade_symbols(), ["XAUUSD"])
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        self.assertIn("RAPID SCALP AUTOTRADE ACTIVATED", call_text)
        self.assertIn("XAUUSD", call_text)

    async def test_autotrade_callback_handler(self):
        update = MagicMock()
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.data = "wl:btc"
        update.callback_query = query
        ctx = MagicMock()

        await main.autotrade_callback_handler(update, ctx)
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT"])
        query.edit_message_text.assert_called_once()

        # Toggle to both
        query.data = "wl:both"
        await main.autotrade_callback_handler(update, ctx)
        self.assertEqual(config.get_autotrade_symbols(), ["BTCUSDT", "XAUUSD"])

        # Turn autotrade on
        query.data = "auto:on"
        await main.autotrade_callback_handler(update, ctx)
        self.assertTrue(main.autotrade_active)

        # Turn autotrade off
        query.data = "auto:off"
        await main.autotrade_callback_handler(update, ctx)
        self.assertFalse(main.autotrade_active)


if __name__ == "__main__":
    unittest.main()
