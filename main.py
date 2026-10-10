#!/usr/bin/env python3
"""
TELEGRAM MULTI-AGENT TRADING BOT & AI ASSISTANT
===============================================
Interactive Telegram bot powered by:
- Google Gemini AI (technical analysis, trade planning, conversational assistant)
- 5-Agent Consensus Engine (Trend, Liquidity SMC, 10 Indicators, AI-Learner, News)
- Advanced Institutional Risk Manager (Auto-BE, Lot Sizing, Circuit Breakers)
- TradingView & Multi-Source Market Data Feeds

Commands:
  /start            - Introduction and command menu
  /help             - Detailed guide and explanation
  /price [SYMBOL]   - Live price, technical ratings, and stats from TradingView
  /signal [SYMBOL]  - Run 5-agent consensus analysis on demand
  /status           - Current bot status, active mode, and equity\n  /health           - Monitoring freshness and error status
  /risk             - Risk manager report, drawdown, and lot sizing
  /news             - Macroeconomic calendar events and crypto sentiment
  /positions        - Open positions and trailing stop status
  /autotrade [on|off] - Toggle automated live signal alerts
  /backtest [bars]  - Run backtest on demand and return report
  /reset            - Clear conversation memory
  any text          - Chat with Gemini AI trading assistant
"""

import asyncio
import logging
from datetime import datetime, timezone
import os
import sys
import threading
import time as time_module
from typing import Dict, Optional

import pandas as pd
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from gemini_client import GeminiClient
from multi_agent_trading_bot import (
    Arbiter,
    MarketDataFeed,
    NewsAgent,
    Position,
    evaluate_symbol,
    send_telegram,
)
from risk_manager import RiskManager
from backtest_engine import BacktestConfig, run_backtest
from ops_dashboard import StatusStore, health_from_snapshot, is_authorized_user

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("TradingBot")

gemini = GeminiClient()
risk_mgr = RiskManager(equity=config.INITIAL_EQUITY, symbol=config.DEFAULT_SYMBOL)
risk_mgr.set_leverage(config.DEFAULT_LEVERAGE)
arbiter = Arbiter()
engine = ExecutionEngine(risk_manager=risk_mgr)

# Bot runtime state. Monitoring is off by default until explicitly enabled by an authorized user.
autotrade_active: bool = config.AUTOTRADE_ENABLED
background_task: Optional[asyncio.Task] = None
monitoring_health = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_poll_at": None,
    "last_success_at": None,
    "last_error": None,
    "consecutive_errors": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_runtime_status() -> None:
    """Publish a read-only operations snapshot; never blocks bot operation on dashboard I/O."""
    try:
        summary = engine.get_portfolio_summary()
        state = risk_mgr.state
        snapshot = {
            "service": "TradingBot",
            "state": "running",
            "started_at": monitoring_health["started_at"],
            "last_poll_at": monitoring_health["last_poll_at"],
            "last_success_at": monitoring_health["last_success_at"],
            "last_error": monitoring_health["last_error"],
            "consecutive_errors": monitoring_health["consecutive_errors"],
            "autotrade_active": bool(autotrade_active),
            "configured_broker_mode": config.BROKER_MODE,
            "symbol": config.DEFAULT_SYMBOL,
            "data_source": config.DEFAULT_DATA_SOURCE,
            "equity": float(summary.get("equity", 0.0)),
            "balance": float(summary.get("balance", 0.0)),
            "active_positions": int(summary.get("active_positions_count", 0)),
            "pending_setups": int(summary.get("pending_setups_count", 0)),
            "risk_mode": str(state.mode),
        }
        StatusStore(config.STATUS_FILE).write(snapshot)
    except Exception as exc:
        logger.warning("Could not publish operations status: %s", exc)


async def require_control_authorization(update: Update) -> bool:
    """Protect state-changing Telegram commands with an explicit user ID allowlist."""
    user = update.effective_user
    user_id = getattr(user, "id", None)
    if not is_authorized_user(user_id, config.TELEGRAM_ALLOWED_USER_IDS):
        message = update.effective_message
        if message:
            await message.reply_text(
                "Control command denied. Configure TELEGRAM_ALLOWED_USER_IDS with your "
                "Telegram numeric user ID before using this command."
            )
        return False
    return True


# ======================================================================
# MARKET DATA HELPERS
# ======================================================================
def get_price_info(symbol: str = "XAUTUSD") -> Dict:
    """Fetch live ticker quote from TradingView with fallback."""
    from tradingview_feed import fetch_tradingview_quote
    tv_info = fetch_tradingview_quote(symbol)
    if tv_info.get("ok"):
        return tv_info

    # Secondary fallback to Delta Exchange if TradingView is unreachable
    sym = symbol.upper()
    if sym.endswith("USDT") and not sym.endswith("USD"):
        sym = sym.replace("USDT", "USD")

    url = f"{config.DELTA_API}/{sym}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            res = r.json().get("result")
            if res:
                mark = float(res.get("mark_price") or res.get("close") or 0.0)
                close_p = float(res.get("close") or mark)
                open_p = float(res.get("open") or mark)
                high_p = float(res.get("high") or mark)
                low_p = float(res.get("low") or mark)
                chg = ((close_p - open_p) / open_p * 100) if open_p > 0 else 0.0

                return {
                    "ok": True,
                    "symbol": sym,
                    "mark": mark,
                    "close": close_p,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "change_pct": round(chg, 2),
                    "recommendation": "NEUTRAL",
                    "source": "Delta Exchange",
                }
    except Exception as e:
        logger.warning(f"Delta API fallback notice for {sym}: {e}")

    # Fail closed instead of presenting generated values as live market data.
    logger.error("Live quote unavailable for %s from TradingView and Delta Exchange", sym)
    return {"ok": False, "symbol": sym, "error": "Live market data unavailable; no synthetic price was substituted.", "source": "unavailable"}


