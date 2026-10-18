"""Unit tests for MT5 Live Candlestick Chart Engine and Telegram Integration.

Tests live tick quotes, ASCII candlestick chart generation, technical calculations
(Floor Pivots, EMA9/21, RSI, ATR), Telegram /chart command, /ask and /chat MT5
market context injection, inline keyboard callbacks, and Gemini fallback handling.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat, CallbackQuery
from telegram.ext import ContextTypes

import config
from mt5_chart import (
    normalize_mt5_symbol,
    get_display_names,
    fetch_mt5_live_quote,
    fetch_mt5_candles,
    generate_ascii_candlestick_chart,
    compute_chart_technicals,
    get_mt5_live_chart,
    format_mt5_chart_card,
    format_combined_mt5_chart_dashboard,
    get_mt5_combined_market_context,
    build_chart_keyboard,
)
from gemini_client import GeminiClient
from main import (
    chart_command,
    ask_command,
    autotrade_callback_handler,
    engine,
)


class TestMT5ChartEngine(unittest.TestCase):
    def test_normalize_mt5_symbol(self):
        self.assertEqual(normalize_mt5_symbol("BTCUSD"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("btcusdt"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("BTC"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("bitcoin"), "BTCUSD")
        self.assertEqual(normalize_mt5_symbol("XAUUSD"), "XAUUSD")
        self.assertEqual(normalize_mt5_symbol("gold"), "XAUUSD")
        self.assertEqual(normalize_mt5_symbol("xau"), "XAUUSD")
        self.assertEqual(normalize_mt5_symbol("paxg"), "XAUUSD")
        # Default fallback
        self.assertEqual(normalize_mt5_symbol("UNKNOWN"), "XAUUSD")

    def test_get_display_names(self):
        sym, name, icon = get_display_names("BTCUSD")
        self.assertEqual(sym, "BTCUSD")
        self.assertEqual(name, "Bitcoin")
        self.assertEqual(icon, "🪙")

        sym_g, name_g, icon_g = get_display_names("XAUUSD")
        self.assertEqual(sym_g, "XAUUSD")
        self.assertEqual(name_g, "Gold")
        self.assertEqual(icon_g, "🟡")

    def test_fetch_mt5_live_quote_with_broker(self):
        broker = MagicMock()
        broker.mark_prices = {"BTCUSD": 68500.0, "XAUUSD": 2720.50}

        btc_quote = fetch_mt5_live_quote("BTCUSD", broker=broker)
        self.assertTrue(btc_quote["ok"])
        self.assertEqual(btc_quote["mark"], 68500.0)
        self.assertEqual(btc_quote["symbol"], "BTCUSD")
        self.assertEqual(btc_quote["bid"], 68498.25)
        self.assertEqual(btc_quote["ask"], 68501.75)
        self.assertEqual(btc_quote["spread"], 3.50)

        gold_quote = fetch_mt5_live_quote("XAUUSD", broker=broker)
        self.assertTrue(gold_quote["ok"])
        self.assertEqual(gold_quote["mark"], 2720.50)
        self.assertEqual(gold_quote["symbol"], "XAUUSD")
        self.assertEqual(gold_quote["bid"], 2720.32)
        self.assertEqual(gold_quote["ask"], 2720.68)
        self.assertEqual(gold_quote["spread"], 0.35)

    def test_fetch_mt5_candles_synthetic_fallback(self):
        broker = MagicMock()
        broker.mark_prices = {"XAUUSD": 2700.0}

        candles = fetch_mt5_candles("XAUUSD", timeframe="1m", limit=10, broker=broker)
        self.assertEqual(len(candles), 10)
        self.assertEqual(candles[-1]["close"], 2700.0)
        for c in candles:
            self.assertIn("open", c)
            self.assertIn("high", c)
            self.assertIn("low", c)
            self.assertIn("close", c)
            self.assertIn("volume", c)
            self.assertGreaterEqual(c["high"], c["low"])

    def test_generate_ascii_candlestick_chart(self):
        candles = [
            {"open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 10},
            {"open": 104.0, "high": 106.0, "low": 101.0, "close": 102.0, "volume": 12},
            {"open": 102.0, "high": 108.0, "low": 100.0, "close": 107.0, "volume": 15},
            {"open": 107.0, "high": 110.0, "low": 105.0, "close": 109.0, "volume": 14},
        ]
        chart_str = generate_ascii_candlestick_chart(candles, height=6)
        self.assertIn("┤", chart_str)
        self.assertIn("└", chart_str)
        self.assertIn("NOW ◄", chart_str)
        # Should render body blocks
        self.assertTrue("█" in chart_str or "░" in chart_str or "│" in chart_str)

        # Empty / insufficient candles handling
        insufficient = generate_ascii_candlestick_chart([], height=6)
        self.assertIn("Insufficient", insufficient)

    def test_compute_chart_technicals(self):
        candles = [
            {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10},
            {"open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0, "volume": 12},
            {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 15},
            {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 14},
            {"open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0, "volume": 20},
        ]
        tech = compute_chart_technicals(candles, live_mark=107.0)
        self.assertIn("ema9", tech)
        self.assertIn("ema21", tech)
        self.assertIn("rsi", tech)
        self.assertIn("atr", tech)
        self.assertIn("pivot", tech)
        self.assertIn("r1", tech)
        self.assertIn("r2", tech)
        self.assertIn("s1", tech)
        self.assertIn("s2", tech)
        self.assertIn("trend_bias", tech)
        self.assertGreaterEqual(tech["r2"], tech["r1"])
        self.assertGreaterEqual(tech["r1"], tech["pivot"])
        self.assertGreaterEqual(tech["pivot"], tech["s1"])
        self.assertGreaterEqual(tech["s1"], tech["s2"])

    def test_get_mt5_live_chart_bundle(self):
        broker = MagicMock()
        broker.mark_prices = {"BTCUSD": 67000.0}
        broker.server = "MetaQuotes-Demo"
        broker.login = 123456
        broker.mode = "LIVE"
        broker.is_connected = lambda: True

        bundle = get_mt5_live_chart("BTCUSD", timeframe="1m", broker=broker)
        self.assertEqual(bundle["symbol"], "BTCUSD")
        self.assertEqual(bundle["name"], "Bitcoin")
        self.assertEqual(bundle["mark"], 67000.0)
        self.assertEqual(bundle["server"], "MetaQuotes-Demo")
        self.assertEqual(bundle["login"], 123456)
        self.assertTrue(bundle["trade_ready"])
        self.assertIn("technicals", bundle)
        self.assertIn("ascii_chart", bundle)

    def test_format_mt5_chart_card(self):
        broker = MagicMock()
        broker.mark_prices = {"XAUUSD": 2715.20}
        chart_data = get_mt5_live_chart("XAUUSD", broker=broker)
        card = format_mt5_chart_card(chart_data)
        self.assertIn("MT5 Live Chart", card)
        self.assertIn("Gold (`XAUUSD`)", card)
        self.assertIn("2,715.20", card)
        self.assertIn("MT5 Structural Pivot Levels", card)
        self.assertIn("R2 Resistance", card)
        self.assertIn("Pivot (Base)", card)
        self.assertIn("S2 Support", card)
        self.assertIn("Live Candlestick Action", card)

    def test_format_combined_mt5_chart_dashboard(self):
        broker = MagicMock()
        broker.mark_prices = {"BTCUSD": 68000.0, "XAUUSD": 2725.0}
        dash = format_combined_mt5_chart_dashboard(broker=broker)
        self.assertIn("MetaTrader 5 (MT5) Institutional Live Charts", dash)
        self.assertIn("Gold (`XAUUSD`)", dash)
        self.assertIn("Bitcoin (`BTCUSD`)", dash)

    def test_get_mt5_combined_market_context(self):
        broker = MagicMock()
        broker.mark_prices = {"BTCUSD": 68200.0, "XAUUSD": 2730.0}
        ctx = get_mt5_combined_market_context(broker=broker)
        self.assertIn("=== MT5 LIVE CHART & MARKET CONTEXT ===", ctx)
        self.assertIn("[MT5 Live Gold (XAUUSD)]", ctx)
        self.assertIn("[MT5 Live Bitcoin (BTCUSD)]", ctx)
        self.assertIn("Mark Price: $2,730.00", ctx)
        self.assertIn("Mark Price: $68,200.00", ctx)
        self.assertIn("MT5 Pivots:", ctx)
        self.assertIn("=== END MT5 LIVE CHART CONTEXT ===", ctx)

    def test_build_chart_keyboard(self):
        kb = build_chart_keyboard("both")
        self.assertIsNotNone(kb)
        btn_data = [[b.callback_data for b in row] for row in kb.inline_keyboard]
        flat_data = [item for sublist in btn_data for item in sublist]
        self.assertIn("chart:gold", flat_data)
        self.assertIn("chart:btc", flat_data)
        self.assertIn("chart:both", flat_data)
        self.assertIn("chart:refresh:both", flat_data)
        self.assertIn("sig:XAUUSD", flat_data)
        self.assertIn("sig:BTCUSDT", flat_data)


class TestTelegramMT5ChartCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.chat = self.chat
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.message = self.message
        self.update.effective_chat = self.chat
        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def test_chart_command_default_both(self):
        self.ctx.args = []
        await chart_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 (MT5) Institutional Live Charts", text)
        self.assertIn("Gold (`XAUUSD`)", text)
        self.assertIn("Bitcoin (`BTCUSD`)", text)

    async def test_chart_command_btc(self):
        self.ctx.args = ["btc"]
        await chart_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Bitcoin (`BTCUSD`)", text)
        self.assertNotIn("Gold (`XAUUSD`)", text)

    async def test_chart_command_gold(self):
        self.ctx.args = ["gold"]
        await chart_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Gold (`XAUUSD`)", text)
        self.assertNotIn("Bitcoin (`BTCUSD`)", text)

    async def test_ask_command_chart_shortcut_btc(self):
        self.ctx.args = ["btc", "chart"]
        await ask_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Bitcoin (`BTCUSD`)", text)
        self.assertIn("MT5 Live Chart", text)

    async def test_ask_command_chart_shortcut_gold(self):
        self.ctx.args = ["gold", "chart"]
        await ask_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Gold (`XAUUSD`)", text)
        self.assertIn("MT5 Live Chart", text)

    @patch("gemini_client.GeminiClient.ask")
    async def test_ask_command_injects_mt5_context(self, mock_ask):
        mock_ask.return_value = "Market analysis from Gemini"
        self.ctx.args = ["What", "is", "the", "current", "trend?"]
        await ask_command(self.update, self.ctx)

        mock_ask.assert_called_once()
        call_kwargs = mock_ask.call_args[1]
        market_ctx = call_kwargs.get("market_context", "")
        self.assertIn("MT5 LIVE CHART & MARKET CONTEXT", market_ctx)
        self.assertIn("Gold (XAUUSD)", market_ctx)
        self.assertIn("Bitcoin (BTCUSD)", market_ctx)

    async def test_callback_chart_btc(self):
        query = MagicMock(spec=CallbackQuery)
        query.data = "chart:btc"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock(spec=Update)
        update.callback_query = query

        await autotrade_callback_handler(update, self.ctx)
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        self.assertIn("Bitcoin (`BTCUSD`)", text)

    async def test_callback_chart_gold(self):
        query = MagicMock(spec=CallbackQuery)
        query.data = "chart:gold"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock(spec=Update)
        update.callback_query = query

        await autotrade_callback_handler(update, self.ctx)
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        self.assertIn("Gold (`XAUUSD`)", text)

    async def test_callback_chart_refresh(self):
        query = MagicMock(spec=CallbackQuery)
        query.data = "chart:refresh:btc"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock(spec=Update)
        update.callback_query = query

        await autotrade_callback_handler(update, self.ctx)
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        self.assertIn("Bitcoin (`BTCUSD`)", text)


class TestGeminiClientMT5ChartFallback(unittest.TestCase):
    def test_gemini_fallback_btc_chart(self):
        client = GeminiClient(api_key="")
        resp = client.ask(chat_id=999, user_text="show me btc live chart")
        self.assertIn("MT5 Live Chart", resp)
        self.assertIn("Bitcoin (`BTCUSD`)", resp)

    def test_gemini_fallback_gold_chart(self):
        client = GeminiClient(api_key="")
        resp = client.ask(chat_id=999, user_text="show gold chart please")
        self.assertIn("MT5 Live Chart", resp)
        self.assertIn("Gold (`XAUUSD`)", resp)

    def test_gemini_fallback_both_charts(self):
        client = GeminiClient(api_key="")
        resp = client.ask(chat_id=999, user_text="give me the charts")
        self.assertIn("MetaTrader 5 (MT5) Institutional Live Charts", resp)
        self.assertIn("Bitcoin (`BTCUSD`)", resp)
        self.assertIn("Gold (`XAUUSD`)", resp)


if __name__ == "__main__":
    unittest.main()
