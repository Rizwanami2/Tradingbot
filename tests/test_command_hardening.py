"""
Unit tests for Telegram & CLI command hardening, safe markdown replies, and collision resilience.
Verifies that:
1. Every single Telegram command handler executes cleanly without exceptions.
2. Every single CLI command in run_cli_interactive() executes cleanly without exceptions.
3. MTF Matrix confluence and Signal Quality filters are active and protect autotrade.
4. TradeJournal lifecycle records both open and close fills for complete learning feedback.
5. Entity formatting and square bracket sanitization prevent Telegram API entity parse errors.
6. Dashboard collision handling recovers gracefully when port 8765 is already bound.
"""
import asyncio
import io
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from mtf_matrix import check_mtf_confluence, render_telegram_heatmap, render_telegram_multi_matrix
from ops_dashboard import start_dashboard_background, stop_dashboard_background
from risk_manager import RiskManager
from telegram_brain import safe_reply
from trade_journal import TradeJournal


class TestCommandHardening(unittest.TestCase):

    def test_safe_reply_handles_parse_error(self):
        """Verify safe_reply falls back to plain text when Telegram raises entity parse errors."""
        async def run_test():
            msg_mock = AsyncMock()
            msg_mock.reply_text.side_effect = [
                Exception("Can't parse entities: character '[' is reserved"),
                AsyncMock(text="plain reply ok"),
            ]
            update_mock = MagicMock()
            update_mock.message = msg_mock

            res = await safe_reply(update_mock, "Test message with broken [bracket markdown", parse_mode="Markdown")
            self.assertIsNotNone(res)
            self.assertEqual(msg_mock.reply_text.call_count, 2)
            self.assertIsNone(msg_mock.reply_text.call_args_list[1].kwargs.get("parse_mode"))

        asyncio.run(run_test())

    def test_safe_reply_with_reply_markup(self):
        """Verify safe_reply forwards reply_markup properly."""
        async def run_test():
            msg_mock = AsyncMock()
            msg_mock.reply_text.return_value = AsyncMock()
            update_mock = MagicMock()
            update_mock.message = msg_mock
            markup_mock = MagicMock()

            await safe_reply(update_mock, "Message with buttons", reply_markup=markup_mock)
            self.assertEqual(msg_mock.reply_text.call_args.kwargs.get("reply_markup"), markup_mock)

        asyncio.run(run_test())

    def test_render_telegram_heatmap_brackets(self):
        """Verify rendered heatmap output does not contain bare unescaped square brackets."""
        hm = render_telegram_heatmap("XAUUSD")
        self.assertIsInstance(hm, str)
        for line in hm.splitlines():
            if "🟥" in line or "🟩" in line or "⬜" in line:
                self.assertTrue(line.startswith("`") and line.endswith("`"), f"Score bar line not wrapped in code backticks: {line}")

        mm = render_telegram_multi_matrix(["XAUUSD", "BTCUSDT"])
        self.assertIsInstance(mm, str)
        for line in mm.splitlines():
            if "Score:" in line:
                self.assertNotIn("[🟥", line)
                self.assertNotIn("[🟩", line)
                self.assertNotIn("[⬜", line)

    def test_format_positions_detail_brackets(self):
        """Verify execution engine format_positions_detail uses parentheses instead of brackets for IDs."""
        rm = RiskManager(equity=10000.0)
        engine = ExecutionEngine(risk_manager=rm)
        engine.active_positions["TEST"] = MagicMock(
            symbol="TEST",
            executed_price=100.0,
            remaining_size=1.0,
            direction="LONG",
            unrealized_pnl=5.0,
            stop_loss=90.0,
            tp1=110.0,
            tp2=120.0,
            lots=0.1,
            leverage=10,
            margin_required=10.0,
            id="pos123",
            ticket=555666,
            trailing_sl_active=False,
        )
        engine.pending_setups["PEND"] = MagicMock(
            symbol="PEND",
            entry_level=200.0,
            stop_loss=190.0,
            lots=0.1,
            direction="BUY",
            id="ord789",
        )

        detail = engine.format_positions_detail()
        self.assertIn("(#pos123)", detail)
        self.assertIn("(#ord789)", detail)
        self.assertNotIn("[#pos123]", detail)
        self.assertNotIn("[#ord789]", detail)

    def test_dashboard_port_collision_resilience(self):
        """Verify start_dashboard_background gracefully handles port collision."""
        stop_dashboard_background()
        with patch("ops_dashboard.create_server") as mock_create:
            mock_create.side_effect = OSError(98, "Address already in use")
            srv, url = start_dashboard_background(host="127.0.0.1", port=8765)
            self.assertIsNone(srv)
            self.assertEqual(url, "http://127.0.0.1:8765")

    def test_mtf_confluence_filter_veto_and_confirm(self):
        """Verify check_mtf_confluence correctly vetoes or confirms signals based on MTF score."""
        with patch("mtf_matrix.analyze_symbol_mtf") as mock_analyze:
            # Case 1: Severe bear trend vs BUY signal -> VETO
            mock_analyze.return_value = MagicMock(
                overall_score=-75.0,
                confluence_grade="STRONG SELL 🔻",
                ltf_htf_alignment="ALIGNED ⚡",
            )
            allowed, reason, mult = check_mtf_confluence("XAUUSD", "BUY")
            self.assertFalse(allowed)
            self.assertIn("bearish", reason.lower())

            # Case 2: Strong bull trend vs BUY signal -> CONFIRM
            mock_analyze.return_value = MagicMock(
                overall_score=55.0,
                confluence_grade="STRONG BUY 🚀",
                ltf_htf_alignment="ALIGNED ⚡",
            )
            allowed, reason, mult = check_mtf_confluence("XAUUSD", "BUY")
            self.assertTrue(allowed)
            self.assertGreater(mult, 1.0)

            # Case 3: Severe bull trend vs SELL signal -> VETO
            mock_analyze.return_value = MagicMock(
                overall_score=75.0,
                confluence_grade="STRONG BUY 🚀",
                ltf_htf_alignment="ALIGNED ⚡",
            )
            allowed, reason, mult = check_mtf_confluence("XAUUSD", "SELL")
            self.assertFalse(allowed)
            self.assertIn("bullish", reason.lower())

    def test_autotrade_trade_journal_lifecycle(self):
        """Verify TradeJournal lifecycle: trade execution records open and close correctly."""
        rm = RiskManager(equity=10000.0)
        engine = ExecutionEngine(risk_manager=rm)
        test_order = TradeOrder(
            id="t-order-test-99",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2650.0,
            stop_loss=2640.0,
            tp1=2665.0,
            tp2=2680.0,
            tp3=None,
            lots=0.1,
            size=10.0,
            leverage=20.0,
            margin_required=132.5,
            liquidation_price=None,
            risk_amount=100.0,
            rr_ratio=1.5,
            setup_reason="MTF Confluence test",
            status=OrderStatus.PENDING,
        )
        engine.pending_setups["t-order-test-99"] = test_order

        # Trigger candle hit to execute order
        candle = {"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2650.0}
        events = engine.update_market_candle("XAUUSD", candle)
        exec_events = [e for e in events if e.get("event") == "TRADE_EXECUTED"]
        self.assertTrue(len(exec_events) >= 1)

        # Verify trade is tracked in TradeJournal open entries
        journal = TradeJournal()
        self.assertIn("t-order-test-99", journal.open)

        # Close position
        closed_pos = engine.close_position("t-order-test-99", reason="TP Hit test")
        self.assertIsNotNone(closed_pos)

        # Verify trade is moved to closed entries in TradeJournal
        journal.reload()
        self.assertNotIn("t-order-test-99", journal.open)
        closed_matches = [e for e in journal.entries if e.get("id") == "t-order-test-99"]
        self.assertTrue(len(closed_matches) >= 1)

    def test_all_telegram_command_handlers_invocable(self):
        """Verify every registered Telegram command handler executes cleanly without exceptions."""
        from main import (
            start_command,
            help_command,
            levels_command,
            price_command,
            chart_command,
            news_command,
            signal_command,
            positions_command,
            slots_command,
            matrix_command,
            dashboard_command,
            autotrade_command,
            watchlist_command,
            close_command,
            closed_command,
            cancel_command,
            balance_command,
            delta_command,
            mt5_command,
            bridge_command,
            switch_command,
            live_command,
            broker_command,
            oanda_command,
            alpaca_command,
            ccxt_command,
            slippage_command,
            vstops_command,
            status_command,
            health_command,
            cooldown_command,
            spam_command,
            risk_command,
            resetrisk_command,
            leverage_command,
            lot_command,
            kelly_command,
            regime_command,
            session_command,
            backtest_command,
            ask_command,
            reset_command,
            unknown_command,
        )

        from telegram_advanced import (
            asignal_command,
            kill_command,
            performance_command,
            abacktest_command,
            profile_command,
        )
        from telegram_feature_pack import (
            board_command,
            journal_command,
            agents_command,
            tiers_command,
        )
        from telegram_feature_pack_v4 import (
            quality_command,
            digest_command,
            replay_command,
            export_command,
            setups_command,
            abooks_command,
            heal_command,
        )
        from telegram_brain import (
            brain_command,
            relearn_command,
            train_command,
        )

        all_handlers = [
            ("start", start_command, []),
            ("help", help_command, []),
            ("help_cat", help_command, ["market"]),
            ("levels", levels_command, ["XAUUSD"]),
            ("price", price_command, ["BTCUSDT"]),
            ("news", news_command, []),
            ("signal", signal_command, ["XAUUSD"]),
            ("asignal", asignal_command, ["XAUUSD"]),
            ("quality", quality_command, ["XAUUSD"]),
            ("positions", positions_command, []),
            ("slots", slots_command, []),
            ("slots_auto", slots_command, ["auto"]),
            ("matrix", matrix_command, []),
            ("matrix_gold", matrix_command, ["XAUUSD"]),
            ("dashboard", dashboard_command, ["status"]),
            ("autotrade", autotrade_command, []),
            ("watchlist", watchlist_command, []),
            ("close", close_command, ["*"]),
            ("closed", closed_command, []),
            ("cancel", cancel_command, ["XAUUSD"]),
            ("balance", balance_command, []),
            ("delta", delta_command, []),
            ("mt5", mt5_command, []),
            ("bridge", bridge_command, []),
            ("switch", switch_command, []),
            ("live", live_command, []),
            ("broker", broker_command, []),
            ("oanda", oanda_command, []),
            ("alpaca", alpaca_command, []),
            ("ccxt", ccxt_command, []),
            ("slippage", slippage_command, []),
            ("vstops", vstops_command, []),
            ("status", status_command, []),
            ("health", health_command, []),
            ("cooldown", cooldown_command, []),
            ("spam", spam_command, []),
            ("risk", risk_command, []),
            ("resetrisk", resetrisk_command, []),
            ("leverage", leverage_command, []),
            ("lot", lot_command, []),
            ("kelly", kelly_command, []),
            ("regime", regime_command, []),
            ("session", session_command, []),
            ("kill", kill_command, []),
            ("backtest", backtest_command, []),
            ("abacktest", abacktest_command, []),
            ("performance", performance_command, []),
            ("profile", profile_command, []),
            ("board", board_command, []),
            ("journal", journal_command, []),
            ("agents", agents_command, []),
            ("tiers", tiers_command, []),
            ("digest", digest_command, []),
            ("replay", replay_command, ["1"]),
            ("export", export_command, []),
            ("setups", setups_command, []),
            ("abooks", abooks_command, []),
            ("heal", heal_command, []),
            ("brain", brain_command, []),
            ("train_status", train_command, ["status"]),
            ("relearn", relearn_command, ["XAUUSD", "0.01"]),
            ("ask", ask_command, ["trend?"]),
            ("reset", reset_command, []),
            ("unknown", unknown_command, []),
        ]

        async def run_all():
            mock_msg = AsyncMock()
            mock_msg.text = "/test"
            mock_update = MagicMock()
            mock_update.message = mock_msg
            mock_update.effective_chat = MagicMock(id=12345)
            mock_ctx = MagicMock()

            for name, handler, args in all_handlers:
                mock_ctx.args = list(args)
                try:
                    await handler(mock_update, mock_ctx)
                except Exception as e:
                    self.fail(f"Handler '{name}' raised an unexpected exception: {e}")

        asyncio.run(run_all())

    def test_all_cli_interactive_commands(self):
        """Verify every CLI command runs without raising an exception in run_cli_interactive."""
        from main import run_cli_interactive

        cli_commands = [
            "help",
            "price BTC",
            "levels XAUUSD",
            "news",
            "quality XAUUSD",
            "positions",
            "slots",
            "slots auto",
            "slots manual 4",
            "matrix",
            "heatmap",
            "dashboard status",
            "autotrade",
            "autotrade on",
            "autotrade off",
            "watchlist",
            "watchlist btc",
            "watchlist gold",
            "watchlist both",
            "close ALL",
            "cancel XAUUSD",
            "closed",
            "mt5",
            "bridge",
            "broker",
            "switch live",
            "live",
            "balance",
            "delta",
            "status",
            "health",
            "cooldown",
            "spam",
            "risk",
            "resetrisk daily",
            "leverage 50",
            "lot auto",
            "kelly XAUUSD",
            "regime",
            "session",
            "vstops",
            "slippage",
            "kill status",
            "profile scalp",
            "board",
            "journal",
            "agents",
            "tiers",
            "digest",
            "replay 1",
            "export",
            "setups",
            "abooks",
            "heal",
            "brain",
            "train quick",
            "relearn XAUUSD 0.01",
            "ask market status?",
            "reset",
            "unknown_command_fallback_test",
            "exit",
        ]

        input_data = io.StringIO("\n".join(cli_commands) + "\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            try:
                run_cli_interactive()
            except Exception as e:
                self.fail(f"run_cli_interactive failed: {e}")

        out = output_data.getvalue()
        self.assertIn("TradingBot CLI", out)


if __name__ == "__main__":
    unittest.main()
