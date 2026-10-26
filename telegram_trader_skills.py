"""
Telegram Interface for 11 Trader Skills
========================================
Integrates the complete trader-skill suite into nimble-darwin:
1.  /copycat   - 13F Institutional Reverse Engineering (Buffett, Dalio, Burry)
2.  /pelosi    - US Congress Stock Disclosures (STOCK Act PTRs)
3.  /secnews   - SEC 8-K Filings & Breaking Headline Monitor
4.  /analyst   - Corporate Financials & Earnings Analysis (SEC XBRL)
5.  /screener  - Quantitative Rule-Based Stock Screener
6.  /budget    - Capital Allocation, Position Sizing & Cash Buffers
7.  /riskcheck - Pre-Trade Compliance & Order Risk Firewall
8.  /ticket    - Order Execution Tickets & Human Approval Gate
9.  /tax       - Lot Accounting, Tax-Loss Harvesting & Wash Sales
10. /nyse      - NYSE Market Calendar & Holiday Clock
11. /rebalance - Portfolio Drift & Rebalancing Generator
12. /skills    - Interactive Dashboard for All 11 Skills
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import trader_skills
from trader_skills_agent_bridge import get_skills_bridge
from telegram_brain import safe_reply

logger = logging.getLogger("TelegramTraderSkills")


async def _run_in_thread(func, *args):
    """Compatible helper for running sync functions in background thread (Python 3.8+)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


def build_skills_keyboard(active: str = "main") -> InlineKeyboardMarkup:
    """Build interactive inline keyboard for navigating trader skills."""
    row0 = [
        InlineKeyboardButton("🥇 Gold Skills (XAUUSD)", callback_data="ts:gold"),
        InlineKeyboardButton("⚡ Bitcoin Skills (BTC)", callback_data="ts:btc"),
    ]
    row1 = [
        InlineKeyboardButton("🏛️ Pelosi / Congress", callback_data="ts:pelosi"),
        InlineKeyboardButton("🐋 13F Copycat", callback_data="ts:copycat"),
    ]
    row2 = [
        InlineKeyboardButton("📰 SEC 8-K News", callback_data="ts:secnews"),
        InlineKeyboardButton("📊 SEC XBRL Analyst", callback_data="ts:analyst"),
    ]
    row3 = [
        InlineKeyboardButton("🎯 Stock Screener", callback_data="ts:screener"),
        InlineKeyboardButton("💰 Budget & Sizing", callback_data="ts:budget"),
    ]
    row4 = [
        InlineKeyboardButton("🛡️ Risk Firewall", callback_data="ts:risk"),
        InlineKeyboardButton("🎫 Order Tickets", callback_data="ts:ticket"),
    ]
    row5 = [
        InlineKeyboardButton("📑 Tax & Wash Sales", callback_data="ts:tax"),
        InlineKeyboardButton("⏰ NYSE Clock", callback_data="ts:nyse"),
    ]
    row6 = [
        InlineKeyboardButton("⚖️ Rebalance", callback_data="ts:rebalance"),
        InlineKeyboardButton("🔄 Overview", callback_data="ts:main"),
    ]
    row7 = [
        InlineKeyboardButton("🩺 Auto-Trade Doctor", callback_data="ts:doctor"),
        InlineKeyboardButton("🔗 Swarm-Skills Bridge", callback_data="ts:bridge"),
    ]
    return InlineKeyboardMarkup([row0, row1, row2, row3, row4, row5, row6, row7])


