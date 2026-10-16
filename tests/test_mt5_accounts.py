"""Unit tests for MT5 Account Management and Live Switcher Commands.

Tests account storage, password masking, fail-closed live switching,
Telegram bot commands (/mt5, /switch, /live, /broker live), and interactive CLI.
"""

import io
import json
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
    connect_and_verify,
    switch_broker_mode,
    get_account_manager,
)
from mt5_broker import MT5Broker
from main import (
    mt5_command,
    switch_command,
    live_command,
    broker_command,
    autotrade_callback_handler,
    build_mt5_dashboard,
    run_cli_interactive,
    engine,
    risk_mgr,
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


class TestSwitchBrokerMode(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = os.path.join(self.temp_dir, "test_accounts.json")
        self.mgr = MT5AccountManager(storage_file=self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_switch_to_paper(self):
        ok, msg, broker = switch_broker_mode("paper", engine=engine, config_mod=config)
        self.assertTrue(ok)
        self.assertIn("MT5 Paper", msg)
        self.assertEqual(config.BROKER_MODE, "mt5_paper")
        self.assertIsInstance(engine.broker, MT5Broker)
        self.assertEqual(engine.broker.mode, "paper")

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
        self.chat = Chat(id=12345, type="private")
        self.message = MagicMock(spec=Message)
        self.message.reply_text = AsyncMock()
        self.update = MagicMock(spec=Update)
        self.update.effective_chat = self.chat
        self.update.message = self.message

        self.ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        self.ctx.args = []

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
        self.assertIn("Switch Broker Execution Mode", text)
        self.assertIn("/switch live", text)
        self.assertIn("/switch paper", text)

    async def test_switch_command_paper(self):
        self.ctx.args = ["paper"]
        await switch_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MT5 Paper", text)
        self.assertEqual(config.BROKER_MODE, "mt5_paper")

    async def test_live_command_off(self):
        self.ctx.args = ["off"]
        await live_command(self.update, self.ctx)
        self.message.reply_text.assert_called_once()
        text = self.message.reply_text.call_args[0][0]
        self.assertIn("MT5 Paper", text)

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
        self.update.callback_query = query

        # 1. Switch to paper callback
        query.data = "mt5:switch_paper"
        await autotrade_callback_handler(self.update, self.ctx)
        self.message.reply_text.assert_called()
        self.assertIn("MT5 Paper", self.message.reply_text.call_args[0][0])

        # 2. List accounts callback
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


class TestCLIInteractiveMT5(unittest.TestCase):
    def test_cli_mt5_and_switch(self):
        mgr = get_account_manager()
        mgr.add_account("100200300", "P@ssw0rd123", "Demo-Server", alias="mytestacc")
        input_data = io.StringIO(
            "mt5 accounts\n"
            "switch\n"
            "mt5 paper\n"
            "switch paper\n"
            "live off\n"
            "exit\n"
        )
        output_data = io.StringIO()
        with patch("sys.stdin", input_data), patch("sys.stdout", output_data):
            run_cli_interactive()
        out = output_data.getvalue()
        self.assertIn("MetaTrader 5 Accounts", out)
        self.assertIn("Current Broker Mode", out)
        self.assertIn("MT5 Paper", out)
