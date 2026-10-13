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
  /health           - Monitoring freshness and error status
  /risk             - Risk manager report, drawdown, and lot sizing
  /news             - Macroeconomic calendar events and crypto sentiment
  /positions        - Open positions and trailing stop status
  /autotrade [on|off] - Toggle setup monitoring and alerts
  /backtest [bars] [SYMBOL] - Auto full suite (WF + MC + sweep)
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
    evaluate_symbol,
    send_telegram,
)
from risk_manager import RiskManager
from backtest_engine import BacktestConfig, run_backtest
from ops_dashboard import StatusStore, health_from_snapshot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("TradingBot")

gemini = GeminiClient()
risk_mgr = RiskManager(equity=config.INITIAL_EQUITY, symbol=config.DEFAULT_SYMBOL)
risk_mgr.set_leverage(config.DEFAULT_LEVERAGE)
arbiter = Arbiter(allow_asian=True)
try:
    from execution import create_broker
    _broker = create_broker(
        broker_mode=getattr(config, "BROKER_MODE", "paper"),
        exchange_id=getattr(config, "DEFAULT_EXCHANGE", "binance"),
        api_key=getattr(config, "EXCHANGE_API_KEY", ""),
        api_secret=getattr(config, "EXCHANGE_API_SECRET", ""),
        initial_balance=getattr(config, "INITIAL_EQUITY", 10000.0),
        mt5_mode=getattr(config, "MT5_MODE", "paper"),
        mt5_login=getattr(config, "MT5_LOGIN", 0),
        mt5_password=getattr(config, "MT5_PASSWORD", ""),
        mt5_server=getattr(config, "MT5_SERVER", "MetaQuotes-Demo"),
        mt5_path=getattr(config, "MT5_PATH", ""),
        mt5_magic=getattr(config, "MT5_MAGIC", 112233),
        mt5_gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
    )
except Exception as _broker_err:
    print("[boot] broker:", _broker_err)
    _broker = None
engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)

# Bot runtime state. Monitoring is off by default until explicitly enabled.
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


def get_price_info(symbol: str = "XAUTUSD") -> Dict:
    try:
        from realtime_prices import fetch_spot_price
        rt = fetch_spot_price(symbol)
        if rt.get("ok") and float(rt.get("mark") or 0) > 0:
            return rt
    except Exception as e:
        logger.warning("realtime_prices failed for %s: %s", symbol, e)

    from tradingview_feed import fetch_tradingview_quote
    tv_info = fetch_tradingview_quote(symbol)
    if tv_info.get("ok"):
        return tv_info

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

    # Fallback 3: CCXT / DataFeed (e.g. Binance / Bybit / Yahoo Finance)
    try:
        from data_feed import DataFeed
        feed = DataFeed(symbol=symbol, source=getattr(config, "DEFAULT_DATA_SOURCE", "ccxt"))
        latest = feed.get_latest_price()
        if latest.get("ok"):
            return latest
    except Exception as e:
        logger.warning(f"DataFeed fallback notice for {symbol}: {e}")

    logger.error("Live quote unavailable for %s from TradingView, Delta Exchange, and DataFeed", sym)
    return {"ok": False, "symbol": sym, "error": "Live market data unavailable; no synthetic price was substituted.", "source": "unavailable"}


def format_price_string(info: Dict) -> str:
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