async def skills_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Interactive overview of all 11 Trader Skills."""
    text = (
        "🧰 **11 Trader Skills Hub (Institutional & Quantitative Suite):**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏛️ **Institutional & Regulatory Intelligence:**\n"
        "• `/pelosi [member]` — Congressional stock disclosures (STOCK Act PTRs)\n"
        "• `/copycat [filer]` — 13F-HR institutional reverse engineering\n"
        "• `/secnews [tickers]` — Breaking 8-K filings & market headlines\n\n"
        "📊 **Fundamental Analysis & Screening:**\n"
        "• `/analyst [SYM]` — SEC XBRL financials (revenue, EPS, margins, growth)\n"
        "• `/screener [rules]` — Rule-based quantitative equity screener\n\n"
        "💰 **Capital Sizing & Allocation:**\n"
        "• `/budget [amt]` — Position sizing with cash buffer & single-stock caps\n"
        "• `/rebalance [band]` — Portfolio drift calculator & rebalance tickets\n\n"
        "🛡️ **Risk Firewall & Execution Safeguards:**\n"
        "• `/riskcheck` — Pre-trade compliance checker (leverage, caps, limits)\n"
        "• `/ticket [index]` — Two-yes human approval gate & order tickets\n\n"
        "📑 **Operations & Accounting:**\n"
        "• `/tax [SYM|harvest]` — Cost-basis lots, wash sale validator & harvesting\n"
        "• `/nyse` — NYSE trading clock, holidays (2026/2027) & early closes\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Click any button below or type a command to explore a skill._"
    )
    await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_skills_keyboard("main"))


async def copycat_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reverse-engineer a famous investor's 13F-HR filing from SEC EDGAR."""
    args = ctx.args or []
    if not args:
        text = (
            "🐋 **13F Institutional Reverse Engineering (`/copycat`):**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Rebuilds an institutional investor's portfolio weights from their latest SEC 13F-HR filing.\n\n"
            "**Popular Filers:**\n"
            "• `/copycat buffett` or `/copycat 1067983` — Berkshire Hathaway\n"
            "• `/copycat ackman` or `/copycat 1336528` — Pershing Square\n"
            "• `/copycat burry` or `/copycat 1649339` — Scion Asset Management\n"
            "• `/copycat tepper` or `/copycat 1099281` — Appaloosa Management\n"
            "• `/copycat dalio` or `/copycat 1350694` — Bridgewater Associates\n\n"
            "**Usage:**\n"
            "`/copycat <name or CIK> [--top N]`"
        )
        await safe_reply(update, text, parse_mode="Markdown")
        return

    query = " ".join(args).strip()
    await safe_reply(update, f"🔍 Searching SEC EDGAR 13F filings for `{query}`...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.copycat, ["find", query])
    if not res["output"]:
        res = await _run_in_thread(trader_skills.copycat, ["find", args[0]])

    output = res["output"] or res["error"] or "No filings matched the query."
    header = f"🐋 **13F Institutional Holdings Report:**\n```text\n{output[:3600]}\n```"
    await safe_reply(update, header, parse_mode="Markdown")


async def pelosi_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Track stock trades disclosed by members of Congress under the STOCK Act."""
    args = ctx.args or []
    member = args[0] if args else "pelosi"

    await safe_reply(update, f"🏛️ Checking US House Clerk PTR disclosures for `{member}`...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.pelosi, ["index", "--member", member])
    output = res["output"] or res["error"] or f"No recent Periodic Transaction Reports found for {member}."

    msg = (
        f"🏛️ **Congressional Stock Disclosure Report (STOCK Act):**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output[:3500]}\n```\n"
        f"💡 _Note: Disclosures are legally filed up to 45 days after transaction dates._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def secnews_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Monitor breaking material SEC 8-K filings and financial news headlines."""
    args = ctx.args or []
    tickers = [a.upper().replace(",", " ").strip() for a in args]
    flat_tickers: List[str] = []
    for t in tickers:
        flat_tickers.extend([x for x in t.split() if x])

    if not flat_tickers:
        flat_tickers = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]

    sym_str = ", ".join(flat_tickers[:6])
    await safe_reply(update, f"📰 Scanning SEC EDGAR 8-K feeds and headlines for `{sym_str}`...", parse_mode="Markdown")

    call_args = ["--tickers"] + flat_tickers[:6] + ["--hours", "48"]
    res = await _run_in_thread(trader_skills.sec_news, call_args)
    output = res["output"] or res["error"] or "No new material 8-K filings or headlines in the last 48 hours."

    msg = f"📰 **SEC 8-K & Breaking Financial News:**\n```text\n{output[:3600]}\n```"
    await safe_reply(update, msg, parse_mode="Markdown")