def format_price_string(info: Dict) -> str:
    """Format price dict into a clean Telegram string."""
    if not info.get("ok"):
        return f"Price unavailable for {info.get('symbol', 'UNKNOWN')}"
    sym = info["symbol"]
    chg = info["change_pct"]
    arrow = "🟢" if chg >= 0 else "🔴"
    rec = info.get("recommendation", "NEUTRAL")
    rec_icon = "🟢" if "BUY" in rec else ("🔴" if "SELL" in rec else "⚪")
    source_tag = info.get("tv_symbol") or info.get("source", "TradingView")

    extra_lines = []
    if "recommendation" in info:
        extra_lines.append(f"• **TradingView Rating:** {rec_icon} `{rec}`")
    if "rsi" in info:
        extra_lines.append(f"• **RSI (14):** `{info['rsi']}`")

    extra_str = "\n" + "\n".join(extra_lines) if extra_lines else ""

    return (
        f"📊 **{sym} Market Price**\n"
        f"_{source_tag}_\n\n"
        f"• **Mark Price:** `${info['mark']:,.2f}`\n"
        f"• **24h Change:** {arrow} `{chg:+.2f}%`\n"
        f"• **High / Low:** `${info['high']:,.2f}` / `${info['low']:,.2f}`\n"
        f"• **24h Open:** `${info['open']:,.2f}`"
        f"{extra_str}"
    )


