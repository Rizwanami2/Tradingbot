"""Unit tests for MT5 Account Management and Live Switcher Commands.

Tests account storage, password masking, fail-closed live switching,
Telegram bot commands (/mt5, /switch, /live, /broker live), and interactive CLI.
"""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, Chat, CallbackQuery
from telegram.ext import ContextTypes

import config
from mt5_accounts import (
    MT5Account,
    MT5AccountManager,
    switch_broker_mode,
    get_account_manager,
)
from mt5_broker import MT5Broker
from main import (
    mt5_command,
    switch_command,
    live_command,
    broker_command,
    balance_command,
    autotrade_callback_handler,
    run_cli_interactive,
    engine,
)


class TestMT5AccountDataclass(unittest.TestCase):
    def test_masked_password(self):
        acc1 = MT5Account(login=12345, password="SecretPassword123", server="DemoServer")
        self.assertTrue(acc1.masked_password.startswith("Se"))
        self.assertTrue(acc1.masked_password.endswith("23"))
        self.assertIn("•", acc1.masked_password)
        self.assertNotIn("SecretPassword", acc1.masked_password)

        acc2 = MT5Account(login=12345, password="abc", server="DemoServer")
        self.assertEqual(acc2.masked_password, "••••")

    def test_dict_serialization(self):
        acc = MT5Account(
            login=987654,
            password="MySecretPassword!",
            server="Exness-Real2",
            alias="exness-live",
            gateway_url="http://192.168.1.100:5000",
            is_live=True,
        )
        d = acc.to_dict()
        self.assertEqual(d["login"], 987654)
        self.assertEqual(d["alias"], "exness-live")
        self.assertEqual(d["gateway_url"], "http://192.168.1.100:5000")

        acc2 = MT5Account.from_dict(d)
        self.assertEqual(acc2.login, acc.login)
        self.assertEqual(acc2.password, acc.password)
        self.assertEqual(acc2.server, acc.server)
        self.assertEqual(acc2.alias, acc.alias)
        self.assertEqual(acc2.gateway_url, acc.gateway_url)