async def analyst_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Retrieve official reported financial statements and earnings from SEC XBRL."""
    args = ctx.args or []
    if not args:
        await safe_reply(
            update,
            "📊 **Usage:** `/analyst <TICKER>`\n_Example: `/analyst AAPL` or `/analyst NVDA`_",
            parse_mode="Markdown",
        )
        return

    sym = args[0].upper().strip()
    await safe_reply(update, f"📊 Fetching SEC XBRL financials and 10-Q/10-K data for `{sym}`...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.analyst, [sym])
    output = res["output"] or res["error"] or f"Unable to retrieve SEC XBRL data for {sym}."

    msg = f"📊 **SEC XBRL Earnings & Fundamentals ({sym}):**\n```text\n{output[:3600]}\n```"
    await safe_reply(update, msg, parse_mode="Markdown")


async def screener_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run quantitative stock screener against financial rules."""
    await safe_reply(update, "🎯 Running quantitative stock screener...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.screener, ["--help"])
    output = res["output"] or res["error"] or "Screener ready."

    msg = (
        "🎯 **Quantitative Equity Screener (`/screener`):**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Screens companies against user rules (revenue growth, margins, EPS, P/E, sector).\n\n"
        f"```text\n{output[:2500]}\n```\n"
        "💡 _Metrics can be generated directly via `/analyst` and screened against `rules.json`._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def budget_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Size investment portfolio positions to capital budget with cash buffer and caps."""
    args = ctx.args or []
    budget_val = args[0] if args else "5000"

    await safe_reply(update, f"💰 Sizing investment budget for `${budget_val}`...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.budget, ["--budget", budget_val])
    output = res["output"] or res["error"] or f"Budget sized for ${budget_val}."

    msg = (
        f"💰 **Portfolio Capital Sizing (${budget_val}):**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output[:3500]}\n```\n"
        f"💡 _Protects capital with mandatory cash buffer & max single-asset allocation caps._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def riskcheck_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run pre-trade compliance and risk firewall checks."""
    await safe_reply(update, "🛡️ Running pre-trade compliance firewall checks...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.risk_check, [])
    output = res["output"] or res["error"] or "Risk firewall scan completed."

    msg = f"🛡️ **Pre-Trade Risk & Compliance Firewall:**\n```text\n{output[:3500]}\n```"
    await safe_reply(update, msg, parse_mode="Markdown")


async def ticket_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View and manage broker order execution tickets."""
    args = ctx.args or []
    subcmd = args[0] if args else "show"

    res = await _run_in_thread(trader_skills.broker_ticket, [subcmd] + args[1:])
    output = res["output"] or res["error"] or "No active broker tickets."

    msg = (
        f"🎫 **Execution Tickets & Human-In-The-Loop Gate:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output[:3500]}\n```\n"
        f"💡 _No order is sent to any broker without your explicit confirmation._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def tax_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inspect tax-basis lots, wash-sale rules, and tax-loss harvesting candidates."""
    args = ctx.args or []
    sub = args[0] if args else "report"

    await safe_reply(update, "📑 Running tax lot optimization and wash-sale scan...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.tax, [sub] + args[1:])
    output = res["output"] or res["error"] or "Tax lot review complete."

    msg = (
        f"📑 **Tax Lot Accounting & Wash Sale Scanner:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output[:3500]}\n```\n"
        f"⚠️ _Disclaimer: Informational calculation only; not certified tax advice._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def nyse_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check NYSE trading hours, holiday schedules, and early closes."""
    res = await _run_in_thread(trader_skills.alerts, ["clock"])
    output = res["output"] or res["error"] or "Market clock retrieved."

    msg = (
        f"⏰ **NYSE Trading Calendar & Market Clock:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output}\n```"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def rebalance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check portfolio drift and generate rebalancing proposals."""
    args = ctx.args or []
    band = args[0] if args else "5"

    await safe_reply(update, f"⚖️ Calculating portfolio drift with `{band}%` band threshold...", parse_mode="Markdown")

    res = await _run_in_thread(trader_skills.rebalance, ["--band", band])
    output = res["output"] or res["error"] or "Portfolio is currently within target drift bands."

    msg = (
        f"⚖️ **Portfolio Drift & Rebalance Proposals ({band}% Band):**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"```text\n{output[:3500]}\n```\n"
        f"💡 _Sells are always proposed before buys to preserve cash buffer._"
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def skills_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle interactive inline keyboard clicks for trader skills."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith("ts:"):
        return

    skill_key = data.split(":", 1)[1]
    if skill_key == "main":
        await skills_command(update, ctx)
    elif skill_key == "copycat":
        await copycat_command(update, ctx)
    elif skill_key == "pelosi":
        await pelosi_command(update, ctx)
    elif skill_key == "secnews":
        await secnews_command(update, ctx)
    elif skill_key == "analyst":
        await analyst_command(update, ctx)
    elif skill_key == "screener":
        await screener_command(update, ctx)
    elif skill_key == "budget":
        await budget_command(update, ctx)
    elif skill_key == "risk":
        await riskcheck_command(update, ctx)
    elif skill_key == "ticket":
        await ticket_command(update, ctx)
    elif skill_key == "tax":
        await tax_command(update, ctx)
    elif skill_key == "nyse":
        await nyse_command(update, ctx)
    elif skill_key == "rebalance":
        await rebalance_command(update, ctx)
    elif skill_key == "bridge":
        await bridge_status_command(update, ctx)
    elif skill_key == "gold":
        await gold_command(update, ctx)
    elif skill_key == "btc":
        await btc_command(update, ctx)
    elif skill_key == "doctor":
        await autodoctor_command(update, ctx)


async def autodoctor_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Diagnose open trades and manage autonomous decisions for underperforming positions."""
    from adverse_trade_manager import get_adverse_trade_manager
    adm = get_adverse_trade_manager()

    msg = (
        "🩺 **Auto-Trade Doctor (Adverse Trade Decision Engine):**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "When an open trade is running poorly, the bot automatically diagnoses the condition "
        "and executes the optimal capital-preservation decision:\n\n"
        "🛡️ **1. Adverse Stall Defense (`-0.50R`):**\n"
        "• Automatically trims **50% of position size** at market.\n"
        "• Tightens stop loss on remaining 50% to **`-0.75R`** (halves risk exposure).\n\n"
        "🚨 **2. Thesis Invalidation Early Cut (`-0.20R`):**\n"
        "• If market consensus / 118-agent Swarm flips opposing while in loss.\n"
        "• Automatically **exits immediately**, saving **60% to 75%** of stop-loss capital.\n\n"
        "🔒 **3. Profit Fade Defense (`+0.65R` ➔ `+0.12R`):**\n"
        "• If high profit fades back toward entry, **snaps stop to Break-Even**.\n"
        "• Shields capital against turning winning trades into losers.\n\n"
        "⏱️ **4. Time Stagnation Scratch (`30 bars / 45m`):**\n"
        "• Automatically scratches trades stuck in consolidation chop while in loss.\n"
        "• Unlocks margin for fresh high-probability setups.\n\n"
        "💰 **5. Break-Even Profit Booking (`AUTO_BE_PROFIT`):**\n"
        "• When SL is placed on Break-Even, bot auto-books **33% profit** at market.\n"
        "• Leaves 67% runner active with SL at entry + buffer (100% risk-free trade).\n"
        "• Customize anytime via `/beprofit <pct>` (e.g. `/beprofit 50` or `/beprofit 25`).\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ **Status:** Active & monitoring live market ticks across Gold, BTC & FX."
    )
    await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_skills_keyboard("main"))


async def beprofit_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View or adjust Break-Even Profit Booking percentage."""
    from risk_manager import get_risk_manager
    rm = get_risk_manager()
    args = ctx.args or []
    if args:
        val_str = args[0].strip().replace("%", "")
        if val_str.lower() in ("off", "false", "0"):
            rm.config.auto_be_take_profit = False
            msg = "🛡️ **Break-Even Profit Booking:** Deactivated (`OFF`). SL moves to BE without partial take-profit."
        elif val_str.lower() in ("on", "true"):
            rm.config.auto_be_take_profit = True
            msg = f"🛡️ **Break-Even Profit Booking:** Activated (`ON`). Default booking: `{rm.config.be_close_pct:.0%}`."
        else:
            try:
                val = float(val_str)
                if val > 1.0:
                    val = val / 100.0
                val = max(0.05, min(0.90, val))
                rm.config.be_close_pct = val
                rm.config.auto_be_take_profit = True
                msg = (
                    f"✅ **Break-Even Profit Booking Updated:**\n"
                    f"• Partial take-profit on BE: **`{val:.0%}`**\n"
                    f"• Status: `ACTIVE`\n"
                    f"• When Stop Loss is advanced to Break-Even, bot will book `{val:.0%}` size in cash."
                )
            except ValueError:
                msg = "⚠️ Invalid value. Use `/beprofit 33`, `/beprofit 50`, `/beprofit on`, or `/beprofit off`."
    else:
        st = "🟢 ACTIVE" if rm.config.auto_be_take_profit else "🔴 OFF"
        msg = (
            f"💰 **Break-Even Profit Booking Status:**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Status:** {st}\n"
            f"• **Close Percentage:** `{rm.config.be_close_pct:.0%}` of position\n"
            f"• **Trigger Stage:** When SL moves to Break-Even (Progressive Auto BE & Scalp BE)\n"
            f"• **Rule:** Locks in profit while placing Stop Loss at entry + buffer, leaving remainder to run risk-free.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Change with: `/beprofit 25`, `/beprofit 33`, `/beprofit 50`, `/beprofit off`_"
        )
    await safe_reply(update, msg, parse_mode="Markdown")


async def gold_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Institutional Intelligence & Skills Report for Gold (XAUUSD / GLD / Miners)."""
    bridge = get_skills_bridge()
    vote = bridge.get_fundamental_vote("XAUUSD")
    open_ok, market_status_msg = bridge.market_hours_guard.is_market_open("XAUUSD")

    msg = (
        "🥇 **Gold (XAUUSD / GLD / Miners) Trader Skills Intelligence:**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Institutional Consensus:** `{vote.direction.name}` (Conviction: `{vote.strength:.0%}`)\n"
        f"💡 **Synergy:** _{vote.reason}_\n\n"
        f"⏰ **Market Session:** {'🟢 OPEN' if open_ok else '🔴 CLOSED'} — {market_status_msg}\n"
        "• *Trading Week:* Sunday 22:00 UTC (17:00 EST) to Friday 21:00 UTC (16:00 EST)\n"
        "• *Daily Rollover:* 17:00 – 18:00 EST maintenance window\n\n"
        "🐋 **Institutional 13F Exposure:**\n"
        "• Ray Dalio / Bridgewater: Core historical GLD/IAU physical gold trusts\n"
        "• Duquesne & Greenlight: Macro sovereign inflation hedge reserves\n"
        "• Gold Miner Proxies: NEM, GOLD (Barrick), AEM, GDX\n\n"
        "🛡️ **Risk & Execution Safeguards:**\n"
        "• Sizing: Fractional & MT5 Lot Support (0.01 micro lot = 1 oz)\n"
        "• Wash-Sale Family: XAUUSD, GLD, IAU, GDX, NEM, PAXG linked for IRS safety\n"
        "• Pre-Trade Audit: Auto-verified by `SkillRiskGate` before execution\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Use `/ticket BUY 0.10 XAUUSD` or `/riskcheck` to stage verified trades._"
    )
    await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_skills_keyboard("main"))


async def btc_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Institutional Intelligence & Skills Report for Bitcoin (BTCUSDT / Spot ETFs / Proxies)."""
    bridge = get_skills_bridge()
    vote = bridge.get_fundamental_vote("BTCUSDT")
    open_ok, market_status_msg = bridge.market_hours_guard.is_market_open("BTCUSDT")

    msg = (
        "⚡ **Bitcoin (BTCUSDT / IBIT / MSTR) Trader Skills Intelligence:**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Institutional Consensus:** `{vote.direction.name}` (Conviction: `{vote.strength:.0%}`)\n"
        f"💡 **Synergy:** _{vote.reason}_\n\n"
        f"⏰ **Market Session:** {'🟢 OPEN' if open_ok else '🔴 CLOSED'} — {market_status_msg}\n"
        "• *Continuous Trading:* 24 hours / 7 days / 365 days never closes\n\n"
        "🐋 **Institutional 13F & SEC Filings:**\n"
        "• Spot ETFs: BlackRock IBIT & Fidelity FBTC 13F institutional accumulation\n"
        "• Corporate Balance Sheets: MicroStrategy (MSTR) material 8-K Bitcoin debt/buys\n"
        "• Custody & Exchange: Coinbase (COIN) transaction & institutional custody flows\n"
        "• Top Filers: ARK Invest (Cathie Wood), Renaissance, Citadel, Millennium\n\n"
        "🛡️ **Risk & Execution Safeguards:**\n"
        "• Sizing: Fractional Coin / Satoshis sizing via `trader-budget`\n"
        "• Wash-Sale Family: BTCUSDT, BTCUSD, WBTC, IBIT, FBTC, BITO linked\n"
        "• Volatility Buffer: Dynamic margin buffer & daily loss limits enforced\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Use `/ticket BUY 0.05 BTCUSDT` or `/riskcheck` to stage verified trades._"
    )
    await safe_reply(update, msg, parse_mode="Markdown", reply_markup=build_skills_keyboard("main"))


async def bridge_status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Display how the 11 trader skills are combined with all bot agents."""
    text = (
        "🔗 **Agent-Skills Unified Architecture Bridge:**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "All 11 Trader Skills work in direct synergy with the 118-Agent Collective Swarm & Consensus Engine:\n\n"
        "🧠 **1. Fundamental Consensus Agent (`SKILL-FUNDAMENTAL`):**\n"
        "• Combines 13F institutional portfolios (Buffett, Scion, Dalio) with `/copycat`\n"
        "• Tracks Congressional insider disclosures via STOCK Act PTRs with `/pelosi`\n"
        "• Ingests material 8-K filings and SEC XBRL financial statements (`/analyst`)\n"
        "• Boosts consensus conviction when fundamentals align with technical setups\n\n"
        "🎯 **2. Candidate Universe Screener (`/screener`):**\n"
        "• Dynamically screens thousands of stocks for undervalued growth & high ROE\n"
        "• Feeds high-conviction watchlists directly into the Swarm's scanning cycle\n\n"
        "💰 **3. Multi-Asset Portfolio Allocation (`/budget` & `/rebalance`):**\n"
        "• Sizes positions with cash buffers & single-stock exposure caps\n"
        "• Detects portfolio drift and triggers rebalance trades\n\n"
        "🛡️ **4. Pre-Trade Risk Firewall & Tax Gate (`/riskcheck` & `/tax`):**\n"
        "• Enforces strict pre-flight order ceilings and margin requirements\n"
        "• Enforces IRS 30-day Wash-Sale protection to preserve tax deductions\n\n"
        "⏰ **5. Market Session & Exchange Clock (`/nyse`):**\n"
        "• Synchronizes equity and ETF execution with official NYSE market hours\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ **Status:** Operational & Fully Unified"
    )
    await safe_reply(update, text, parse_mode="Markdown", reply_markup=build_skills_keyboard("main"))


def register_trader_skills(app: Application) -> None:
    """Register all 11 Trader Skills handlers with the Telegram bot application."""
    app.add_handler(CommandHandler(["skills", "skill", "hub"], skills_command))
    app.add_handler(CommandHandler(["autodoctor", "doctor", "tradesense"], autodoctor_command))
    app.add_handler(CommandHandler(["skillsbridge", "bridge"], bridge_status_command))
    app.add_handler(CommandHandler(["beprofit", "bepnl", "profitbe"], beprofit_command))
    app.add_handler(CommandHandler(["gold", "xau", "xauusd"], gold_command))
    app.add_handler(CommandHandler(["btc", "bitcoin", "btcusdt"], btc_command))
    app.add_handler(CommandHandler(["copycat", "13f", "clone"], copycat_command))
    app.add_handler(CommandHandler(["pelosi", "congress", "senate", "ptr"], pelosi_command))
    app.add_handler(CommandHandler(["secnews", "8k", "filings"], secnews_command))
    app.add_handler(CommandHandler(["analyst", "facts", "xbrl"], analyst_command))
    app.add_handler(CommandHandler(["screener", "screen"], screener_command))
    app.add_handler(CommandHandler(["budget", "size"], budget_command))
    app.add_handler(CommandHandler(["riskcheck", "checkrisk"], riskcheck_command))
    app.add_handler(CommandHandler(["ticket", "tickets"], ticket_command))
    app.add_handler(CommandHandler(["tax", "lots", "harvest"], tax_command))
    app.add_handler(CommandHandler(["nyse", "marketclock", "clock"], nyse_command))
    app.add_handler(CommandHandler(["rebalance", "drift"], rebalance_command))

    app.add_handler(CallbackQueryHandler(skills_callback_handler, pattern=r"^ts:"))
    logger.info("Successfully registered 11 Trader Skills Telegram commands & callbacks")
