import io
import unittest
from unittest.mock import MagicMock, patch

import config
from main import run_cli_interactive, engine
from execution_engine import TradeOrder, OrderStatus


class TestCliInteractive(unittest.TestCase):
    def setUp(self):
        self._orig_broker = engine.broker
        self._orig_positions = dict(engine.active_positions)
        self._orig_setups = dict(engine.pending_setups)
        self._orig_mt5_backend = getattr(config, "MT5_BACKEND", "auto")

    def tearDown(self):
        engine.broker = self._orig_broker
        engine.active_positions = self._orig_positions
        engine.pending_setups = self._orig_setups
        config.MT5_BACKEND = self._orig_mt5_backend

    def test_cli_help_and_exit(self):
        input_data = io.StringIO("help\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("TradingBot CLI", out)
        self.assertIn("Commands: signal", out)

    def test_cli_status_and_risk(self):
        input_data = io.StringIO("status\nrisk\npositions\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("equity", out)
        self.assertIn("Eq:", out)

    def test_cli_autotrade_toggle(self):
        input_data = io.StringIO("autotrade\nautotrade on\nautotrade off\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Autotrade is", out)
        self.assertIn("Autotrade ON", out)
        self.assertIn("Autotrade OFF", out)

    def test_cli_close_and_cancel_empty(self):
        input_data = io.StringIO("close BTC\ncancel BTC\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("No active position for BTC", out)
        self.assertIn("No pending setup for BTC", out)

    def test_cli_close_and_cancel_with_orders(self):
        # Register a pending setup and an active position
        dummy_order = TradeOrder(
            id="order-test-1",
            symbol="TESTSYM",
            direction="LONG",
            entry_level=100.0,
            stop_loss=90.0,
            tp1=120.0,
            tp2=130.0,
            tp3=None,
            lots=0.1,
            size=10.0,
            leverage=1.0,
            margin_required=10.0,
            liquidation_price=None,
            risk_amount=10.0,
            rr_ratio=2.0,
            setup_reason="test",
            status=OrderStatus.OPEN,
            executed_price=100.0,
            remaining_size=10.0,
        )
        engine.active_positions["TESTSYM"] = dummy_order

        pending_order = TradeOrder(
            id="order-test-2",
            symbol="TESTPEND",
            direction="LONG",
            entry_level=100.0,
            stop_loss=90.0,
            tp1=120.0,
            tp2=130.0,
            tp3=None,
            lots=0.1,
            size=10.0,
            leverage=1.0,
            margin_required=10.0,
            liquidation_price=None,
            risk_amount=10.0,
            rr_ratio=2.0,
            setup_reason="test",
            status=OrderStatus.PENDING,
        )
        engine.pending_setups["TESTPEND"] = pending_order

        input_data = io.StringIO("close TESTSYM\ncancel TESTPEND\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Close executed for TESTSYM", out)
        self.assertIn("Cancelled pending setup for TESTPEND", out)

    def test_cli_news_command(self):
        input_data = io.StringIO("news BTC\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertTrue("News" in out or "Market Pulse" in out or "Macro" in out or "Score" in out)

    def test_cli_health(self):
        input_data = io.StringIO("health\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Monitoring Health:", out)

    def test_cli_leverage(self):
        input_data = io.StringIO("leverage\nleverage 15\nleverage\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Leverage set to 15x", out)

    def test_cli_kill_switch(self):
        input_data = io.StringIO("kill\nkill on\nkill off\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("KILL SWITCH ON", out)
        self.assertIn("Kill switch OFF", out)

    def test_cli_performance(self):
        input_data = io.StringIO("performance\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Performance", out)

    def test_cli_profile(self):
        input_data = io.StringIO("profile\nprofile swing\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Available profiles:", out)
        self.assertIn("Profile:", out)

    def test_cli_ask_and_chat(self):
        input_data = io.StringIO("ask\nask What is the market trend?\nchat analyze BTC\nreset\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Usage: ask <question>", out)
        self.assertIn("Trading Assistant:", out)
        self.assertIn("Conversation memory cleared.", out)

    def test_cli_quality(self):
        input_data = io.StringIO("quality BTC\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Signal Quality BTC", out)

    def test_cli_unknown_command(self):
        input_data = io.StringIO("unknown_cmd\nexit\n")
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("Commands: signal", out)

    def test_cli_bridge_commands(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 1, "volume": 0.05, "login": 778899})
        with patch.object(ea_client, "status", mock_status):
            input_data = io.StringIO("bridge\nbridge ping\nbridge on\nbridge off\nbridge dir /tmp/test_bridge\nexit\n")
            output_data = io.StringIO()
            with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
                run_cli_interactive()
            out = output_data.getvalue()
            self.assertIn("EA Bridge Status:", out)
            self.assertIn("EA Bridge Ping: PONG", out)
            self.assertIn("Switched to EA Bridge backend", out)
            self.assertIn("Switched to Auto MT5 backend", out)
            self.assertIn("EA Bridge directory updated to: /tmp/test_bridge", out)

    def test_cli_mt5_bridge_and_backend(self):
        from bridge import ea_client
        mock_status = MagicMock(return_value={"ok": True, "positions": 0, "volume": 0.0, "login": 123456})
        with patch.object(ea_client, "status", mock_status):
            input_data = io.StringIO("mt5 bridge\nmt5 backend\nmt5 backend ea_bridge\nexit\n")
            output_data = io.StringIO()
            with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
                run_cli_interactive()
            out = output_data.getvalue()
            self.assertIn("EA Bridge Status:", out)
            self.assertIn("Configured backend:", out)
            self.assertIn("MT5 backend set to: ea_bridge", out)


if __name__ == "__main__":
    unittest.main()