class TestMT5AccountManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = os.path.join(self.temp_dir, "test_accounts.json")
        self.mgr = MT5AccountManager(storage_file=self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_and_retrieve_account(self):
        acc = self.mgr.add_account(
            login="11223344",
            password="StrongPassword123",
            server="MetaQuotes-Demo",
            alias="demo1",
            is_live=False,
            set_active=True,
        )
        self.assertEqual(acc.login, 11223344)
        self.assertEqual(acc.alias, "demo1")

        found_by_login = self.mgr.get_account("11223344")
        self.assertIsNotNone(found_by_login)
        self.assertEqual(found_by_login.alias, "demo1")

        found_by_alias = self.mgr.get_account("demo1")
        self.assertIsNotNone(found_by_alias)
        self.assertEqual(found_by_alias.login, 11223344)

        active = self.mgr.get_active_account()
        self.assertIsNotNone(active)
        self.assertEqual(active.login, 11223344)

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            self.mgr.add_account("", "pass", "srv")
        with self.assertRaises(ValueError):
            self.mgr.add_account("abc-not-int", "pass", "srv")
        with self.assertRaises(ValueError):
            self.mgr.add_account("12345", "", "srv")
        with self.assertRaises(ValueError):
            self.mgr.add_account("12345", "pass", "")

    def test_persistence(self):
        self.mgr.add_account("556677", "Pass1", "Srv1", alias="acc1", set_active=True)
        self.mgr.add_account("889900", "Pass2", "Srv2", alias="acc2", set_active=False)

        # Re-instantiate from the same storage file
        mgr2 = MT5AccountManager(storage_file=self.storage_file)
        self.assertEqual(len(mgr2.list_accounts()), 2)
        active = mgr2.get_active_account()
        self.assertIsNotNone(active)
        self.assertEqual(active.login, 556677)

    def test_remove_account(self):
        self.mgr.add_account("1001", "P1", "S1", alias="first", set_active=True)
        self.mgr.add_account("1002", "P2", "S2", alias="second", set_active=False)

        removed = self.mgr.remove_account("first")
        self.assertIsNotNone(removed)
        self.assertEqual(removed.login, 1001)

        # After removing active account, remaining account becomes active
        active = self.mgr.get_active_account()
        self.assertIsNotNone(active)
        self.assertEqual(active.login, 1002)

    def test_clear_accounts_and_remove_all(self):
        self.mgr.add_account("2001", "P1", "S1", alias="acc1", set_active=True)
        self.mgr.add_account("2002", "P2", "S2", alias="acc2", set_active=False)
        self.assertEqual(len(self.mgr.list_accounts()), 2)

        # remove_account with "all"
        removed = self.mgr.remove_account("all")
        self.assertIsNotNone(removed)
        self.assertEqual(len(self.mgr.list_accounts()), 0)
        self.assertIsNone(self.mgr.get_active_account())

        # clear_accounts
        self.mgr.add_account("3001", "P3", "S3", alias="acc3", set_active=True)
        self.assertEqual(len(self.mgr.list_accounts()), 1)
        count = self.mgr.clear_accounts()
        self.assertEqual(count, 1)
        self.assertEqual(len(self.mgr.list_accounts()), 0)


class TestSwitchBrokerMode(unittest.TestCase):
    def setUp(self):
        self._orig_broker = engine.broker
        self._orig_mode = config.BROKER_MODE
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = os.path.join(self.temp_dir, "test_accounts.json")
        self.mgr = MT5AccountManager(storage_file=self.storage_file)

    def tearDown(self):
        engine.broker = self._orig_broker
        config.BROKER_MODE = self._orig_mode
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_switch_to_paper(self):
        ok, msg, broker = switch_broker_mode("paper", engine=engine, config_mod=config)
        self.assertFalse(ok)
        self.assertIn("Paper Trading Removed", msg)
        self.assertEqual(config.BROKER_MODE, "mt5_live")

    def test_switch_to_live_no_accounts_fail_closed(self):
        # Empty accounts manager
        empty_mgr = MT5AccountManager(storage_file=os.path.join(self.temp_dir, "empty.json"))
        # Force clear any default seeded account
        empty_mgr._accounts.clear()
        empty_mgr._active_login = None

        old_broker = engine.broker
        old_mode = config.BROKER_MODE

        ok, msg, broker = switch_broker_mode(
            "live",
            engine=engine,
            config_mod=config,
            account_mgr=empty_mgr,
        )
        self.assertFalse(ok)
        self.assertIn("No MT5 live account registered", msg)
        # Ensure fail-closed: engine broker and config mode remain unchanged
        self.assertEqual(engine.broker, old_broker)
        self.assertEqual(config.BROKER_MODE, old_mode)

    @patch("mt5_accounts.connect_and_verify")
    def test_switch_to_live_connection_failure_fail_closed(self, mock_connect):
        mock_connect.return_value = (False, "Timeout connecting to gateway", None)
        self.mgr.add_account("999888", "secret", "Exness-Real", alias="live1", set_active=True)

        old_broker = engine.broker
        old_mode = config.BROKER_MODE

        ok, msg, broker = switch_broker_mode(
            "live",
            engine=engine,
            config_mod=config,
            account_mgr=self.mgr,
        )
        self.assertFalse(ok)
        self.assertIn("Fail-Closed", msg)
        self.assertIn("Timeout connecting to gateway", msg)
        # Broker remains unchanged
        self.assertEqual(engine.broker, old_broker)
        self.assertEqual(config.BROKER_MODE, old_mode)

    @patch("mt5_accounts.connect_and_verify")
    def test_switch_to_live_success(self, mock_connect):
        mock_live_broker = MagicMock(spec=MT5Broker)
        mock_live_broker.mode = "live"
        mock_live_broker.balance = 50000.0
        mock_live_broker.equity = 50250.0
        mock_live_broker.leverage = 100.0
        mock_live_broker.is_connected = True
        mock_connect.return_value = (True, "Connected", mock_live_broker)

        self.mgr.add_account("999888", "secret", "Exness-Real", alias="live1", set_active=True)

        ok, msg, broker = switch_broker_mode(
            "live",
            engine=engine,
            config_mod=config,
            account_mgr=self.mgr,
        )
        self.assertTrue(ok)
        self.assertIn("LIVE MT5 EXECUTION ACTIVATED", msg)
        self.assertEqual(config.BROKER_MODE, "mt5_live")
        self.assertEqual(engine.broker, mock_live_broker)


class TestTelegramMT5Commands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_optin = config.AUTOTRADE_LIVE_OPT_IN
        self._orig_broker = engine.broker
        self._orig_mode = config.BROKER_MODE
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message
        self.user = MagicMock()
        self.user.id = 12345
        self.update.effective_user = self.user

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

    async def asyncTearDown(self):
        import main
        main.stop_background_autotrade_worker()
        config.AUTOTRADE_LIVE_OPT_IN = self._orig_optin
        engine.broker = self._orig_broker
        config.BROKER_MODE = self._orig_mode

    async def test_mt5_command_status_dashboard(self):
        self.ctx.args = ["status"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 Dashboard", text)
        self.assertIn("Quick Controls", text)

    @patch("mt5_accounts.connect_and_verify")
    async def test_mt5_command_add_account(self, mock_connect):
        mock_connect.return_value = (True, "Connected", MagicMock(balance=10000.0, equity=10000.0, leverage=100.0))
        self.ctx.args = ["add", "100200300", "P@ssw0rd123", "Demo-Server", "mytestacc"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Added & Verified Successfully", text)
        self.assertIn("100200300", text)
        self.assertIn("mytestacc", text)

    @patch("mt5_accounts.connect_and_verify")
    async def test_mt5_command_add_account_auth_failed_shows_diagnostics(self, mock_connect):
        mock_connect.return_value = (
            False,
            "Live MT5 connection error: MT5 Authorization Failed: (-6, 'Terminal: Authorization failed')",
            None,
        )
        self.ctx.args = ["add", "10055086", "Rizwan@123", "Mtrading-Live"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Authorization Check Needed", text)
        self.assertIn("Authorization Failed (-6)", text)
        self.assertIn("10055086", text)
        self.assertIn("Mtrading-Live", text)
        self.assertIn("Server Not Found in MT5 Terminal", text)
        self.assertIn("Trading Password vs Cabinet Password", text)
        self.assertIn("Zero-Password EA Bridge", text)

    async def test_mt5_command_accounts_list(self):
        mgr = get_account_manager()
        mgr.add_account("100200300", "P@ssw0rd123", "Demo-Server", alias="mytestacc")
        self.ctx.args = ["accounts"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MetaTrader 5 Accounts", text)

    async def test_switch_command_no_args(self):
        self.ctx.args = []
        await switch_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Switch MT5 Live Account", text)
        self.assertIn("/switch live", text)

    async def test_switch_command_paper(self):
        self.ctx.args = ["paper"]
        await switch_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Paper Trading Removed", text)
        self.assertEqual(config.BROKER_MODE, "mt5_live")

    async def test_live_command_off(self):
        self.ctx.args = ["off"]
        await live_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Live Execution is Exclusive", text)
        self.assertIn("PAUSED", text)

    @patch("mt5_accounts.connect_and_verify")
    async def test_live_command_on(self, mock_connect):
        mock_broker = MagicMock()
        mock_broker.balance = 12500.0
        mock_broker.equity = 12500.0
        mock_broker.leverage = 50.0
        mock_broker.is_connected = True
        mock_connect.return_value = (True, "Connected", mock_broker)

        mgr = get_account_manager()
        mgr.add_account("888777", "secret", "Exness-Real", alias="primary", set_active=True)

        self.ctx.args = ["on"]
        await live_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("LIVE MT5 EXECUTION ACTIVATED", text)
        self.assertTrue(config.AUTOTRADE_LIVE_OPT_IN)

    async def test_mt5_command_remove_all(self):
        mgr = get_account_manager()
        mgr.add_account("111222", "pass1", "Server1", alias="acc1")
        mgr.add_account("333444", "pass2", "Server2", alias="acc2")
        self.ctx.args = ["remove", "all"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("All MT5 Accounts Removed", text)
        self.assertEqual(len(mgr.list_accounts()), 0)

    async def test_mt5_command_balance(self):
        self.ctx.args = ["balance", "18500"]
        await mt5_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MT5 Live Balance Updated", text)
        self.assertIn("18,500.00", text)

    async def test_balance_command_view_and_update(self):
        self.ctx.args = ["22000"]
        await balance_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("Live Account Balance Updated", text)
        self.assertIn("22,000.00", text)

    async def test_broker_command_live(self):
        self.ctx.args = ["live"]
        with patch("mt5_accounts.switch_broker_mode") as mock_sw:
            mock_sw.return_value = (True, "Switched to MT5 LIVE", MagicMock())
            await broker_command(self.update, self.ctx)
            mock_sw.assert_called_once()
            self.message.reply_text.assert_called_once()

    async def test_autotrade_callback_handler_mt5(self):
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = self.message
        query.from_user = self.user
        self.update.callback_query = query
        self.update.effective_user = self.user

        # 1. Switch to paper callback
        query.data = "mt5:switch_paper"
        await autotrade_callback_handler(self.update, self.ctx)
        self.message.reply_text.assert_called()
        self.assertIn("Paper Trading Removed", self.message.reply_text.call_args[0][0])

        # 2. List accounts callback
        mgr = get_account_manager()
        if not mgr.list_accounts():
            mgr.add_account("100200300", "P@ssw0rd123", "Demo-Server", alias="mytestacc")
        self.message.reply_text.reset_mock()
        query.data = "mt5:list_accounts"
        await autotrade_callback_handler(self.update, self.ctx)
        self.message.reply_text.assert_called()
        self.assertIn("MetaTrader 5 Accounts", self.message.reply_text.call_args[0][0])

        # 3. Help add callback
        self.message.reply_text.reset_mock()
        query.data = "mt5:help_add"
        await autotrade_callback_handler(self.update, self.ctx)
        self.message.reply_text.assert_called()
        self.assertIn("Add MetaTrader 5 Account", self.message.reply_text.call_args[0][0])

        # 4. Check callback
        self.message.reply_text.reset_mock()
        query.data = "mt5:check"
        from mt5_broker import MockMT5Broker
        orig_broker = engine.broker
        engine.broker = MockMT5Broker()
        try:
            await autotrade_callback_handler(self.update, self.ctx)
            self.message.reply_text.assert_called()
            self.assertIn("Pre-Flight Diagnostics Check", self.message.reply_text.call_args[0][0])
        finally:
            engine.broker = orig_broker

    async def test_mt5_check_and_reset_commands(self):
        from mt5_broker import MockMT5Broker
        orig_broker = engine.broker
        engine.broker = MockMT5Broker()
        try:
            # Test /mt5 check
            self.ctx.args = ["check"]
            await mt5_command(self.update, self.ctx)
            self.message.reply_text.assert_called()
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("Pre-Flight Diagnostics Check", msg)
            self.assertIn("READY FOR TRADING", msg)

            # Test /mt5 reset
            engine.execution_halted = True
            self.ctx.args = ["reset"]
            await mt5_command(self.update, self.ctx)
            self.assertFalse(engine.execution_halted)
            msg = self.message.reply_text.call_args[0][0]
            self.assertIn("Execution Halt Cleared", msg)
        finally:
            engine.broker = orig_broker



class TestCLIInteractiveMT5(unittest.TestCase):
    def setUp(self):
        self._orig_broker = engine.broker
        self._orig_mode = config.BROKER_MODE

    def tearDown(self):
        import main
        main.stop_background_autotrade_worker()
        engine.broker = self._orig_broker
        config.BROKER_MODE = self._orig_mode

    def test_cli_mt5_and_switch(self):
        mgr = get_account_manager()
        mgr.add_account("100200300", "P@ssw0rd123", "Demo-Server", alias="mytestacc")
        input_data = io.StringIO(
            "mt5 accounts\n"
            "switch\n"
            "mt5 paper\n"
            "switch paper\n"
            "live off\n"
            "balance 16000\n"
            "mt5 balance\n"
            "mt5 remove all\n"
            "exit\n"
        )
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("MetaTrader 5 Accounts", out)
        self.assertIn("Current Broker Mode", out)
        self.assertIn("Paper Trading Removed", out)
        self.assertIn("Live Balance Updated", out)
        self.assertIn("All MT5 accounts removed", out)

    def test_mt5_broker_balance_resolution(self):
        # When initial_balance is not passed, it uses config.MT5_BALANCE or config.INITIAL_EQUITY (never 1000.0)
        broker = MT5Broker(login=999111, password="secret", server="MetaQuotes-Demo")
        self.assertEqual(broker.balance, getattr(config, "INITIAL_EQUITY", 10000.0))
        self.assertNotEqual(broker.balance, 1000.0)

    def test_cli_mt5_check_and_reset(self):
        from mt5_broker import MockMT5Broker
        orig_broker = engine.broker
        engine.broker = MockMT5Broker()
        engine.execution_halted = True
        try:
            input_data = io.StringIO("mt5 check\nmt5 reset\nexit\n")
            output_data = io.StringIO()
            with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
                run_cli_interactive()
            out = output_data.getvalue()
            self.assertIn("MT5 Pre-Flight Diagnostics Check", out)
            self.assertIn("MT5 execution halt cleared", out)
            self.assertFalse(engine.execution_halted)
        finally:
            engine.broker = orig_broker

