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
  /status           - Current bot status, active mode, and equity
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
import os
import sys
import threading
import time as time_module
from typing import Dict, Optional

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("TradingBot")

gemini = GeminiClient()
risk_mgr = RiskManager(equity=config.INITIAL_EQUITY, symbol=config.DEFAULT_SYMBOL)
arbiter = Arbiter()

# Bot runtime state
autotrade_active: bool = False
active_positions: Dict[str, Position] = {}
background_task: Optional[asyncio.Task] = None


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

    # Fallback to feed
    feed = MarketDataFeed(symbol=sym, source="demo")
    q = feed.get_latest_price()
    return {
        "ok": True,
        "symbol": sym,
        "mark": q.get("mark", 2650.0),
        "close": q.get("close", 2650.0),
        "open": q.get("open", 2650.0),
        "high": q.get("high", 2651.0),
        "low": q.get("low", 2649.0),
        "change_pct": 0.0,
        "recommendation": "NEUTRAL",
        "source": "Synthetic",
    }


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
        "• **Gemini AI:** Trade planning, risk analysis, and mobile-friendly conversational assistance.\n"
        "• **Institutional Risk Manager:** Progressive Auto-BE (+0.5R/+1.0R), dynamic lot sizing, and drawdown protection.\n\n"
        "⚡ **Quick Commands:**\n"
        "• `/price [SYMBOL]` — Live ticker (e.g., `/price XAUTUSD`, `/price BTCUSD`)\n"
        "• `/signal [SYMBOL]` — Run 5-agent consensus analysis\n"
        "• `/status` — View bot state, mode, and equity\n"
        "• `/risk` — Lot-sizing and risk parameters\n"
        "• `/news` — High-impact economic calendar & crypto sentiment\n"
        "• `/autotrade on` — Enable automated signal alerts\n"
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
        "**Risk & Portfolio:**\n"
        "• `/status` — Overall status, equity curve, active trading mode (normal / recovery / aggressive), and Risk Factor (RF).\n"
        "• `/risk` — Current drawdown, daily loss limit, and lot-size calculation for Gold, Forex, and Crypto.\n"
        "• `/positions` — View active open positions and trailing stops.\n\n"
        "**Automation & Backtesting:**\n"
        "• `/autotrade on` — Activate live background monitoring & signal alerts.\n"
        "• `/autotrade off` — Deactivate automated alerts.\n"
        "• `/backtest [bars]` — Simulate trading on historical/demo candles.\n\n"
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

    res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
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
            f"• **Recommended Lots:** `{sig['lots']}`\n"
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

    msg = (
        f"📈 **Trading Bot Status Dashboard**\n\n"
        f"• **Autotrade Alerts:** {auto_status}\n"
        f"• **Active Symbol:** `{config.DEFAULT_SYMBOL}`\n"
        f"• **Data Feed:** `{config.DEFAULT_DATA_SOURCE}`\n"
        f"• **Account Equity:** `${s.equity:,.2f}`\n"
        f"• **Peak Equity:** `${s.peak_equity:,.2f}`\n"
        f"• **Risk Mode:** {mode_icon} `{s.mode.upper()}` (RF ×`{s.rf:.2f}`)\n"
        f"• **Today's PnL:** `${s.daily_pnl:+,.2f}`\n"
        f"• **Consecutive Losses:** `{s.consecutive_losses}`\n"
        f"• **Total Trades (Win Rate):** `{s.total_trades}` ({s.recent_winrate * 100:.1f}%)\n"
        f"• **Open Positions:** `{len(active_positions)}`\n\n"
        f"_{risk_mgr.summary()}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def risk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View detailed risk manager metrics and lot size calculations."""
    s = risk_mgr.state
    cfg = risk_mgr.config
    risk_pct, reason = risk_mgr.current_risk_pct()
    dd = (s.peak_equity - s.equity) / s.peak_equity * 100 if s.peak_equity else 0.0

    # Calculate lot size example for Gold (XAUUSD) with 10 pt stop
    plan_gold = risk_mgr.autotrade_plan(entry=2650.0, stop=2640.0, symbol="XAUUSD")
    plan_btc = risk_mgr.autotrade_plan(entry=80000.0, stop=79000.0, symbol="BTCUSDT")

    msg = (
        f"🛡️ **Institutional Risk Manager Report**\n\n"
        f"• **Current Risk Per Trade:** `{risk_pct:.2f}%` ({reason})\n"
        f"• **Current Drawdown:** `{dd:.1f}%`\n"
        f"• **Risk Factor (RF):** `{s.rf:.2f}` (Min: {cfg.rf_min}, Max: {cfg.rf_max})\n"
        f"• **Daily Loss Limit:** `{cfg.max_daily_loss_pct}%`\n"
        f"• **Weekly Loss Limit:** `{cfg.max_weekly_loss_pct}%`\n"
        f"• **Auto Break-Even Trigger:** Lock +0.1R @ +{cfg.be_lock_r}R | Full BE @ +{cfg.be_at_r}R\n\n"
        f"📏 **Lot Sizing Calculations for Current Equity (${s.equity:,.2f}):**\n"
        f"• **XAUUSD (10 pt SL):** `{plan_gold.get('lots', 0.01)} lots` (Risk: `${plan_gold.get('risk_amount', 0.0):.2f}`)\n"
        f"• **BTCUSDT ($1000 SL):** `{plan_btc.get('lots', 0.001)} lots` (Risk: `${plan_btc.get('risk_amount', 0.0):.2f}`)\n\n"
        f"_{risk_mgr.summary()}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


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
    """View active open positions."""
    if not active_positions:
        await update.message.reply_text("📂 No open positions currently tracked. Bot is flat.")
        return

    lines = []
    for sym, p in active_positions.items():
        dir_icon = "🟢 LONG" if p.direction.name == "LONG" else "🔴 SHORT"
        lines.append(
            f"• **{sym} ({dir_icon})**\n"
            f"  Entry: `${p.entry:,.2f}` | Stop: `${p.stop:,.2f}`\n"
            f"  TP1: `${p.tp1:,.2f}` | TP2: `${p.tp2:,.2f}`\n"
            f"  Lots: `{p.lots}` | Partials Taken: `{p.partials}`"
        )
    msg = "📊 **Active Positions:**\n\n" + "\n\n".join(lines)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle automated live monitoring and alerts."""
    global autotrade_active, background_task
    action = ctx.args[0].lower() if ctx.args else ""

    if action in ("on", "start", "enable"):
        autotrade_active = True
        msg = "🟢 **Autotrade Monitoring ENABLED**\nThe bot is now monitoring market candles in the background and will dispatch real-time trade signals to this chat!"
    elif action in ("off", "stop", "disable"):
        autotrade_active = False
        msg = "⚪ **Autotrade Monitoring DISABLED**\nBackground scanning paused. You can still use `/signal` for on-demand analysis."
    else:
        status_text = "ENABLED 🟢" if autotrade_active else "DISABLED ⚪"
        msg = f"Autotrade monitoring is currently **{status_text}**.\nUse `/autotrade on` or `/autotrade off` to toggle."

    await update.message.reply_text(msg, parse_mode="Markdown")


async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run backtest on demand."""
    bars = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 2000
    await update.message.reply_text(f"⏳ Running backtest simulation over **{bars}** candles...", parse_mode="Markdown")

    class DummyArgs:
        def __init__(self, bars):
            self.bars = bars
            self.symbol = config.DEFAULT_SYMBOL
            self.equity = config.INITIAL_EQUITY

    from multi_agent_trading_bot import run_backtest
    stats = run_backtest(DummyArgs(bars))

    ret_arrow = "🟢" if stats["return_pct"] >= 0 else "🔴"
    msg = (
        f"📊 **Multi-Agent Backtest Results**\n\n"
        f"• **Candles Simulated:** `{bars}` bars\n"
        f"• **Total Trades:** `{stats['trades']}`\n"
        f"• **Win Rate:** `{stats['winrate']}%` ({stats['wins']} wins)\n"
        f"• **Net PnL:** `${stats['net_pnl']:+,.2f}`\n"
        f"• **Return:** {ret_arrow} `{stats['return_pct']:+.2f}%`\n"
        f"• **Max Drawdown:** `{stats['max_drawdown']}%`\n"
        f"• **Final Equity:** `${stats['final_equity']:,.2f}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


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
    market_context = (
        f"[LIVE MARKET DATA: {p_info['symbol']} Mark: {p_info['mark']:.2f} | "
        f"Close: {p_info['close']:.2f} | High: {p_info['high']:.2f} | Low: {p_info['low']:.2f} | "
        f"24h: {p_info['change_pct']:+.2f}%]"
    )

    # Let user know assistant is thinking for lengthy queries
    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")

    reply = gemini.ask(chat_id=chat_id, user_text=user_text, market_context=market_context)
    await update.message.reply_text(reply, parse_mode="Markdown")


# ======================================================================
# BACKGROUND MONITORING WORKER
# ======================================================================
async def live_monitoring_worker(app):
    """Background task that polls candles and sends alerts if autotrade is active."""
    feed = MarketDataFeed(symbol=config.DEFAULT_SYMBOL, source=config.DEFAULT_DATA_SOURCE)
    symbol = config.DEFAULT_SYMBOL

    while True:
        try:
            if autotrade_active:
                data = feed.multi_tf(limit_1m=500)
                if data and "1m" in data and not data["1m"].empty:
                    df1 = data["1m"]
                    daily = data.get("daily", pd.DataFrame())
                    candle = df1.iloc[-1]

                    # Manage open positions
                    if symbol in active_positions:
                        pos = active_positions[symbol]
                        ep, closed, reason = arbiter.manage(pos, candle, risk_mgr)
                        if ep > 0:
                            pnl = ((ep - pos.entry) if pos.direction.name == "LONG" else (pos.entry - ep)) * pos.remaining
                            risk_mgr.update_after_trade(pnl)

                            alert = (
                                f"🔔 **POSITION UPDATE ({symbol})**\n"
                                f"Reason: {reason}\n"
                                f"Exit Price: `${ep:,.2f}` | PnL: `${pnl:+,.2f}`\n\n"
                                f"_{risk_mgr.summary()}_"
                            )
                            if config.TELEGRAM_CHAT_ID:
                                try:
                                    await app.bot.send_message(
                                        chat_id=config.TELEGRAM_CHAT_ID, text=alert, parse_mode="Markdown"
                                    )
                                except Exception:
                                    pass

                            if closed:
                                active_positions.pop(symbol, None)

                    # Scan for new entries if flat
                    if symbol not in active_positions:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr, symbol=symbol
                        )
                        if sig:
                            pos = Position(
                                direction=sig["direction"],
                                entry=sig["entry"],
                                stop=sig["stop"],
                                size=sig["size"],
                                lots=sig["lots"],
                                tp1=sig["tp1"],
                                tp2=sig["tp2"],
                                tp3=sig.get("tp3"),
                                remaining=sig["size"],
                                risk=abs(sig["entry"] - sig["stop"]),
                                highest=sig["entry"],
                                lowest=sig["entry"],
                                symbol=symbol,
                            )
                            active_positions[symbol] = pos

                            alert = (
                                f"⚡ **AUTO-TRADE SIGNAL TRIGGERED**\n"
                                f"Symbol: `{symbol}` | **{sig['direction'].name}**\n"
                                f"• Entry: `${sig['entry']:,.2f}`\n"
                                f"• Stop: `${sig['stop']:,.2f}`\n"
                                f"• Target 1: `${sig['tp1']:,.2f}` | Target 2: `${sig['tp2']:,.2f}`\n"
                                f"• Recommended Lots: `{sig['lots']}`\n"
                                f"• Score: `{sig['score']:.2f}` | R:R: `1:{sig['rr']:.1f}`\n\n"
                                f"**Confluence:** {sig['reason']}"
                            )
                            if config.TELEGRAM_CHAT_ID:
                                try:
                                    await app.bot.send_message(
                                        chat_id=config.TELEGRAM_CHAT_ID, text=alert, parse_mode="Markdown"
                                    )
                                except Exception:
                                    pass

        except Exception as e:
            logger.error(f"Monitoring worker error: {e}")

        await asyncio.sleep(config.POLL_INTERVAL)


# ======================================================================
# SIMULATED CLI MODE (for testing without a Telegram token)
# ======================================================================
def run_cli_interactive():
    """Interactive command-line interface for running the bot directly in terminal."""
    print(f"\n{'='*64}")
    print("  TRADING BOT - INTERACTIVE TERMINAL MODE")
    print(f"  Symbol: {config.DEFAULT_SYMBOL} | Equity: ${risk_mgr.state.equity:,.2f}")
    print(f"  Type commands like /price, /signal, /risk, /status, /help, or chat.")
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
                print("\nAvailable commands: /price [SYM], /signal [SYM], /status, /risk, /news, /backtest [N]\n")
            elif c == "/price":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                info = get_price_info(sym)
                print("\n" + format_price_string(info) + "\n")
            elif c == "/signal":
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                res = evaluate_symbol(sym, source=config.DEFAULT_DATA_SOURCE)
                print(f"\n[Signal for {sym}] Price: ${res['price']:,.2f} | Status: {res['status']}")
                for v in res['votes']:
                    print(f"  • {v['agent']:<12}: {v['direction']} ({v['reason']})")
                if res['signal']:
                    s = res['signal']
                    print(f"\n  >>> TRADE: {s['direction'].name} @ {s['entry']:.2f} | SL {s['stop']:.2f} | TP1 {s['tp1']:.2f} | Lots: {s['lots']}\n")
                else:
                    print("\n  >>> Recommendation: Flat (waiting for confluence)\n")
            elif c == "/status":
                print(f"\n[Status] Equity: ${risk_mgr.state.equity:,.2f} | Mode: {risk_mgr.state.mode} (RF ×{risk_mgr.state.rf:.2f}) | {risk_mgr.summary()}\n")
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
                from multi_agent_trading_bot import run_backtest
                class DummyArgs:
                    def __init__(self, b):
                        self.bars = b
                        self.symbol = config.DEFAULT_SYMBOL
                        self.equity = config.INITIAL_EQUITY
                run_backtest(DummyArgs(bars))
            else:
                # Regular chat
                p_info = get_price_info(config.DEFAULT_SYMBOL)
                ctx = f"[Market: {p_info['symbol']} Mark: {p_info['mark']:.2f}]"
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
    app = ApplicationBuilder().token(token).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("risk", risk_command))
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