# ======================================================================
# COMMAND HANDLERS
# ======================================================================
async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Intro and dashboard."""
    welcome = (
        "🤖 **Institutional Multi-Agent Trading Assistant**\n\n"
        "Welcome! I am an automated trading and technical analysis bot combining:\n"
        "• **5 Specialized Analysis Agents:** Trend, SMC Liquidity, 10 Indicators, AI-Learner, and Global News.\n"
        "• **Automated Execution Engine:** Limit setup triggers, auto break-even, partials & trailing stops.\n"
        "• **Configured leverage cap (1x - 20x):** Manually adjustable margin management and liquidation protection.\n"
        "• **Gemini AI:** Technical analysis, trade planning, and conversational assistance.\n\n"
        "⚡ **Quick Commands:**\n"
        "• `/price [SYMBOL]` — Live ticker (e.g., `/price XAUTUSD`, `/price BTCUSD`)\n"
        "• `/signal [SYMBOL]` — Run 5-agent consensus analysis\n"
        "• `/leverage [1-20]` — View or manually adjust account leverage\n"
        "• `/positions` — View active positions & pending setup triggers\n"
        "• `/close [SYMBOL]` — Manually close an active position immediately\n"
        "• `/cancel [SYMBOL]` — Cancel a pending setup order before it executes\n"
        "• `/status` — View bot state, leverage, margin utilization, and equity\n"
        "• `/risk` — Lot-sizing, margin requirements, and risk report\n"
        "• `/news` — High-impact economic calendar & crypto sentiment\n"
        "• `/autotrade on|off` — Toggle automated live execution & alerts\n"
        "• `/backtest 2000` — Run historical simulation\n"
        "• `/help` — Full command manual\n\n"
        "💬 *You can also ask me anything about setups, risk, or market levels!*"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Detailed help reference."""
    help_text = (
        "📖 **Trading Bot Command Guide**\n\n"
        "**Market Data & Analysis:**\n"
        "• `/price [SYMBOL]` — Live price, metrics, and technical rating from TradingView.\n"
        "• `/signal [SYMBOL]` — Evaluates symbol across all 5 agents (Trend, Liquidity, Indicators, AI-Learner, News) and prints consensus.\n"
        "• `/news` — Displays upcoming high-impact economic calendar events (FOMC, CPI, NFP) and crypto headlines.\n\n"
        "**Leverage & Trade Execution:**\n"
        "• `/leverage` — View current leverage (1x-500x), margin requirement rate, and lot capacities.\n"
        "• `/leverage [1-20]` — Manually set leverage (e.g., `/leverage 50`, `/leverage 200`, `/leverage 500`).\n"
        "• `/positions` — View active live trades and pending setups waiting for level trigger.\n"
        "• `/close [SYMBOL]` — Manually exit an active trade immediately at current market price.\n"
        "• `/cancel [SYMBOL]` — Cancel a pending setup order before it executes.\n\n"
        "**Risk & Portfolio:**\n"
        "• `/status` — Overall status, equity, used margin, free margin, margin level %, and active mode.\n"
        "• `/risk` — Drawdown, loss limits, margin calculations, and auto-BE rules.\n\n"
        "**Automation & Backtesting:**\n"
        "• `/autotrade on` — Activate live automatic execution when setup levels are reached.\n"
        "• `/autotrade off` — Deactivate automated trading execution.\n"
        "• `/backtest [bars] [SYMBOL] [TIMEFRAME]` — Backtest historical candles (no live orders).\n\n"
        "**AI Assistant:**\n"
        "• `/reset` — Clear conversational memory.\n"
        "• Send any question — The AI answers with latest live price context!"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def price_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Get live price for symbol."""
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    info = get_price_info(sym)
    await update.message.reply_text(format_price_string(info), parse_mode="Markdown")


async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run on-demand multi-agent consensus analysis."""
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    await update.message.reply_text(f"⏳ Evaluating **{sym}** across all 5 agents...", parse_mode="Markdown")

    res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE, leverage=risk_mgr.get_leverage())
    price_val = res.get("price", 0.0)
    sig = res.get("signal")
    votes = res.get("votes", [])

    vote_lines = []
    for v in votes:
        icon = "🟢" if v["direction"] == "LONG" else ("🔴" if v["direction"] == "SHORT" else "⚪")
        vote_lines.append(f"{icon} **{v['agent']}:** {v['direction']} ({v['reason']})")

    status_str = f"⚡ **Consensus:** `{res.get('status', 'No consensus')}`"
    if sig:
        dir_icon = "🟢 LONG" if sig["direction"].name == "LONG" else "🔴 SHORT"
        sig_box = (
            f"\n🎯 **TRADE SETUP IDENTIFIED ({dir_icon}):**\n"
            f"• **Entry:** `${sig['entry']:,.2f}`\n"
            f"• **Stop Loss:** `${sig['stop']:,.2f}` (Risk: `{abs(sig['entry'] - sig['stop']):.2f}`)\n"
            f"• **Target 1:** `${sig['tp1']:,.2f}` (Move to BE)\n"
            f"• **Target 2:** `${sig['tp2']:,.2f}` (Trail remainder)\n"
            f"• **Recommended Lots:** `{sig['lots']}` | **Leverage:** `{sig.get('leverage', risk_mgr.get_leverage()):.0f}x`\n"
            f"• **Margin Required:** `${sig.get('margin_required', 0.0):,.2f}`\n"
            f"• **R:R Ratio:** `1:{sig['rr']:.1f}` | **Score:** `{sig['score']:.2f}`\n"
        )
    else:
        sig_box = "\n🛡️ **Recommendation:** Stay Flat. Waiting for high-probability confluence.\n"

    msg = (
        f"📊 **Multi-Agent Evaluation for {sym}**\n"
        f"Mark Price: `${price_val:,.2f}`\n\n"
        f"**Agent Votes:**\n"
        + "\n".join(vote_lines)
        + f"\n\n{status_str}\n{sig_box}\n"
        f"_{res.get('risk_summary', '')}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View bot status, mode, and equity."""
    s = risk_mgr.state
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    auto_status = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
    summary = engine.get_portfolio_summary()

    msg = (
        f"📈 **Trading Bot Status Dashboard**\n\n"
        f"• **Autotrade Execution:** {auto_status}\n"
        f"• **Active Symbol:** `{config.DEFAULT_SYMBOL}`\n"
        f"• **Account Equity:** `${summary['equity']:,.2f}` (Balance: `${summary['balance']:,.2f}`)\n"
        f"• **Leverage:** `{summary['leverage']:.0f}x` (1x-500x adjustable)\n"
        f"• **Used Margin:** `${summary['used_margin']:,.2f}` | **Free Margin:** `${summary['free_margin']:,.2f}`\n"
        f"• **Margin Level:** `{summary['margin_level_pct']:.1f}%`\n"
        f"• **Unrealized PnL:** `${summary['unrealized_pnl']:+,.2f}`\n"
        f"• **Open Positions:** `{summary['active_positions_count']}` | **Pending Setups:** `{summary['pending_setups_count']}`\n"
        f"• **Risk Mode:** {mode_icon} `{s.mode.upper()}` (RF ×`{s.rf:.2f}`)\n"
        f"• **Today's PnL:** `${s.daily_pnl:+,.2f}`\n"
        f"• **Total Trades (Win Rate):** `{s.total_trades}` ({s.recent_winrate * 100:.1f}%)\n\n"
        f"_{risk_mgr.summary()}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def risk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View detailed risk manager metrics and lot size calculations."""
    s = risk_mgr.state
    cfg = risk_mgr.config
    risk_pct, reason = risk_mgr.current_risk_pct()
    dd = (s.peak_equity - s.equity) / s.peak_equity * 100 if s.peak_equity else 0.0
    lev = risk_mgr.get_leverage()

    # Calculate lot size example for Gold (XAUUSD) with 10 pt stop
    plan_gold = risk_mgr.autotrade_plan(entry=2650.0, stop=2640.0, symbol="XAUUSD")
    plan_btc = risk_mgr.autotrade_plan(entry=80000.0, stop=79000.0, symbol="BTCUSDT")

    msg = (
        f"🛡️ **Institutional Risk Manager Report**\n\n"
        f"• **Current Risk Per Trade:** `{risk_pct:.2f}%` ({reason})\n"
        f"• **Current Drawdown:** `{dd:.1f}%`\n"
        f"• **Leverage System:** `{lev:.0f}x` (Allowed: {cfg.min_leverage:.0f}x - {cfg.max_leverage:.0f}x)\n"
        f"• **Max Margin Utilization:** `{cfg.max_margin_utilization_pct}%`\n"
        f"• **Risk Factor (RF):** `{s.rf:.2f}` (Min: {cfg.rf_min}, Max: {cfg.rf_max})\n"
        f"• **Daily Loss Limit:** `{cfg.max_daily_loss_pct}%`\n"
        f"• **Weekly Loss Limit:** `{cfg.max_weekly_loss_pct}%`\n"
        f"• **Auto Break-Even Trigger:** Lock +0.1R @ +{cfg.be_lock_r}R | Full BE @ +{cfg.be_at_r}R\n\n"
        f"📏 **Position & Margin Calculations (${s.equity:,.2f} Equity @ {lev:.0f}x Leverage):**\n"
        f"• **XAUUSD (10 pt SL):** `{plan_gold.get('lots', 0.01)} lots` | Margin: `${plan_gold.get('margin_required', 0.0):.2f}` | Risk: `${plan_gold.get('risk_amount', 0.0):.2f}`\n"
        f"• **BTCUSDT ($1000 SL):** `{plan_btc.get('lots', 0.001)} lots` | Margin: `${plan_btc.get('margin_required', 0.0):.2f}` | Risk: `${plan_btc.get('risk_amount', 0.0):.2f}`\n\n"
        f"_{risk_mgr.summary()}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def leverage_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View or adjust configured leverage."""
    if ctx.args and not await require_control_authorization(update):
        return
    if ctx.args:
        try:
            val = float(ctx.args[0])
            ok, msg = risk_mgr.set_leverage(val)
            if ok:
                lev = risk_mgr.get_leverage()
                margin_pct = (1.0 / lev) * 100.0
                gold_p = get_price_info("XAUUSD").get("mark", 2650.0)
                gold_margin = (0.01 * 100 * gold_p) / lev
                text = (
                    f"⚡ **LEVERAGE UPDATED SUCCESSFULLY**\n\n"
                    f"• **New Leverage:** `{lev:.0f}x`\n"
                    f"• **Margin Requirement Rate:** `{margin_pct:.2f}%` of notional\n"
                    f"• **Max Margin Utilization Cap:** `{risk_mgr.config.max_margin_utilization_pct:.0f}%`\n"
                    f"• **Estimated Gold Margin (0.01 lot):** `~${gold_margin:,.2f}` @ `${gold_p:,.2f}`\n\n"
                    f"_{msg}_"
                )
            else:
                text = f"❌ **Error Setting Leverage:**\n{msg}\n\nValid range is `1x` to `500x` (e.g. `/leverage 100`)."
        except ValueError:
            text = "❌ **Invalid Leverage Value**\nPlease specify a number between 1 and 500.\nExample: `/leverage 50`, `/leverage 100`, `/leverage 500`."
    else:
        lev = risk_mgr.get_leverage()
        margin_pct = (1.0 / lev) * 100.0
        gold_p = get_price_info("XAUUSD").get("mark", 2650.0)
        gold_margin = (0.01 * 100 * gold_p) / lev
        btc_p = get_price_info("BTCUSDT").get("mark", 80000.0)
        btc_margin = (0.001 * 1 * btc_p) / lev
        text = (
            f"⚙️ **LEVERAGE SYSTEM CONFIGURATION**\n\n"
            f"• **Current Leverage:** `{lev:.0f}x`\n"
            f"• **Allowed Range:** `{risk_mgr.config.min_leverage:.0f}x` to `{risk_mgr.config.max_leverage:.0f}x`\n"
            f"• **Margin Rate:** `{margin_pct:.2f}%` (Notional / Leverage)\n"
            f"• **Max Margin Utilization:** `{risk_mgr.config.max_margin_utilization_pct:.0f}%`\n\n"
            f"📏 **Current Margin Required for Setups:**\n"
            f"• **XAUUSD (0.01 lot / 1 oz):** `~${gold_margin:,.2f}`\n"
            f"• **BTCUSDT (0.001 BTC):** `~${btc_margin:,.2f}`\n\n"
            f"💡 **To adjust leverage manually:**\n"
            f"Send `/leverage [1-20]`\n"
            f"Examples: `/leverage 20` | `/leverage 100` | `/leverage 200` | `/leverage 500`"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def close_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Close an in-process position after authorization."""
    if not await require_control_authorization(update):
        return
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    if sym not in engine.active_positions:
        await update.message.reply_text(f"📂 No active position found for `{sym}`.", parse_mode="Markdown")
        return

    curr_p = get_price_info(sym).get("mark", engine.active_positions[sym].executed_price or engine.active_positions[sym].entry_level)
    pos = engine.close_manually(sym, curr_p, reason="Manual user exit via /close")
    if pos:
        pnl_sign = "+" if pos.realized_pnl >= 0 else "-"
        text = (
            f"⏹️ **POSITION CLOSED MANUALLY**\n\n"
            f"• **Symbol:** `{pos.symbol}` ({pos.direction})\n"
            f"• **Exit Price:** `${curr_p:,.2f}`\n"
            f"• **Realized PnL:** `{pnl_sign}${abs(pos.realized_pnl):,.2f}`\n"
            f"• **Account Equity:** `${risk_mgr.state.equity:,.2f}`"
        )
    else:
        text = f"Could not close position for `{sym}`."
    await update.message.reply_text(text, parse_mode="Markdown")


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending setup after authorization."""
    if not await require_control_authorization(update):
        return
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    cancelled = engine.cancel_setup(sym, reason="Telegram user cancelled via /cancel")
    if cancelled:
        text = f"🚫 **SETUP CANCELLED**\nPending setup for `{sym}` ({cancelled.direction} @ `${cancelled.entry_level:,.2f}`) has been cancelled."
    else:
        text = f"ℹ️ No pending setup found for `{sym}`."
    await update.message.reply_text(text, parse_mode="Markdown")


async def news_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View upcoming high-impact economic calendar events and crypto sentiment."""
    news = NewsAgent(enabled=True)
    events = news.calendar()
    d, strength, shock = news.sentiment()

    ev_lines = []
    for dt, title in events[:7]:
        ev_lines.append(f"• `{dt.strftime('%b %d %H:%M UTC')}`: **{title}**")

    shock_text = "🚨 **SHOCK DETECTED** (stay flat)" if shock else "✅ Normal market flow"
    sentiment_label = "🟢 Bullish" if d.name == "LONG" else ("🔴 Bearish" if d.name == "SHORT" else "⚪ Neutral")

    msg = (
        f"📰 **Global Macroeconomic & News Intelligence**\n\n"
        f"**Crypto RSS Headline Sentiment:** {sentiment_label} (Conviction: `{strength:.0%}`)\n"
        f"**Blackout Filter:** {shock_text}\n\n"
        f"**High-Impact USD Calendar Events This Week:**\n"
        + (("\n".join(ev_lines)) if ev_lines else "• No upcoming high-impact events detected")
        + f"\n\n_Auto-veto active within ±{config.NEWS_WINDOW_MIN} mins of high-impact events._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def positions_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View active open positions and pending level triggers."""
    lines = []

    if engine.active_positions:
        lines.append("🟢 **LIVE ACTIVE POSITIONS:**")
        for sym, p in engine.active_positions.items():
            dir_icon = "🟢 LONG" if p.direction == "LONG" else "🔴 SHORT"
            curr_p = get_price_info(sym).get("mark", p.executed_price or p.entry_level)
            pnl_val = (curr_p - p.executed_price) * p.remaining_size if p.direction == "LONG" else (p.executed_price - curr_p) * p.remaining_size
            pnl_icon = "🟢" if pnl_val >= 0 else "🔴"
            liq_str = f" | Liq: `${p.liquidation_price:,.2f}`" if p.liquidation_price else ""
            lines.append(
                f"• **{sym} ({dir_icon})**\n"
                f"  Entry: `${p.executed_price or p.entry_level:,.2f}` | Current: `${curr_p:,.2f}`\n"
                f"  Unrealized PnL: {pnl_icon} `${pnl_val:+,.2f}`\n"
                f"  Leverage: `{p.leverage:.0f}x` | Margin: `${p.margin_required:,.2f}`{liq_str}\n"
                f"  Stop Loss: `${p.stop_loss:,.2f}` (Stage: {p.be_stage})\n"
                f"  Targets: TP1 `${p.tp1:,.2f}` | TP2 `${p.tp2:,.2f}`\n"
                f"  Lots: `{p.remaining_lots}` (Partials: {p.partials_done})"
            )
        lines.append("")

    if engine.pending_setups:
        lines.append("⏳ **PENDING SETUPS (WAITING FOR TRIGGER LEVEL):**")
        for sym, s in engine.pending_setups.items():
            dir_icon = "🟢 LONG" if s.direction == "LONG" else "🔴 SHORT"
            curr_p = get_price_info(sym).get("mark", s.entry_level)
            dist_pct = abs(curr_p - s.entry_level) / s.entry_level * 100
            lines.append(
                f"• **{sym} ({dir_icon})**\n"
                f"  Trigger Entry Level: `${s.entry_level:,.2f}` (Current: `${curr_p:,.2f}`, Dist: `{dist_pct:.2f}%`)\n"
                f"  Stop Loss: `${s.stop_loss:,.2f}` | R:R: `1:{s.rr_ratio:.1f}`\n"
                f"  Planned Lots: `{s.lots}` | Leverage: `{s.leverage:.0f}x` | Margin: `${s.margin_required:,.2f}`\n"
                f"  Targets: TP1 `${s.tp1:,.2f}` | TP2 `${s.tp2:,.2f}`\n"
                f"  Setup Reason: {s.setup_reason}\n"
                f"  Status: ⚡ Auto-execute when level is reached"
            )

    if not lines:
        await update.message.reply_text("📂 No active positions or pending setups. Engine is flat.")
        return

    summary = engine.get_portfolio_summary()
    lines.append(
        f"\n💼 **Portfolio Margin Summary:**\n"
        f"• Equity: `${summary['equity']:,.2f}` | Balance: `${summary['balance']:,.2f}`\n"
        f"• Used Margin: `${summary['used_margin']:,.2f}` | Free: `${summary['free_margin']:,.2f}`\n"
        f"• Margin Level: `{summary['margin_level_pct']:.1f}%` | Leverage: `{summary['leverage']:.0f}x`"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle automated live monitoring and alerts."""
    global autotrade_active, background_task
    action = ctx.args[0].lower() if ctx.args else ""

    if action in ("on", "start", "enable"):
        if not await require_control_authorization(update):
            return
        autotrade_active = True
        msg = "🟢 **Monitoring ENABLED**\nThe bot will scan market data and report setups. This build's monitoring toggle does not authorize live exchange orders."
    elif action in ("off", "stop", "disable"):
        if not await require_control_authorization(update):
            return
        autotrade_active = False
        msg = "⚪ **Monitoring DISABLED**\nNew setup scanning is paused. Existing in-process positions continue to be monitored."
    else:
        status_text = "ENABLED 🟢" if autotrade_active else "DISABLED ⚪"
        msg = f"Autotrade monitoring is currently **{status_text}**.\nUse `/autotrade on` or `/autotrade off` to toggle."

    await update.message.reply_text(msg, parse_mode="Markdown")


async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run a historical OHLCV backtest without submitting exchange orders."""
    args = list(ctx.args or [])
    bars = 1000
    if args:
        try:
            bars = int(args[0])
        except (TypeError, ValueError):
            await update.message.reply_text("Usage: /backtest [100-10000 bars] [SYMBOL] [TIMEFRAME]")
            return
    if not 100 <= bars <= 10000:
        await update.message.reply_text("Choose between 100 and 10,000 candles.")
        return

    symbol = args[1].upper() if len(args) > 1 else config.DEFAULT_SYMBOL.upper()
    timeframe = args[2].lower() if len(args) > 2 else "1h"
    allowed_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    if timeframe not in allowed_timeframes:
        await update.message.reply_text("Supported timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d")
        return
    if not symbol or len(symbol) > 30 or any(ch.isspace() for ch in symbol):
        await update.message.reply_text("Please provide a valid symbol.")
        return

    await update.message.reply_text(
        f"Loading up to {bars} historical {timeframe} candles for {symbol}. "
        "This is a simulation only; no exchange orders will be submitted."
    )

    def _run_historical_backtest():
        # Use the direct data_feed adapter, not the embedded multi-agent fallback
        # that can synthesize demo candles. Missing history must fail closed.
        from data_feed import DataFeed
        feed = DataFeed(
            symbol=symbol,
            source="ccxt",
            exchange_id=config.DEFAULT_EXCHANGE,
            timeframe=timeframe,
            api_key="",
            api_secret="",
        )
        historical = feed.fetch(limit=bars, timeframe=timeframe)
        if historical is None or historical.empty:
            raise ValueError(
                "No historical candles were returned. Check the symbol, timeframe, "
                "data provider, and available history; synthetic candles are not used."
            )
        return run_backtest(
            historical.tail(bars),
            config=BacktestConfig(initial_equity=config.INITIAL_EQUITY),
            symbol=symbol,
            timeframe=timeframe,
        )

    try:
        stats = await asyncio.to_thread(_run_historical_backtest)
    except Exception as exc:
        logger.warning("Historical backtest failed for %s %s: %s", symbol, timeframe, exc)
        await update.message.reply_text(f"Backtest unavailable: {exc}")
        return

    pf = stats["profit_factor"]
    pf_text = "n/a" if pf is None else ("infinity" if pf == float("inf") else f"{pf:.2f}")
    ret_icon = "🟢" if stats["return_pct"] >= 0 else "🔴"
    message = (
        f"📊 *Historical Backtest — {stats['symbol']} ({stats['timeframe']})*\n\n"
        f"• Strategy: {stats['strategy']}\n"
        f"• Candles: {stats['candles']}\n"
        f"• Closed trades: {stats['trades']}\n"
        f"• Win rate: {stats['win_rate_pct']:.1f}% ({stats['wins']} wins)\n"
        f"• Net P&L: USD {stats['net_pnl']:+,.2f}\n"
        f"• Return: {ret_icon} {stats['return_pct']:+.2f}%\n"
        f"• Max drawdown: {stats['max_drawdown_pct']:.2f}%\n"
        f"• Profit factor: {pf_text}\n"
        f"• Expectancy/trade: USD {stats['expectancy_per_trade']:+,.2f}\n"
        f"• Fees: USD {stats['fees']:,.2f}\n"
        f"• Final equity: USD {stats['final_equity']:,.2f}\n\n"
        "_Assumptions: next-candle-open entries, fees/slippage, stop-first on same-candle "
        "stop/target touches. Historical results are estimates, not guarantees. No live orders submitted._"
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def health_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show monitoring freshness and recent error count."""
    health = health_from_snapshot(monitoring_health)
    age = health.get("last_success_age_seconds")
    age_text = "never" if age is None else f"{int(age)} seconds ago"
    reasons = "; ".join(health.get("reasons", [])) or "No health warnings"
    await update.message.reply_text(
        f"Health: {health['status'].upper()}\n"
        f"Last successful market-data poll: {age_text}\n"
        f"Consecutive errors: {monitoring_health['consecutive_errors']}\n"
        f"Details: {reasons}"
    )


async def reset_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Clear conversation history."""
    gemini.reset(update.effective_chat.id)
    await update.message.reply_text("🧹 Conversation history cleared. Ready for fresh queries.")


async def chat_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Chat with the Gemini AI trading assistant."""
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    # Fetch live price for default symbol to give Gemini latest market context
    p_info = get_price_info(config.DEFAULT_SYMBOL)
    if p_info.get("ok"):
        market_context = (
            f"[LIVE MARKET DATA: {p_info['symbol']} Mark: {p_info['mark']:.2f} | "
            f"Close: {p_info['close']:.2f} | High: {p_info['high']:.2f} | Low: {p_info['low']:.2f} | "
            f"24h: {p_info['change_pct']:+.2f}%]"
        )
    else:
        market_context = "[LIVE MARKET DATA UNAVAILABLE. Do not infer or invent a current price.]"

    # Let user know assistant is thinking for lengthy queries
    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")

    reply = gemini.ask(chat_id=chat_id, user_text=user_text, market_context=market_context)
    await update.message.reply_text(reply, parse_mode="Markdown")


# ======================================================================
# BACKGROUND MONITORING WORKER
# ======================================================================
async def live_monitoring_worker(app):
    """Background task that polls candles and executes setups when levels are reached."""
    feed = MarketDataFeed(symbol=config.DEFAULT_SYMBOL, source=config.DEFAULT_DATA_SOURCE)
    symbol = config.DEFAULT_SYMBOL

    _write_runtime_status()
    while True:
        monitoring_health["last_poll_at"] = _now_iso()
        try:
            # Continue monitoring existing positions even when new entries are disabled.
            if True:
                data = feed.multi_tf(limit_1m=500)
                if data and "1m" in data and not data["1m"].empty:
                    monitoring_health["last_success_at"] = _now_iso()
                    monitoring_health["last_error"] = None
                    monitoring_health["consecutive_errors"] = 0
                    df1 = data["1m"]
                    daily = data.get("daily", pd.DataFrame())
                    candle = df1.iloc[-1]
                    candle_dict = {
                        "open": float(candle.get("open", candle["close"])),
                        "high": float(candle["high"]),
                        "low": float(candle["low"]),
                        "close": float(candle["close"]),
                    }

                    # Autotrade OFF disables pending entries too, but never stops
                    # monitoring positions that are already open.
                    if not autotrade_active and symbol in engine.pending_setups:
                        engine.cancel_setup(symbol, reason="Autotrade disabled; pending entry cancelled")

                    # 1. Update pending setups and open positions
                    events = engine.update_market_candle(symbol, candle_dict)
                    for ev in events:
                        logger.info(f"Execution Event: {ev['event']} - {ev.get('message')}")
                        if config.TELEGRAM_CHAT_ID:
                            try:
                                await app.bot.send_message(
                                    chat_id=config.TELEGRAM_CHAT_ID, text=ev["message"], parse_mode="Markdown"
                                )
                            except Exception:
                                pass

                    # 2. Scan for new entries if flat and no pending order
                    if autotrade_active and symbol not in engine.active_positions and symbol not in engine.pending_setups:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr, symbol=symbol
                        )
                        if sig:
                            order = engine.register_setup(sig, reason=sig.get("reason", ""))
                            liq_str = f"• Est. Liquidation: `${order.liquidation_price:,.2f}`\n" if order.liquidation_price else ""
                            alert = (
                                f"⚡ **NEW MULTI-AGENT SETUP IDENTIFIED**\n"
                                f"Symbol: `{symbol}` | **{order.direction}**\n"
                                f"• Entry Level: `${order.entry_level:,.2f}`\n"
                                f"• Stop Loss: `${order.stop_loss:,.2f}` | R:R: `1:{order.rr_ratio:.1f}`\n"
                                f"• Lots: `{order.lots}` | Leverage: `{order.leverage:.0f}x` | Margin: `${order.margin_required:,.2f}`\n"
                                f"• Target 1: `${order.tp1:,.2f}` | Target 2: `${order.tp2:,.2f}`\n"
                                f"{liq_str}"
                                f"• Score: `{sig.get('score', 0.0):.2f}`\n\n"
                                f"**Confluence:** {order.setup_reason}\n"
                                f"_Status: PENDING. Will execute automatically when price reaches ${order.entry_level:,.2f}._"
                            )
                            if config.TELEGRAM_CHAT_ID:
                                try:
                                    await app.bot.send_message(
                                        chat_id=config.TELEGRAM_CHAT_ID, text=alert, parse_mode="Markdown"
                                    )
                                except Exception:
                                    pass

                            # Immediate check if entry level is touched on current candle
                            trig_events = engine.update_market_candle(symbol, candle_dict)
                            for ev in trig_events:
                                if config.TELEGRAM_CHAT_ID:
                                    try:
                                        await app.bot.send_message(
                                            chat_id=config.TELEGRAM_CHAT_ID, text=ev["message"], parse_mode="Markdown"
                                        )
                                    except Exception:
                                        pass
                else:
                    monitoring_health["last_error"] = "No market data returned for the monitoring timeframe"
                    monitoring_health["consecutive_errors"] += 1

        except Exception as e:
            monitoring_health["last_error"] = str(e)
            monitoring_health["consecutive_errors"] += 1
            logger.error(f"Monitoring worker error: {e}")

        _write_runtime_status()
        # Adaptive polling: fast-track when tracking active trades or pending setups
        sleep_interval = config.FAST_POLL_INTERVAL if (engine.active_positions or engine.pending_setups) else config.POLL_INTERVAL
        await asyncio.sleep(sleep_interval)


# ======================================================================
# SIMULATED CLI MODE (for testing without a Telegram token)
# ======================================================================
def run_cli_interactive():
    """Interactive command-line interface for running the bot directly in terminal."""
    global autotrade_active
    print(f"\n{'='*64}")
    print("  TRADING BOT - INTERACTIVE TERMINAL MODE")
    print(f"  Symbol: {config.DEFAULT_SYMBOL} | Equity: ${risk_mgr.state.equity:,.2f}")
    print(f"  Leverage: {risk_mgr.get_leverage():.0f}x (1x-500x) | Autotrade: {'ON' if autotrade_active else 'OFF'}")
    print("  Commands: /price, /signal, /leverage [1-20], /positions, /close, /autotrade, /status, /risk, /news, /help")
    print("  Type 'exit' to quit.")
    print(f"{'='*64}\n")

    chat_id = 99999
    while True:
        try:
            cmd = input("TradingBot> ").strip()
            if not cmd:
                continue
            if cmd.lower() in ("exit", "quit"):
                break

            parts = cmd.split()
            c = parts[0].lower()

            if c == "/start":
                print("\n[TradingBot] Multi-Agent Trading Assistant ready. Type /help for commands.\n")
            elif c == "/help":
                print("\nAvailable commands:")
                print("  /price [SYM]      - Live ticker from TradingView")
                print("  /signal [SYM]     - Multi-agent consensus analysis")
                print("  /leverage [1-20] - View or adjust leverage (1x to 500x)")
                print("  /positions        - View open positions and pending level triggers")
                print("  /close [SYM]      - Manually exit an open trade")
                print("  /cancel [SYM]     - Cancel a pending setup order")
                print("  /autotrade on|off - Toggle automated setup execution")
                print("  /status           - Account equity, margin, and bot status")
                print("  /risk             - Risk report and lot-size calculation")
                print("  /news             - Economic calendar & crypto sentiment")
                print("  /backtest [N]     - Run backtest simulation")
                print("  or type any question to chat with Gemini AI.\n")
            elif c == "/price":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                info = get_price_info(sym)
                print("\n" + format_price_string(info) + "\n")
            elif c == "/leverage":
                if len(parts) > 1:
                    ok, msg = risk_mgr.set_leverage(parts[1])
                    if ok:
                        print(f"\n[Leverage] ✅ {msg} (Margin requirement: {100.0/risk_mgr.get_leverage():.2f}% of notional)\n")
                    else:
                        print(f"\n[Leverage] ❌ {msg}\n")
                else:
                    lev = risk_mgr.get_leverage()
                    print(f"\n[Leverage] Current: {lev:.0f}x | Allowed: {risk_mgr.config.min_leverage:.0f}x - {risk_mgr.config.max_leverage:.0f}x | Max Margin: {risk_mgr.config.max_margin_utilization_pct:.0f}%\n")
            elif c == "/positions":
                summary = engine.get_portfolio_summary()
                print(f"\n[Portfolio] Equity: ${summary['equity']:,.2f} | Used Margin: ${summary['used_margin']:,.2f} | Free Margin: ${summary['free_margin']:,.2f} | Leverage: {summary['leverage']:.0f}x")
                if engine.active_positions:
                    print("  Active Positions:")
                    for sym, p in engine.active_positions.items():
                        print(f"    • {sym} {p.direction}: Entry ${p.executed_price:,.2f} | SL ${p.stop_loss:,.2f} | Lots: {p.remaining_lots} | Margin: ${p.margin_required:,.2f}")
                else:
                    print("  No active open positions.")
                if engine.pending_setups:
                    print("  Pending Setup Triggers:")
                    for sym, s in engine.pending_setups.items():
                        print(f"    • {sym} {s.direction}: Waiting for ${s.entry_level:,.2f} | Lots: {s.lots} | Leverage: {s.leverage:.0f}x")
                else:
                    print("  No pending setup triggers.")
                print()
            elif c == "/close":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                curr_p = get_price_info(sym).get("mark", 2650.0)
                closed_order = engine.close_manually(sym, curr_p, reason="CLI manual exit")
                if closed_order:
                    print(f"\n[Close] ✅ Closed {sym} @ ${curr_p:,.2f} | PnL: ${closed_order.realized_pnl:+,.2f} | Equity: ${risk_mgr.state.equity:,.2f}\n")
                else:
                    print(f"\n[Close] ❌ No active position for {sym}\n")
            elif c == "/cancel":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                cancelled = engine.cancel_setup(sym, reason="CLI manual cancel")
                if cancelled:
                    print(f"\n[Cancel] 🚫 Cancelled pending setup for {sym} ({cancelled.direction} @ ${cancelled.entry_level:,.2f})\n")
                else:
                    print(f"\n[Cancel] ℹ️ No pending setup found for {sym}\n")
            elif c == "/autotrade":
                if len(parts) > 1 and parts[1].lower() in ("on", "start", "enable"):
                    autotrade_active = True
                    print("\n[Autotrade] 🟢 Automated trade execution ENABLED\n")
                elif len(parts) > 1 and parts[1].lower() in ("off", "stop", "disable"):
                    autotrade_active = False
                    print("\n[Autotrade] ⚪ Automated trade execution DISABLED\n")
                else:
                    print(f"\n[Autotrade] Currently {'ENABLED 🟢' if autotrade_active else 'DISABLED ⚪'}\n")
            elif c == "/signal":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                res = evaluate_symbol(sym, source=config.DEFAULT_DATA_SOURCE, leverage=risk_mgr.get_leverage())
                print(f"\n[Signal for {sym}] Price: ${res['price']:,.2f} | Status: {res['status']}")
                for v in res['votes']:
                    print(f"  • {v['agent']:<12}: {v['direction']} ({v['reason']})")
                if res['signal']:
                    s = res['signal']
                    print(f"\n  >>> TRADE: {s['direction'].name} @ {s['entry']:.2f} | SL {s['stop']:.2f} | TP1 {s['tp1']:.2f} | Lots: {s['lots']} | Leverage: {risk_mgr.get_leverage():.0f}x\n")
                else:
                    print("\n  >>> Recommendation: Flat (waiting for confluence)\n")
            elif c == "/status":
                summ = engine.get_portfolio_summary()
                print(f"\n[Status] Equity: ${summ['equity']:,.2f} | Leverage: {summ['leverage']:.0f}x | Used Margin: ${summ['used_margin']:,.2f} | Free Margin: ${summ['free_margin']:,.2f}")
                print(f"         Mode: {risk_mgr.state.mode} (RF ×{risk_mgr.state.rf:.2f}) | Active: {summ['active_positions_count']} | Pending: {summ['pending_setups_count']}\n")
            elif c == "/risk":
                print(f"\n[Risk] {risk_mgr.summary()}\n")
            elif c == "/news":
                n = NewsAgent(enabled=True)
                evs = n.calendar()
                print(f"\n[News] {len(evs)} high-impact events loaded.")
                for dt, t in evs[:5]:
                    print(f"  • {dt.strftime('%b %d %H:%M UTC')}: {t}")
                print()
            elif c == "/backtest":
                bars = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1000
                symbol = parts[2].upper() if len(parts) > 2 else config.DEFAULT_SYMBOL.upper()
                timeframe = parts[3].lower() if len(parts) > 3 else "1h"
                if not 100 <= bars <= 10000 or timeframe not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}:
                    print("Usage: /backtest [100-10000 bars] [SYMBOL] [1m|5m|15m|30m|1h|4h|1d]")
                    continue
                from data_feed import DataFeed
                historical = DataFeed(symbol=symbol, source="ccxt", exchange_id=config.DEFAULT_EXCHANGE,
                                      timeframe=timeframe, api_key="", api_secret="").fetch(limit=bars, timeframe=timeframe)
                if historical is None or historical.empty:
                    print("Backtest unavailable: no real historical candles found; synthetic data is not used.")
                else:
                    stats = run_backtest(historical.tail(bars),
                                         config=BacktestConfig(initial_equity=config.INITIAL_EQUITY),
                                         symbol=symbol, timeframe=timeframe)
                    print(f"Backtest {symbol} {timeframe}: {stats['trades']} trades | "
                          f"net PnL {stats['net_pnl']:+.2f} | return {stats['return_pct']:+.2f}% | "
                          f"max DD {stats['max_drawdown_pct']:.2f}% | final equity {stats['final_equity']:.2f}")
            else:
                # Regular chat
                p_info = get_price_info(config.DEFAULT_SYMBOL)
                ctx = f"[Market: {p_info['symbol']} Mark: {p_info['mark']:.2f} | Leverage: {risk_mgr.get_leverage():.0f}x]"
                reply = gemini.ask(chat_id=chat_id, user_text=cmd, market_context=ctx)
                print(f"\n[Assistant]:\n{reply}\n")

        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break


# ======================================================================
# MAIN LAUNCHER
# ======================================================================
def main():
    token = config.TELEGRAM_TOKEN

    # If --cli flag is specified, run interactive CLI terminal mode
    if "--cli" in sys.argv:
        run_cli_interactive()
        return

    # If no Telegram token is set, inform the user clearly and offer CLI
    if not token or token == "your_telegram_bot_token_here":
        print(f"\n{'='*64}")
        print("  NOTICE: TELEGRAM_TOKEN IS NOT CONFIGURED")
        print(f"{'='*64}")
        print("To run the bot on Telegram:")
        print("  1. Get a bot token from @BotFather on Telegram.")
        print("  2. Add it to .env: TELEGRAM_TOKEN=123456:ABC-DEF...")
        print("  3. Run: python main.py")
        print("\nTo test all bot features right now in the terminal:")
        print("  Run: python main.py --cli")
        print("  Or:  python multi_agent_trading_bot.py backtest --bars 1000")
        print(f"{'='*64}\n")

        # Start CLI mode automatically so the command doesn't just crash!
        if sys.stdin.isatty():
            run_cli_interactive()
        else:
            print("Non-interactive terminal detected. Exiting.")
        return

    print(f"\nStarting Telegram Trading Bot on token: {token[:8]}***...")
    
    async def on_post_init(application):
        logger.info("Initializing live monitoring background worker...")
        asyncio.create_task(live_monitoring_worker(application))

    app = ApplicationBuilder().token(token).post_init(on_post_init).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("risk", risk_command))
    app.add_handler(CommandHandler("leverage", leverage_command))
    app.add_handler(CommandHandler("close", close_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("autotrade", autotrade_command))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("reset", reset_command))

    # General chat message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))

    print("Trading Bot is running. Press Ctrl+C to terminate.")
    app.run_polling()


if __name__ == "__main__":
    main()
