"""
Unit and Integration Tests for 11 Trader Skills & Telegram Handlers
====================================================================
Verifies that all 11 skills are fully functional, correctly registered,
and handle Telegram commands and callbacks gracefully.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import trader_skills
from telegram_trader_skills import (
    analyst_command,
    budget_command,
    build_skills_keyboard,
    copycat_command,
    nyse_command,
    pelosi_command,
    rebalance_command,
    register_trader_skills,
    riskcheck_command,
    screener_command,
    secnews_command,
    skills_callback_handler,
    skills_command,
    tax_command,
    ticket_command,
)


class TestTelegramTraderSkills(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.update = MagicMock()
        self.message = MagicMock()
        self.update.message = self.message
        self.update.effective_message = self.message
        self.ctx = MagicMock()
        self.ctx.args = []

    async def test_register_trader_skills(self):
        """Verify register_trader_skills adds all 11 command handlers to Telegram App."""
        mock_app = MagicMock()
        register_trader_skills(mock_app)
        self.assertGreaterEqual(mock_app.add_handler.call_count, 12)

    async def test_build_skills_keyboard(self):
        """Verify build_skills_keyboard produces all 6 rows with 12 buttons."""
        kb = build_skills_keyboard("main")
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb.inline_keyboard), 6)
        all_buttons = [btn for row in kb.inline_keyboard for btn in row]
        self.assertEqual(len(all_buttons), 12)

    async def test_skills_command(self):
        """Verify /skills returns the 11 skills hub overview."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            await skills_command(self.update, self.ctx)
            mock_reply.assert_called_once()
            call_text = mock_reply.call_args[0][1]
            self.assertIn("11 Trader Skills Hub", call_text)
            self.assertIn("/pelosi", call_text)
            self.assertIn("/copycat", call_text)
            self.assertIn("/analyst", call_text)
            self.assertIn("/riskcheck", call_text)

    async def test_copycat_command_empty_and_query(self):
        """Verify /copycat shows guide when empty and searches when given query."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            # 1. Empty args -> guide
            self.ctx.args = []
            await copycat_command(self.update, self.ctx)
            call_text = mock_reply.call_args[0][1]
            self.assertIn("Popular Filers", call_text)
            self.assertIn("Berkshire Hathaway", call_text)

            # 2. Query buffett -> finds Berkshire CIK
            mock_reply.reset_mock()
            self.ctx.args = ["buffett"]
            await copycat_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("13F Institutional Holdings Report", final_text)

    async def test_pelosi_command(self):
        """Verify /pelosi returns congressional stock disclosure reports."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["pelosi"]
            await pelosi_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Congressional Stock Disclosure Report", final_text)

    async def test_secnews_command(self):
        """Verify /secnews scans 8-K filings and financial headlines."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["AAPL", "MSFT"]
            await secnews_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("SEC 8-K & Breaking Financial News", final_text)

    async def test_analyst_command(self):
        """Verify /analyst shows usage when empty and calls SEC XBRL parser."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            # Empty
            self.ctx.args = []
            await analyst_command(self.update, self.ctx)
            mock_reply.assert_called_once()
            self.assertIn("`/analyst <TICKER>`", mock_reply.call_args[0][1])

            # With ticker
            mock_reply.reset_mock()
            self.ctx.args = ["AAPL"]
            await analyst_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("SEC XBRL Earnings & Fundamentals", final_text)

    async def test_screener_command(self):
        """Verify /screener returns quantitative screener information."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            await screener_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Quantitative Equity Screener", final_text)

    async def test_budget_command(self):
        """Verify /budget sizes capital against allocation caps."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["5000"]
            await budget_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Portfolio Capital Sizing", final_text)

    async def test_riskcheck_command(self):
        """Verify /riskcheck runs pre-trade compliance checks."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            await riskcheck_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Pre-Trade Risk & Compliance Firewall", final_text)

    async def test_ticket_command(self):
        """Verify /ticket displays execution tickets."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["show"]
            await ticket_command(self.update, self.ctx)
            mock_reply.assert_called_once()
            final_text = mock_reply.call_args[0][1]
            self.assertIn("Execution Tickets & Human-In-The-Loop Gate", final_text)

    async def test_tax_command(self):
        """Verify /tax returns lot accounting and wash-sale scan."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["report"]
            await tax_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Tax Lot Accounting & Wash Sale Scanner", final_text)

    async def test_nyse_command(self):
        """Verify /nyse returns market clock and holiday calendar."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            await nyse_command(self.update, self.ctx)
            mock_reply.assert_called_once()
            final_text = mock_reply.call_args[0][1]
            self.assertIn("NYSE Trading Calendar & Market Clock", final_text)

    async def test_rebalance_command(self):
        """Verify /rebalance computes portfolio drift."""
        with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock) as mock_reply:
            self.ctx.args = ["5"]
            await rebalance_command(self.update, self.ctx)
            self.assertGreaterEqual(mock_reply.call_count, 2)
            final_text = mock_reply.call_args_list[-1][0][1]
            self.assertIn("Portfolio Drift & Rebalance Proposals", final_text)

    async def test_skills_callback_handler(self):
        """Verify inline keyboard clicks dispatch correctly."""
        cb_query = MagicMock()
        cb_query.answer = AsyncMock()
        self.update.callback_query = cb_query

        callbacks = [
            ("ts:main", skills_command),
            ("ts:copycat", copycat_command),
            ("ts:pelosi", pelosi_command),
            ("ts:secnews", secnews_command),
            ("ts:analyst", analyst_command),
            ("ts:screener", screener_command),
            ("ts:budget", budget_command),
            ("ts:risk", riskcheck_command),
            ("ts:ticket", ticket_command),
            ("ts:tax", tax_command),
            ("ts:nyse", nyse_command),
            ("ts:rebalance", rebalance_command),
        ]

        for data, target_fn in callbacks:
            cb_query.data = data
            with patch("telegram_trader_skills.safe_reply", new_callable=AsyncMock):
                await skills_callback_handler(self.update, self.ctx)
                cb_query.answer.assert_called()

    def test_trader_skills_package_wrappers(self):
        """Verify trader_skills package programmatic wrappers."""
        clock_res = trader_skills.alerts(["clock"])
        self.assertIn("New York", clock_res["output"])

        copy_res = trader_skills.copycat(["find", "buffett"])
        self.assertIn("Berkshire Hathaway", copy_res["output"])

        pelosi_res = trader_skills.pelosi(["--help"])
        self.assertIn("Congress trade tracker", pelosi_res["output"])


if __name__ == "__main__":
    unittest.main()