async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🤖 **TradingBot — Multi-Agent Paper & MT5 Assistant**\n\n"
        "Welcome! Institutional crypto & gold algorithmic trading assistant powered by 7 autonomous strategy agents, dynamic entry planning, and safe paper/MT5 execution.\n\n"
        "⚡ **Quick Start:**\n"
        "• `/signal btc` — Multi-agent consensus & best entry zone\n"
        "• `/autotrade on` — Automated trading (up to 5 active trades)\n"
        "• `/positions` — Monitor open trades, PnL & dynamic trailing stops\n"
        "• `/status` — Portfolio equity & system health\n"
        "• `/commands` or `/help` — Full categorized command catalog\n\n"
        "🛡️ _Default paper equity: $1,000 | Max concurrent active trades: 5_"
    )
    try:
        await update.message.reply_text(welcome, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(welcome.replace("*", "").replace("_", "").replace("`", ""))


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = (ctx.args[0].lower() if ctx.args else "").strip()
    if cat in ("market", "price", "quotes", "levels"):
        text = (
            "📊 **Market Data & Live Quotes:**\n"
            "• `/price [SYM]` — Live mark price, 24h change & stats\n"
            "• `/levels [SYM]` — Key technical levels (PDH/PDL, pivots, swings, 2Y S/R)\n"
            "• `/news [SYM]` — Macro calendar & economic sentiment"
        )
    elif cat in ("signal", "signals", "setup", "confluence"):
        text = (
            "🎯 **Signals & Strategy Confluence:**\n"
            "• `/signal [SYM]` — 7-Agent consensus & optimal entry zone\n"
            "• `/asignal [SYM] [profile]` — Advanced SMC + CVD orderflow setup\n"
            "• `/quality [SYM]` — Comprehensive signal quality score & breakdown"
        )
    elif cat in ("execution", "orders", "trades", "pos", "positions"):
        text = (
            "⚡ **Execution & Positions (Active Limit: 5):**\n"
            "• `/positions` — Open active trades, pending setups & real-time PnL\n"
            "• `/autotrade on|off` — Multi-symbol autotrading (up to 5 concurrent active trades)\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL"
        )
    elif cat in ("mt5", "broker", "brokers", "oanda", "ccxt", "alpaca"):
        text = (
            "🏦 **MetaTrader 5 & Broker Accounts (Native Linux & Cloud):**\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker [paper|ccxt|oanda|alpaca|mt5|mt5_live]` — Switch execution broker\n"
            "• `/oanda [status|live|positions]` — Native OANDA v20 Forex & Metals REST (Linux-Ready)\n"
            "• `/ccxt [status|live|positions]` — Native CCXT Crypto Exchanges (100+ exchanges)\n"
            "• `/alpaca [status|live|positions]` — Native Alpaca US Stocks & Crypto REST\n"
            "• `/mt5 [status|paper|live|positions]` — MetaTrader 5 Simulation or Windows Bridge"
        )
    elif cat in ("risk", "safety", "limits"):
        text = (
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/leverage [1-20]` — Set maximum account leverage ceiling\n"
            "• `/kill [on|off]` — Emergency execution stop circuit breaker"
        )
    elif cat in ("backtest", "research", "abacktest"):
        text = (
            "📈 **Backtesting & Research:**\n"
            "• `/backtest [bars] [SYM]` — Fast backtest engine\n"
            "• `/abacktest [mode] [bars] [SYM]` — Advanced suite (WF / MC / sweep)\n"
            "• `/performance` — Detailed trade metrics, win rate & Sharpe ratio\n"
            "• `/profile [name]` — Switch strategy profile (scalp/swing/trend/balanced)"
        )
    elif cat in ("ai", "brain", "learner", "agent", "agents"):
        text = (
            "🧠 **Autonomous AI & Standings:**\n"
            "• `/board` — Strategy board & agent standings\n"
            "• `/journal` — Trade journal analytics & history\n"
            "• `/agents` — Agent performance rankings & stats\n"
            "• `/tiers` — Risk tier allocations\n"
            "• `/digest` — Daily market performance digest\n"
            "• `/brain` — Autonomous learner knowledge & win rates\n"
            "• `/relearn [SYM]` — Force 5Y history retraining bootstrap\n"
            "• `/reset` — Clear conversation context\n"
            "• `/ask [question]` — Conversational assistant with Gemini AI"
        )
    else:
        text = (
            "📖 **TradingBot Command Center**\n"
            "_Institutional Multi-Agent Paper & MT5 Trading System_\n\n"
            "📊 **Market Data & Live Quotes:**\n"
            "• `/price [SYM]` — Live mark price, 24h change & stats\n"
            "• `/levels [SYM]` — Key technical levels (PDH/PDL, pivots, swings, 2Y S/R)\n"
            "• `/news [SYM]` — Macro calendar & economic sentiment\n\n"
            "🎯 **Signals & Strategy Confluence:**\n"
            "• `/signal [SYM]` — 7-Agent consensus (Trend, SMC, Indicators, AI, News, Gautam, VWAP)\n"
            "• `/asignal [SYM] [profile]` — Advanced SMC + CVD orderflow setup\n"
            "• `/quality [SYM]` — Comprehensive signal quality score & breakdown\n\n"
            "⚡ **Execution & Positions (Active Limit: 5):**\n"
            "• `/positions` — Open active trades, pending setups & real-time PnL\n"
            "• `/autotrade on|off` — Multi-symbol autotrading (up to 5 concurrent active trades)\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL\n\n"
            "🏦 **Native Live Brokers & Broker Accounts:**\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker [paper|ccxt|oanda|alpaca|mt5|mt5_live]` — Switch execution broker\n"
            "• `/oanda [status|live|positions]` — Native OANDA v20 Forex & Metals REST\n"
            "• `/ccxt [status|live|positions]` — Native CCXT Crypto Exchanges (100+ exchanges)\n"
            "• `/alpaca [status|live|positions]` — Native Alpaca US Stocks & Crypto REST\n"
            "• `/mt5 [status|paper|live|positions]` — MetaTrader 5 Simulation or Windows Bridge\n\n"
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/leverage [1-20]` — Set maximum account leverage ceiling\n"
            "• `/kill [on|off]` — Emergency execution stop circuit breaker\n\n"
            "📈 **Backtesting & Research:**\n"
            "• `/backtest [bars] [SYM]` — Fast backtest engine\n"
            "• `/abacktest [mode] [bars] [SYM]` — Advanced suite (WF / MC / sweep)\n"
            "• `/performance` — Detailed trade metrics, win rate & Sharpe ratio\n"
            "• `/profile [name]` — Switch strategy profile (scalp/swing/trend/balanced)\n\n"
            "🧠 **Autonomous AI & Standings:**\n"
            "• `/board` — Strategy board & agent standings\n"
            "• `/journal` — Trade journal analytics & history\n"
            "• `/agents` — Agent performance rankings & stats\n"
            "• `/tiers` — Risk tier allocations\n"
            "• `/digest` — Daily market performance digest\n"
            "• `/brain` — Autonomous learner knowledge & win rates\n"
            "• `/relearn [SYM]` — Force 5Y history retraining bootstrap\n"
            "• `/reset` — Clear conversation context\n"
            "• `/ask [question]` — Conversational assistant with Gemini AI\n\n"
            "_Filter: `/commands market`, `/commands signals`, `/commands execution`, `/commands risk`, `/commands mt5`, `/commands backtest`, `/commands ai`_"
        )
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(text.replace("*", "").replace("_", "").replace("`", ""))


async def levels_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Real-time key levels for BTC or Gold (PDH/PDL, pivots, swings)."""
    sym = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL).upper()
    if sym in ("BTC", "BITCOIN"):
        sym = "BTCUSDT"
    if sym in ("GOLD", "XAU", "XAUT"):
        sym = "XAUUSD"
    await update.message.reply_text(f"⏳ Fetching **{sym}** levels…", parse_mode="Markdown")
    try:
        from realtime_prices import compute_levels, format_levels_message
        data = compute_levels(sym)
        await update.message.reply_text(format_levels_message(data), parse_mode="Markdown")
    except Exception as e:
        logger.exception("levels failed")
        await update.message.reply_text(f"Levels error: `{e}`", parse_mode="Markdown")


async def price_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    info = get_price_info(sym)
    await update.message.reply_text(format_price_string(info), parse_mode="Markdown")


async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """High-reliability /signal — leverage + paper micro size safe."""
    raw = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL)
    try:
        from signal_pipeline import run_signal, format_signal_message, try_paper_execute, _norm_symbol
        sym = _norm_symbol(raw)
        await update.message.reply_text(
            f"⏳ Evaluating **{sym}** (lev `{risk_mgr.get_leverage():.0f}x`)...",
            parse_mode="Markdown",
        )
        res = run_signal(
            sym,
            equity=float(getattr(risk_mgr.state, "equity", None) or config.INITIAL_EQUITY),
            leverage=float(risk_mgr.get_leverage()),
            source=config.DEFAULT_DATA_SOURCE,
        )
        msg = format_signal_message(res)
        try:
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(msg.replace("*", "").replace("`", ""))
        sig = res.get("signal")
        if sig and autotrade_active:
            ok, note = try_paper_execute(engine, sig, candle_price=res.get("price") or sig.get("entry"))
            tag = "✅" if ok else "⚠️"
            try:
                await update.message.reply_text(f"{tag} Autotrade: {note}", parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(f"{tag} Autotrade: {note}")
    except Exception as e:
        logger.exception("signal_command failed")
        await update.message.reply_text(f"Signal error: `{type(e).__name__}: {e}`", parse_mode="Markdown")



async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = risk_mgr.state
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    auto_status = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
    summary = engine.get_portfolio_summary()
    max_trades = getattr(engine, "max_open_trades", 5)
    slots = engine.slots_available()
    symbols_str = ", ".join(getattr(config, "AUTOTRADE_SYMBOLS", ["BTCUSDT", "XAUUSD"]))
    bname = type(engine.broker).__name__ if engine.broker else "None"
    bmode = getattr(config, "BROKER_MODE", "paper").upper()
    b_extra = ""
    if hasattr(engine.broker, "get_account_info"):
        try:
            ai = engine.broker.get_account_info()
            b_extra = f"\n• **MT5 Server:** `{ai.server}` | **Free Margin:** `${ai.free_margin:,.2f}`"
        except Exception:
            pass
    msg = (
        f"📊 **System & Portfolio Status**\n"
        f"• **Autotrade:** {auto_status} (Cap: `{summary['active_positions_count']}/{max_trades}` active | `{slots}` slots free)\n"
        f"• **Watchlist:** `{symbols_str}`\n"
        f"• **Equity:** `${summary['equity']:,.2f}` | **Leverage:** `{summary['leverage']:.0f}x`\n"
        f"• **Active Positions:** `{summary['active_positions_count']}` | **Pending Setups:** `{summary['pending_setups_count']}`\n"
        f"• **Risk Mode:** {mode_icon} `{s.mode.upper()}` | **Today PnL:** `${s.daily_pnl:+,.2f}`\n"
        f"• **Broker:** `{bname}` (Mode: `{bmode}`){b_extra}\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")



async def health_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"❤️ **Health Telemetry**\n"
        f"• **Last Poll:** `{monitoring_health.get('last_poll_at')}`\n"
        f"• **Last Success:** `{monitoring_health.get('last_success_at')}`\n"
        f"• **Last Error:** `{monitoring_health.get('last_error') or 'None'}`\n"
        f"• **Consecutive Errors:** `{monitoring_health.get('consecutive_errors', 0)}`\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def risk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = risk_mgr.state
    risk_pct, reason = risk_mgr.current_risk_pct()
    max_trades = getattr(engine, "max_open_trades", 5)
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    msg = (
        f"🛡️ **Institutional Risk Controls**\n"
        f"• **Risk/Trade:** `{risk_pct:.2f}%` ({reason})\n"
        f"• **Max Concurrent Trades:** `{max_trades}` active slots\n"
        f"• **Account Equity:** `${s.equity:,.2f}` | **Mode:** {mode_icon} `{s.mode.upper()}`\n"
        f"• **Max Daily Loss:** `{risk_mgr.config.max_daily_loss_pct:.1f}%` | **Weekly:** `{risk_mgr.config.max_weekly_loss_pct:.1f}%`\n"
        f"• **Margin Utilization Cap:** `{risk_mgr.config.max_margin_utilization_pct:.0f}%`\n"
        f"• {_safe_summary()}\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


def _safe_summary() -> str:
    try:
        return risk_mgr.summary()
    except Exception:
        return ""


async def leverage_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        try:
            lev = float(ctx.args[0])
            risk_mgr.set_leverage(lev)
            await update.message.reply_text(f"Leverage set to `{risk_mgr.get_leverage():.0f}x`", parse_mode="Markdown")
            return
        except Exception as e:
            await update.message.reply_text(f"Invalid leverage: {e}")
            return
    await update.message.reply_text(f"Current leverage: `{risk_mgr.get_leverage():.0f}x`", parse_mode="Markdown")


async def positions_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if hasattr(engine, "format_positions_detail"):
            msg = engine.format_positions_detail()
        else:
            summary = engine.get_portfolio_summary()
            msg = f"📋 Positions open={summary.get('active_positions_count', 0)}"
        try:
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(msg.replace("*", "").replace("`", ""))
    except Exception as e:
        await update.message.reply_text(f"Positions error: {e}")


async def close_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = (ctx.args[0] if ctx.args else "").strip().upper()
    if not arg or arg in ("ALL", "*"):
        if not engine.active_positions:
            await update.message.reply_text("ℹ️ No open positions to close.")
            return
        closed = []
        if hasattr(engine, "close_all_positions"):
            closed = engine.close_all_positions(reason="Manual close all")
        else:
            for sym in list(engine.active_positions.keys()):
                p = engine.close_position(sym, reason="Manual close all")
                if p:
                    closed.append(p)
        lines = [f"✅ **Closed {len(closed)} position(s):**"]
        for p in closed:
            pnl = float(getattr(p, 'realized_pnl', 0) or 0)
            pnl_icon = "📈" if pnl >= 0 else "📉"
            lines.append(f"• `{p.symbol}` PnL: {pnl_icon} `${pnl:+,.2f}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    sym = arg if arg not in ("BTC", "BTCUSD") else "BTCUSDT"
    closed = engine.close_position(sym, reason="Manual close")
    if closed:
        pnl = float(getattr(closed, 'realized_pnl', 0) or 0)
        pnl_icon = "📈" if pnl >= 0 else "📉"
        await update.message.reply_text(f"✅ Closed `{sym}` — Realized PnL: {pnl_icon} `${pnl:+,.2f}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ No active position found for `{sym}`. Use `/close all`", parse_mode="Markdown")


async def closed_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recently closed trades and realized PnL history."""
    try:
        from trade_journal import TradeJournal
        journal = TradeJournal()
        closes = journal.closed_summary(8)
    except Exception:
        closes = []

    lines = ["📜 **Recently Closed Trades History**"]
    if closes:
        total_pnl = 0.0
        for r in reversed(closes):
            won = r.get("won")
            icon = "✅" if won else "❌"
            pnl = float(r.get("pnl") or 0.0)
            total_pnl += pnl
            r_mult = r.get("r_multiple", 0.0)
            sym = r.get("symbol", "")
            d = r.get("direction", "")
            lines.append(f"• {icon} `{sym}` {d} — PnL: `${pnl:+,.2f}` (R: `{r_mult:+.2f}`)")
        lines.append(f"\n💰 **Total Realized PnL:** `${total_pnl:+,.2f}`")
    elif getattr(engine, "trade_history", None):
        history = list(reversed(engine.trade_history))[:8]
        total_pnl = 0.0
        for t in history:
            pnl = float(getattr(t, "realized_pnl", 0.0) or 0.0)
            total_pnl += pnl
            icon = "✅" if pnl >= 0 else "❌"
            sym = getattr(t, "symbol", "")
            d = getattr(t, "direction", "")
            lines.append(f"• {icon} `{sym}` {d} — PnL: `${pnl:+,.2f}`")
        lines.append(f"\n💰 **Total Realized PnL:** `${total_pnl:+,.2f}`")
    else:
        lines.append("ℹ️ No closed trades on record yet. Completed trades will appear here.")

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("\n".join(lines).replace("*", "").replace("`", ""))


async def ask_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Direct query to Gemini conversational trading assistant."""
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text("Usage: `/ask <your question>`", parse_mode="Markdown")
        return
    market_context = ""
    try:
        info = get_price_info(config.DEFAULT_SYMBOL)
        if info.get("ok"):
            market_context = f"{config.DEFAULT_SYMBOL} mark={info.get('mark')} change={info.get('change_pct')}%"
    except Exception:
        pass
    reply = gemini.ask(chat_id=update.effective_chat.id, user_text=query, market_context=market_context)
    await update.message.reply_text(reply)


async def unknown_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Friendly fallback when an unrecognized command is typed."""
    cmd = update.message.text.split()[0] if (update.message and update.message.text) else "/command"
    await update.message.reply_text(
        f"❓ Unknown command: `{cmd}`\n\n"
        "Use `/commands` or `/help` to view all available commands.",
        parse_mode="Markdown",
    )


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = (ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL)
    try:
        cancelled = engine.cancel_setup(sym, reason="Manual cancel")
        if cancelled:
            await update.message.reply_text(f"✅ Cancelled pending setup for `{sym}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"ℹ️ No pending setup for `{sym}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Cancel failed: `{e}`", parse_mode="Markdown")


async def news_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from news_agent import NewsAgent
        agent = NewsAgent(symbol=getattr(config, "DEFAULT_SYMBOL", "XAUUSD"))
        if hasattr(agent, "report"):
            await update.message.reply_text(agent.report(), parse_mode="Markdown")
            return
        v = agent.vote()
        await update.message.reply_text(f"📰 **News**\n{v.direction.name}: {v.reason}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"News error: {e}")


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global autotrade_active
    arg = (ctx.args[0].lower() if ctx.args else "")
    broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
    max_trades = getattr(engine, "max_open_trades", 5)
    symbols_str = ", ".join(getattr(config, "AUTOTRADE_SYMBOLS", ["BTCUSDT", "XAUUSD"]))
    if arg in ("on", "1", "true", "enable"):
        autotrade_active = True
        engine.reset_halt()
        await update.message.reply_text(
            f"🟢 **Autotrade ACTIVATED**\n"
            f"• **Execution Mode:** `{broker_mode}`\n"
            f"• **Active Trade Limit:** `{max_trades}` concurrent positions\n"
            f"• **Active Watchlist:** `{symbols_str}`",
            parse_mode="Markdown",
        )
    elif arg in ("off", "0", "false", "disable"):
        autotrade_active = False
        await update.message.reply_text("⚪ **Autotrade PAUSED** (monitoring stopped)", parse_mode="Markdown")
    else:
        st = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
        await update.message.reply_text(
            f"⚡ **Autotrade Status:** {st}\n"
            f"• **Mode:** `{broker_mode}`\n"
            f"• **Active Limit:** `{max_trades}` concurrent trades\n"
            f"• **Watchlist:** `{symbols_str}`\n"
            f"Toggle with: `/autotrade on` or `/autotrade off`",
            parse_mode="Markdown",
        )


async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Backtest suite or single mode: /backtest [single|auto] [bars] [SYMBOL]"""
    args = list(ctx.args or [])
    bars = 2000
    symbol = config.DEFAULT_SYMBOL.upper()
    mode = "auto"

    if args and args[0].lower() in ("single", "auto", "wf", "mc", "sweep", "multiagent"):
        mode = args.pop(0).lower()

    if args and args[0].isdigit():
        bars = int(args.pop(0))

    if args:
        symbol = args.pop(0).upper()

    if not 50 <= bars <= 10000:
        await update.message.reply_text("Choose between 50 and 10,000 candles.")
        return

    if mode == "single":
        await update.message.reply_text(
            f"⏳ **Single EMA Backtest** — `{symbol}` — {bars} bars…",
            parse_mode="Markdown",
        )
        def _run_single():
            from advanced_backtest import load_data, SimConfig, AdvancedSimulator, ema_cross_signal
            df = load_data(symbol, bars, getattr(config, "DEFAULT_DATA_SOURCE", "ccxt"), None)
            cfg = SimConfig(
                initial_equity=getattr(config, "INITIAL_EQUITY", 10000.0),
                risk_per_trade=max(0.001, getattr(config, "DEFAULT_RISK_PCT", 0.18) / 100.0),
            )
            sim = AdvancedSimulator(cfg)
            m, trades, _ = sim.run(df, ema_cross_signal, symbol)
            out = m.summary()
            if trades:
                out += f"\nLast trades (up to 5):\n"
                for t in trades[-5:]:
                    out += f"  {t.direction.upper()} R={t.r_multiple:+.2f} PnL={t.pnl:+.2f} ({t.reason})\n"
            return out

        try:
            text = await asyncio.to_thread(_run_single)
            await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Single backtest failed for %s: %s", symbol, exc)
            await update.message.reply_text(f"Single backtest failed: `{exc}`", parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"⏳ **Auto backtest suite** — `{symbol}` — {bars} bars\n"
        f"single + multiagent + walk-forward + Monte Carlo + sweep…",
        parse_mode="Markdown",
    )

    def _run():
        from auto_backtest import run_auto_backtest
        return run_auto_backtest(
            symbol=symbol,
            bars=bars,
            source=getattr(config, "DEFAULT_DATA_SOURCE", "ccxt"),
            equity=getattr(config, "INITIAL_EQUITY", 10000.0),
            risk=max(0.001, getattr(config, "DEFAULT_RISK_PCT", 0.18) / 100.0),
            mc_runs=300,
            wf_windows=4,
            do_sweep=True,
        )

    try:
        report = await asyncio.to_thread(_run)
        text = report.text(max_len=3800)
        await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Auto backtest failed for %s: %s", symbol, exc)
        await update.message.reply_text(
            f"Backtest failed: `{type(exc).__name__}: {exc}`\n"
            f"Tip: set a real data source or provide CSV; synthetic data is last resort.",
            parse_mode="Markdown",
        )



async def mt5_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("status", "info"):
        if hasattr(engine.broker, "get_account_info"):
            try:
                info = engine.broker.get_account_info()
                await update.message.reply_text(info.summary(), parse_mode="Markdown")
                return
            except Exception as e:
                logger.warning("MT5 get_account_info failed: %s", e)
        if hasattr(engine.broker, "get_account_summary"):
            s = engine.broker.get_account_summary()
            msg = (
                f"🏦 **Broker Account Summary**\n"
                f"• **Type:** `{type(engine.broker).__name__}`\n"
                f"• **Balance:** `${s.get('balance', 0):,.2f}` | **Equity:** `${s.get('equity', 0):,.2f}`\n"
                f"• **Open Positions:** `{s.get('open_positions', 0)}`"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            bname = type(engine.broker).__name__ if engine.broker else "None"
            await update.message.reply_text(f"🏦 Active broker: `{bname}` (Mode: `{getattr(config, 'BROKER_MODE', 'paper').upper()}`)")

    elif sub in ("paper", "demo"):
        from mt5_broker import MT5Broker
        engine.broker = MT5Broker(
            mode="paper",
            initial_balance=risk_mgr.state.equity,
            login=getattr(config, "MT5_LOGIN", 0),
            server=getattr(config, "MT5_SERVER", "MetaQuotes-Demo"),
            magic=getattr(config, "MT5_MAGIC", 112233),
            gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
        )
        config.BROKER_MODE = "mt5_paper"
        await update.message.reply_text(
            "⚪ **MT5 Paper / Demo Mode Activated**\n"
            "• High-fidelity MT5 simulation engine active\n"
            "• Real MT5 tickets, fees, and SL/TP bounds\n"
            "• Safe testing with zero capital risk",
            parse_mode="Markdown",
        )

    elif sub in ("live", "connect"):
        login = int(args[1]) if len(args) > 1 and args[1].isdigit() else getattr(config, "MT5_LOGIN", 0)
        password = args[2] if len(args) > 2 else getattr(config, "MT5_PASSWORD", "")
        server = args[3] if len(args) > 3 else getattr(config, "MT5_SERVER", "")
        gateway_url = args[4] if len(args) > 4 else getattr(config, "MT5_GATEWAY_URL", "")

        if not login or not password or not server:
            await update.message.reply_text(
                "❌ **MT5 Live Connection Failed (Missing Credentials)**\n\n"
                "Usage: `/mt5 live <LOGIN> <PASSWORD> <SERVER> [GATEWAY_URL]`\n"
                "Example: `/mt5 live 10054330 Secret123 Mtrading-live`\n"
                "Via Gateway: `/mt5 live 10054330 Secret123 Mtrading-live http://192.168.1.50:5000`\n\n"
                "Or configure `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, and `MT5_GATEWAY_URL` in `.env`.",
                parse_mode="Markdown",
            )
            return

        try:
            from mt5_broker import MT5Broker
            new_broker = MT5Broker(
                mode="live",
                login=login,
                password=password,
                server=server,
                path=getattr(config, "MT5_PATH", ""),
                magic=getattr(config, "MT5_MAGIC", 112233),
                gateway_url=gateway_url,
            )
            engine.broker = new_broker
            config.BROKER_MODE = "mt5_live"
            via_mode = f"Gateway (`{gateway_url}`)" if gateway_url else "Native MetaTrader 5"
            await update.message.reply_text(
                f"🟢 **MT5 Live Account Connected Successfully**\n"
                f"• **Account #:** `{login}`\n"
                f"• **Server:** `{server}`\n"
                f"• **Bridge:** {via_mode}\n"
                f"• **Balance:** `${new_broker.balance:,.2f}` | **Equity:** `${new_broker.equity:,.2f}`\n"
                f"• **Status:** Live deal execution active with server-side SL/TP.",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("MT5 Live connect failed: %s", exc)
            gw_hint = ""
            err_str = str(exc)
            if "Native MetaTrader5 package not available" in err_str or "MT5_GATEWAY_URL" in err_str:
                gw_hint = (
                    "\n\n💡 **Why did this happen?**\n"
                    "The official `MetaTrader5` Python library requires Windows and the MT5 desktop terminal (`terminal64.exe`). "
                    "When running on Linux, macOS, or cloud containers, connect using the MT5 REST Bridge:\n"
                    "1. On your Windows PC with MT5 running, execute:\n"
                    "   `python mt5_gateway_server.py --port 5000`\n"
                    "2. Reconnect with the gateway URL:\n"
                    f"   `/mt5 live {login} <PASSWORD> {server} http://<WINDOWS_IP>:5000`\n"
                    "3. Or switch to high-fidelity risk-free simulation:\n"
                    "   `/mt5 paper`"
                )
            await update.message.reply_text(
                f"🚫 **MT5 Live Connection Rejected (Fail-Closed)**\n"
                f"Error: `{exc}`{gw_hint}\n\n"
                f"_Live mode aborted safely. Current broker remains unchanged._",
                parse_mode="Markdown",
            )

    elif sub in ("positions", "pos"):
        if engine.broker:
            try:
                positions = engine.broker.get_positions()
                if not positions:
                    await update.message.reply_text("ℹ️ No active positions on MT5 broker.")
                    return
                lines = [f"🏦 **MT5 Active Broker Positions ({len(positions)}):**"]
                for p in positions:
                    icon = "📈" if p.unrealized_pnl >= 0 else "📉"
                    lines.append(
                        f"• `{p.symbol}` **{p.side.upper()}** `{p.qty:.2f}` lots @ `${p.entry_price:,.2f}` | "
                        f"PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                    )
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"⚠️ Error fetching MT5 positions: `{e}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ No broker attached to engine.")

    else:
        await update.message.reply_text(
            "🏦 **MetaTrader 5 Commands:**\n"
            "• `/mt5 status` — Check MT5 account telemetry & connection\n"
            "• `/mt5 paper` — Switch to MT5 Paper simulation engine\n"
            "• `/mt5 live [login] [pass] [server] [gateway_url]` — Connect live MT5 account\n"
            "• `/mt5 positions` — Show all active MT5 broker positions",
            parse_mode="Markdown",
        )


async def broker_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        bname = type(engine.broker).__name__ if engine.broker else "None"
        bmode = getattr(config, "BROKER_MODE", "paper").upper()
        summary = ""
        if hasattr(engine.broker, "get_account_info"):
            try:
                summary = f"\n\n{engine.broker.get_account_info().summary()}"
            except Exception:
                pass
        await update.message.reply_text(
            f"🏦 **Active Execution Broker Environment**\n"
            f"• **Class:** `{bname}`\n"
            f"• **Mode:** `{bmode}`{summary}\n\n"
            f"🌐 **Native Linux Broker Environments (No Windows/Desktop Required):**\n"
            f"• `/oanda` — Institutional Forex, Gold (XAU/USD) & Metals via REST v20\n"
            f"• `/ccxt` — 100+ Crypto Exchanges (Binance, Bybit, OKX, Bitget, etc.)\n"
            f"• `/alpaca` — US Equities, ETFs, Options & Crypto via REST API\n"
            f"• `/broker paper` — Zero-risk local deterministic simulation\n\n"
            f"🖥️ **MetaTrader 5 Bridge:**\n"
            f"• `/mt5 paper` (Simulation) | `/mt5 live ...` (Windows terminal / Gateway)",
            parse_mode="Markdown",
        )
        return

    mode = args[0].lower()
    exchange_id = getattr(config, "DEFAULT_EXCHANGE", "binance")
    api_key = getattr(config, "EXCHANGE_API_KEY", "")
    api_secret = getattr(config, "EXCHANGE_API_SECRET", "")
    api_password = getattr(config, "EXCHANGE_PASSWORD", "")
    is_testnet = getattr(config, "EXCHANGE_TESTNET", False)
    oanda_account_id = getattr(config, "OANDA_ACCOUNT_ID", "")
    oanda_api_token = getattr(config, "OANDA_API_TOKEN", "")
    oanda_env = getattr(config, "OANDA_ENVIRONMENT", "practice")
    alpaca_api_key = getattr(config, "ALPACA_API_KEY", "")
    alpaca_api_secret = getattr(config, "ALPACA_API_SECRET", "")
    alpaca_paper = getattr(config, "ALPACA_PAPER", True)

    if mode in ("ccxt", "live", "ccxt_live"):
        if len(args) > 1:
            exchange_id = args[1].lower()
        if len(args) > 2:
            api_key = args[2]
        if len(args) > 3:
            api_secret = args[3]
        if len(args) > 4:
            api_password = args[4]
    elif mode in ("oanda", "oanda_practice", "oanda_live"):
        if len(args) > 1:
            oanda_account_id = args[1]
        if len(args) > 2:
            oanda_api_token = args[2]
        if len(args) > 3:
            oanda_env = args[3].lower()
        elif mode == "oanda_live":
            oanda_env = "live"
    elif mode in ("alpaca", "alpaca_paper", "alpaca_live"):
        if len(args) > 1:
            alpaca_api_key = args[1]
        if len(args) > 2:
            alpaca_api_secret = args[2]
        if len(args) > 3:
            alpaca_paper = args[3].lower() in ("paper", "demo", "true")
        elif mode == "alpaca_live":
            alpaca_paper = False

    try:
        from execution import create_broker
        new_b = create_broker(
            broker_mode=mode,
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            api_password=api_password,
            is_testnet=is_testnet,
            initial_balance=risk_mgr.state.equity,
            mt5_login=getattr(config, "MT5_LOGIN", 0),
            mt5_password=getattr(config, "MT5_PASSWORD", ""),
            mt5_server=getattr(config, "MT5_SERVER", "MetaQuotes-Demo"),
            mt5_path=getattr(config, "MT5_PATH", ""),
            mt5_magic=getattr(config, "MT5_MAGIC", 112233),
            mt5_gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
            oanda_account_id=oanda_account_id,
            oanda_api_token=oanda_api_token,
            oanda_env=oanda_env,
            alpaca_api_key=alpaca_api_key,
            alpaca_api_secret=alpaca_api_secret,
            alpaca_paper=alpaca_paper,
        )
        engine.broker = new_b
        config.BROKER_MODE = mode
        await update.message.reply_text(
            f"✅ **Broker Switched to `{mode.upper()}`**\n"
            f"• **Instance:** `{type(new_b).__name__}`\n"
            f"• **Status:** Active & Ready for live order execution",
            parse_mode="Markdown",
        )
    except Exception as exc:
        await update.message.reply_text(
            f"🚫 **Failed to Switch Broker (Fail-Closed):**\n`{exc}`",
            parse_mode="Markdown",
        )


async def oanda_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("status", "info"):
        from oanda_broker import OANDABroker
        if isinstance(engine.broker, OANDABroker):
            info = engine.broker.get_account_info()
            await update.message.reply_text(info.summary(), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "🌐 **OANDA v20 REST Broker (Native Linux-Ready)**\n"
                "• **Current Engine:** OANDA broker is currently inactive.\n"
                "• **Connect Practice:** `/oanda live <ACCOUNT_ID> <API_TOKEN> practice`\n"
                "• **Connect Live:** `/oanda live <ACCOUNT_ID> <API_TOKEN> live`\n\n"
                "💡 _Free demo accounts with $100k balance and instant API tokens available at oanda.com._",
                parse_mode="Markdown",
            )
    elif sub in ("live", "practice", "connect"):
        acc_id = args[1] if len(args) > 1 else getattr(config, "OANDA_ACCOUNT_ID", "")
        token = args[2] if len(args) > 2 else getattr(config, "OANDA_API_TOKEN", "")
        env = args[3].lower() if len(args) > 3 else ("live" if sub == "live" else getattr(config, "OANDA_ENVIRONMENT", "practice"))

        if not acc_id or not token:
            await update.message.reply_text(
                "❌ **OANDA Connection Failed (Missing Credentials)**\n\n"
                "Usage: `/oanda live <ACCOUNT_ID> <API_TOKEN> [practice|live]`\n"
                "Example: `/oanda live 101-001-12345678-001 abcdef123456 practice`\n\n"
                "Or configure `OANDA_ACCOUNT_ID` and `OANDA_API_TOKEN` in `.env`.",
                parse_mode="Markdown",
            )
            return

        try:
            from oanda_broker import OANDABroker
            new_b = OANDABroker(account_id=acc_id, api_token=token, environment=env)
            engine.broker = new_b
            config.BROKER_MODE = f"oanda_{env}"
            info = new_b.get_account_info()
            await update.message.reply_text(
                f"🟢 **OANDA v20 Broker Connected Successfully**\n"
                f"• **Account ID:** `{acc_id}`\n"
                f"• **Environment:** `{env.upper()}`\n"
                f"• **NAV / Equity:** `${info.equity:,.2f}` {info.currency}\n"
                f"• **Free Margin:** `${info.margin_available:,.2f}`\n"
                f"• **Status:** Live deal execution active with server-side SL/TP (Linux-Native).",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("OANDA connect failed: %s", exc)
            await update.message.reply_text(
                f"🚫 **OANDA Connection Rejected (Fail-Closed)**\n"
                f"Error: `{exc}`\n\n"
                f"_Live mode aborted safely. Current broker remains unchanged._",
                parse_mode="Markdown",
            )
    elif sub in ("positions", "pos"):
        from oanda_broker import OANDABroker
        if isinstance(engine.broker, OANDABroker):
            pos = engine.broker.get_positions()
            if not pos:
                await update.message.reply_text("ℹ️ No active positions on OANDA broker.")
                return
            lines = [f"🌐 **OANDA Active Broker Positions ({len(pos)}):**"]
            for p in pos:
                icon = "📈" if p.unrealized_pnl >= 0 else "📉"
                lines.append(
                    f"• `{p.symbol}` **{p.side.upper()}** `{p.qty:.0f}` units @ `${p.entry_price:,.5f}` | "
                    f"PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Active broker is not OANDA.")
    else:
        await update.message.reply_text(
            "🌐 **OANDA Commands:**\n"
            "• `/oanda status` — Check OANDA account balance & NAV\n"
            "• `/oanda live <ACCOUNT_ID> <API_TOKEN> [practice|live]` — Connect OANDA account\n"
            "• `/oanda positions` — View open OANDA positions",
            parse_mode="Markdown",
        )


async def alpaca_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("status", "info"):
        from alpaca_broker import AlpacaBroker
        if isinstance(engine.broker, AlpacaBroker):
            info = engine.broker.get_account_info()
            await update.message.reply_text(info.summary(), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "🦙 **Alpaca REST Broker (US Stocks, ETFs, Crypto)**\n"
                "• **Current Engine:** Alpaca broker is currently inactive.\n"
                "• **Connect Paper:** `/alpaca live <KEY> <SECRET> paper`\n"
                "• **Connect Live:** `/alpaca live <KEY> <SECRET> live`\n\n"
                "💡 _Free paper trading keys available at alpaca.markets._",
                parse_mode="Markdown",
            )
    elif sub in ("live", "paper", "connect"):
        key = args[1] if len(args) > 1 else getattr(config, "ALPACA_API_KEY", "")
        secret = args[2] if len(args) > 2 else getattr(config, "ALPACA_API_SECRET", "")
        is_paper = False if (len(args) > 3 and args[3].lower() == "live") or sub == "live" else True

        if not key or not secret:
            await update.message.reply_text(
                "❌ **Alpaca Connection Failed (Missing Credentials)**\n\n"
                "Usage: `/alpaca live <KEY> <SECRET> [paper|live]`\n\n"
                "Or configure `ALPACA_API_KEY` and `ALPACA_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
            return

        try:
            from alpaca_broker import AlpacaBroker
            new_b = AlpacaBroker(api_key=key, api_secret=secret, is_paper=is_paper)
            engine.broker = new_b
            config.BROKER_MODE = "alpaca_paper" if is_paper else "alpaca_live"
            info = new_b.get_account_info()
            await update.message.reply_text(
                f"🟢 **Alpaca Broker Connected Successfully**\n"
                f"• **Account #:** `{info.account_number}`\n"
                f"• **Mode:** `{info.environment.upper()}`\n"
                f"• **Portfolio Equity:** `${info.equity:,.2f}` {info.currency}\n"
                f"• **Buying Power:** `${info.buying_power:,.2f}`\n"
                f"• **Status:** Live execution active (Zero desktop dependencies).",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("Alpaca connect failed: %s", exc)
            await update.message.reply_text(
                f"🚫 **Alpaca Connection Rejected (Fail-Closed)**\n"
                f"Error: `{exc}`\n\n"
                f"_Live mode aborted safely. Current broker remains unchanged._",
                parse_mode="Markdown",
            )
    elif sub in ("positions", "pos"):
        from alpaca_broker import AlpacaBroker
        if isinstance(engine.broker, AlpacaBroker):
            pos = engine.broker.get_positions()
            if not pos:
                await update.message.reply_text("ℹ️ No active positions on Alpaca broker.")
                return
            lines = [f"🦙 **Alpaca Active Broker Positions ({len(pos)}):**"]
            for p in pos:
                icon = "📈" if p.unrealized_pnl >= 0 else "📉"
                lines.append(
                    f"• `{p.symbol}` **{p.side.upper()}** `{p.qty:.2f}` @ `${p.entry_price:,.2f}` | "
                    f"PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Active broker is not Alpaca.")
    else:
        await update.message.reply_text(
            "🦙 **Alpaca Commands:**\n"
            "• `/alpaca status` — Check Alpaca account balance & portfolio value\n"
            "• `/alpaca live <KEY> <SECRET> [paper|live]` — Connect Alpaca account\n"
            "• `/alpaca positions` — View open Alpaca positions",
            parse_mode="Markdown",
        )


async def ccxt_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("status", "info"):
        from execution import CCXTBroker
        if isinstance(engine.broker, CCXTBroker):
            info = engine.broker.get_account_info()
            await update.message.reply_text(info.summary(), parse_mode="Markdown")
        else:
            ex = getattr(config, "DEFAULT_EXCHANGE", "binance")
            await update.message.reply_text(
                f"⚡ **CCXT Live Exchange Broker (Native Linux-Ready)**\n"
                f"• **Current Engine:** CCXT broker is currently inactive.\n"
                f"• **Connect Exchange:** `/ccxt live <exchange> <API_KEY> <SECRET> [PASSWORD]`\n"
                f"• **Supported:** Binance, Bybit, OKX, Bitget, Kraken, Gate.io, KuCoin (100+ exchanges)\n"
                f"• **Default Exchange:** `{ex}`",
                parse_mode="Markdown",
            )
    elif sub in ("live", "connect"):
        ex_id = args[1].lower() if len(args) > 1 else getattr(config, "DEFAULT_EXCHANGE", "binance")
        key = args[2] if len(args) > 2 else getattr(config, "EXCHANGE_API_KEY", "")
        secret = args[3] if len(args) > 3 else getattr(config, "EXCHANGE_API_SECRET", "")
        password = args[4] if len(args) > 4 else getattr(config, "EXCHANGE_PASSWORD", "")
        testnet = getattr(config, "EXCHANGE_TESTNET", False)

        if not key or not secret:
            await update.message.reply_text(
                "❌ **CCXT Connection Failed (Missing Credentials)**\n\n"
                "Usage: `/ccxt live <EXCHANGE> <API_KEY> <SECRET> [PASSWORD]`\n"
                "Example: `/ccxt live bybit my_key my_secret`\n\n"
                "Or configure `EXCHANGE_API_KEY` and `EXCHANGE_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
            return

        try:
            from execution import CCXTBroker
            new_b = CCXTBroker(
                exchange_id=ex_id,
                api_key=key,
                api_secret=secret,
                password=password,
                is_testnet=testnet,
            )
            engine.broker = new_b
            config.BROKER_MODE = "live"
            config.DEFAULT_EXCHANGE = ex_id
            info = new_b.get_account_info()
            await update.message.reply_text(
                f"🟢 **CCXT Live Broker Connected Successfully**\n"
                f"• **Exchange:** `{ex_id.upper()}`\n"
                f"• **Free Balance:** `${info.free_margin:,.2f}` {info.currency}\n"
                f"• **Total Equity:** `${info.equity:,.2f}` {info.currency}\n"
                f"• **Status:** Live spot/futures execution active (Zero desktop dependencies).",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("CCXT connect failed: %s", exc)
            await update.message.reply_text(
                f"🚫 **CCXT Connection Rejected (Fail-Closed)**\n"
                f"Error: `{exc}`\n\n"
                f"_Live mode aborted safely. Current broker remains unchanged._",
                parse_mode="Markdown",
            )
    elif sub in ("positions", "pos"):
        from execution import CCXTBroker
        if isinstance(engine.broker, CCXTBroker):
            pos = engine.broker.get_positions()
            if not pos:
                await update.message.reply_text("ℹ️ No active positions on CCXT exchange.")
                return
            lines = [f"⚡ **CCXT Active Positions ({len(pos)}):**"]
            for p in pos:
                icon = "📈" if p.unrealized_pnl >= 0 else "📉"
                lines.append(
                    f"• `{p.symbol}` **{p.side.upper()}** `{p.qty:.4f}` @ `${p.entry_price:,.2f}` | "
                    f"PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Active broker is not CCXT.")
    else:
        await update.message.reply_text(
            "⚡ **CCXT Commands:**\n"
            "• `/ccxt status` — Check live exchange account balance & margin\n"
            "• `/ccxt live <exchange> <key> <secret> [password]` — Connect live exchange\n"
            "• `/ccxt positions` — View open exchange positions",
            parse_mode="Markdown",
        )


async def reset_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        gemini.reset(update.effective_chat.id)
    except Exception:
        pass
    await update.message.reply_text("Conversation memory cleared.")


async def chat_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text or ""
    market_context = ""
    try:
        info = get_price_info(config.DEFAULT_SYMBOL)
        if info.get("ok"):
            market_context = f"{config.DEFAULT_SYMBOL} mark={info.get('mark')} change={info.get('change_pct')}%"
    except Exception:
        pass
    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
    reply = gemini.ask(chat_id=chat_id, user_text=user_text, market_context=market_context)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def live_monitoring_worker(app):
    try:
        from autotrade_worker import live_monitoring_worker as _multi_worker
        return await _multi_worker(
            app,
            config=config,
            engine=engine,
            risk_mgr=risk_mgr,
            arbiter=arbiter,
            MarketDataFeed=MarketDataFeed,
            autotrade_active_getter=lambda: autotrade_active,
            write_runtime_status=_write_runtime_status,
            monitoring_health=monitoring_health,
            now_iso=_now_iso,
        )
    except Exception as _aw_err:
        print("[autotrade_worker] using legacy worker:", _aw_err)
    """Background task that polls candles and tracks setups/positions."""
    try:
        from multi_agent_trading_bot import _norm_trade_symbol
        symbol = _norm_trade_symbol(getattr(config, "DEFAULT_SYMBOL", "XAUUSD"))
    except Exception:
        symbol = str(getattr(config, "DEFAULT_SYMBOL", "XAUUSD")).upper()
        if symbol in ("XAUTUSD", "XAUT", "GOLD", "XAU"):
            symbol = "XAUUSD"
        if symbol in ("BTC", "BTCUSD", "XBT"):
            symbol = "BTCUSDT"
    feed = MarketDataFeed(symbol=symbol, source=config.DEFAULT_DATA_SOURCE)
    _write_runtime_status()
    while True:
        monitoring_health["last_poll_at"] = _now_iso()
        try:
            data = feed.multi_tf(limit_1m=500)
            if data and "1m" in data and not data["1m"].empty:
                monitoring_health["last_success_at"] = _now_iso()
                monitoring_health["last_error"] = None
                monitoring_health["consecutive_errors"] = 0
                df1 = data["1m"]
                daily = data.get("daily")
                if daily is None or getattr(daily, "empty", True):
                    try:
                        daily = pd.DataFrame()
                    except NameError:
                        import pandas as _pd
                        daily = _pd.DataFrame()
                candle = df1.iloc[-1]
                candle_dict = {
                    "open": float(candle.get("open", candle["close"])),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                }

                if not autotrade_active and symbol in engine.pending_setups:
                    engine.cancel_setup(symbol, reason="Autotrade disabled; pending entry cancelled")

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

                if autotrade_active and symbol not in engine.active_positions and symbol not in engine.pending_setups:
                    sig, why, votes = arbiter.decide(
                        df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr, symbol=symbol, on_demand=True
                    )
                    if not sig:
                        logger.info("Autotrade: no setup — %s", why)
                    if sig:
                        order = engine.register_setup(sig, reason=sig.get("reason", ""))
                        alert = (
                            f"⚡ **NEW MULTI-AGENT SETUP**\n"
                            f"`{symbol}` **{order.direction}**\n"
                            f"Entry `${order.entry_level:,.2f}` | SL `${order.stop_loss:,.2f}`\n"
                            f"Lots `{order.lots}` | Lev `{order.leverage:.0f}x`\n"
                            f"_{order.setup_reason}_"
                        )
                        if config.TELEGRAM_CHAT_ID:
                            try:
                                await app.bot.send_message(
                                    chat_id=config.TELEGRAM_CHAT_ID, text=alert, parse_mode="Markdown"
                                )
                            except Exception:
                                pass
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
                monitoring_health["last_error"] = "No market data returned"
                monitoring_health["consecutive_errors"] += 1
        except Exception as e:
            monitoring_health["last_error"] = str(e)
            monitoring_health["consecutive_errors"] += 1
            logger.error(f"Monitoring worker error: {e}")

        _write_runtime_status()
        sleep_interval = config.FAST_POLL_INTERVAL if (engine.active_positions or engine.pending_setups) else config.POLL_INTERVAL
        await asyncio.sleep(sleep_interval)


def run_cli_interactive():
    global autotrade_active
    print("TradingBot CLI — type help for commands, or signal, status, price, news, autotrade, exit")
    while True:
        try:
            cmd = input("TradingBot> ").strip()
        except EOFError:
            break
        if not cmd:
            continue
        if cmd.lower() in ("exit", "quit"):
            break
        parts = cmd.split()
        name = parts[0].lower().lstrip("/")
        if name in ("signal", "single"):
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
            print(res.get("status"), res.get("signal"))
        elif name in ("backtest", "abacktest"):
            from auto_backtest import run_auto_backtest
            b_args = parts[1:]
            b_sym = config.DEFAULT_SYMBOL
            b_bars = 500
            for a in b_args:
                if a.isdigit():
                    b_bars = int(a)
                elif a.isalpha() and len(a) > 2:
                    b_sym = a.upper()
            report = run_auto_backtest(symbol=b_sym, bars=b_bars, source=config.DEFAULT_DATA_SOURCE)
            print(report.text())
        elif name == "status":
            print(engine.get_portfolio_summary())
        elif name == "levels":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            from realtime_prices import compute_levels, format_levels_message
            print(format_levels_message(compute_levels(sym)))
        elif name == "price":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            print(get_price_info(sym))
        elif name == "news":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            try:
                from news_agent import NewsAgent
                agent = NewsAgent(symbol=sym)
                if hasattr(agent, "report"):
                    print(agent.report())
                else:
                    v = agent.vote()
                    print(f"{v.direction.name}: {v.reason}")
            except Exception as e:
                print(f"News error: {e}")
        elif name == "risk":
            print(risk_mgr.summary())
        elif name == "positions":
            print(engine.get_portfolio_summary())
        elif name == "autotrade":
            arg = parts[1].lower() if len(parts) > 1 else ""
            broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
            if arg in ("on", "1", "true", "enable"):
                autotrade_active = True
                engine.reset_halt()
                print(f"Autotrade ON ({broker_mode} mode)")
            elif arg in ("off", "0", "false", "disable"):
                autotrade_active = False
                print("Autotrade OFF")
            else:
                st = "ON" if autotrade_active else "OFF"
                print(f"Autotrade is {st} ({broker_mode}). Use: autotrade on|off")
        elif name == "close":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            try:
                closed = engine.close_position(sym)
                if closed:
                    print(f"Close executed for {sym}")
                else:
                    print(f"No active position for {sym}")
            except Exception as e:
                print(f"Close failed: {e}")
        elif name == "cancel":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            try:
                cancelled = engine.cancel_setup(sym, reason="Manual CLI cancel")
                if cancelled:
                    print(f"Cancelled pending setup for {sym}")
                else:
                    print(f"No pending setup for {sym}")
            except Exception as e:
                print(f"Cancel failed: {e}")
        elif name == "health":
            print(
                f"Monitoring Health:\n"
                f"• Active: {monitoring_health.get('active', False)}\n"
                f"• Last poll: {monitoring_health.get('last_poll_at')}\n"
                f"• Last success: {monitoring_health.get('last_success_at')}\n"
                f"• Last error: {monitoring_health.get('last_error')}\n"
                f"• Consecutive errors: {monitoring_health.get('consecutive_errors', 0)}"
            )
        elif name == "leverage":
            if len(parts) > 1:
                try:
                    lev = float(parts[1])
                    risk_mgr.set_leverage(lev)
                    print(f"Leverage set to {risk_mgr.get_leverage():.0f}x")
                except Exception as e:
                    print(f"Invalid leverage: {e}")
            else:
                print(f"Current leverage: {risk_mgr.get_leverage():.0f}x")
        elif name == "kill":
            try:
                from advanced_integration import AdvancedEngine
                eng = AdvancedEngine()
                arg = parts[1].lower() if len(parts) > 1 else "status"
                if arg in ("on", "1", "true", "activate"):
                    eng.activate_kill("Manual kill via CLI")
                    print("🛑 KILL SWITCH ON — no new entries.")
                elif arg in ("off", "0", "false", "deactivate"):
                    eng.deactivate_kill()
                    print("🟢 Kill switch OFF.")
                else:
                    print(f"Kill switch status: {eng.kill_status()}")
            except Exception as e:
                print(f"Kill error: {e}")
        elif name in ("performance", "perf"):
            try:
                from advanced_integration import AdvancedEngine
                eng = AdvancedEngine()
                print(eng.performance_report())
            except Exception as e:
                print(f"Performance error: {e}")
        elif name == "profile":
            try:
                from strategy_profiles import load_profiles
                profiles = load_profiles()
                if len(parts) > 1:
                    pname = parts[1].lower()
                    if pname in profiles:
                        p = profiles[pname]
                        print(f"Profile: {p.name} ({p.description})\n"
                              f"• Score >= {p.min_score} | Agree >= {p.min_agreement} | RR >= {p.min_rr}\n"
                              f"• Risk {p.base_risk_pct}% | Max lev {p.max_leverage}x")
                    else:
                        print(f"Unknown profile '{pname}'. Available: {', '.join(profiles.keys())}")
                else:
                    print(f"Available profiles: {', '.join(profiles.keys())}")
            except Exception as e:
                print(f"Profile error: {e}")
        elif name in ("ask", "chat"):
            query = " ".join(parts[1:]).strip()
            if not query:
                print("Usage: ask <question> or chat <message>")
            else:
                market_context = ""
                try:
                    info = get_price_info(config.DEFAULT_SYMBOL)
                    if info.get("ok"):
                        market_context = f"{config.DEFAULT_SYMBOL} mark={info.get('mark')} change={info.get('change_pct')}%"
                except Exception:
                    pass
                reply = gemini.ask(chat_id=0, user_text=query, market_context=market_context)
                print(reply)
        elif name == "reset":
            gemini.reset(0)
            print("Conversation memory cleared.")
        elif name == "quality":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            try:
                from signal_quality import evaluate_signal_quality
                feed = MarketDataFeed(symbol=sym, source=getattr(config, "DEFAULT_DATA_SOURCE", "demo"))
                data = feed.multi_tf(limit_1m=120)
                df = data.get("1m") if data else None
                if df is None or df.empty:
                    print(f"No market data returned for {sym}")
                else:
                    q = evaluate_signal_quality(df, "LONG", spread=0.0)
                    icon = "✅" if q.allowed else "❌"
                    print(f"{icon} Signal Quality {sym} (score={q.score:.2f}):\n" + "\n".join(f"• {r}" for r in q.reasons))
            except Exception as e:
                print(f"Quality check failed: {e}")
        elif name == "mt5":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in ("status", "info"):
                if hasattr(engine.broker, "get_account_info"):
                    info = engine.broker.get_account_info()
                    print(info.summary())
                elif hasattr(engine.broker, "get_account_summary"):
                    print(engine.broker.get_account_summary())
                else:
                    bname = type(engine.broker).__name__ if engine.broker else "None"
                    print(f"Broker: {bname} ({getattr(config, 'BROKER_MODE', 'paper')})")
            elif sub in ("paper", "demo"):
                from mt5_broker import MT5Broker
                engine.broker = MT5Broker(mode="paper", initial_balance=risk_mgr.state.equity)
                config.BROKER_MODE = "mt5_paper"
                print("MT5 Paper mode activated.")
            elif sub in ("live", "connect"):
                login = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else getattr(config, "MT5_LOGIN", 0)
                password = parts[3] if len(parts) > 3 else getattr(config, "MT5_PASSWORD", "")
                server = parts[4] if len(parts) > 4 else getattr(config, "MT5_SERVER", "")
                gateway_url = parts[5] if len(parts) > 5 else getattr(config, "MT5_GATEWAY_URL", "")
                if not login or not password or not server:
                    print("Credentials required: mt5 live <LOGIN> <PASSWORD> <SERVER> [GATEWAY_URL] or set in .env")
                else:
                    try:
                        from mt5_broker import MT5Broker
                        new_broker = MT5Broker(
                            mode="live",
                            login=login,
                            password=password,
                            server=server,
                            gateway_url=gateway_url,
                        )
                        engine.broker = new_broker
                        config.BROKER_MODE = "mt5_live"
                        bridge = f" via {gateway_url}" if gateway_url else ""
                        print(f"Connected to MT5 Live account #{login} on {server}{bridge}")
                    except Exception as exc:
                        print(f"MT5 Live connection failed: {exc}")
            elif sub in ("positions", "pos"):
                if engine.broker:
                    print(engine.broker.get_positions())
                else:
                    print("No broker attached.")
            else:
                print("Usage: mt5 status | mt5 paper | mt5 live [login] [pass] [server] [gateway_url] | mt5 positions")
        elif name == "broker":
            if len(parts) > 1:
                bmode = parts[1].lower()
                try:
                    from execution import create_broker
                    new_b = create_broker(broker_mode=bmode, initial_balance=risk_mgr.state.equity)
                    engine.broker = new_b
                    config.BROKER_MODE = bmode
                    print(f"Broker switched to {bmode} ({type(new_b).__name__})")
                except Exception as exc:
                    print(f"Broker switch failed: {exc}")
            else:
                bname = type(engine.broker).__name__ if engine.broker else "None"
                print(f"Active broker: {bname} ({getattr(config, 'BROKER_MODE', 'paper')})")
        elif name == "oanda":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in ("status", "info"):
                from oanda_broker import OANDABroker
                if isinstance(engine.broker, OANDABroker):
                    print(engine.broker.get_account_info().summary())
                else:
                    print(f"Active broker is {type(engine.broker).__name__}. Connect: oanda live <ACCOUNT_ID> <TOKEN> [practice|live]")
            elif sub in ("live", "practice", "connect"):
                acc_id = parts[2] if len(parts) > 2 else getattr(config, "OANDA_ACCOUNT_ID", "")
                token = parts[3] if len(parts) > 3 else getattr(config, "OANDA_API_TOKEN", "")
                env = parts[4].lower() if len(parts) > 4 else ("live" if sub == "live" else getattr(config, "OANDA_ENVIRONMENT", "practice"))
                if not acc_id or not token:
                    print("Usage: oanda live <ACCOUNT_ID> <API_TOKEN> [practice|live]")
                else:
                    try:
                        from oanda_broker import OANDABroker
                        new_b = OANDABroker(account_id=acc_id, api_token=token, environment=env)
                        engine.broker = new_b
                        config.BROKER_MODE = f"oanda_{env}"
                        print(f"Connected to OANDA {env} account #{acc_id}")
                    except Exception as exc:
                        print(f"OANDA connection failed: {exc}")
            elif sub in ("positions", "pos"):
                from oanda_broker import OANDABroker
                if isinstance(engine.broker, OANDABroker):
                    print(engine.broker.get_positions())
                else:
                    print("Active broker is not OANDA.")
            else:
                print("Usage: oanda status | oanda live <ACCOUNT_ID> <TOKEN> [practice|live] | oanda positions")
        elif name == "alpaca":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in ("status", "info"):
                from alpaca_broker import AlpacaBroker
                if isinstance(engine.broker, AlpacaBroker):
                    print(engine.broker.get_account_info().summary())
                else:
                    print(f"Active broker is {type(engine.broker).__name__}. Connect: alpaca live <KEY> <SECRET> [paper|live]")
            elif sub in ("live", "paper", "connect"):
                key = parts[2] if len(parts) > 2 else getattr(config, "ALPACA_API_KEY", "")
                secret = parts[3] if len(parts) > 3 else getattr(config, "ALPACA_API_SECRET", "")
                is_paper = False if (len(parts) > 4 and parts[4].lower() == "live") or sub == "live" else True
                if not key or not secret:
                    print("Usage: alpaca live <KEY> <SECRET> [paper|live]")
                else:
                    try:
                        from alpaca_broker import AlpacaBroker
                        new_b = AlpacaBroker(api_key=key, api_secret=secret, is_paper=is_paper)
                        engine.broker = new_b
                        config.BROKER_MODE = "alpaca_paper" if is_paper else "alpaca_live"
                        print(f"Connected to Alpaca account #{new_b.account_number}")
                    except Exception as exc:
                        print(f"Alpaca connection failed: {exc}")
            elif sub in ("positions", "pos"):
                from alpaca_broker import AlpacaBroker
                if isinstance(engine.broker, AlpacaBroker):
                    print(engine.broker.get_positions())
                else:
                    print("Active broker is not Alpaca.")
            else:
                print("Usage: alpaca status | alpaca live <KEY> <SECRET> [paper|live] | alpaca positions")
        elif name == "ccxt":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in ("status", "info"):
                from execution import CCXTBroker
                if isinstance(engine.broker, CCXTBroker):
                    print(engine.broker.get_account_info().summary())
                else:
                    print(f"Active broker is {type(engine.broker).__name__}. Connect: ccxt live <exchange> <KEY> <SECRET> [password]")
            elif sub in ("live", "connect"):
                ex_id = parts[2].lower() if len(parts) > 2 else getattr(config, "DEFAULT_EXCHANGE", "binance")
                key = parts[3] if len(parts) > 3 else getattr(config, "EXCHANGE_API_KEY", "")
                secret = parts[4] if len(parts) > 4 else getattr(config, "EXCHANGE_API_SECRET", "")
                pwd = parts[5] if len(parts) > 5 else getattr(config, "EXCHANGE_PASSWORD", "")
                if not key or not secret:
                    print("Usage: ccxt live <exchange> <KEY> <SECRET> [password]")
                else:
                    try:
                        from execution import CCXTBroker
                        new_b = CCXTBroker(exchange_id=ex_id, api_key=key, api_secret=secret, password=pwd)
                        engine.broker = new_b
                        config.BROKER_MODE = "live"
                        config.DEFAULT_EXCHANGE = ex_id
                        print(f"Connected to CCXT exchange {ex_id}")
                    except Exception as exc:
                        print(f"CCXT connection failed: {exc}")
            elif sub in ("positions", "pos"):
                from execution import CCXTBroker
                if isinstance(engine.broker, CCXTBroker):
                    print(engine.broker.get_positions())
                else:
                    print("Active broker is not CCXT.")
            else:
                print("Usage: ccxt status | ccxt live <exchange> <KEY> <SECRET> [password] | ccxt positions")
        elif name in ("help", "commands", "cmd", "menu", "list", "all"):
            print(
                "Commands: signal [SYM], price [SYM], levels [SYM], news [SYM], quality [SYM], positions, autotrade on|off, close [SYM], cancel [SYM], closed, mt5 [CMD], broker [MODE], status, health, risk, leverage [LEV], kill [on|off], backtest [BARS] [SYM], abacktest [MODE] [BARS] [SYM], performance, profile [NAME], board, journal, agents, tiers, digest, brain, relearn [SYM], ask [TEXT], reset, exit\n\n"
                "Commands by category:\n"
                "📊 Market: price [SYM], levels [SYM], news [SYM]\n"
                "🎯 Signals: signal [SYM], asignal [SYM] [profile], single [SYM], quality [SYM]\n"
                "⚡ Execution: positions, autotrade on|off, close [SYM|all], cancel [SYM], closed, mt5 [CMD], broker [MODE]\n"
                "🛡️ Risk: status, health, risk, leverage [LEV], kill [on|off]\n"
                "📈 Research: backtest [BARS] [SYM], abacktest [MODE] [BARS] [SYM], performance, profile [NAME]\n"
                "🧠 Autonomous AI: board, journal, agents, tiers, digest, brain, relearn [SYM]\n"
                "💬 Assistant: ask [TEXT], reset, exit"
            )
        elif name == "asignal":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            prof = parts[2].lower() if len(parts) > 2 else "balanced"
            try:
                from advanced_integration import AdvancedEngine
                eng = AdvancedEngine(symbol=sym, profile=prof)
                res = eng.evaluate(sym)
                print(res.get("status"), res.get("signal"))
            except Exception as e:
                print(f"asignal error: {e}")
        elif name == "closed":
            try:
                from trade_journal import TradeJournal
                j = TradeJournal()
                closes = j.closed_summary(8)
                if closes:
                    print("Closed trades:")
                    for r in reversed(closes):
                        print(f"• {'✅' if r.get('won') else '❌'} {r.get('symbol')} {r.get('direction')} PnL: ${float(r.get('pnl') or 0):+,.2f} (R: {r.get('r_multiple')})")
                elif getattr(engine, "trade_history", None):
                    print("Closed trades:")
                    for t in list(reversed(engine.trade_history))[:8]:
                        print(f"• {'✅' if getattr(t, 'realized_pnl', 0) >= 0 else '❌'} {getattr(t, 'symbol', '')} {getattr(t, 'direction', '')} PnL: ${float(getattr(t, 'realized_pnl', 0)):+,.2f}")
                else:
                    print("No closed trades on record yet.")
            except Exception as e:
                print(f"Closed history error: {e}")
        elif name == "board":
            try:
                from telegram_feature_pack import _get_pack
                print(_get_pack().board())
            except Exception as e:
                print(f"Board error: {e}")
        elif name == "journal":
            try:
                from telegram_feature_pack import _get_pack
                print(_get_pack().journal.telegram_report())
            except Exception as e:
                print(f"Journal error: {e}")
        elif name == "agents":
            try:
                from telegram_feature_pack import _get_pack
                print(_get_pack().agent_stats.telegram_report())
            except Exception as e:
                print(f"Agents error: {e}")
        elif name == "tiers":
            try:
                from telegram_feature_pack import _get_pack
                print(_get_pack().risk_tiers.telegram_line())
            except Exception as e:
                print(f"Tiers error: {e}")
        elif name == "digest":
            try:
                from telegram_feature_pack_v4 import _get
                print(_get().daily_digest())
            except Exception as e:
                print(f"Digest error: {e}")
        elif name == "brain":
            try:
                from autonomous_learner import get_brain
                print(get_brain(auto_bootstrap=False).summary())
            except Exception as e:
                print(f"Brain error: {e}")
        elif name == "relearn":
            try:
                from autonomous_learner import get_brain
                sym = parts[1].upper() if len(parts) > 1 else "XAUUSD"
                print(get_brain(auto_bootstrap=False).bootstrap(symbol=sym, years=5.0))
            except Exception as e:
                print(f"Relearn error: {e}")
        else:
            print("Commands: signal, price, levels, news, quality, positions, autotrade, close, cancel, mt5, broker, status, health, risk, leverage, kill, backtest, performance, profile, ask, reset, exit")


def main():
    token = getattr(config, "TELEGRAM_TOKEN", None) or os.getenv("TELEGRAM_TOKEN", "")
    if "--cli" in sys.argv:
        run_cli_interactive()
        return

    if not token or token == "your_telegram_bot_token_here":
        print("TELEGRAM_TOKEN not configured. Use: python main.py --cli")
        if sys.stdin.isatty():
            run_cli_interactive()
        return

    print(f"Starting Telegram Trading Bot on token: {token[:8]}***...")

    async def on_post_init(application):
        logger.info("Initializing live monitoring background worker...")
        asyncio.create_task(live_monitoring_worker(application))

        # Register official Telegram Bot Command list so Telegram UI command menu ('/') works
        try:
            from telegram import BotCommand
            bot_commands = [
                BotCommand("start", "Start bot & interactive overview"),
                BotCommand("help", "Full command center & user guide"),
                BotCommand("commands", "Interactive catalog of all bot commands"),
                BotCommand("price", "Live mark price & 24h market stats"),
                BotCommand("levels", "Key levels (PDH/PDL, pivots, 2Y S/R)"),
                BotCommand("signal", "7-Agent consensus & optimal entry zone"),
                BotCommand("asignal", "Advanced SMC + CVD orderflow setup"),
                BotCommand("quality", "Signal quality score & breakdown"),
                BotCommand("positions", "Active trades & real-time PnL"),
                BotCommand("autotrade", "Toggle multi-symbol autotrade (max 5 slots)"),
                BotCommand("close", "Close position(s) by symbol or all"),
                BotCommand("cancel", "Cancel pending setup order"),
                BotCommand("closed", "Recently closed trades history & PnL"),
                BotCommand("status", "Portfolio equity & margin capacity"),
                BotCommand("health", "System health & background worker telemetry"),
                BotCommand("risk", "Risk tier report & lot sizing"),
                BotCommand("leverage", "Set or view leverage ceiling (1-20x)"),
                BotCommand("kill", "Emergency execution circuit breaker switch"),
                BotCommand("news", "Macro calendar & economic sentiment"),
                BotCommand("mt5", "MetaTrader 5 paper/live status & telemetry"),
                BotCommand("broker", "View or switch active broker engine"),
                BotCommand("oanda", "OANDA v20 Forex & Metals REST broker"),
                BotCommand("ccxt", "CCXT Crypto live exchange broker"),
                BotCommand("alpaca", "Alpaca US Stocks & Crypto REST broker"),
                BotCommand("backtest", "Fast backtest engine on historical data"),
                BotCommand("abacktest", "Advanced backtest suite (WF / MC / sweep)"),
                BotCommand("performance", "Trade metrics, win rate & Sharpe ratio"),
                BotCommand("profile", "View or switch strategy profile"),
                BotCommand("board", "Strategy board & agent standings"),
                BotCommand("journal", "Trade journal analytics & history"),
                BotCommand("agents", "Agent performance rankings & stats"),
                BotCommand("tiers", "Risk tier status & allocations"),
                BotCommand("digest", "Daily market performance digest"),
                BotCommand("brain", "Autonomous learner intelligence & state"),
                BotCommand("relearn", "Retrain autonomous learner on 5Y data"),
                BotCommand("reset", "Clear conversational chat memory"),
            ]
            await application.bot.set_my_commands(bot_commands)
            logger.info("Registered %d commands with Telegram Bot API", len(bot_commands))
        except Exception as cmd_err:
            logger.warning("Failed to sync Bot Commands with Telegram API: %s", cmd_err)

        # Advanced commands and autonomous learner
        try:
            from telegram_advanced import register_all_advanced
            register_all_advanced(application)
            print("Advanced commands registered")
        except Exception as adv_err:
            print(f"Advanced commands not loaded: {adv_err}")

    app = ApplicationBuilder().token(token).post_init(on_post_init).build()

    # Core Navigation & Help
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["help", "commands", "cmd", "menu", "list", "all", "command"], help_command))

    # Market Data
    app.add_handler(CommandHandler("levels", levels_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("news", news_command))

    # Signals & Confluence
    app.add_handler(CommandHandler(["signal", "single"], signal_command))
    try:
        from telegram_advanced import asignal_command
        app.add_handler(CommandHandler("asignal", asignal_command))
    except Exception:
        app.add_handler(CommandHandler("asignal", signal_command))
    try:
        from telegram_feature_pack_v4 import quality_command
        app.add_handler(CommandHandler("quality", quality_command))
    except Exception:
        pass

    # Execution & Trades
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("autotrade", autotrade_command))
    app.add_handler(CommandHandler(["close", "closeall"], close_command))
    app.add_handler(CommandHandler("closed", closed_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # MT5 & Native Execution Brokers
    app.add_handler(CommandHandler("mt5", mt5_command))
    app.add_handler(CommandHandler("broker", broker_command))
    app.add_handler(CommandHandler("oanda", oanda_command))
    app.add_handler(CommandHandler("alpaca", alpaca_command))
    app.add_handler(CommandHandler("ccxt", ccxt_command))

    # Risk & Safety
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("risk", risk_command))
    app.add_handler(CommandHandler("leverage", leverage_command))
    try:
        from telegram_advanced import kill_command
        app.add_handler(CommandHandler("kill", kill_command))
    except Exception:
        pass

    # Backtesting & Research
    app.add_handler(CommandHandler("backtest", backtest_command))
    try:
        from telegram_advanced import abacktest_command, performance_command, profile_command
        app.add_handler(CommandHandler(["abacktest", "backtestall"], abacktest_command))
        app.add_handler(CommandHandler(["performance", "perf"], performance_command))
        app.add_handler(CommandHandler("profile", profile_command))
    except Exception:
        pass

    # Autonomous AI & Feature Packs
    try:
        from telegram_feature_pack import board_command, journal_command, agents_command, tiers_command
        app.add_handler(CommandHandler("board", board_command))
        app.add_handler(CommandHandler("journal", journal_command))
        app.add_handler(CommandHandler("agents", agents_command))
        app.add_handler(CommandHandler("tiers", tiers_command))
    except Exception:
        pass
    try:
        from telegram_feature_pack_v4 import digest_command, replay_command, export_command, setups_command, abooks_command, heal_command
        app.add_handler(CommandHandler("digest", digest_command))
        app.add_handler(CommandHandler("replay", replay_command))
        app.add_handler(CommandHandler("export", export_command))
        app.add_handler(CommandHandler("setups", setups_command))
        app.add_handler(CommandHandler("abooks", abooks_command))
        app.add_handler(CommandHandler("heal", heal_command))
    except Exception:
        pass
    try:
        from telegram_brain import brain_command, relearn_command
        app.add_handler(CommandHandler("brain", brain_command))
        app.add_handler(CommandHandler("relearn", relearn_command))
    except Exception:
        pass

    # Conversational Assistant & Controls
    app.add_handler(CommandHandler(["ask", "chat"], ask_command))
    app.add_handler(CommandHandler("reset", reset_command))

    # Fallback for unrecognized commands
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Conversational chat handler for non-commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))


    print("Trading Bot is running. Press Ctrl+C to terminate.")
    app.run_polling()


if __name__ == "__main__":
    main()
