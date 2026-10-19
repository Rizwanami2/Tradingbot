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
import time
from typing import Any, Dict, Optional, Tuple

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import pandas as pd
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import config
from execution_engine import ExecutionEngine
from gemini_client import GeminiClient
from multi_agent_trading_bot import (
    Arbiter,
    MarketDataFeed,
    evaluate_symbol,
)
from risk_manager import RiskManager
from ops_dashboard import StatusStore
from telegram_brain import safe_reply


if not hasattr(asyncio, "to_thread"):
    import functools

    async def _asyncio_to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    asyncio.to_thread = _asyncio_to_thread


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*cryptography.*")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
    force=True,
)
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("TradingBot")

try:
    from mt5_accounts import get_account_manager
    _active_acc = get_account_manager().get_active_account()
    if _active_acc and _active_acc.login:
        config.MT5_LOGIN = _active_acc.login
        config.MT5_PASSWORD = _active_acc.password
        config.MT5_SERVER = _active_acc.server
        config.MT5_GATEWAY_URL = _active_acc.gateway_url
        if _active_acc.balance > 0:
            config.MT5_BALANCE = _active_acc.balance
            config.INITIAL_EQUITY = _active_acc.balance
except Exception:
    pass

gemini = GeminiClient()
risk_mgr = RiskManager(equity=config.INITIAL_EQUITY, symbol=config.DEFAULT_SYMBOL)
risk_mgr.set_leverage(config.DEFAULT_LEVERAGE)
arbiter = Arbiter(allow_asian=True)

from execution import create_broker_fail_closed

try:
    _broker = create_broker_fail_closed(
        broker_mode=getattr(config, "BROKER_MODE", "mt5_live"),
        api_key=getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", ""),
        api_secret=getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", ""),
    )
    if _broker and hasattr(_broker, "balance") and _broker.balance > 0:
        risk_mgr.state.equity = _broker.balance
        risk_mgr.state.peak_equity = max(risk_mgr.state.peak_equity, _broker.balance)
        risk_mgr.equity = _broker.balance
except Exception as _broker_err:
    if "PYTEST_CURRENT_TEST" in os.environ or getattr(config, "TESTING", False):
        b_m = str(getattr(config, "BROKER_MODE", "")).lower()
        if "delta" in b_m:
            from delta_broker import MockDeltaBroker
            _broker = MockDeltaBroker()
        else:
            from mt5_broker import MockMT5Broker
            _broker = MockMT5Broker()
    else:
        logger.warning(
            "Live broker initialization note: %s. Initializing MT5 Cloud execution fallback...",
            _broker_err,
        )
        try:
            from mt5_broker import MT5Broker
            _broker = MT5Broker(
                mode="live",
                login=getattr(config, "MT5_LOGIN", 100200300) or 100200300,
                password=getattr(config, "MT5_PASSWORD", "demo") or "demo",
                server=getattr(config, "MT5_SERVER", "Demo-Server") or "Demo-Server",
                initial_balance=getattr(config, "INITIAL_EQUITY", 10000.0),
            )
        except Exception as _fb_err:
            from mt5_broker import MockMT5Broker
            _broker = MockMT5Broker(initial_balance=getattr(config, "INITIAL_EQUITY", 10000.0))
engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)
try:
    from ops_dashboard import set_dashboard_engine
    set_dashboard_engine(engine)
except Exception:
    pass
try:
    from strategy_profiles import apply_profile
    apply_profile(getattr(config, "CURRENT_PROFILE", "scalp"), arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
except Exception as _p_boot_err:
    print("[boot] profile init:", _p_boot_err)

# Bot runtime state. Monitoring is off by default until explicitly enabled.
autotrade_active: bool = False
if getattr(config, "AUTOTRADE_ENABLED", False):
    b_mode = str(getattr(config, "BROKER_MODE", "")).lower()
    is_live_env = "live" in b_mode or (hasattr(config, "DELTA_EXCHANGE_TESTNET") and not config.DELTA_EXCHANGE_TESTNET and "delta" in b_mode)
    if is_live_env and not getattr(config, "AUTOTRADE_LIVE_OPT_IN", False):
        logger.warning(
            "[Startup Safety] BOT_AUTOTRADE=true requested on LIVE broker mode (%s), "
            "but BOT_AUTOTRADE_LIVE_OPT_IN is not enabled. Keeping autotrade OFF.",
            getattr(config, "BROKER_MODE", "")
        )
        autotrade_active = False
    else:
        autotrade_active = True


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
            "slots_mode": getattr(engine, "slots_mode", "auto"),
            "max_open_trades": int(getattr(engine, "max_open_trades", 6) or 6),
            "free_margin": float(summary.get("free_margin", summary.get("balance", 0.0))),
            "unrealized_pnl": float(summary.get("unrealized_pnl", 0.0)),
            "margin_level_pct": float(summary.get("margin_level_pct", 0.0)),
        }
        try:
            from ops_dashboard import set_dashboard_engine
            set_dashboard_engine(engine)
        except Exception:
            pass
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
        "• `/autotrade on` — Automated trading (up to 6 active trades)\n"
        "• `/positions` — Monitor open trades, PnL & dynamic trailing stops\n"
        "• `/status` — Portfolio equity & system health\n"
        "• `/commands` or `/help` — Full categorized command catalog\n\n"
        "🛡️ _Default paper equity: $1,000 | Max concurrent active trades: 6_"
    )
    try:
        await update.message.reply_text(welcome, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(welcome.replace("*", "").replace("_", "").replace("`", ""))


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = (ctx.args[0].lower() if ctx.args else "").strip()
    if cat in ("market", "price", "quotes", "levels", "chart", "charts", "matrix", "heatmap"):
        text = (
            "📊 **Market Data & Live Quotes:**\n"
            "• `/matrix [SYM]` or `/heatmap` — Multi-Timeframe Trend & Volatility Heatmap Matrix (M1..D1)\n"
            "• `/chart [btc|gold]` — MT5 Live Candlestick Chart with pivots & trend\n"
            "• `/price [SYM]` — Live mark price, 24h change & stats\n"
            "• `/levels [SYM]` — Key technical levels (PDH/PDL, pivots, swings, 2Y S/R)\n"
            "• `/news [SYM]` — Macro calendar & economic sentiment"
        )
    elif cat in ("signal", "signals", "setup", "confluence"):
        text = (
            "🎯 **Signals & Strategy Confluence:**\n"
            "• `/signal [SYM]` — 7-Agent consensus & optimal entry zone\n"
            "• `/asignal [SYM] [profile]` — Advanced SMC + CVD orderflow setup\n"
            "• `/quality [SYM]` — Comprehensive signal quality score & breakdown\n"
            "• `/matrix [SYM]` — Multi-Timeframe Trend & Volatility Confluence Matrix"
        )
    elif cat in ("execution", "orders", "trades", "pos", "positions", "slots", "slot", "dashboard", "dash", "web"):
        text = (
            "⚡ **Execution & Positions (Active Limit: Max 6):**\n"
            "• `/dashboard [start|stop]` — Local Web Companion Dashboard & Real-Time Terminal\n"
            "• `/positions [auto|manual <1-6>]` — Open active trades, pending setups & real-time PnL\n"
            "• `/slots [auto|manual <1-6>|<1-6>]` — Set manual / auto position slots (1-6 positions max)\n"
            "• `/autotrade on|off` — Multi-symbol autonomous trade execution\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL\n"
            "• `/vstops [clear]` — Client-side anti-stop-hunt virtual hidden stops\n"
            "• `/slippage` — Broker execution latency, slippage USD & grade (A+ to F)"
        )
    elif cat in ("mt5", "broker", "brokers", "balance", "wallet", "equity", "oanda", "ccxt", "alpaca", "switch", "live", "delta", "bridge"):
        text = (
            "🏦 **MetaTrader 5 & Broker Accounts:**\n"
            "• `/bridge [ping|test|deploy|on|off|dir]` — MT5 EA Bridge diagnostics & auto-deploy\n"
            "• `/switch [delta|live|<login>|<alias>]` — Fast switcher between brokers & MT5 accounts\n"
            "• `/delta [status|live|testnet|leverage|auto_leverage|positions]` — Delta Exchange India Live REST broker (Max 200x)\n"
            "• `/live [on|off|<login> <pass> <server>]` — Quick MT5 Live trading toggle & connect\n"
            "• `/mt5` — Interactive MT5 dashboard & controls\n"
            "• `/mt5 add <login> <pass> <server> [alias]` — Add & save MT5 Live account\n"
            "• `/mt5 switch [<login>|<alias>]` — Switch active MT5 account\n"
            "• `/mt5 balance [amount]` — View or update MT5 live balance\n"
            "• `/mt5 accounts` — List registered MT5 accounts\n"
            "• `/balance [amount]` — View or update active broker balance, equity, and margin\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker [mt5|delta]` — Switch active execution broker engine"
        )
    elif cat in ("risk", "safety", "limits"):
        text = (
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/cooldown [active|deactivate|reset]` — Anti-spam cooldown toggle & timer reset\n"
            "• `/spam [on|off]` — Spam trade mode (max 6 trades without cooldown)\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/risk reset [daily|weekly|all]` — Reset loss limits & unblock trading\n"
            "• `/resetrisk [daily|weekly|all|restart]` — Reset risk & restart autotrade\n"
            "• `/leverage [1-200|auto]` — Set manual leverage or dynamic auto-leverage (up to 200x)\n"
            "• `/lot [auto|manual|<size>]` — Auto dynamic risk lots or fixed manual lot size\n"
            "• `/kelly [SYM]` — Fractional Kelly lot sizing & 99% portfolio VaR / CVaR\n"
            "• `/session [asian|london|ny]` — End-of-session performance report & killzones\n"
            "• `/kill [on|off]` — Emergency execution stop circuit breaker"
        )
    elif cat in ("backtest", "research", "abacktest", "regime"):
        text = (
            "📈 **Backtesting & Research:**\n"
            "• `/backtest [bars] [SYM]` — Fast backtest engine\n"
            "• `/abacktest [mode] [bars] [SYM]` — Advanced suite (WF / MC / sweep)\n"
            "• `/performance` — Detailed trade metrics, win rate & Sharpe ratio\n"
            "• `/profile [name]` — Switch strategy profile (scalp/swing/trend/balanced)\n"
            "• `/regime [auto|calibrate|switch]` — Real-time regime detector & Sharpe calibrator"
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
            "• `/learner [on|off|unban|rules]` — Toggle learner veto filter & unban hours\n"
            "• `/veto [on|off]` — Quick toggle for trade veto filtering\n"
            "• `/train [heavy|quick]` — Deep multi-asset AI & autonomous training\n"
            "• `/relearn [SYM|ALL]` — Force 5Y history retraining bootstrap\n"
            "• `/reset` — Clear conversation context\n"
            "• `/ask [question]` — Conversational assistant with Gemini AI"
        )
    else:
        text = (
            "📖 **TradingBot Command Center**\n"
            "_Institutional Multi-Agent Paper & MT5 Trading System_\n\n"
            "📊 **Market Data & Live Quotes:**\n"
            "• `/chart [btc|gold]` — MT5 Live Candlestick Chart with pivots & trend\n"
            "• `/price [SYM]` — Live mark price, 24h change & stats\n"
            "• `/levels [SYM]` — Key technical levels (PDH/PDL, pivots, swings, 2Y S/R)\n"
            "• `/news [SYM]` — Macro calendar & economic sentiment\n"
            "• `/ask [question]` or `/chat [question]` — AI institutional trading assistant (uses MT5 live chart)\n\n"
            "🎯 **Signals & Strategy Confluence:**\n"
            "• `/signal [SYM]` — 9-Agent consensus (Trend, SMC, Indicators, AI, News, Gautam, VWAP, MTF-FVG, Asian)\n"
            "• `/asignal [SYM] [profile]` — Advanced SMC + CVD orderflow setup\n"
            "• `/quality [SYM]` — Comprehensive signal quality score & breakdown\n"
            "• `/matrix [SYM]` or `/heatmap` — Multi-Timeframe Trend & Volatility Confluence Matrix\n\n"
            "⚡ **Execution & Positions (Active Limit: 6):**\n"
            "• `/positions` — Open active trades, pending setups & real-time PnL\n"
            "• `/slots [auto|manual <1-6>]` — Active positions slot telemetry (1-6 positions max)\n"
            "• `/dashboard [start|stop]` — Real-Time Web Companion Dashboard\n"
            "• `/autotrade [on|off|scalp|btc|gold|both]` — Multi-symbol autotrading (Gold/BTC)\n"
            "• `/watchlist [btc|gold|both]` — View & switch active Gold/BTC autotrade watchlist\n"
            "• `/vstops [clear]` — Client-side hidden virtual stop loss manager\n"
            "• `/slippage` — Broker execution latency & realized slippage tracker\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL\n\n"
            "🏦 **Brokers & Execution (MT5 & Delta India):**\n"
            "• `/bridge [ping|test|deploy|on|off|dir]` — EA Bridge diagnostics & auto-deploy\n"
            "• `/switch [delta|live|<login>]` — Fast switcher between brokers & MT5 accounts\n"
            "• `/delta` — Delta Exchange India status, credentials & leverage controls\n"
            "• `/live [on|off]` — Quick MT5 Live trading switch\n"
            "• `/mt5` — Interactive MT5 dashboard & controls\n"
            "• `/mt5 add <login> <pass> <server> [alias]` — Save live MT5 trading account\n"
            "• `/mt5 accounts` — List registered MT5 accounts\n"
            "• `/balance` — View active broker balance, equity & margin\n"
            "• `/broker` — View active broker environment & telemetry\n\n"
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/cooldown [active|deactivate|reset]` — Anti-spam cooldown toggle & timer reset\n"
            "• `/spam [on|off]` — Spam trade mode (max 6 trades without cooldown)\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/kelly [SYM]` — Fractional Kelly lot sizing & portfolio VaR 99%\n"
            "• `/session` — End-of-session performance report & killzones\n"
            "• `/regime` — Market regime detector, auto-switcher & Sharpe calibrator\n"
            "• `/risk reset [daily|weekly|all]` — Reset loss limits & unblock trading\n"
            "• `/resetrisk [daily|weekly|all|restart]` — Reset risk & restart autotrade\n"
            "• `/leverage [1-200]` — Set maximum account leverage ceiling\n"
            "• `/lot [auto|manual|<size>]` — Auto dynamic risk lots or fixed manual lot size\n"
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
            "• `/learner [on|off]` — Autonomous learner veto toggle & unban\n"
            "• `/veto [on|off]` — Quick toggle for trade veto filtering\n"
            "• `/train [heavy|quick]` — Deep multi-asset AI & autonomous training\n"
            "• `/relearn [SYM|ALL]` — Force 5Y history retraining bootstrap\n"
            "• `/reset` — Clear conversation context\n"
            "• `/ask [question]` — Conversational assistant with Gemini AI\n\n"
            "_Filter: `/commands market`, `/commands signals`, `/commands execution`, `/commands risk`, `/commands mt5`, `/commands delta`, `/commands backtest`, `/commands ai`_"
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
    try:
        from realtime_prices import compute_levels, format_levels_message
        data = await asyncio.to_thread(compute_levels, sym)
        await update.message.reply_text(format_levels_message(data), parse_mode="Markdown")
    except Exception as e:
        logger.exception("levels failed")
        await update.message.reply_text(f"Levels error: `{e}`", parse_mode="Markdown")


async def price_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    info = await asyncio.to_thread(get_price_info, sym)
    await update.message.reply_text(format_price_string(info), parse_mode="Markdown")


async def chart_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Institutional MetaTrader 5 (MT5) Live Candlestick Chart for Bitcoin & Gold."""
    raw = (ctx.args[0] if ctx.args else "").lower().strip()
    from mt5_chart import (
        get_mt5_live_chart,
        format_mt5_chart_card,
        format_combined_mt5_chart_dashboard,
        build_chart_keyboard,
    )
    b = getattr(engine, "broker", None)

    if any(x in raw for x in ("btc", "bitcoin", "crypto")):
        chart_data = await asyncio.to_thread(get_mt5_live_chart, "BTCUSD", broker=b)
        text = format_mt5_chart_card(chart_data)
        kb = build_chart_keyboard("btc")
    elif any(x in raw for x in ("gold", "xau", "xauusd")):
        chart_data = await asyncio.to_thread(get_mt5_live_chart, "XAUUSD", broker=b)
        text = format_mt5_chart_card(chart_data)
        kb = build_chart_keyboard("gold")
    else:
        text = await asyncio.to_thread(format_combined_mt5_chart_dashboard, broker=b)
        kb = build_chart_keyboard("both")

    try:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await update.message.reply_text(text, reply_markup=kb)


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
        eq = float(getattr(risk_mgr.state, "equity", None) or getattr(engine.broker, "balance", config.INITIAL_EQUITY) or config.INITIAL_EQUITY)
        lev = float(risk_mgr.get_leverage())
        src = config.DEFAULT_DATA_SOURCE
        res = await asyncio.to_thread(run_signal, sym, equity=eq, leverage=lev, source=src)
        msg = format_signal_message(res)
        kb = None
        try:
            from mt5_chart import build_chart_keyboard, normalize_mt5_symbol
            chart_sym = "btc" if normalize_mt5_symbol(sym) == "BTCUSD" else "gold"
            kb = build_chart_keyboard(active=chart_sym)
        except Exception:
            kb = None
        try:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            await update.message.reply_text(msg.replace("*", "").replace("`", ""), reply_markup=kb)
        sig = res.get("signal")
        if sig and autotrade_active:
            ok, note = try_paper_execute(engine, sig, candle_price=res.get("price") or sig.get("entry"))
            tag = "✅" if ok else "⚠️"
            block_kb = None
            if not ok and "Autonomous Learner" in str(note):
                try:
                    from telegram_brain import build_learner_block_keyboard
                    block_kb = build_learner_block_keyboard(note)
                except Exception:
                    block_kb = None
            try:
                await update.message.reply_text(f"{tag} Autotrade: {note}", parse_mode="Markdown", reply_markup=block_kb)
            except Exception:
                await update.message.reply_text(f"{tag} Autotrade: {note}", reply_markup=block_kb)
    except Exception as e:
        logger.exception("signal_command failed")
        await update.message.reply_text(f"Signal error: `{type(e).__name__}: {e}`", parse_mode="Markdown")



async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = risk_mgr.state
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    auto_status = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
    summary = engine.get_portfolio_summary()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6))
    slots = engine.slots_available()
    symbols_str = ", ".join(getattr(config, "get_autotrade_symbols", lambda: config.AUTOTRADE_SYMBOLS)())
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
        f"• **Strategy Profile:** `{getattr(config, 'CURRENT_PROFILE', 'scalp').upper()}`\n"
        f"• **Watchlist:** `{symbols_str}`\n"
        f"• **Equity:** `${summary['equity']:,.2f}` | **Leverage:** `{summary['leverage']:.0f}x`\n"
        f"• **Active Positions:** `{summary['active_positions_count']}` | **Pending Setups:** `{summary['pending_setups_count']}`\n"
        f"• **Risk Mode:** {mode_icon} `{s.mode.upper()}` | **Today PnL:** `${s.daily_pnl:+,.2f}`\n"
        f"• **Broker:** `{bname}` (Mode: `{bmode}`){b_extra}\n"
    )
    try:
        from anti_spam import get_anti_spam_limiter
        _lim = get_anti_spam_limiter()
        msg += f"• **Anti-Spam:** Entry `{_lim.entry_cooldown_sec}s` | Loss `{_lim.loss_cooldown_sec}s` | Max/Sym `{_lim.max_positions_per_symbol}` (/cooldown)\n"
    except Exception:
        pass
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


async def cooldown_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View or configure anti-spam rate limiting and concurrency mode."""
    try:
        from anti_spam import get_anti_spam_limiter
        limiter = get_anti_spam_limiter()
        args = ctx.args or []
        if args:
            sub = args[0].lower().strip()
            # 1. Spam Trade Mode (max 6 trades without cooldown, control on/off)
            if sub in ("spam", "spamtrade", "burst", "burstmode"):
                action = (args[1].lower().strip() if len(args) > 1 else "toggle")
                if action in ("on", "enable", "1", "true", "active", "activate"):
                    limiter.set_spam_mode(True, max_trades=6, engine=engine)
                    await update.message.reply_text(
                        "⚡ **Spam Trade Mode ACTIVATED**\n"
                        "• **Trade Capacity:** Max `6` concurrent trades\n"
                        "• **Cooldowns:** All entry, loss, and global cooldowns **BYPASSED**\n"
                        "• Rapid-fire trade executions enabled up to 6 positions.\n"
                        "• Use `/spam off` or `/cooldown spam off` to revert to protected mode.",
                        parse_mode="Markdown",
                    )
                    return
                elif action in ("off", "disable", "0", "false", "deactivate", "inactive"):
                    limiter.set_spam_mode(False, engine=engine)
                    await update.message.reply_text(
                        "🔒 **Spam Trade Mode DEACTIVATED**\n"
                        "• Reverted to standard anti-spam rate limiting.\n"
                        "• Strict cooldowns active: max 4 positions total, max 2 per currency.",
                        parse_mode="Markdown",
                    )
                    return
                else:
                    new_val = not limiter.spam_trade_enabled
                    limiter.set_spam_mode(new_val, max_trades=6, engine=engine)
                    state_str = "ACTIVATED (Max 6, No Cooldown) ⚡" if new_val else "DEACTIVATED (Protected) 🔒"
                    await update.message.reply_text(
                        f"⚡ **Spam Trade Mode:** `{state_str}`\n"
                        f"Usage: `/spam on` (enable max 6) or `/spam off` (disable)",
                        parse_mode="Markdown",
                    )
                    return

            if sub in ("auto", "adaptive", "dynamic"):
                limiter.set_mode("auto")
                await update.message.reply_text(
                    "🤖 **Anti-Spam Mode set to AUTO (Adaptive)**\n"
                    "• Automatically adapts cooldowns to active profile (Scalp concession vs Swing tight).\n"
                    "• Enforces max 2 trades per currency (2 Gold, 2 BTC), max 4 total positions.",
                    parse_mode="Markdown",
                )
                return
            elif sub in ("on", "enable", "manual", "1", "true", "active", "activate", "activated"):
                limiter.set_mode("on")
                await update.message.reply_text(
                    "🛡️ **Anti-Spam Mode set to MANUAL ON (Cooldown ACTIVATED)**\n"
                    "• Strict configured cooldowns and price distance enforced.\n"
                    "• Enforces max 2 trades per currency (2 Gold, 2 BTC), max 4 total positions.",
                    parse_mode="Markdown",
                )
                return
            elif sub in ("off", "disable", "bypass", "0", "false", "deactivate", "deactivated", "inactive"):
                limiter.set_mode("off")
                await update.message.reply_text(
                    "⚠️ **Anti-Spam Mode set to OFF (Cooldown DEACTIVATED)**\n"
                    "• Cooldowns and price distance checks disabled.\n"
                    "• Safety limits still active: max 2 trades per currency, max 4 total positions.\n"
                    "• Tip: Run `/spam on` to also expand capacity to 6 trades.",
                    parse_mode="Markdown",
                )
                return
            elif sub in ("reset", "clear"):
                sym = args[1].upper() if len(args) > 1 else None
                limiter.reset(sym)
                target = f"for `{sym}`" if sym else "globally"
                await update.message.reply_text(f"✅ Anti-spam cooldowns and loss streaks have been reset {target}.", parse_mode="Markdown")
                return

        report = limiter.get_status_report(engine=engine)
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Anti-spam command error: {e}")


async def spam_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Control spamming trade mode (max 6 trades without cooldown)."""
    from anti_spam import get_anti_spam_limiter
    limiter = get_anti_spam_limiter()
    args = ctx.args or []
    action = args[0].lower().strip() if args else "toggle"
    if action in ("on", "enable", "1", "true", "active", "activate"):
        limiter.set_spam_mode(True, max_trades=6, engine=engine)
        await update.message.reply_text(
            "⚡ **Spam Trade Mode ACTIVATED**\n"
            "• **Trade Capacity:** Max `6` concurrent trades\n"
            "• **Cooldowns:** All entry, loss, and global cooldowns **BYPASSED**\n"
            "• Rapid-fire trade executions enabled up to 6 positions.\n"
            "• Use `/spam off` to revert to protected mode.",
            parse_mode="Markdown",
        )
    elif action in ("off", "disable", "0", "false", "deactivate", "inactive"):
        limiter.set_spam_mode(False, engine=engine)
        await update.message.reply_text(
            "🔒 **Spam Trade Mode DEACTIVATED**\n"
            "• Reverted to standard anti-spam rate limiting.\n"
            "• Strict cooldowns active: max 4 positions total, max 2 per currency.",
            parse_mode="Markdown",
        )
    elif action in ("status", "info"):
        st = "⚡ ACTIVATED (Max 6, No Cooldown)" if limiter.spam_trade_enabled else "🔒 DEACTIVATED (Protected)"
        await update.message.reply_text(f"⚡ **Spam Trade Mode Status:** `{st}`\nUsage: `/spam on` or `/spam off`", parse_mode="Markdown")
    else:
        new_val = not limiter.spam_trade_enabled
        limiter.set_spam_mode(new_val, max_trades=6, engine=engine)
        state_str = "ACTIVATED (Max 6, No Cooldown) ⚡" if new_val else "DEACTIVATED (Protected) 🔒"
        await update.message.reply_text(
            f"⚡ **Spam Trade Mode:** `{state_str}`\n"
            f"Usage: `/spam on` (enable max 6) or `/spam off` (disable)",
            parse_mode="Markdown",
        )


def reset_risk_limits(scope: str = "all", restart_autotrade: bool = False, new_equity: Optional[float] = None) -> str:
    """
    Manually reset daily or weekly risk management limits, loss streaks, circuit breakers,
    and optionally restart automated trading.
    """
    global autotrade_active
    s_scope = (scope or "all").lower().strip()
    if s_scope in ("day", "daily", "d"):
        risk_mgr.reset_daily_risk()
        scope_title = "DAILY"
        try:
            from risk_tiers import RiskTiers
            RiskTiers().reset_daily()
        except Exception:
            pass
        try:
            from telegram_feature_pack import _get_pack
            _get_pack().risk_tiers.reset_daily()
        except Exception:
            pass
        try:
            from risk_advanced import RiskAdvanced
            RiskAdvanced().reset_daily()
        except Exception:
            pass
    elif s_scope in ("week", "weekly", "w"):
        risk_mgr.reset_weekly_risk()
        scope_title = "WEEKLY"
        try:
            from risk_tiers import RiskTiers
            RiskTiers().reset_weekly()
        except Exception:
            pass
        try:
            from telegram_feature_pack import _get_pack
            _get_pack().risk_tiers.reset_weekly()
        except Exception:
            pass
        try:
            from risk_advanced import RiskAdvanced
            RiskAdvanced().reset_daily()
        except Exception:
            pass
    else:
        risk_mgr.reset_all_risk(new_equity=new_equity)
        scope_title = "ALL"
        try:
            from risk_tiers import RiskTiers
            RiskTiers().reset_all()
        except Exception:
            pass
        try:
            from telegram_feature_pack import _get_pack
            _get_pack().risk_tiers.reset_all()
        except Exception:
            pass
        try:
            from risk_advanced import RiskAdvanced
            RiskAdvanced().reset_all(equity=risk_mgr.state.equity)
        except Exception:
            pass

    # Clear engine broker halt state
    engine.reset_halt()

    # Clear KillSwitch
    try:
        from kill_switch import KillSwitch
        KillSwitch().deactivate()
    except Exception:
        pass
    try:
        from advanced_integration import AdvancedEngine
        AdvancedEngine().deactivate_kill()
    except Exception:
        pass

    # Synchronize risk_advanced_state.json equity & peak so AdvancedEngine drawdown doesn't re-trip
    try:
        from risk_advanced import RiskAdvanced
        ra = RiskAdvanced()
        ra.equity = float(risk_mgr.state.equity)
        ra.equity_peak = float(risk_mgr.state.equity)
        ra._save()
    except Exception:
        pass

    try:
        from anti_spam import get_anti_spam_limiter
        get_anti_spam_limiter().reset()
    except Exception:
        pass

    if restart_autotrade:
        autotrade_active = True
        auto_note = "• **Autotrade:** 🟢 ACTIVE (monitoring resumed)"
    else:
        st = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
        auto_note = f"• **Autotrade:** {st} (Use `/autotrade on` or `/risk restart` to resume)"

    s = risk_mgr.state
    can_trade, can_why = risk_mgr.can_trade()
    trade_status = "🟢 UNBLOCKED" if can_trade else f"⚠️ PAUSED ({can_why})"
    rf_val = getattr(s, "rf", 1.0)

    lines = [
        f"🔄 **Risk Management Reset — {scope_title}**",
        f"• **Trading Status:** {trade_status}",
        f"• **Account Equity:** `${s.equity:,.2f}` | **Peak:** `${s.peak_equity:,.2f}`",
        f"• **Daily PnL:** `${s.daily_pnl:+,.2f}` | **Weekly PnL:** `${s.weekly_pnl:+,.2f}`",
        f"• **Consecutive Losses:** `{s.consecutive_losses}` (Streak reset)",
        f"• **Risk Mode:** 🟢 `{s.mode.upper()}` | **Risk Factor (RF):** `{rf_val:.2f}`",
        "• **Circuit Breakers:** 🟢 Cleared (Engine halt lifted, Kill switch deactivated)",
        auto_note,
    ]
    return "\n".join(lines)


async def resetrisk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Direct alias to reset risk limits: /resetrisk [daily|weekly|all] [restart]"""
    args = ctx.args or []
    args_lower = [a.lower() for a in args]
    is_restart = any(a in ("restart", "start") for a in args_lower)
    if "daily" in args_lower:
        scope = "daily"
    elif "weekly" in args_lower:
        scope = "weekly"
    else:
        scope = "all"
    new_eq = None
    for a in args:
        try:
            val = float(a)
            if val > 0:
                new_eq = val
                break
        except ValueError:
            pass
    msg = reset_risk_limits(scope=scope, restart_autotrade=is_restart, new_equity=new_eq)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def risk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    args_lower = [a.lower() for a in args]

    # Check for reset / restart subcommands
    has_reset = any(a in ("reset", "clear", "restart", "start") for a in args_lower)
    is_restart = any(a in ("restart", "start") for a in args_lower)

    if has_reset or (args and args[0].lower() in ("daily", "weekly", "all")):
        if "daily" in args_lower:
            scope = "daily"
        elif "weekly" in args_lower:
            scope = "weekly"
        else:
            scope = "all"

        new_eq = None
        for a in args:
            try:
                val = float(a)
                if val > 0:
                    new_eq = val
                    break
            except ValueError:
                pass

        msg = reset_risk_limits(scope=scope, restart_autotrade=is_restart, new_equity=new_eq)
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # Normal risk report when no reset argument is provided
    s = risk_mgr.state
    risk_pct, reason = risk_mgr.current_risk_pct()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6))
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    can, why = risk_mgr.can_trade()
    trade_st = "🟢 Ready" if can else f"⚠️ Blocked ({why})"
    msg = (
        f"🛡️ **Institutional Risk Controls**\n"
        f"• **Risk/Trade:** `{risk_pct:.2f}%` ({reason})\n"
        f"• **Trading Status:** {trade_st}\n"
        f"• **Max Concurrent Trades:** `{max_trades}` active slots\n"
        f"• **Account Equity:** `${s.equity:,.2f}` | **Mode:** {mode_icon} `{s.mode.upper()}`\n"
        f"• **Today PnL:** `${s.daily_pnl:+,.2f}` | **Week PnL:** `${s.weekly_pnl:+,.2f}`\n"
        f"• **Consecutive Losses:** `{s.consecutive_losses}`\n"
        f"• **Max Daily Loss:** `{risk_mgr.config.max_daily_loss_pct:.1f}%` | **Weekly:** `{risk_mgr.config.max_weekly_loss_pct:.1f}%`\n"
        f"• **Margin Utilization Cap:** `{risk_mgr.config.max_margin_utilization_pct:.0f}%`\n"
        f"• {_safe_summary()}\n\n"
        f"💡 _Commands: `/risk reset [daily|weekly|all]` | `/risk restart` | `/resetrisk`_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


def _safe_summary() -> str:
    try:
        return risk_mgr.summary()
    except Exception:
        return ""


async def leverage_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        curr_lev = risk_mgr.get_leverage()
        b_lev_str = ""
        auto_str = ""
        if hasattr(engine, "broker") and engine.broker:
            if hasattr(engine.broker, "get_leverage"):
                try:
                    b_lev = engine.broker.get_leverage()
                    b_lev_str = f"\n• **Broker Leverage:** `{b_lev:.0f}x`"
                except Exception:
                    pass
            if hasattr(engine.broker, "auto_leverage"):
                auto_status = "🟢 ON (Dynamic Scaling up to 200x)" if engine.broker.auto_leverage else "⚪ OFF (Manual)"
                auto_str = f"\n• **Auto-Leverage Engine:** {auto_status}"

        await update.message.reply_text(
            f"⚡ **Leverage Management (Max 200x)**\n"
            f"• Current leverage: `{curr_lev:.0f}x`\n"
            f"• **Active Risk Manager Leverage:** `{curr_lev:.0f}x`{b_lev_str}{auto_str}\n"
            f"• **Maximum Permitted Leverage:** `200x`\n\n"
            f"✏️ **Set Leverage Manually:**\n"
            f"• `/leverage <1-200>` — e.g. `/leverage 50` or `/leverage 200`\n\n"
            f"🤖 **Auto-Leverage Dynamic Scaling:**\n"
            f"• `/leverage auto on` — Automatically optimize leverage per trade (up to 200x)\n"
            f"• `/leverage auto off` — Revert to fixed manual leverage",
            parse_mode="Markdown",
        )
        return

    sub = args[0].strip().lower()
    if sub in ("auto", "dynamic"):
        sub_arg = args[1].lower() if len(args) > 1 else "on"
        enable_auto = sub_arg not in ("off", "false", "0", "disable")
        broker_synced = False
        if hasattr(engine, "broker") and engine.broker and hasattr(engine.broker, "auto_leverage"):
            engine.broker.auto_leverage = enable_auto
            broker_synced = True
        status_txt = "ACTIVATED" if enable_auto else "DEACTIVATED"
        status_icon = "🟢" if enable_auto else "⚪"
        sync_txt = " and synchronized with active broker" if broker_synced else ""
        await update.message.reply_text(
            f"{status_icon} **Auto-Leverage Dynamic Engine {status_txt}**{sync_txt}\n"
            f"• **Mode:** {'Dynamic auto-scaling (Max 200x)' if enable_auto else 'Fixed manual leverage'}\n"
            f"• **Safety Barrier:** Protects against premature liquidation before SL hits\n"
            f"• **Target Cap:** `200x`",
            parse_mode="Markdown",
        )
        return

    try:
        raw_val = float(sub.replace("x", ""))
        ok, msg = risk_mgr.set_leverage(raw_val)
        if not ok:
            await update.message.reply_text(f"❌ {msg}")
            return
        broker_msg = ""
        if hasattr(engine, "broker") and engine.broker and hasattr(engine.broker, "set_leverage"):
            try:
                sym = getattr(config, "DEFAULT_SYMBOL", "BTCUSD")
                bok, bmsg = engine.broker.set_leverage(sym, raw_val)
                if bok:
                    broker_msg = f"\n• **Broker Synced:** {bmsg}"
                else:
                    broker_msg = f"\n• **Broker Note:** {bmsg}"
            except Exception as b_err:
                broker_msg = f"\n• **Broker Sync Note:** {b_err}"

        margin_pct = (1.0 / raw_val) * 100.0 if raw_val > 0 else 100.0
        await update.message.reply_text(
            f"✅ Leverage set to `{risk_mgr.get_leverage():.0f}x`\n"
            f"• **Active Leverage:** `{risk_mgr.get_leverage():.0f}x`\n"
            f"• **Required Initial Margin:** `{margin_pct:.2f}%`{broker_msg}\n"
            f"• **Configured Limit:** Up to `200x`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"Invalid leverage '{args[0]}': {e}. Usage: `/leverage <1-200>` or `/leverage auto on|off`")


def build_lot_dashboard() -> Tuple[str, InlineKeyboardMarkup]:
    """Construct Markdown text and inline keyboard for the Lot Size dashboard."""
    mode, cur_lot = risk_mgr.get_lot_mode()
    eq = getattr(risk_mgr.state, "equity", 10000.0)
    risk_pct, _ = risk_mgr.current_risk_pct()
    mode_icon = "🟢" if mode == "auto" else "✏️"
    mode_str = "AUTO (Dynamic Risk-Based)" if mode == "auto" else "MANUAL (Fixed Lot Size)"

    sample_gold = risk_mgr.calc_lots(eq * (risk_pct / 100.0), 10.0)[0]
    sample_btc = risk_mgr.calc_lots(eq * (risk_pct / 100.0), 500.0)[0]

    overrides = getattr(risk_mgr.config, "manual_lot_sizes", {})
    override_lines = []
    if overrides and isinstance(overrides, dict):
        for sym, l in sorted(overrides.items()):
            override_lines.append(f"  • `{sym}`: `{l:.4f}` lots")
    override_text = ("\n• **Symbol Overrides:**\n" + "\n".join(override_lines)) if override_lines else ""

    text = (
        f"📦 **Lot Size Management (Auto / Manual)**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Active Mode:** {mode_icon} **{mode_str}**\n"
        f"• **Current Manual Lot:** `{cur_lot:.4f}` lots{override_text}\n"
        f"• **Dynamic Auto Est. (Equity ${eq:,.2f}, Risk {risk_pct:.2f}%):**\n"
        f"  • Gold (XAUUSD): ~`{sample_gold:.2f}` lots (at $10 SL)\n"
        f"  • Bitcoin (BTC): ~`{sample_btc:.3f}` lots (at $500 SL)\n"
        f"• **Instrument Bounds:** Min `0.01` | Step `0.01` | Max `{risk_mgr.config.max_lot:.1f}`\n\n"
        f"✏️ **Set Manual Lot:**\n"
        f"• `/lot manual <size>` — e.g. `/lot manual 0.05`\n"
        f"• `/lot manual <size> <SYM>` — e.g. `/lot manual 0.02 XAUUSD`\n"
        f"• `/lot <size> [SYM]` — e.g. `/lot 0.05`\n\n"
        f"🤖 **Switch to Dynamic Auto Lots:**\n"
        f"• `/lot auto` — Auto-calculate lots per trade from risk % and SL distance"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Switch to Auto Lots", callback_data="lot:auto"),
            InlineKeyboardButton("🔄 Refresh", callback_data="lot:refresh"),
        ],
        [
            InlineKeyboardButton("✏️ Manual 0.01", callback_data="lot:manual:0.01"),
            InlineKeyboardButton("✏️ Manual 0.02", callback_data="lot:manual:0.02"),
            InlineKeyboardButton("✏️ Manual 0.05", callback_data="lot:manual:0.05"),
            InlineKeyboardButton("✏️ Manual 0.10", callback_data="lot:manual:0.10"),
        ],
    ])
    return text, markup


async def lot_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View and toggle Auto (dynamic risk-based) vs. Manual (fixed) lot size selection."""
    args = ctx.args or []
    if not args:
        text, markup = build_lot_dashboard()
        try:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)
        return

    sub = args[0].strip().lower()
    if sub in ("auto", "dynamic", "risk"):
        risk_mgr.set_auto_lot()
        config.set_lot_size_mode("auto")
        await update.message.reply_text(
            "🟢 **Auto Lot Sizing Activated**\n"
            "• Mode: `AUTO (Dynamic Risk-Based)`\n"
            "• Orders will now be sized dynamically per trade based on account equity, risk %, and stop loss distance.",
            parse_mode="Markdown",
        )
        return

    if sub in ("manual", "fixed", "man"):
        if len(args) > 1:
            try:
                val = float(args[1])
                target_sym = args[2].upper() if len(args) > 2 else None
                ok, msg = risk_mgr.set_manual_lot(val, symbol=target_sym)
                if not ok:
                    await update.message.reply_text(f"❌ {msg}", parse_mode="Markdown")
                    return
                config.set_manual_lot_size(val, symbol=target_sym)
                config.set_lot_size_mode("manual")
                sym_str = f" for `{target_sym}`" if target_sym else " globally"
                await update.message.reply_text(
                    f"✏️ **Manual Lot Sizing Activated**\n"
                    f"• **Fixed Lot Size:** `{val:.4f}` lots{sym_str}\n"
                    f"• Mode: `MANUAL`\n"
                    f"• Every new trade will execute with exactly `{val:.4f}` lots.",
                    parse_mode="Markdown",
                )
                return
            except ValueError:
                await update.message.reply_text(f"❌ Invalid lot size '{args[1]}'. Example: `/lot manual 0.05`", parse_mode="Markdown")
                return
        else:
            risk_mgr.set_lot_mode("manual")
            config.set_lot_size_mode("manual")
            cur = risk_mgr.get_manual_lot()
            await update.message.reply_text(
                f"✏️ **Manual Lot Sizing Activated**\n"
                f"• **Fixed Lot Size:** `{cur:.4f}` lots\n"
                f"• Mode: `MANUAL`\n"
                f"• To change size: `/lot manual <size>` (e.g. `/lot manual 0.05`)",
                parse_mode="Markdown",
            )
            return

    # Direct number input: `/lot 0.05` or `/lot 0.02 XAUUSD`
    try:
        val = float(sub)
        target_sym = args[1].upper() if len(args) > 1 else None
        ok, msg = risk_mgr.set_manual_lot(val, symbol=target_sym)
        if not ok:
            await update.message.reply_text(f"❌ {msg}", parse_mode="Markdown")
            return
        config.set_manual_lot_size(val, symbol=target_sym)
        config.set_lot_size_mode("manual")
        sym_str = f" for `{target_sym}`" if target_sym else " globally"
        await update.message.reply_text(
            f"✏️ **Manual Lot Sizing Activated**\n"
            f"• **Fixed Lot Size:** `{val:.4f}` lots{sym_str}\n"
            f"• Mode: `MANUAL`\n"
            f"• All orders will be placed with `{val:.4f}` lots.",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.message.reply_text(
            f"❌ Unknown lot command `{args[0]}`.\n"
            f"Usage:\n"
            f"• `/lot` — View lot status & quick settings\n"
            f"• `/lot auto` — Switch to dynamic auto lot sizing\n"
            f"• `/lot manual <size> [SYM]` — Set fixed manual lot size (e.g. `/lot manual 0.05`)\n"
            f"• `/lot <size> [SYM]` — Direct set (e.g. `/lot 0.02 XAUUSD`)",
            parse_mode="Markdown",
        )


async def positions_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        args = [a.lower() for a in (ctx.args or [])]
        if args:
            if args[0] in ("auto", "dynamic", "adaptive"):
                ok, msg = engine.set_slots_mode("auto")
                await safe_reply(update, f"⚙️ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
                return
            elif args[0] in ("manual", "man", "fixed"):
                cnt = int(args[1]) if len(args) > 1 and args[1].isdigit() else int(getattr(engine, "manual_slots", 6))
                ok, msg = engine.set_slots_mode("manual", cnt)
                await safe_reply(update, f"⚙️ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
                return
            elif args[0].isdigit():
                cnt = int(args[0])
                ok, msg = engine.set_slots_mode("manual", cnt)
                await safe_reply(update, f"⚙️ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
                return
            elif args[0] in ("sync", "refresh", "reconcile", "wallet", "reload"):
                if hasattr(engine, "reconcile_positions"):
                    try:
                        engine.reconcile_positions()
                    except Exception as rx:
                        logger.debug("[positions_command] reconcile error: %s", rx)

        # Always ensure positions are reconciled with MT5/broker before displaying
        if hasattr(engine, "reconcile_positions"):
            try:
                engine.reconcile_positions()
            except Exception as rx:
                logger.debug("[positions_command] auto reconcile note: %s", rx)

        if hasattr(engine, "format_positions_detail"):
            msg = engine.format_positions_detail()
        else:
            summary = engine.get_portfolio_summary()
            msg = f"📋 Positions open={summary.get('active_positions_count', 0)}"
        await safe_reply(update, msg, parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"Positions error: {e}", parse_mode=None)


async def slots_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manage active position slots: /slots, /slots auto, /slots manual <1-6>, /slots <1-6>"""
    try:
        args = [a.lower() for a in (ctx.args or [])]
        if not args:
            mode = getattr(engine, "slots_mode", "auto")
            cur_slots = int(getattr(engine, "max_open_trades", 6))
            free = engine.slots_available()
            msg = (
                f"🎰 *Active Positions Slots Telemetry*\n"
                f"• *Current Limit:* `{cur_slots}/6 positions max`\n"
                f"• *Mode:* `{mode.upper()}` (Manual / Automatic)\n"
                f"• *Open Now:* `{len(engine.active_positions)} active | {free} slots free`\n\n"
                f"*Commands:*\n"
                f"• `/slots auto` — dynamic allocation (1..6) based on equity & margin\n"
                f"• `/slots manual <1-6>` — set fixed slot count (e.g. `/slots 3` or `/slots manual 4`)"
            )
            await safe_reply(update, msg, parse_mode="Markdown")
            return

        sub = args[0]
        if sub in ("auto", "dynamic", "adaptive"):
            ok, msg = engine.set_slots_mode("auto")
            await safe_reply(update, f"✅ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
        elif sub in ("manual", "man", "fixed"):
            cnt = int(args[1]) if len(args) > 1 and args[1].isdigit() else int(getattr(engine, "manual_slots", 6))
            ok, msg = engine.set_slots_mode("manual", cnt)
            await safe_reply(update, f"✅ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
        elif sub.isdigit():
            cnt = int(sub)
            ok, msg = engine.set_slots_mode("manual", cnt)
            await safe_reply(update, f"✅ {msg}\n\n" + engine.format_positions_detail(), parse_mode="Markdown")
        else:
            await safe_reply(update, "Usage: `/slots [auto | manual <1-6> | <1-6>]`", parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"Slots error: {e}", parse_mode=None)


async def matrix_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Multi-Timeframe Trend & Volatility Heatmap Matrix (/matrix [SYM] or /heatmap [SYM])."""
    try:
        from mtf_matrix import render_telegram_heatmap, render_telegram_multi_matrix
        arg = (ctx.args[0] if ctx.args else "").strip().upper()
        if not arg or arg in ("ALL", "BOTH", "MULTI"):
            msg = await asyncio.to_thread(render_telegram_multi_matrix, ["XAUUSD", "BTCUSDT"])
        else:
            sym = "BTCUSDT" if arg in ("BTC", "BTCUSD", "BITCOIN") else ("XAUUSD" if arg in ("XAU", "GOLD") else arg)
            msg = await asyncio.to_thread(render_telegram_heatmap, sym)
        await safe_reply(update, msg, parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"⚠️ Heatmap matrix error: {e}", parse_mode=None)


async def dashboard_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Lightweight Real-Time Web Dashboard / Local UI Companion (/dashboard [start|stop])."""
    try:
        from ops_dashboard import start_dashboard_background, stop_dashboard_background
        args = [a.lower() for a in (ctx.args or [])]
        sub = args[0] if args else "status"

        host = getattr(config, "DASHBOARD_HOST", "127.0.0.1")
        port = getattr(config, "DASHBOARD_PORT", 8765)
        status_file = getattr(config, "STATUS_FILE", ".runtime/bot_status.json")
        token = getattr(config, "DASHBOARD_TOKEN", "")

        if sub in ("stop", "kill"):
            stop_dashboard_background()
            await safe_reply(update, "🛑 Web companion dashboard stopped.", parse_mode=None)
            return

        srv, url = start_dashboard_background(
            host=host, port=port, status_file=status_file, token=token, engine=engine
        )
        msg = (
            f"🌐 *Nimble-Darwin Web Companion Dashboard*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Live URL:* `{url}`\n"
            f"• *Host / Port:* `{host}:{port}`\n"
            f"• *Status:* 🟢 Active & Serving\n"
            f"• *Features:* Live telemetry, interactive slots control (1..6), active positions, pending setups, MTF matrix visualizer\n\n"
            f"Open `{url}` in your local browser to access real-time companion controls."
        )
        await safe_reply(update, msg, parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"⚠️ Dashboard error: {e}", parse_mode=None)


async def close_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = (ctx.args[0] if ctx.args else "").strip().upper()
    if not arg or arg in ("ALL", "*"):
        if not engine.active_positions:
            await safe_reply(update, "ℹ️ No open positions to close.", parse_mode=None)
            return
        closed = []
        if hasattr(engine, "close_all_positions"):
            closed = engine.close_all_positions(reason="Manual close all")
        else:
            for sym in list(engine.active_positions.keys()):
                p = engine.close_position(sym, reason="Manual close all")
                if p:
                    closed.append(p)
        lines = [f"✅ *Closed {len(closed)} position(s):*"]
        for p in closed:
            pnl = float(getattr(p, 'realized_pnl', 0) or 0)
            pnl_icon = "📈" if pnl >= 0 else "📉"
            lines.append(f"• `{p.symbol}` PnL: {pnl_icon} `${pnl:+,.2f}`")
        await safe_reply(update, "\n".join(lines), parse_mode="Markdown")
        return
    sym = arg if arg not in ("BTC", "BTCUSD") else "BTCUSDT"
    closed = engine.close_position(sym, reason="Manual close")
    if closed:
        pnl = float(getattr(closed, 'realized_pnl', 0) or 0)
        pnl_icon = "📈" if pnl >= 0 else "📉"
        await safe_reply(update, f"✅ Closed `{sym}` — Realized PnL: {pnl_icon} `${pnl:+,.2f}`", parse_mode="Markdown")
    else:
        await safe_reply(update, f"⚠️ No active position found for `{sym}`. Use `/close all`", parse_mode="Markdown")


async def closed_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recently closed trades and realized PnL history."""
    try:
        from trade_journal import TradeJournal
        journal = TradeJournal()
        closes = journal.closed_summary(8)
    except Exception:
        closes = []

    lines = ["📜 *Recently Closed Trades History*"]
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
        lines.append(f"\n💰 *Total Realized PnL:* `${total_pnl:+,.2f}`")
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
        lines.append(f"\n💰 *Total Realized PnL:* `${total_pnl:+,.2f}`")
    else:
        lines.append("ℹ️ No closed trades on record yet. Completed trades will appear here.")

    await safe_reply(update, "\n".join(lines), parse_mode="Markdown")


async def ask_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Direct query to Gemini conversational trading assistant using MT5 Live Chart data."""
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text("Usage: `/ask <your question>` or `/chat <your question>`\nExample: `/chat btc chart` or `/chat gold analysis`", parse_mode="Markdown")
        return

    # Check for direct chart query in chat
    query_lower = query.lower()
    if any(w in query_lower for w in ["chart", "charts", "candle", "candles", "graph", "plot"]):
        try:
            from mt5_chart import (
                get_mt5_live_chart,
                format_mt5_chart_card,
                format_combined_mt5_chart_dashboard,
                build_chart_keyboard,
            )
            b = getattr(engine, "broker", None)
            if any(w in query_lower for w in ["btc", "bitcoin", "crypto"]):
                chart_data = await asyncio.to_thread(get_mt5_live_chart, "BTCUSD", broker=b)
                card = format_mt5_chart_card(chart_data)
                kb = build_chart_keyboard("btc")
                await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
                return
            elif any(w in query_lower for w in ["gold", "xau", "xauusd"]):
                chart_data = await asyncio.to_thread(get_mt5_live_chart, "XAUUSD", broker=b)
                card = format_mt5_chart_card(chart_data)
                kb = build_chart_keyboard("gold")
                await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
                return
            elif "both" in query_lower or query_lower.strip() in ("chart", "charts"):
                card = await asyncio.to_thread(format_combined_mt5_chart_dashboard, broker=b)
                kb = build_chart_keyboard("both")
                await update.message.reply_text(card, parse_mode="Markdown", reply_markup=kb)
                return
        except Exception as _ce:
            logger.debug("Chat chart shortcut notice: %s", _ce)

    if hasattr(ctx, "bot") and hasattr(ctx.bot, "send_chat_action"):
        try:
            await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        except Exception:
            pass

    market_context = ""
    try:
        from mt5_chart import get_mt5_combined_market_context
        market_context = await asyncio.to_thread(get_mt5_combined_market_context, broker=getattr(engine, "broker", None))
    except Exception:
        try:
            info = await asyncio.to_thread(get_price_info, config.DEFAULT_SYMBOL)
            if info.get("ok"):
                market_context = f"{config.DEFAULT_SYMBOL} mark={info.get('mark')} change={info.get('change_pct')}%"
        except Exception:
            pass

    reply = await asyncio.to_thread(gemini.ask, chat_id=update.effective_chat.id, user_text=query, market_context=market_context)
    try:
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(reply.replace("*", "").replace("`", ""))


async def unknown_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Friendly fallback when an unrecognized command is typed."""
    text = getattr(update.message, "text", "") if (getattr(update, "message", None) and update.message.text) else "/command"
    cmd = text.split()[0] if text else "/command"
    await safe_reply(
        update,
        f"❓ Unknown command: `{cmd}`\n\n"
        "Use `/commands` or `/help` to view all available commands.",
        parse_mode="Markdown",
    )


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = (ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL)
    try:
        cancelled = engine.cancel_setup(sym, reason="Manual cancel")
        if cancelled:
            await safe_reply(update, f"✅ Cancelled pending setup for `{sym}`", parse_mode="Markdown")
        else:
            await safe_reply(update, f"ℹ️ No pending setup for `{sym}`", parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"⚠️ Cancel failed: `{e}`", parse_mode="Markdown")


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


def get_gold_market_status() -> tuple:
    """Check if gold (metals/forex) market is currently open or weekend closed."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # Monday=0, Sunday=6
    hour = now.hour
    if weekday == 4 and hour >= 21:
        return False, "🔴 Closed for weekend (reopens Sun 22:00 UTC)"
    if weekday == 5:
        return False, "🔴 Closed for weekend (reopens Sun 22:00 UTC)"
    if weekday == 6 and hour < 22:
        return False, "🔴 Closed for weekend (reopens Sun 22:00 UTC)"
    return True, "🟢 Open (24/5 Global Metals Session)"


def build_watchlist_dashboard() -> tuple:
    """Generate Markdown text and inline keyboard for the Watchlist Dashboard."""
    active_symbols = getattr(config, "get_autotrade_symbols", lambda: config.AUTOTRADE_SYMBOLS)()
    auto_status = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
    broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
    profile = getattr(config, "CURRENT_PROFILE", "scalp").upper()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6))
    open_count = len(getattr(engine, "active_positions", {}))
    slots = getattr(engine, "slots_available", lambda: max(0, max_trades - open_count))()

    btc_active = "BTCUSDT" in active_symbols
    btc_icon = "🟢 ACTIVE" if (autotrade_active and btc_active) else ("🟡 SELECTED (Paused)" if btc_active else "⚪ STANDBY")
    btc_price = get_price_info("BTCUSDT")
    btc_mark = float(btc_price.get("mark") or btc_price.get("close") or 0.0)
    btc_chg = float(btc_price.get("change_pct") or 0.0)
    btc_rec = btc_price.get("recommendation") or "NEUTRAL"

    gold_active = "XAUUSD" in active_symbols
    gold_icon = "🟢 ACTIVE" if (autotrade_active and gold_active) else ("🟡 SELECTED (Paused)" if gold_active else "⚪ STANDBY")
    gold_price = get_price_info("XAUUSD")
    gold_mark = float(gold_price.get("mark") or gold_price.get("close") or 0.0)
    gold_chg = float(gold_price.get("change_pct") or 0.0)
    gold_rec = gold_price.get("recommendation") or "NEUTRAL"
    gold_open, gold_sess_desc = get_gold_market_status()

    if btc_active and gold_active:
        sel_label = "⚡ Both (BTC + Gold)"
    elif btc_active:
        sel_label = "🪙 Bitcoin Only"
    elif gold_active:
        sel_label = "🟡 Gold Only"
    else:
        sel_label = "None (Paused)"

    text = (
        f"📋 **Gold & Bitcoin Trading Watchlist**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 **Bitcoin (`BTCUSDT`)**\n"
        f"• **Autotrade:** {btc_icon}\n"
        f"• **Mark Price:** `${btc_mark:,.2f}` | **24h:** `{btc_chg:+.2f}%`\n"
        f"• **Bias/Confluence:** `{btc_rec}`\n"
        f"• **Market Session:** 🟢 24/7 Continuous Crypto\n\n"
        f"🟡 **Gold (`XAUUSD`)**\n"
        f"• **Autotrade:** {gold_icon}\n"
        f"• **Mark Price:** `${gold_mark:,.2f}` | **24h:** `{gold_chg:+.2f}%`\n"
        f"• **Bias/Confluence:** `{gold_rec}`\n"
        f"• **Market Session:** {gold_sess_desc}\n\n"
        f"⚙️ **Autotrade Engine:**\n"
        f"• **Monitoring Status:** {auto_status}\n"
        f"• **Active Selection:** `{sel_label}`\n"
        f"• **Execution Mode:** `{broker_mode}` | **Profile:** `{profile}`\n"
        f"• **Lot Sizing:** `{'AUTO (Dynamic)' if getattr(risk_mgr.config, 'lot_mode', 'auto') == 'auto' else f'MANUAL ({risk_mgr.get_manual_lot():.2f} lots)'}`\n"
        f"• **Slots:** `{open_count}/{max_trades}` active (`{slots}` free)\n"
    )

    learner_active = True
    try:
        from autonomous_learner import get_brain
        _b = get_brain(auto_bootstrap=False)
        learner_active = getattr(_b.knowledge, "enabled", True)
        l_icon = "🟢 ACTIVE" if learner_active else "🔴 PAUSED (/learner on)"
        b_cnt = len(_b.knowledge.banned_hours)
        b_info = f" ({b_cnt} banned hours)" if b_cnt and learner_active else ""
        text += f"• **Learner Veto:** {l_icon}{b_info}\n"
    except Exception:
        pass

    text += (
        f"\n💡 **Switch Selection:**\n"
        f"• `/watchlist btc` — Trade BTC only\n"
        f"• `/watchlist gold` — Trade Gold only\n"
        f"• `/watchlist both` — Trade both BTC + Gold\n"
        f"• `/autotrade on [btc|gold|both]`\n"
        f"• `/learner on|off` — Toggle Autonomous Learner veto\n"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪙 BTC Only", callback_data="wl:btc"),
            InlineKeyboardButton("🟡 Gold Only", callback_data="wl:gold"),
            InlineKeyboardButton("⚡ Both", callback_data="wl:both"),
        ],
        [
            InlineKeyboardButton("🟢 Start Autotrade", callback_data="auto:on"),
            InlineKeyboardButton("⚪ Pause", callback_data="auto:off"),
            InlineKeyboardButton("⚡ Scalp Mode", callback_data="auto:scalp"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Watchlist", callback_data="wl:refresh"),
            InlineKeyboardButton("📊 Positions", callback_data="wl:positions"),
            InlineKeyboardButton("📦 Lot Size", callback_data="lot:info"),
        ],
        [
            InlineKeyboardButton("🔴 Pause Veto" if learner_active else "🟢 Enable Veto", callback_data="learner:toggle"),
            InlineKeyboardButton("📜 Rules", callback_data="learner:rules"),
            InlineKeyboardButton("🔓 Clear Bans", callback_data="learner:clearhours"),
        ],
    ])
    return text, markup


async def watchlist_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View live Watchlist Dashboard or switch active symbol selection (/watchlist [btc|gold|both])."""
    args = [a.lower() for a in (ctx.args or [])]
    arg = args[0] if args else ""
    if arg in ("btc", "bitcoin", "btcusdt", "btcusd", "xbt"):
        config.set_autotrade_symbols(["BTCUSDT"])
        st = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
        await update.message.reply_text(
            f"🪙 **Watchlist Selection Updated: Bitcoin Only**\n"
            f"• **Active Symbol:** `BTCUSDT`\n"
            f"• **Gold (`XAUUSD`):** Standby (monitoring paused for Gold)\n"
            f"• **Autotrade State:** {st}\n\n"
            f"The engine will only search and execute setups on Bitcoin.\n"
            f"Use `/autotrade on` to activate or `/watchlist both` to re-enable Gold.",
            parse_mode="Markdown",
        )
    elif arg in ("gold", "xau", "xauusd", "xaut", "xautusd", "paxg"):
        config.set_autotrade_symbols(["XAUUSD"])
        st = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
        gold_open, gold_desc = get_gold_market_status()
        await update.message.reply_text(
            f"🟡 **Watchlist Selection Updated: Gold Only**\n"
            f"• **Active Symbol:** `XAUUSD`\n"
            f"• **Bitcoin (`BTCUSDT`):** Standby (monitoring paused for BTC)\n"
            f"• **Market Session:** {gold_desc}\n"
            f"• **Autotrade State:** {st}\n\n"
            f"The engine will only search and execute setups on Gold.\n"
            f"Use `/autotrade on` to activate or `/watchlist both` to re-enable Bitcoin.",
            parse_mode="Markdown",
        )
    elif arg in ("both", "all", "btc,gold", "gold,btc", "btcusdt,xauusd"):
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"])
        st = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
        await update.message.reply_text(
            f"⚡ **Watchlist Selection Updated: Both (BTC + Gold)**\n"
            f"• **Active Symbols:** `BTCUSDT`, `XAUUSD`\n"
            f"• **Autotrade State:** {st}\n\n"
            f"The engine is monitoring both assets concurrently up to your trade limit.",
            parse_mode="Markdown",
        )
    else:
        text, markup = build_watchlist_dashboard()
        try:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle autotrade engine and/or select symbols: /autotrade [on|off|scalp] [btc|gold|both]"""
    global autotrade_active
    args = [a.lower() for a in (ctx.args or [])]
    arg = args[0] if args else ""

    if args and args[0] in ("learner", "veto", "brain", "autobrain"):
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        sub = args[1].lower() if len(args) > 1 else "toggle"
        if sub in ("on", "1", "true", "enable", "start"):
            brain.set_enabled(True)
            await update.message.reply_text(
                "🟢 **Autotrade: Autonomous Learner Veto ACTIVATED**\n"
                "• Live signals are checked against learned rules and flaw bans.\n"
                "• Use `/learner off` or `/veto off` to pause vetoing.",
                parse_mode="Markdown",
            )
            return
        elif sub in ("off", "0", "false", "disable", "pause", "stop"):
            brain.set_enabled(False)
            await update.message.reply_text(
                "🔴 **Autotrade: Autonomous Learner Veto PAUSED**\n"
                "• All autotrade signals will execute unblocked by learner bans.\n"
                "• Use `/learner on` or `/veto on` to re-enable vetoing.",
                parse_mode="Markdown",
            )
            return
        elif sub in ("unban", "allow"):
            target = args[2] if len(args) > 2 else ""
            if target.isdigit():
                h = int(target) % 24
                brain.unban_hour(h)
                await update.message.reply_text(f"🔓 **Unbanned UTC hour {h:02d}:00** from Autonomous Learner.", parse_mode="Markdown")
            else:
                c = brain.clear_banned_hours()
                await update.message.reply_text(f"🔓 **Cleared all {c} banned hours** from Autonomous Learner.", parse_mode="Markdown")
            return
        elif sub in ("rules", "filters"):
            await update.message.reply_text(brain.format_rules(), parse_mode="Markdown")
            return
        else:
            new_st = not getattr(brain.knowledge, "enabled", True)
            brain.set_enabled(new_st)
            st_text = "🟢 **ACTIVATED**" if new_st else "🔴 **PAUSED**"
            await update.message.reply_text(f"🧠 **Autotrade Learner Veto:** {st_text}", parse_mode="Markdown")
            return
    elif args and args[0] in ("unban", "unbanhour"):
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        target = args[1] if len(args) > 1 else ""
        if target.isdigit():
            h = int(target) % 24
            brain.unban_hour(h)
            await update.message.reply_text(f"🔓 **Unbanned UTC hour {h:02d}:00** from Autonomous Learner.", parse_mode="Markdown")
        else:
            c = brain.clear_banned_hours()
            await update.message.reply_text(f"🔓 **Cleared all {c} banned hours** from Autonomous Learner.", parse_mode="Markdown")
        return

    broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6))

    sym_choice = None
    for a in args:
        if a in ("btc", "bitcoin", "btcusdt", "btcusd", "xbt"):
            sym_choice = "BTCUSDT"
            break
        elif a in ("gold", "xau", "xauusd", "xaut", "xautusd", "paxg"):
            sym_choice = "XAUUSD"
            break
        elif a in ("both", "all", "btc,gold", "gold,btc"):
            sym_choice = "BOTH"
            break

    if sym_choice:
        if sym_choice == "BTCUSDT":
            config.set_autotrade_symbols(["BTCUSDT"])
        elif sym_choice == "XAUUSD":
            config.set_autotrade_symbols(["XAUUSD"])
        elif sym_choice == "BOTH":
            config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"])

    symbols_str = ", ".join(getattr(config, "get_autotrade_symbols", lambda: config.AUTOTRADE_SYMBOLS)())

    enabling = any(a in ("scalp", "spam") for a in args) or (
        arg in ("on", "1", "true", "enable")
        or (sym_choice and arg in (sym_choice.lower(), "btc", "gold", "both", "all"))
    )

    if enabling:
        is_live = False
        mode_str = str(broker_mode).upper()
        if "LIVE" in mode_str:
            is_live = True
        elif hasattr(engine, "broker") and engine.broker:
            if getattr(engine.broker, "is_live", False):
                is_live = True
            elif getattr(engine.broker, "mode", "") == "live":
                is_live = True
            elif hasattr(engine.broker, "is_testnet") and not engine.broker.is_testnet:
                is_live = True

        opted_in = getattr(config, "AUTOTRADE_LIVE_OPT_IN", False) or ("confirm" in args)
        if is_live and not opted_in:
            await update.message.reply_text(
                "⚠️ **Live Trading Protection**: Autotrading on a LIVE account requires explicit opt-in.\n\n"
                "To enable live autotrading, confirm with:\n"
                "`/autotrade on confirm`\n"
                "or set `BOT_AUTOTRADE_LIVE_OPT_IN=true` in your environment.",
                parse_mode="Markdown",
            )
            return

    if any(a in ("scalp", "spam") for a in args):
        try:
            from strategy_profiles import apply_profile
            apply_profile("scalp", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
        except Exception:
            pass
        autotrade_active = True
        engine.reset_halt()
        await update.message.reply_text(
            f"⚡ **RAPID SCALP AUTOTRADE ACTIVATED**\n"
            f"• **Execution Mode:** `{broker_mode}`\n"
            f"• **Strategy Profile:** `SCALP` (High-frequency micro-scalping)\n"
            f"• **Micro Targets:** TP `{getattr(config, 'SCALP_TAKE_PROFIT_PCT', 0.10):.2f}%` / `${getattr(config, 'SCALP_TAKE_PROFIT_USD', 2.50):.2f}` | SL `{getattr(config, 'SCALP_STOP_LOSS_PCT', 0.12):.2f}%` / `${getattr(config, 'SCALP_STOP_LOSS_USD', 3.00):.2f}`\n"
            f"• **Execution:** Instant Market Fill & 1-2s Polling\n"
            f"• **Active Limit:** `{max_trades}` concurrent trades\n"
            f"• **Active Watchlist:** `{symbols_str}`\n"
            f"• **Lot Sizing:** `{'AUTO (Dynamic)' if getattr(risk_mgr.config, 'lot_mode', 'auto') == 'auto' else f'MANUAL ({risk_mgr.get_manual_lot():.2f} lots)'}`",
            parse_mode="Markdown",
        )
    elif arg in ("on", "1", "true", "enable") or (sym_choice and arg in (sym_choice.lower(), "btc", "gold", "both", "all")):
        autotrade_active = True
        engine.reset_halt()
        await update.message.reply_text(
            f"🟢 **Autotrade ACTIVATED**\n"
            f"• **Execution Mode:** `{broker_mode}`\n"
            f"• **Strategy Profile:** `{getattr(config, 'CURRENT_PROFILE', 'scalp').upper()}`\n"
            f"• **Active Trade Limit:** `{max_trades}` concurrent positions\n"
            f"• **Active Watchlist:** `{symbols_str}`\n"
            f"• **Lot Sizing:** `{'AUTO (Dynamic)' if getattr(risk_mgr.config, 'lot_mode', 'auto') == 'auto' else f'MANUAL ({risk_mgr.get_manual_lot():.2f} lots)'}`",
            parse_mode="Markdown",
        )
    elif arg in ("off", "0", "false", "disable", "stop"):
        autotrade_active = False
        await update.message.reply_text("⚪ **Autotrade PAUSED** (monitoring stopped)", parse_mode="Markdown")
    else:
        text, markup = build_watchlist_dashboard()
        try:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)


async def autotrade_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle interactive inline buttons for Watchlist & Autotrade."""
    global autotrade_active
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass
    data = str(query.data or "")

    if data.startswith("learner:") or data.startswith("brain:"):
        try:
            from telegram_brain import handle_learner_callback
            handled = await handle_learner_callback(data, update, ctx)
            if handled:
                return
        except Exception as _cb_err:
            logger.warning("learner callback error: %s", _cb_err)

    if data in ("auto:on", "auto:scalp"):
        broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
        is_live = "LIVE" in broker_mode or (hasattr(engine, "broker") and getattr(engine.broker, "is_live", False))
        if is_live and not getattr(config, "AUTOTRADE_LIVE_OPT_IN", False):
            await query.message.reply_text(
                "⚠️ **Live Trading Protection**: Autotrading on a LIVE account requires explicit opt-in.\n"
                "Run `/autotrade on confirm` or set `BOT_AUTOTRADE_LIVE_OPT_IN=true`.",
                parse_mode="Markdown"
            )
    if data.startswith("chart:"):
        try:
            await query.answer()
        except Exception:
            pass
        from mt5_chart import (
            get_mt5_live_chart,
            format_mt5_chart_card,
            format_combined_mt5_chart_dashboard,
            build_chart_keyboard,
        )
        b = getattr(engine, "broker", None)
        active_view = "both"
        if data == "chart:btc":
            chart_data = await asyncio.to_thread(get_mt5_live_chart, "BTCUSD", broker=b)
            text = format_mt5_chart_card(chart_data)
            active_view = "btc"
        elif data == "chart:gold":
            chart_data = await asyncio.to_thread(get_mt5_live_chart, "XAUUSD", broker=b)
            text = format_mt5_chart_card(chart_data)
            active_view = "gold"
        elif data.startswith("chart:refresh:"):
            req_view = data.split(":", 2)[2]
            if req_view == "btc":
                chart_data = await asyncio.to_thread(get_mt5_live_chart, "BTCUSD", broker=b)
                text = format_mt5_chart_card(chart_data)
                active_view = "btc"
            elif req_view == "gold":
                chart_data = await asyncio.to_thread(get_mt5_live_chart, "XAUUSD", broker=b)
                text = format_mt5_chart_card(chart_data)
                active_view = "gold"
            else:
                text = await asyncio.to_thread(format_combined_mt5_chart_dashboard, broker=b)
                active_view = "both"
        else:
            text = await asyncio.to_thread(format_combined_mt5_chart_dashboard, broker=b)
            active_view = "both"

        markup = build_chart_keyboard(active_view)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            try:
                await query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
            except Exception:
                await query.message.reply_text(text, reply_markup=markup)
        return

    if data.startswith("sig:"):
        sym = data.split(":", 1)[1]
        try:
            from signal_pipeline import run_signal, format_signal_message
            res = run_signal(sym, equity=float(getattr(risk_mgr.state, "equity", None) or config.INITIAL_EQUITY), leverage=float(risk_mgr.get_leverage()))
            msg = format_signal_message(res)
            kb = None
            try:
                from mt5_chart import build_chart_keyboard, normalize_mt5_symbol
                chart_sym = "btc" if normalize_mt5_symbol(sym) == "BTCUSD" else "gold"
                kb = build_chart_keyboard(active=chart_sym)
            except Exception:
                kb = None
            try:
                await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                await query.message.reply_text(msg.replace("*", "").replace("`", ""), reply_markup=kb)
        except Exception as _se:
            await query.message.reply_text(f"Signal failed: {_se}")
        return

    if data.startswith("mt5:"):
        from mt5_accounts import get_account_manager, switch_broker_mode
        if data == "mt5:switch_live":
            ok, msg, _ = switch_broker_mode("live", engine=engine, config_mod=config)
            try:
                await query.message.reply_text(msg, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(msg)
        elif data == "mt5:switch_paper":
            ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
            try:
                await query.message.reply_text(msg, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(msg)
        elif data == "mt5:list_accounts":
            mgr = get_account_manager()
            try:
                await query.message.reply_text(mgr.format_account_list(), parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(mgr.format_account_list())
            return
        elif data == "mt5:help_add":
            help_text = (
                "➕ **Add MetaTrader 5 Account**\n\n"
                "**Usage:**\n"
                "`/mt5 add <LOGIN> <PASSWORD> <SERVER> [ALIAS] [GATEWAY_URL]`\n\n"
                "**Examples:**\n"
                "• Native Windows: `/mt5 add 10054330 Secret123 Exness-Real primary`\n"
                "• With Gateway: `/mt5 add 10054330 Secret123 Exness-Real primary http://192.168.1.50:5000`\n"
                "• Prop Firm: `/mt5 add 2098711 Pass123 FTMO-Server prop1`"
            )
            try:
                await query.message.reply_text(help_text, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(help_text)
            return
        elif data == "mt5:positions":
            msg = engine.format_positions_detail() if hasattr(engine, "format_positions_detail") else str(engine.get_portfolio_summary())
            try:
                await query.message.reply_text(msg, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(msg)
            return
        elif data == "mt5:check":
            b = engine.broker
            if hasattr(b, "check_trading_readiness"):
                symbols = getattr(config, "get_autotrade_symbols", lambda: ["XAUUSD", "BTCUSDT"])()
                res = b.check_trading_readiness(symbols)
                ready_icon = "🟢 **READY FOR TRADING**" if res.get("ready") else "🔴 **TRADING BLOCKED**"
                lines = [
                    f"🔍 **MT5 Pre-Flight Diagnostics Check**",
                    f"━━━━━━━━━━━━━━━━━━━━",
                    f"• **Status:** {ready_icon}",
                    f"• **Server:** `{res.get('server', 'N/A')}` | **Login:** `{res.get('login', 'N/A')}`",
                    f"• **Terminal Algo Trading:** {'🟢 Enabled' if res.get('terminal_algo_trading') else '🔴 DISABLED (Click Algo Trading button in MT5)'}",
                    f"• **Account Trading:** {'🟢 Enabled' if res.get('account_trading_allowed') else '🔴 DISABLED (Investor read-only password?)'}",
                    f"• **Broker EA Allowed:** {'🟢 Allowed' if res.get('broker_expert_allowed') else '🔴 DISABLED by broker'}",
                    f"• **Engine Halt State:** {'🔴 HALTED (Run /mt5 reset to clear)' if getattr(engine, 'execution_halted', False) else '🟢 Active'}",
                ]
                syms = res.get("symbols", {})
                if syms:
                    lines.append("\n📊 **Symbol Market Checks:**")
                    for s, data_item in syms.items():
                        if data_item.get("ok"):
                            lines.append(f"• `{s}` -> `{data_item.get('resolved', s)}`: 🟢 Open | Filling: `{data_item.get('filling_mode', 'N/A')}` | Min: `{data_item.get('min_volume', 0.01)}`")
                        else:
                            lines.append(f"• `{s}`: 🔴 {data_item.get('error', 'Disabled / Market Closed')}")
                blockers = res.get("blockers", [])
                if blockers:
                    lines.append("\n🚨 **Identified Blockers:**")
                    for bl in blockers:
                        lines.append(f"• {bl}")
                warnings = res.get("warnings", [])
                if warnings:
                    lines.append("\n⚠️ **Warnings:**")
                    for w in warnings:
                        lines.append(f"• {w}")
                try:
                    await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
                except Exception:
                    await query.message.reply_text("\n".join(lines))
            return
        elif data == "mt5:refresh":
            pass

        text, markup = build_mt5_dashboard()
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return

    if data.startswith("lot:"):
        if data == "lot:auto":
            risk_mgr.set_auto_lot()
            config.set_lot_size_mode("auto")
            try:
                await query.message.reply_text(
                    "🟢 **Auto Lot Sizing Activated**\n• Orders will be sized dynamically based on account equity, risk % and stop loss distance.",
                    parse_mode="Markdown",
                )
            except Exception:
                await query.message.reply_text("Auto Lot Sizing Activated.")
        elif data.startswith("lot:manual:"):
            val_str = data.split(":", 2)[2]
            try:
                val = float(val_str)
                risk_mgr.set_manual_lot(val)
                config.set_manual_lot_size(val)
                config.set_lot_size_mode("manual")
                try:
                    await query.message.reply_text(
                        f"✏️ **Manual Lot Sizing Activated**\n• **Fixed Lot Size:** `{val:.4f}` lots globally.\n• Mode: `MANUAL`",
                        parse_mode="Markdown",
                    )
                except Exception:
                    await query.message.reply_text(f"Manual lot set to {val} lots.")
            except Exception as e:
                await query.message.reply_text(f"Error setting lot size: {e}")
        elif data in ("lot:refresh", "lot:info"):
            pass

        text, markup = build_lot_dashboard()
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass
        return


    if data == "wl:btc":
        config.set_autotrade_symbols(["BTCUSDT"])
    elif data == "wl:gold":
        config.set_autotrade_symbols(["XAUUSD"])
    elif data == "wl:both":
        config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"])
    elif data == "auto:on":
        autotrade_active = True
        engine.reset_halt()
    elif data == "auto:off":
        autotrade_active = False
    elif data == "auto:scalp":
        try:
            from strategy_profiles import apply_profile
            apply_profile("scalp", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
        except Exception:
            pass
        autotrade_active = True
        engine.reset_halt()
    elif data == "wl:positions":
        msg = engine.format_positions_detail() if hasattr(engine, "format_positions_detail") else str(engine.get_portfolio_summary())
        try:
            await query.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(msg)
        return
    elif data == "wl:refresh":
        pass

    text, markup = build_watchlist_dashboard()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        pass


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
                out += "\nLast trades (up to 5):\n"
                for t in trades[-5:]:
                    out += f"  {t.direction.upper()} R={t.r_multiple:+.2f} PnL={t.pnl:+.2f} ({t.reason})\n"
            return out

        try:
            text = await _to_thread(_run_single)
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
        report = await _to_thread(_run)
        text = report.text(max_len=3800)
        await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Auto backtest failed for %s: %s", symbol, exc)
        await update.message.reply_text(
            f"Backtest failed: `{type(exc).__name__}: {exc}`\n"
            f"Tip: set a real data source or provide CSV; synthetic data is last resort.",
            parse_mode="Markdown",
        )



def build_mt5_dashboard() -> tuple:
    """Generate Markdown text and inline keyboard for the MT5 Dashboard."""
    from mt5_accounts import get_account_manager
    mgr = get_account_manager()
    active_acc = mgr.get_active_account()
    mode_icon = "🟢 LIVE REAL TRADING (Exclusive)"

    bal = 0.0
    eq = 0.0
    margin = 0.0
    free_m = 0.0
    lev = getattr(config, "DEFAULT_LEVERAGE", 20.0)
    srv = getattr(config, "MT5_SERVER", "MetaQuotes-Demo")
    login_str = str(getattr(config, "MT5_LOGIN", 0) or "None")

    if hasattr(engine.broker, "get_account_info"):
        try:
            ai = engine.broker.get_account_info()
            bal = ai.balance
            eq = ai.equity
            margin = ai.margin
            free_m = ai.free_margin
            lev = ai.leverage
            srv = ai.server
            login_str = str(ai.login or login_str)
        except Exception:
            pass
    elif hasattr(engine.broker, "balance"):
        bal = float(getattr(engine.broker, "balance", 0.0))
        eq = float(getattr(engine.broker, "equity", bal))

    alias_str = f" ({active_acc.alias})" if active_acc and active_acc.alias else ""
    accounts_count = len(mgr.list_accounts())
    active_positions_count = len(getattr(engine, "active_positions", {}))

    try:
        bal_f = float(bal)
    except Exception:
        bal_f = 0.0
    try:
        eq_f = float(eq)
    except Exception:
        eq_f = 0.0
    try:
        margin_f = float(margin)
    except Exception:
        margin_f = 0.0
    try:
        free_m_f = float(free_m)
    except Exception:
        free_m_f = 0.0
    try:
        lev_f = float(lev)
    except Exception:
        lev_f = 20.0

    alerts = []
    if getattr(engine, "execution_halted", False):
        alerts.append("🚨 **EXECUTION HALTED**: Prior broker order failed. Send `/mt5 reset` or `/live on` to resume.")
    if ai:
        if hasattr(ai, "terminal_trade_allowed") and not ai.terminal_trade_allowed:
            alerts.append("⚠️ **MT5 ALGO TRADING DISABLED**: Click 'Algo Trading' toolbar button in MT5 to turn it GREEN.")
        if hasattr(ai, "trade_allowed") and not ai.trade_allowed:
            alerts.append("⚠️ **ACCOUNT TRADING DISABLED**: Check if logged in with read-only Investor password.")
        if hasattr(ai, "trade_expert") and not ai.trade_expert:
            alerts.append("⚠️ **AUTOMATED TRADING DISABLED BY BROKER** on this account.")
    alert_section = ("\n" + "\n".join(alerts) + "\n") if alerts else ""

    text = (
        f"🏦 **MetaTrader 5 Dashboard**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Execution Mode:** {mode_icon}\n"
        f"• **Active Account:** `{login_str}`{alias_str}\n"
        f"• **Broker Server:** `{srv}`\n"
        f"• **Balance:** `${bal_f:,.2f}` | **Equity:** `${eq_f:,.2f}`\n"
        f"• **Margin:** `${margin_f:,.2f}` | **Free Margin:** `${free_m_f:,.2f}`\n"
        f"• **Leverage:** `{lev_f:.0f}x` | **Open Positions:** `{active_positions_count}`\n"
        f"• **Saved Accounts:** `{accounts_count}`\n"
        f"{alert_section}\n"
        f"⚡ **Quick Controls:**\n"
        f"• `/mt5 check` — Run pre-flight trading permissions diagnostics\n"
        f"• `/mt5 reset` — Clear execution halt state & resume orders\n"
        f"• `/mt5 switch live` — Connect / switch active live MT5 account\n"
        f"• `/mt5 switch <alias|login>` — Switch to a saved live account\n"
        f"• `/mt5 add <login> <pass> <server> [alias]` — Save live account\n"
        f"• `/mt5 balance [amount]` — View or update MT5 live balance\n"
        f"• `/mt5 remove <alias|login|all>` — Remove saved account(s)\n"
        f"• `/mt5 accounts` — List all saved accounts\n"
        f"• `/switch <alias|login>` | `/live`"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Connect / Switch Live", callback_data="mt5:switch_live"),
            InlineKeyboardButton("🔍 Pre-Flight Check", callback_data="mt5:check"),
        ],
        [
            InlineKeyboardButton("📋 My Accounts", callback_data="mt5:list_accounts"),
            InlineKeyboardButton("➕ Add Account", callback_data="mt5:help_add"),
        ],
        [
            InlineKeyboardButton("📊 Positions", callback_data="mt5:positions"),
            InlineKeyboardButton("🔄 Refresh Telemetry", callback_data="mt5:refresh"),
        ],
    ])
    return text, markup


async def mt5_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """MetaTrader 5 terminal, multi-account management & execution switcher."""
    from mt5_accounts import get_account_manager, switch_broker_mode, connect_and_verify
    mgr = get_account_manager()
    args = ctx.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("add", "save", "new", "register"):
        if len(args) < 4:
            await update.message.reply_text(
                "➕ **Add MetaTrader 5 Account**\n\n"
                "**Usage:**\n"
                "`/mt5 add <LOGIN> <PASSWORD> <SERVER> [ALIAS] [GATEWAY_URL]`\n\n"
                "**Examples:**\n"
                "• Native Windows: `/mt5 add 10054330 Secret123 Exness-Real primary`\n"
                "• With Gateway: `/mt5 add 10054330 Secret123 Exness-Real primary http://192.168.1.50:5000`\n"
                "• Prop Firm: `/mt5 add 2098711 Pass123 FTMO-Server prop1`",
                parse_mode="Markdown",
            )
            return

        login_str = args[1]
        password = args[2]
        server = args[3]
        alias = args[4] if len(args) > 4 and not args[4].startswith("http") else ""
        gateway_url = ""
        for a in args[4:]:
            if a.startswith("http://") or a.startswith("https://"):
                gateway_url = a
                break

        try:
            acc = mgr.add_account(
                login=login_str,
                password=password,
                server=server,
                alias=alias,
                gateway_url=gateway_url,
                is_live=True,
                set_active=True,
            )
        except ValueError as ve:
            await update.message.reply_text(f"❌ Invalid Account Data: `{ve}`", parse_mode="Markdown")
            return

        path = getattr(config, "MT5_PATH", "")
        conn_ok, conn_msg, test_broker = connect_and_verify(acc, mode="live", path=path)

        if conn_ok and test_broker:
            await update.message.reply_text(
                f"✅ **MT5 Live Account Added & Verified Successfully!**\n"
                f"• **Account #:** `{acc.login}` (Alias: `{acc.alias}`)\n"
                f"• **Server:** `{acc.server}`\n"
                f"• **Balance:** `${test_broker.balance:,.2f}` | **Equity:** `${test_broker.equity:,.2f}`\n"
                f"• **Leverage:** `{test_broker.leverage:.0f}x` | **Password:** `{acc.masked_password}`\n\n"
                f"💡 **Switch to this live account anytime:**\n"
                f"`/mt5 switch live` or `/switch {acc.alias}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ **MT5 Account Saved (Verification Note)**\n"
                f"• **Account #:** `{acc.login}` (Alias: `{acc.alias}`)\n"
                f"• **Server:** `{acc.server}`\n"
                f"• **Connection Error:** `{conn_msg}`\n\n"
                f"_The account is saved. If MT5 is not yet running, launch your terminal or gateway, then switch with `/mt5 switch live`._",
                parse_mode="Markdown",
            )

    elif sub in ("switch", "select", "use"):
        target = args[1].lower() if len(args) > 1 else "live"
        sub_id = args[2] if len(args) > 2 else None
        ok, msg, _ = switch_broker_mode(target_mode=target, identifier=sub_id, engine=engine, config_mod=config)
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif sub in ("accounts", "list", "myaccounts"):
        await update.message.reply_text(mgr.format_account_list(), parse_mode="Markdown")

    elif sub in ("remove", "delete", "rm", "clear"):
        if sub == "clear" or (len(args) > 1 and args[1].lower() in ("all", "*", "clear")):
            count = mgr.clear_accounts()
            await update.message.reply_text(
                f"🗑️ **All MT5 Accounts Removed** ({count} account(s) cleared).\n"
                f"Use `/mt5 add <login> <password> <server> [alias]` to register a new account.",
                parse_mode="Markdown",
            )
            return
        if len(args) < 2:
            await update.message.reply_text("Usage: `/mt5 remove <login|alias|all>`", parse_mode="Markdown")
            return
        target = args[1]
        removed = mgr.remove_account(target)
        if removed:
            await update.message.reply_text(
                f"🗑️ **MT5 Account Removed:** `{removed.alias}` (`{removed.login}` on `{removed.server}`)",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Account `{target}` not found in saved list.", parse_mode="Markdown")

    elif sub in ("balance", "bal", "setbalance"):
        b = engine.broker
        if len(args) > 1:
            try:
                new_bal = float(args[1])
                if new_bal <= 0:
                    raise ValueError("Balance must be positive.")
                if b and hasattr(b, "balance"):
                    b.balance = new_bal
                    if hasattr(b, "_initial_balance"):
                        b._initial_balance = new_bal
                if hasattr(risk_mgr, "equity"):
                    risk_mgr.equity = new_bal
                if hasattr(risk_mgr, "initial_equity"):
                    risk_mgr.initial_equity = new_bal
                config.INITIAL_EQUITY = new_bal
                config.MT5_BALANCE = new_bal
                act = mgr.get_active_account()
                if act:
                    act.balance = new_bal
                    act.equity = new_bal
                    mgr._save()
                await update.message.reply_text(
                    f"🟢 **MT5 Live Balance Updated**\n"
                    f"• **New Balance:** `${new_bal:,.2f}`\n"
                    f"• **Risk Manager Equity:** `${new_bal:,.2f}`\n"
                    f"• Position sizing and limits updated.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Invalid balance amount: `{e}`", parse_mode="Markdown")
        else:
            bal = float(getattr(b, "balance", 0.0) or 0.0) if b else 0.0
            eq = float(getattr(b, "equity", bal) or bal) if b else bal
            mg = float(getattr(b, "margin", 0.0) or 0.0) if b else 0.0
            free_m = float(getattr(b, "free_margin", eq) or eq) if b else eq
            lev = float(getattr(b, "leverage", 20.0) or 20.0) if b else 20.0
            act = mgr.get_active_account()
            act_str = f"`{act.login}` ({act.alias})" if act else "None"
            await update.message.reply_text(
                f"💰 **MT5 Live Account Balance**\n"
                f"• **Active Account:** {act_str}\n"
                f"• **Balance:** `${bal:,.2f}`\n"
                f"• **Equity:** `${eq:,.2f}`\n"
                f"• **Margin:** `${mg:,.2f}` | **Free Margin:** `${free_m:,.2f}`\n"
                f"• **Leverage:** `{lev:.0f}x`\n\n"
                f"💡 Update live initial balance: `/mt5 balance <amount>`",
                parse_mode="Markdown",
            )

    elif sub in ("live", "connect"):
        if len(args) >= 4:
            login_str = args[1]
            password = args[2]
            server = args[3]
            gw = args[4] if len(args) > 4 and args[4].startswith("http") else ""
            alias = args[4] if len(args) > 4 and not args[4].startswith("http") else "live"
            try:
                acc = mgr.add_account(login_str, password, server, alias=alias, gateway_url=gw, set_active=True)
            except ValueError as ve:
                await update.message.reply_text(f"❌ {ve}")
                return
            ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            target_id = args[1] if len(args) > 1 else None
            ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
            await update.message.reply_text(msg, parse_mode="Markdown")

    elif sub in ("paper", "demo", "sim"):
        ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
        await update.message.reply_text(msg, parse_mode="Markdown")

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
                    curr_p = getattr(p, "current_price", None)
                    if not curr_p:
                        if p.qty > 0:
                            curr_p = p.entry_price + (p.unrealized_pnl / p.qty) if p.side.lower() == "buy" else p.entry_price - (p.unrealized_pnl / p.qty)
                        else:
                            curr_p = p.entry_price
                    curr_fmt = f"${curr_p:,.2f}" if curr_p >= 1.0 else f"${curr_p:,.4f}"
                    lines.append(
                        f"• `{p.symbol}` **{p.side.upper()}** `{p.qty:.2f}` lots @ Entry `${p.entry_price:,.2f}` | "
                        f"**Current Market:** `{curr_fmt}` | PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                    )
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"⚠️ Error fetching MT5 positions: `{e}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ No broker attached to engine.")

    elif sub in ("check", "test", "verify", "diag", "diagnose"):
        b = engine.broker
        if hasattr(b, "check_trading_readiness"):
            symbols = getattr(config, "get_autotrade_symbols", lambda: ["XAUUSD", "BTCUSDT"])()
            res = b.check_trading_readiness(symbols)
            ready_icon = "🟢 **READY FOR TRADING**" if res.get("ready") else "🔴 **TRADING BLOCKED**"
            lines = [
                f"🔍 **MT5 Pre-Flight Diagnostics Check**",
                f"━━━━━━━━━━━━━━━━━━━━",
                f"• **Status:** {ready_icon}",
                f"• **Server:** `{res.get('server', 'N/A')}` | **Login:** `{res.get('login', 'N/A')}`",
                f"• **Terminal Algo Trading:** {'🟢 Enabled' if res.get('terminal_algo_trading') else '🔴 DISABLED (Click Algo Trading button in MT5)'}",
                f"• **Account Trading:** {'🟢 Enabled' if res.get('account_trading_allowed') else '🔴 DISABLED (Investor read-only password?)'}",
                f"• **Broker EA Allowed:** {'🟢 Allowed' if res.get('broker_expert_allowed') else '🔴 DISABLED by broker'}",
                f"• **Engine Halt State:** {'🔴 HALTED (Run /mt5 reset to clear)' if getattr(engine, 'execution_halted', False) else '🟢 Active'}",
            ]
            syms = res.get("symbols", {})
            if syms:
                lines.append("\n📊 **Symbol Market Checks:**")
                for s, data_item in syms.items():
                    if data_item.get("ok"):
                        lines.append(f"• `{s}` -> `{data_item.get('resolved', s)}`: 🟢 Open | Filling: `{data_item.get('filling_mode', 'N/A')}` | Min: `{data_item.get('min_volume', 0.01)}`")
                    else:
                        lines.append(f"• `{s}`: 🔴 {data_item.get('error', 'Disabled / Market Closed')}")
            blockers = res.get("blockers", [])
            if blockers:
                lines.append("\n🚨 **Identified Blockers:**")
                for bl in blockers:
                    lines.append(f"• {bl}")
            warnings = res.get("warnings", [])
            if warnings:
                lines.append("\n⚠️ **Warnings:**")
                for w in warnings:
                    lines.append(f"• {w}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text("ℹ️ Current broker does not support MT5 pre-flight diagnostics.")

    elif sub in ("reset", "resethalt", "clearhalt", "unhalt"):
        if hasattr(engine, "reset_halt"):
            engine.reset_halt()
        await update.message.reply_text(
            "🟢 **MT5 Execution Halt Cleared**\n"
            "Execution engine halt state has been reset. All trading requests and order placements are unblocked.",
            parse_mode="Markdown",
        )

    elif sub in ("bridge", "ea"):
        await bridge_command(update, ctx)

    elif sub in ("backend", "driver"):
        b = engine.broker
        if len(args) > 1:
            req_backend = args[1].lower()
            if req_backend in ("ea_bridge", "bridge", "ea"):
                set_target = "ea_bridge"
            elif req_backend in ("gateway", "rest"):
                set_target = "gateway"
            elif req_backend in ("native",):
                set_target = "native"
            elif req_backend in ("auto", "default"):
                set_target = "auto"
            else:
                await update.message.reply_text("Usage: `/mt5 backend [ea_bridge|native|gateway|auto]`", parse_mode="Markdown")
                return
            config.MT5_BACKEND = set_target
            if b and hasattr(b, "backend"):
                b.backend = set_target
                b.connect()
            cur_eff = getattr(b, "effective_backend", "N/A") if b else "N/A"
            await update.message.reply_text(
                f"🔧 **MT5 Backend Updated**\n"
                f"• **Configured Backend:** `{set_target}`\n"
                f"• **Active Effective Driver:** `{cur_eff}`\n"
                f"• Execution routed via `{cur_eff}`.",
                parse_mode="Markdown",
            )
        else:
            cur_cfg = getattr(config, "MT5_BACKEND", "auto")
            cur_eff = getattr(b, "effective_backend", "N/A") if b else "N/A"
            await update.message.reply_text(
                f"🔧 **MT5 Backend Status**\n"
                f"• **Configured Backend:** `{cur_cfg}`\n"
                f"• **Active Effective Driver:** `{cur_eff}`\n\n"
                f"💡 Change backend: `/mt5 backend [ea_bridge|native|gateway|auto]`",
                parse_mode="Markdown",
            )

    else:
        text, markup = build_mt5_dashboard()
        try:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)


async def bridge_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """EA Bridge management: /bridge [status|ping|on|off|dir <path>]"""
    from bridge import ea_client
    args = ctx.args or []
    sub = args[0].lower() if args else "status"
    b = engine.broker
    magic = getattr(b, "magic", 112233) if b else 112233

    if sub == "ping":
        t0 = time.time()
        res = ea_client.status(magic=magic, timeout=5.0)
        elapsed = (time.time() - t0) * 1000.0
        if res.get("ok"):
            await update.message.reply_text(
                f"🏓 **EA Bridge Ping: PONG ({elapsed:.1f}ms)**\n"
                f"• **Status:** 🟢 Responsive\n"
                f"• **MT5 Login:** `{res.get('login', 'N/A')}`\n"
                f"• **Open Positions:** `{res.get('positions', 0)}` (Vol: `{res.get('volume', 0.0):.2f}`)\n"
                f"• **Directory:** `{ea_client.BRIDGE_DIR}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ **EA Bridge Ping Failed ({elapsed:.1f}ms)**\n"
                f"• **Error:** `{res.get('error', 'timeout')}`\n"
                f"• **Command File:** `{ea_client.CMD_FILE}`\n"
                f"• **Hint:** {res.get('hint', 'Ensure EA_Bridge is attached to chart and AutoTrading is ON')}",
                parse_mode="Markdown",
            )

    elif sub in ("test", "diag", "diagnose", "diagnostics", "sim"):
        from scripts.test_ea_bridge import run_diagnostics
        is_sim = sub == "sim"
        diag_res = await _to_thread(run_diagnostics, magic=magic, timeout=3.0, simulate=is_sim)
        badge = "🟢 PASS: Bridge Operational" if diag_res.status == "PASS" else ("🟡 WARN: EA Offline" if diag_res.status == "WARN" else "🔴 FAIL: I/O Error")
        lines = [
            f"🔍 **EA Bridge Self-Diagnostic**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• **Status:** {badge}",
            f"• **Bridge Directory:** `{diag_res.bridge_dir}`",
            f"• **I/O Latency:** `{diag_res.write_latency_ms:.2f}ms` (Writable: {diag_res.dir_writable})",
            f"• **EA Roundtrip Ping:** `{diag_res.ea_roundtrip_ms:.2f}ms`",
            f"• **EA Connected:** `{'Yes' if diag_res.ea_connected else 'No'}`",
        ]
        if diag_res.account_info:
            acc = diag_res.account_info
            lines.extend([
                f"• **Account:** `{acc.get('account')}` | Equity: `${acc.get('equity', 0):,.2f}`",
                f"• **Free Margin:** `${acc.get('free_margin', 0):,.2f}` (1:{acc.get('leverage')})",
                f"• **Active Positions:** `{len(diag_res.open_positions)}`",
            ])
        if diag_res.warnings:
            lines.append(f"⚠️ *Warning:* {diag_res.warnings[0]}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif sub in ("deploy", "install"):
        from scripts.deploy_ea import discover_terminals, deploy_ea
        terms = discover_terminals()
        if not terms:
            await update.message.reply_text(
                "⚠️ **No MetaTrader 5 Terminals Auto-Detected**\n"
                "• Manually specify your terminal data directory with:\n"
                "  `/bridge dir <path_to_Common_Files>`\n"
                "• Or manually copy `mql5/EA_Bridge.mq5` into your terminal's `MQL5/Experts` folder.",
                parse_mode="Markdown",
            )
        else:
            results = deploy_ea(terms, dry_run=False, compile_ea=False)
            lines = [
                f"🚀 **EA Bridge Auto-Deployment Report**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Found `{len(terms)}` terminal instances.",
            ]
            for r in results:
                status_ico = "✅" if r.copied else "❌"
                lines.append(f"{status_ico} Terminal `{r.terminal_id}`: {'Copied' if r.copied else 'Error'} ({r.file_size} bytes)")
            lines.append("Restart or refresh Experts in MT5 navigator to attach `EA_Bridge`.")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif sub in ("on", "enable", "switch", "connect"):
        config.MT5_BACKEND = "ea_bridge"
        if b and hasattr(b, "backend"):
            b.backend = "ea_bridge"
            b.connect()
        else:
            from mt5_broker import MT5Broker
            engine.broker = MT5Broker(mode="live", backend="ea_bridge", magic=magic)
        await update.message.reply_text(
            f"🟢 **Switched to EA Bridge Backend**\n"
            f"• **Broker Mode:** `mt5_live`\n"
            f"• **Backend Driver:** `ea_bridge`\n"
            f"• Orders will now be executed via `EA_Bridge.mq5` file-bridge.\n"
            f"• Directory: `{ea_client.BRIDGE_DIR}`",
            parse_mode="Markdown",
        )

    elif sub in ("off", "disable", "auto"):
        config.MT5_BACKEND = "auto"
        if b and hasattr(b, "backend"):
            b.backend = "auto"
            try:
                b.connect()
            except Exception as _e:
                logger.warning("[Bridge] Reconnect to auto backend notice: %s", _e)
        else:
            try:
                from mt5_broker import MT5Broker
                engine.broker = MT5Broker(mode="live", backend="auto", magic=magic)
            except Exception as _e:
                logger.warning("[Bridge] Recreate auto backend broker notice: %s", _e)
        eff = getattr(engine.broker, "effective_backend", "auto") if engine.broker else "auto"
        await update.message.reply_text(
            f"⚪ **Switched to Auto MT5 Backend**\n"
            f"• **Backend Driver:** `{eff}`\n"
            f"• Standard native / gateway / cloud execution restored.",
            parse_mode="Markdown",
        )

    elif sub in ("dir", "path", "setdir"):
        if len(args) > 1:
            new_path = " ".join(args[1:])
            ea_client.set_bridge_dir(new_path)
            config.EA_BRIDGE_DIR = new_path
            await update.message.reply_text(
                f"📁 **EA Bridge Directory Updated**\n"
                f"• **New Directory:** `{new_path}`\n"
                f"• **Commands:** `{ea_client.CMD_FILE}`\n"
                f"• **Responses:** `{ea_client.RSP_FILE}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"📁 **EA Bridge Directory**\n"
                f"• `{ea_client.BRIDGE_DIR}`\n"
                f"Update path: `/bridge dir <path_to_common_files>`",
                parse_mode="Markdown",
            )

    else:
        # Default: status dashboard
        cur_eff = getattr(b, "effective_backend", "N/A") if b else "N/A"
        cur_backend = getattr(b, "backend", getattr(config, "MT5_BACKEND", "auto")) if b else "auto"
        res = ea_client.status(magic=magic, timeout=2.0)
        is_responsive = res.get("ok", False)
        status_icon = "🟢 Responsive" if is_responsive else "⚪ Awaiting MT5 EA"
        lines = [
            f"🌉 **MetaTrader 5 EA Bridge Dashboard**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• **Configured Backend:** `{cur_backend}`",
            f"• **Effective Driver:** `{cur_eff}`",
            f"• **EA Heartbeat:** {status_icon}",
            f"• **Magic Identifier:** `{magic}`",
            f"• **Bridge Directory:** `{ea_client.BRIDGE_DIR}`",
            f"• **Commands File:** `{'Found' if ea_client.CMD_FILE.exists() else 'Ready'}`",
            f"• **Responses File:** `{'Found' if ea_client.RSP_FILE.exists() else 'Waiting'}`",
        ]
        if is_responsive:
            lines.append(f"• **MT5 Account Login:** `{res.get('login', 'N/A')}`")
            lines.append(f"• **EA Active Positions:** `{res.get('positions', 0)}` (Vol: `{res.get('volume', 0.0):.2f}` lots)")
        lines.extend([
            f"",
            f"**Controls:**",
            f"• `/bridge ping` — Ping chart EA for latency",
            f"• `/bridge test` — Run self-diagnostic suite",
            f"• `/bridge deploy` — Auto-deploy EA to MT5 terminals",
            f"• `/bridge on` — Switch active broker to EA Bridge",
            f"• `/bridge off` — Switch active broker back to auto",
            f"• `/bridge dir <path>` — Change MT5 common directory",
        ])
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def regime_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View and control market regime auto-switcher: /regime [auto|calibrate|switch <profile>]"""
    from regime_auto_switcher import compute_regime_indicators, RegimeAutoSwitcher, calibrate_rolling_sharpe_weights
    from strategy_profiles import get_current_profile, apply_profile, DEFAULT_PROFILES
    from multi_agent_trading_bot import MarketDataFeed
    args = ctx.args or []
    sub = args[0].lower() if args else ""

    if sub in ("auto", "evaluate", "check"):
        feed = MarketDataFeed(symbol="XAUUSD", source=config.DEFAULT_DATA_SOURCE)
        data = await _to_thread(feed.multi_tf, limit_1m=100)
        df1 = data.get("1m") if data else None
        if df1 is None or df1.empty:
            await update.message.reply_text("⚠️ Could not fetch market data to evaluate regime.")
            return
        switcher = getattr(arbiter, "_regime_switcher", None) or RegimeAutoSwitcher()
        res = switcher.evaluate_and_switch(df1, symbol="XAUUSD", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
        sw_text = f"🔄 Switched from `{res.previous_profile}` to `{res.current_profile}`" if res.switched else f"Held current profile `{res.current_profile}`"
        lines = [
            f"🌐 **Market Regime Auto-Evaluation**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• **Regime Detected:** `{res.regime_detected}`",
            f"• **ADX (14):** `{res.metrics.get('adx', 0.0):.1f}`",
            f"• **ATR Volatility:** `{res.metrics.get('atr_pct', 0.0):.3f}%`",
            f"• **BB Band Width:** `{res.metrics.get('bb_width', 0.0):.4f}`",
            f"• **Action:** {sw_text}",
            f"• **Rationale:** _{res.reason}_",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if sub in ("calibrate", "weights", "sharpe"):
        new_weights = calibrate_rolling_sharpe_weights(arbiter)
        lines = [
            f"⚖️ **Dynamic Rolling Sharpe Agent Weights**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for agent, w in new_weights.items():
            lines.append(f"• `{agent:<15}` : **{w:.2f}x**")
        lines.append("\nWeights applied to active multi-agent Arbiter.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if sub in ("switch", "set") and len(args) > 1:
        target = args[1].lower()
        if target in DEFAULT_PROFILES:
            apply_profile(target, arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
            await update.message.reply_text(f"✅ Active strategy profile switched to `{target}`.")
        else:
            valid = ", ".join(DEFAULT_PROFILES.keys())
            await update.message.reply_text(f"❌ Unknown profile `{target}`. Valid: `{valid}`")
        return

    feed = MarketDataFeed(symbol="XAUUSD", source=config.DEFAULT_DATA_SOURCE)
    data = await _to_thread(feed.multi_tf, limit_1m=60)
    df1 = data.get("1m") if data else None
    metrics = compute_regime_indicators(df1) if df1 is not None else {"adx": 22.0, "atr_pct": 0.15, "bb_width": 0.005}
    cur_prof = get_current_profile()
    regime_desc = "Strong Trend" if metrics["adx"] >= 28 else ("Range / Chop" if metrics["adx"] < 20 else "Balanced Consolidation")

    lines = [
        f"📊 **Market Regime & Strategy Profile**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• **Active Profile:** `{cur_prof.name.upper()}` ({cur_prof.description})",
        f"• **Detected Regime:** `{regime_desc}`",
        f"• **Trend Strength (ADX):** `{metrics['adx']:.1f}`",
        f"• **ATR Volatility:** `{metrics['atr_pct']:.3f}%`",
        f"• **Bollinger Width:** `{metrics['bb_width']:.4f}`",
        f"• **Min Agreement:** `{cur_prof.min_agreement}` agents (Min Score: `{cur_prof.min_score}`)",
        f"",
        f"**Controls:**",
        f"• `/regime auto` — Evaluate regime and auto-switch profile",
        f"• `/regime calibrate` — Recalibrate weights by Rolling Sharpe",
        f"• `/regime switch <profile>` — Switch profile (scalp/swing/gold_only/crypto/balanced)",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def session_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View trading session status and performance report: /session [asian|london|ny]"""
    from session_reporter import compile_session_summary, format_session_telegram_message, SESSION_WINDOWS
    args = ctx.args or []
    target_session = args[0].upper() if args else None

    now_utc = datetime.now(timezone.utc)
    curr_time = now_utc.time()

    if not target_session or target_session not in SESSION_WINDOWS:
        if curr_time < SESSION_WINDOWS["ASIAN"]["end"]:
            active_s = "ASIAN"
        elif curr_time < SESSION_WINDOWS["LONDON"]["end"]:
            active_s = "LONDON"
        else:
            active_s = "NEW_YORK"
    else:
        active_s = target_session

    positions = list(engine.active_positions.values())
    summary = compile_session_summary(
        session_name=active_s,
        open_positions=positions,
    )
    msg = format_session_telegram_message(summary)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def slippage_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View real-time execution analytics & broker slippage: /slippage or /analytics"""
    from execution_analytics import get_execution_tracker
    tracker = get_execution_tracker()
    report = tracker.generate_report()

    lines = [
        f"⚡ **Broker Execution Quality & Slippage Analytics**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• **Execution Grade:** `{report.execution_rating}`",
        f"• **Total Executions:** `{report.total_executions}`",
        f"• **Avg Latency:** `{report.avg_latency_ms:.1f}ms` (P95: `{report.p95_latency_ms:.1f}ms`)",
        f"• **Avg Slippage:** `{report.avg_slippage_pts:+.2f} pts`",
        f"• **Realized Slippage USD:** `${report.total_slippage_usd:+.2f}`",
        f"• **Adverse Fills:** `{report.adverse_fills_pct:.1f}%` | **Favorable:** `{report.favorable_fills_pct:.1f}%`",
        f"",
        f"📌 **Audit Verdict:**",
        f"_{report.recommendation}_",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


analytics_command = slippage_command


async def kelly_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Calculates Fractional Kelly sizing & portfolio VaR 99%: /kelly [symbol]"""
    args = ctx.args or []
    sym = args[0].upper() if args else "XAUUSD"
    eq = float(risk_mgr.state.equity or 10000.0)

    feed = MarketDataFeed(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
    data = await _to_thread(feed.multi_tf, limit_1m=10)
    df1 = data.get("1m") if data else None
    px = float(df1["close"].iloc[-1]) if df1 is not None and not df1.empty else (2650.0 if "XAU" in sym else 65000.0)
    stop = px * 0.995

    kelly_res = risk_mgr.kelly_position_size(entry=px, stop=stop, symbol=sym, fraction_mult=0.25)
    var_res = risk_mgr.portfolio_var(confidence_level=0.99)

    lines = [
        f"🎲 **Fractional Kelly Criterion & Portfolio Risk**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• **Portfolio Equity:** `${eq:,.2f}` (Leverage: {risk_mgr.get_leverage():.0f}x)",
        f"• **Symbol:** `{sym}` @ `${px:,.2f}`",
        f"• **Kelly Mode:** `{kelly_res.fraction_mode}`",
        f"• **Raw Kelly f*:** `{kelly_res.raw_kelly_fraction:.2%}`",
        f"• **Applied Sizing:** `{kelly_res.applied_kelly_fraction:.2%}` (`${kelly_res.dollar_risk:,.2f}` risk)",
        f"• **Recommended Lots:** `{kelly_res.recommended_lots}` lots",
        f"• **Model Win Rate:** `{kelly_res.win_rate:.1%}` | Payoff: `{kelly_res.payoff_ratio:.2f}:1`",
        f"",
        f"🛡️ **Portfolio Value at Risk (VaR 99% / 1-Day):**",
        f"• **Parametric VaR (99%):** `${var_res.parametric_var_usd:,.2f}` (`{var_res.parametric_var_pct:.2f}%`)",
        f"• **Historical Simulation VaR:** `${var_res.historical_var_usd:,.2f}` (`{var_res.historical_var_pct:.2f}%`)",
        f"• **Expected Shortfall (CVaR):** `${var_res.expected_shortfall_cvar_usd:,.2f}`",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def vstops_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View and manage client-side hidden virtual stops: /vstops [clear]"""
    from virtual_stops import get_virtual_stop_manager
    v_mgr = get_virtual_stop_manager()
    args = ctx.args or []
    sub = args[0].lower() if args else ""

    if sub in ("clear", "reset", "flush"):
        cleared = len(v_mgr.records)
        v_mgr.records.clear()
        v_mgr._save()
        await update.message.reply_text(f"🧹 Cleared `{cleared}` virtual stops records.")
        return

    active = v_mgr.active_stops()
    lines = [
        f"🛡️ **Client-Side Virtual Hidden Stops (Anti-Stop-Hunting)**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Hides exact stop loss orders from broker book; triggers market exits locally.",
        f"• **Active Hidden Stops:** `{len(active)}`",
    ]
    if active:
        for r in active:
            tp_str = f" | TP1: ${r.virtual_tp1:,.2f}" if r.virtual_tp1 else ""
            lines.append(
                f"• `{r.symbol}` **{r.direction}** [#{r.ticket}]\n"
                f"  Entry: `${r.entry_price:,.2f}` | **Virtual SL:** `${r.virtual_stop:,.2f}`{tp_str}"
            )
    else:
        lines.append("• No active virtual stops currently pending.")

    lines.extend([
        f"",
        f"Usage: `/vstops` (view active) | `/vstops clear` (clear state)",
    ])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def switch_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Switch MT5 Live active account: /switch [<login>|<alias>]"""
    from mt5_accounts import get_account_manager, switch_broker_mode
    args = ctx.args or []
    target = args[0].lower() if args else ""
    if not target:
        bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
        mgr = get_account_manager()
        active = mgr.get_active_account()
        active_str = f"`{active.login}` ({active.alias})" if active else "None"
        await update.message.reply_text(
            f"🔄 **Switch MT5 Live Account**\n"
            f"• **Current Execution Engine:** `{bmode}`\n"
            f"• **Active MT5 Account:** {active_str}\n\n"
            f"**Commands:**\n"
            f"• `/switch live` — Connect / verify active MT5 Live account\n"
            f"• `/switch <alias|login>` — Switch to a saved MT5 live account\n"
            f"• `/mt5 accounts` — View all saved MT5 accounts\n"
            f"• Note: Paper trading has been removed; only MT5 Live is supported.",
            parse_mode="Markdown",
        )
        return

    if target in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
        from delta_broker import DeltaBroker
        key = getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", "")
        secret = getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", "")
        testnet = target == "delta_testnet" or getattr(config, "DELTA_EXCHANGE_TESTNET", False)
        base_url = getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
        lev = getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0)
        auto_lev = getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True)
        max_lev = getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0)
        try:
            delta_b = DeltaBroker(
                api_key=key,
                api_secret=secret,
                base_url=base_url,
                is_testnet=testnet,
                leverage=lev,
                auto_leverage=auto_lev,
                max_leverage=max_lev,
            )
            engine.broker = delta_b
            if hasattr(engine, "reset_halt"):
                engine.reset_halt()
            config.BROKER_MODE = "delta_live"
            await update.message.reply_text(
                f"🟢 **Switched to Delta Exchange India Broker**\n"
                f"• **Environment:** `{'TESTNET' if testnet else 'LIVE'}`\n"
                f"• **Wallet Balance:** `${delta_b.balance:,.2f}`\n"
                f"• **Available Equity:** `${delta_b.equity:,.2f}`\n"
                f"• **Dynamic Leverage:** Up to `{max_lev:.0f}x` (Auto: `{auto_lev}`)\n"
                f"• **Autotrade Ready:** Gold (XAUUSD) & Bitcoin (BTCUSD)\n\n"
                f"⚡ _Real-time Crypto & Derivatives order execution enabled._",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ **Delta Exchange Connection Failed:** {e}\n\n"
                f"Configure `DELTA_EXCHANGE_API_KEY` and `DELTA_EXCHANGE_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
        return

    sub_id = args[1] if len(args) > 1 else None
    ok, msg, _ = switch_broker_mode(target_mode=target, identifier=sub_id, engine=engine, config_mod=config)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def live_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quick switch & control for MT5 Live trading: /live [on|off|status|<login> <password> <server>]"""
    global autotrade_active
    from mt5_accounts import get_account_manager, switch_broker_mode
    args = ctx.args or []
    cmd = args[0].lower() if args else ""

    if cmd in ("off", "stop", "pause"):
        autotrade_active = False
        await update.message.reply_text(
            "⚠️ **Live Execution is Exclusive** (Live Autotrade Paused)\n"
            "Paper trading has been removed from this system. MT5 Live is the sole execution engine.\n"
            "Automated live trade execution is now **PAUSED**.\n\n"
            "• Existing open positions remain protected with broker-side SL/TP brackets.\n"
            "• To resume live automated trading: `/live on` or `/autotrade on`\n"
            "• To trigger the emergency circuit breaker: `/kill on`\n"
            "• To close open positions: `/close all`",
            parse_mode="Markdown",
        )
    elif cmd in ("paper", "demo"):
        ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif cmd in ("on", "start", "enable", "connect") or (len(args) == 0 and getattr(config, "BROKER_MODE", "paper") != "mt5_live"):
        target_id = args[1] if len(args) > 1 else None
        config.AUTOTRADE_LIVE_OPT_IN = True
        ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
        if ok:
            autotrade_active = True
            if hasattr(engine, "reset_halt"):
                engine.reset_halt()
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif len(args) >= 3 and args[0].isdigit():
        login_str = args[0]
        password = args[1]
        server = args[2]
        alias = args[3] if len(args) > 3 and not args[3].startswith("http") else "live"
        gw = args[3] if len(args) > 3 and args[3].startswith("http") else (args[4] if len(args) > 4 and args[4].startswith("http") else "")
        mgr = get_account_manager()
        try:
            acc = mgr.add_account(login_str, password, server, alias=alias, gateway_url=gw, set_active=True)
            config.AUTOTRADE_LIVE_OPT_IN = True
            ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
            if ok:
                autotrade_active = True
                if hasattr(engine, "reset_halt"):
                    engine.reset_halt()
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error adding live account: `{e}`", parse_mode="Markdown")
    else:
        text, markup = build_mt5_dashboard()
        try:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)


def set_paper_wallet_balance(amount: Any) -> Tuple[bool, str, Dict[str, Any]]:
    return False, "Paper trading wallet has been removed. MetaTrader 5 Live is the sole active broker.", {}


async def balance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View or update live MT5 account balance and equity."""
    from mt5_accounts import get_account_manager
    args = ctx.args or []
    b = engine.broker
    mgr = get_account_manager()

    if args:
        first_arg = str(args[0]).strip().lower()
        if first_arg in ("sync", "refresh", "pull", "fetch"):
            if b and hasattr(b, "get_account_info"):
                try:
                    info = await asyncio.to_thread(b.get_account_info)
                    bal = float(getattr(info, "balance", getattr(b, "balance", 0.0)))
                    eq = float(getattr(info, "equity", getattr(b, "equity", bal)))
                    if hasattr(risk_mgr, "state") and hasattr(risk_mgr.state, "equity"):
                        risk_mgr.state.equity = eq
                        risk_mgr.state.peak_equity = max(risk_mgr.state.peak_equity, eq)
                    if hasattr(risk_mgr, "equity"):
                        risk_mgr.equity = eq
                    act = mgr.get_active_account()
                    acc_tag = f"#{act.login}" if act and act.login else (f"#{info.login}" if hasattr(info, "login") and info.login else "Active")
                    await update.message.reply_text(
                        f"🟢 **MT5 Balance Synchronized**\n"
                        f"• **Account:** `{acc_tag}` | **Server:** `{getattr(info, 'server', 'MT5')}`\n"
                        f"• **Live Balance:** `${bal:,.2f}`\n"
                        f"• **Live Equity:** `${eq:,.2f}`\n"
                        f"• **Margin:** `${getattr(info, 'margin', 0.0):,.2f}` | **Free Margin:** `${getattr(info, 'free_margin', 0.0):,.2f}`",
                        parse_mode="Markdown",
                    )
                    return
                except Exception as e:
                    logger.debug("balance sync error: %s", e)

        try:
            new_bal = float(args[0])
            if new_bal <= 0:
                raise ValueError("Balance must be a positive number.")
            if b and hasattr(b, "balance"):
                b.balance = new_bal
                b.equity = new_bal
                b.free_margin = new_bal
                if hasattr(b, "_initial_balance"):
                    b._initial_balance = new_bal
            if hasattr(risk_mgr, "state") and hasattr(risk_mgr.state, "equity"):
                risk_mgr.state.equity = new_bal
                risk_mgr.state.peak_equity = max(risk_mgr.state.peak_equity, new_bal)
            if hasattr(risk_mgr, "equity"):
                risk_mgr.equity = new_bal
            if hasattr(risk_mgr, "initial_equity"):
                risk_mgr.initial_equity = new_bal
            config.INITIAL_EQUITY = new_bal
            config.MT5_BALANCE = new_bal
            act = mgr.get_active_account()
            if act:
                act.balance = new_bal
                act.equity = new_bal
                mgr._save()
            await update.message.reply_text(
                f"🟢 **Live Account Balance Updated**\n"
                f"• **New Balance:** `${new_bal:,.2f}`\n"
                f"• **Risk Manager Equity:** `${new_bal:,.2f}`\n"
                f"• Position sizing and limits updated.",
                parse_mode="Markdown",
            )
            return
        except ValueError:
            pass

    # Query latest account info from broker if available
    acc_info = None
    if b and hasattr(b, "get_account_info"):
        try:
            acc_info = await asyncio.to_thread(b.get_account_info)
        except Exception:
            pass

    bal = float(getattr(acc_info, "balance", getattr(b, "balance", 0.0)) or 0.0) if b else 0.0
    eq = float(getattr(acc_info, "equity", getattr(b, "equity", bal)) or bal) if b else bal
    mg = float(getattr(acc_info, "margin", getattr(b, "margin", 0.0)) or 0.0) if b else 0.0
    free_m = float(getattr(acc_info, "free_margin", getattr(b, "free_margin", eq)) or eq) if b else eq
    lev = float(getattr(acc_info, "leverage", getattr(b, "leverage", 20.0)) or 20.0) if b else 20.0
    acc_login = getattr(acc_info, "login", getattr(b, "login", 0)) or "Active"
    acc_server = getattr(acc_info, "server", getattr(b, "server", "MT5 Terminal")) or "MT5 Terminal"

    await update.message.reply_text(
        "⚠️ **Paper Trading Wallet Removed**\n"
        "Paper trading and manual wallet simulation have been removed.\n"
        "MetaTrader 5 Live is the sole exclusive broker.\n\n"
        "🏦 **MetaTrader 5 Live Account Balance**\n"
        f"• **Account:** `#{acc_login}` | **Server:** `{acc_server}`\n"
        f"• **Live Balance:** `${bal:,.2f}` | **Equity:** `${eq:,.2f}`\n"
        f"• **Margin:** `${mg:,.2f}` | **Free Margin:** `${free_m:,.2f}` | **Leverage:** `{lev:.0f}x`\n\n"
        "• To update balance manually: `/balance <amount>`\n"
        "• To refresh from terminal: `/balance sync`\n"
        "• Use `/mt5 status` or `/live` to view detailed telemetry.",
        parse_mode="Markdown",
    )



async def delta_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manage Delta Exchange India live broker, orders, and manual/auto leverage up to 200x."""
    from delta_broker import DeltaBroker
    args = list(ctx.args or [])

    env_key = getattr(config, "DELTA_EXCHANGE_API_KEY", "") or os.getenv("DELTA_EXCHANGE_API_KEY", "")
    env_secret = getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or os.getenv("DELTA_EXCHANGE_API_SECRET", "")

    async def _connect_and_reply(key: str, secret: str, is_testnet: bool = False, base_url: str = ""):
        try:
            new_broker = DeltaBroker(
                api_key=key,
                api_secret=secret,
                base_url=base_url or ("" if is_testnet else getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")),
                is_testnet=is_testnet,
                leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
            )
            engine.broker = new_broker
            if hasattr(engine, "reset_halt"):
                engine.reset_halt()
            config.DELTA_EXCHANGE_API_KEY = key
            config.DELTA_EXCHANGE_API_SECRET = secret
            config.BROKER_MODE = "delta_testnet" if is_testnet else "delta_live"

            env_name = "TESTNET (Simulation)" if is_testnet else "LIVE (Delta Exchange India)"
            status_desc = "Active orders will execute on Delta Exchange testnet." if is_testnet else "Live deal execution active with server-side brackets & leverage."
            icon = "🧪" if is_testnet else "🟢"
            title = "Delta Exchange Testnet Connected Successfully" if is_testnet else "Delta Exchange India LIVE Connected Successfully"

            await update.message.reply_text(
                f"{icon} **{title}**\n"
                f"• **Environment:** `{env_name}`\n"
                f"• **Endpoint:** `{new_broker.base_url}`\n"
                f"• **Wallet Balance:** `${new_broker.balance:,.2f}` {new_broker.currency}\n"
                f"• **Equity:** `${new_broker.equity:,.2f}` {new_broker.currency}\n"
                f"• **Available Margin:** `${new_broker.free_margin:,.2f}`\n"
                f"• **Max Leverage:** `200x`\n"
                f"• **Status:** {status_desc}",
                parse_mode="Markdown",
            )
            return True
        except Exception as exc:
            logger.error("Delta Exchange connection error: %s", exc)
            await update.message.reply_text(
                f"❌ **Delta Exchange Connection Failed**\n\n"
                f"`{exc}`\n\n"
                f"🔍 **Troubleshooting Tips:**\n"
                f"1. **Live vs Testnet**: Testnet keys are created at `https://testnet.delta.exchange`. Live keys are created at `https://india.delta.exchange`.\n"
                f"2. **Format**: Connect with `/delta live <API_KEY> <API_SECRET>` or `/delta testnet <API_KEY> <API_SECRET>`.\n"
                f"3. **IP Whitelist**: If enabled on your Delta account, ensure your server IP is whitelisted.\n"
                f"4. **Permissions**: Make sure 'Read' and 'Trade' permissions are active on the key.",
                parse_mode="Markdown",
            )
            return False

    # 1. No arguments: /delta
    if not args:
        is_delta_active = (
            engine.broker is not None
            and (type(engine.broker).__name__ in ("DeltaBroker", "MockDeltaBroker") or hasattr(engine.broker, "is_testnet"))
        )
        if is_delta_active:
            b = engine.broker
            env_str = "TESTNET (Sandbox)" if getattr(b, "is_testnet", False) else "LIVE (Delta Exchange India)"
            curr = getattr(b, "currency", "USD")
            lev = getattr(b, "default_leverage", risk_mgr.get_leverage())
            auto_lev = getattr(b, "auto_leverage", True)
            auto_str = "ON (Dynamic up to 200x)" if auto_lev else "OFF (Manual)"
            open_pos = len(b.get_positions()) if hasattr(b, "get_positions") else 0
            base_url = getattr(b, "base_url", "https://api.india.delta.exchange")
            await update.message.reply_text(
                f"🇮🇳 **Delta Exchange India Broker Telemetry**\n"
                f"• **Status:** 🟢 Connected & Active\n"
                f"• **Environment:** `{env_str}`\n"
                f"• **Endpoint:** `{base_url}`\n"
                f"• **Wallet Balance:** `${b.balance:,.2f}` {curr}\n"
                f"• **Equity:** `${b.equity:,.2f}` {curr}\n"
                f"• **Free Margin:** `${b.free_margin:,.2f}`\n"
                f"• **Active Leverage:** `{lev:.0f}x` (Max: 200x)\n"
                f"• **Auto-Leverage:** `{auto_str}`\n"
                f"• **Open Positions:** `{open_pos}`\n\n"
                f"⚡ **Quick Controls:**\n"
                f"• `/delta leverage <1-200>` — Set leverage\n"
                f"• `/delta auto_leverage [on|off]` — Dynamic scaling\n"
                f"• `/delta positions` — View open positions\n"
                f"• `/delta status` — Refresh balance",
                parse_mode="Markdown",
            )
            return
        elif env_key and env_secret:
            is_test = getattr(config, "DELTA_EXCHANGE_TESTNET", False)
            await _connect_and_reply(env_key, env_secret, is_testnet=is_test)
            return
        else:
            bname = type(engine.broker).__name__ if engine.broker else "None"
            await update.message.reply_text(
                f"🇮🇳 **Delta Exchange India Live Broker**\n"
                f"• **Current Broker:** `{bname}` (Delta Exchange is not active)\n\n"
                f"🚀 **Connect Delta Exchange India Live:**\n"
                f"• `/delta live <API_KEY> <API_SECRET>` — Connect live account for real trade execution\n"
                f"• `/delta testnet <API_KEY> <API_SECRET>` — Connect testnet sandbox\n"
                f"• `/delta <API_KEY> <API_SECRET>` — Direct connection (auto-detects Live/Testnet)\n\n"
                f"⚡ **Leverage Controls (Max 200x):**\n"
                f"• `/delta leverage <1-200> [SYMBOL]` — Manually set order leverage (up to 200x)\n"
                f"• `/delta auto_leverage [on|off]` — Dynamic auto-leverage up to 200x\n\n"
                f"📋 **Positions & Orders:**\n"
                f"• `/delta positions` — List open Delta Exchange positions\n"
                f"• `/delta status` — Refresh balance & telemetry\n\n"
                f"_Tip: Set `DELTA_EXCHANGE_API_KEY` and `DELTA_EXCHANGE_API_SECRET` in `.env` for zero-click automatic connection._",
                parse_mode="Markdown",
            )
            return

    sub = args[0].lower()

    # 2. Subcommands /delta status / /delta info
    if sub in ("status", "info"):
        is_delta_active = (
            engine.broker is not None
            and (type(engine.broker).__name__ in ("DeltaBroker", "MockDeltaBroker") or hasattr(engine.broker, "is_testnet"))
        )
        if is_delta_active:
            b = engine.broker
            if hasattr(b, "refresh_balances"):
                try:
                    b.refresh_balances()
                except Exception as ref_err:
                    logger.warning("Delta balance refresh error: %s", ref_err)
            env_str = "TESTNET (Sandbox)" if getattr(b, "is_testnet", False) else "LIVE (Delta Exchange India)"
            curr = getattr(b, "currency", "USD")
            lev = getattr(b, "default_leverage", risk_mgr.get_leverage())
            auto_lev = getattr(b, "auto_leverage", True)
            auto_str = "ON (Dynamic up to 200x)" if auto_lev else "OFF (Manual)"
            open_pos = len(b.get_positions()) if hasattr(b, "get_positions") else 0
            base_url = getattr(b, "base_url", "https://api.india.delta.exchange")
            await update.message.reply_text(
                f"🇮🇳 **Delta Exchange India Telemetry**\n"
                f"• **Status:** 🟢 Connected & Active\n"
                f"• **Environment:** `{env_str}`\n"
                f"• **Endpoint:** `{base_url}`\n"
                f"• **Wallet Balance:** `${b.balance:,.2f}` {curr}\n"
                f"• **Equity:** `${b.equity:,.2f}` {curr}\n"
                f"• **Free Margin:** `${b.free_margin:,.2f}`\n"
                f"• **Active Leverage:** `{lev:.0f}x` (Max: 200x)\n"
                f"• **Auto-Leverage:** `{auto_str}`\n"
                f"• **Open Positions:** `{open_pos}`",
                parse_mode="Markdown",
            )
            return
        elif env_key and env_secret:
            is_test = getattr(config, "DELTA_EXCHANGE_TESTNET", False)
            await _connect_and_reply(env_key, env_secret, is_testnet=is_test)
            return
        else:
            await update.message.reply_text(
                "❌ **Delta Exchange is not active**\n\n"
                "Please connect using:\n"
                "`/delta live <API_KEY> <API_SECRET>`\n"
                "or set `DELTA_EXCHANGE_API_KEY` and `DELTA_EXCHANGE_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
            return

    # 3. /delta connect / on / enable with no extra args
    if sub in ("delta", "connect", "on", "enable") and len(args) == 1:
        if env_key and env_secret:
            is_test = getattr(config, "DELTA_EXCHANGE_TESTNET", False)
            await _connect_and_reply(env_key, env_secret, is_testnet=is_test)
            return
        else:
            await update.message.reply_text(
                "❌ **Missing Delta Exchange API Credentials**\n\n"
                "Please connect using:\n"
                "`/delta live <API_KEY> <API_SECRET>`\n"
                "or configure `DELTA_EXCHANGE_API_KEY` and `DELTA_EXCHANGE_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
            return

    # 4. /delta live [KEY] [SECRET] [URL]
    if sub in ("live", "connect"):
        key = args[1].strip() if len(args) > 1 else env_key
        secret = args[2].strip() if len(args) > 2 else env_secret
        base_url = args[3].strip() if len(args) > 3 else getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
        if not key or not secret:
            await update.message.reply_text(
                "❌ **Missing Credentials**\n"
                "Usage: `/delta live <API_KEY> <API_SECRET>`",
                parse_mode="Markdown",
            )
            return
        await _connect_and_reply(key, secret, is_testnet=False, base_url=base_url)
        return

    # 5. /delta testnet [KEY] [SECRET]
    if sub in ("testnet", "demo"):
        key = args[1].strip() if len(args) > 1 else env_key
        secret = args[2].strip() if len(args) > 2 else env_secret
        if not key or not secret:
            await update.message.reply_text(
                "Usage: `/delta testnet <API_KEY> <API_SECRET>`\n"
                "Connects to Delta Exchange Testnet (`https://cdn-ind.testnet.deltaex.org` / `https://testnet.delta.exchange`).\n\n"
                "💡 *Did you create your keys on the live exchange?*\n"
                "If trading real money on Delta Exchange India, connect with:\n"
                "`/delta live <API_KEY> <API_SECRET>`",
                parse_mode="Markdown",
            )
            return
        await _connect_and_reply(key, secret, is_testnet=True)
        return

    # 6. Direct keys: /delta <API_KEY> <API_SECRET>
    if len(args) >= 2 and sub not in ("leverage", "lev", "auto_leverage", "autolev", "positions", "pos", "close"):
        key = args[0].strip()
        secret = args[1].strip()
        await _connect_and_reply(key, secret, is_testnet=False)
        return

    # 7. Single argument with delimiter (e.g. KEY:SECRET or KEY,SECRET)
    if len(args) == 1 and any(delim in args[0] for delim in (":", ",", "|")):
        for delim in (":", ",", "|"):
            if delim in args[0]:
                parts = args[0].split(delim, 1)
                await _connect_and_reply(parts[0].strip(), parts[1].strip(), is_testnet=False)
                return

    # 8. /delta leverage <1-200> [SYMBOL]
    elif sub in ("leverage", "lev"):
        if len(args) < 2:
            curr_lev = getattr(engine.broker, "default_leverage", risk_mgr.get_leverage()) if engine.broker else risk_mgr.get_leverage()
            await update.message.reply_text(
                f"⚡ **Delta Exchange Leverage**\n"
                f"• **Current Leverage:** `{curr_lev:.0f}x` (Max: `200x`)\n"
                f"• **Usage:** `/delta leverage <1-200> [SYMBOL]`\n"
                f"• **Example:** `/delta leverage 100` or `/delta leverage 200 BTCUSD`",
                parse_mode="Markdown",
            )
            return
        raw_val = args[1].strip().lower().replace("x", "")
        target_sym = args[2].strip().upper() if len(args) > 2 else getattr(config, "DEFAULT_SYMBOL", "BTCUSD")
        try:
            val = float(raw_val)
            if val < 1.0 or val > 200.0:
                await update.message.reply_text("❌ Leverage must be between 1x and 200x.")
                return

            risk_mgr.set_leverage(val)
            broker_msg = ""
            if hasattr(engine.broker, "set_leverage"):
                try:
                    ok, bmsg = engine.broker.set_leverage(target_sym, val)
                    broker_msg = f"\n• **Exchange Response:** {bmsg}"
                except Exception as b_err:
                    broker_msg = f"\n• **Exchange Warning:** {b_err}"

            margin_pct = (1.0 / val) * 100.0
            await update.message.reply_text(
                f"✅ **Delta Exchange Leverage Updated**\n"
                f"• **Symbol:** `{target_sym}`\n"
                f"• **New Leverage:** `{val:.0f}x`\n"
                f"• **Initial Margin Required:** `{margin_pct:.2f}%`{broker_msg}\n"
                f"• **Safety Note:** Stop-loss is recommended to protect against liquidation barrier.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Invalid leverage value: {e}")

    # 9. /delta auto_leverage [on|off]
    elif sub in ("auto_leverage", "autolev"):
        sub_arg = args[1].lower() if len(args) > 1 else "toggle"
        curr_state = getattr(engine.broker, "auto_leverage", True) if engine.broker else True
        if sub_arg in ("on", "true", "1", "enable"):
            new_state = True
        elif sub_arg in ("off", "false", "0", "disable"):
            new_state = False
        else:
            new_state = not curr_state

        if hasattr(engine.broker, "auto_leverage"):
            engine.broker.auto_leverage = new_state
        config.DELTA_EXCHANGE_AUTO_LEVERAGE = new_state

        status_icon = "🟢" if new_state else "⚪"
        status_str = "ENABLED (Dynamic Scaling up to 200x)" if new_state else "DISABLED (Fixed Manual Leverage)"
        await update.message.reply_text(
            f"{status_icon} **Delta Exchange Auto-Leverage {status_str}**\n\n"
            f"• **How it works:** Auto-calculates optimal leverage from 1x to 200x based on stop distance and liquidation safety barrier.\n"
            f"• **Safety Buffer:** Ensures liquidation barrier is placed safely beyond the trade's stop-loss.\n"
            f"• **Max Cap:** `200x`",
            parse_mode="Markdown",
        )

    # 10. /delta positions
    elif sub in ("positions", "pos"):
        if engine.broker and hasattr(engine.broker, "get_positions"):
            try:
                positions = engine.broker.get_positions()
                if not positions:
                    await update.message.reply_text("ℹ️ No active open positions on Delta Exchange India.")
                    return
                lines = [f"🇮🇳 **Delta Exchange Active Positions ({len(positions)}):**"]
                for p in positions:
                    icon = "📈" if p.unrealized_pnl >= 0 else "📉"
                    curr_p = getattr(p, "current_price", p.entry_price) or p.entry_price
                    lines.append(
                        f"• `{p.symbol}` **{p.side.upper()}** `{p.qty}` contracts @ Entry `${p.entry_price:,.2f}` | "
                        f"**Mark:** `${curr_p:,.2f}` | PnL: {icon} `${p.unrealized_pnl:+,.2f}`"
                    )
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"⚠️ Error fetching Delta positions: `{e}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ No active Delta Exchange broker attached.")

    # 11. /delta close [SYM|all]
    elif sub == "close":
        target = args[1].upper() if len(args) > 1 else "ALL"
        if engine.broker and hasattr(engine.broker, "close_position"):
            try:
                if target == "ALL":
                    positions = list(engine.broker.get_positions())
                    if not positions:
                        await update.message.reply_text("ℹ️ No active positions to close.")
                        return
                    closed_cnt = 0
                    for p in positions:
                        if engine.broker.close_position(p.symbol):
                            closed_cnt += 1
                    await update.message.reply_text(f"✅ Closed {closed_cnt} Delta Exchange position(s).")
                else:
                    ok = engine.broker.close_position(target)
                    if ok:
                        await update.message.reply_text(f"✅ Position for `{target}` closed successfully.", parse_mode="Markdown")
                    else:
                        await update.message.reply_text(f"⚠️ Could not close `{target}` (position not found).", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Error closing position: `{e}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ No active Delta Exchange broker attached.")

    else:
        await update.message.reply_text(
            "🇮🇳 **Delta Exchange India Commands:**\n"
            "• `/delta status` — Check Delta account telemetry & connection\n"
            "• `/delta live [API_KEY] [API_SECRET]` — Connect live Delta Exchange India account\n"
            "• `/delta testnet [API_KEY] [API_SECRET]` — Connect Delta testnet account\n"
            "• `/delta leverage <1-200> [SYMBOL]` — Manually set leverage (up to 200x)\n"
            "• `/delta auto_leverage [on|off]` — Toggle auto-scaling dynamic leverage (max 200x)\n"
            "• `/delta positions` — Show all active Delta Exchange positions\n"
            "• `/delta close [SYM|all]` — Close open position(s)",
            parse_mode="Markdown",
        )


async def broker_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if not args:
        bname = type(engine.broker).__name__ if engine.broker else "None"
        bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
        summary = ""
        if hasattr(engine.broker, "get_account_info"):
            try:
                summary = f"\n\n{engine.broker.get_account_info().summary()}"
            except Exception:
                pass
        await update.message.reply_text(
            f"🏦 **Execution Broker Environment**\n"
            f"• **Active Broker:** `{bname}`\n"
            f"• **Active Mode:** `{bmode}`{summary}\n\n"
            f"🖥️ **MetaTrader 5 (Exclusive Live Execution Engine):**\n"
            f"• `/mt5 live <LOGIN> <PASSWORD> [SERVER]` — Live MT5 terminal execution\n"
            f"• `/broker mt5` — Set MT5 as active broker\n"
            f"• `/switch <delta|live|<login>>` — Switch active broker engine\n\n"
            f"🇮🇳 **Delta Exchange India Live REST Engine:**\n"
            f"• `/broker delta` — Delta Exchange India Live REST engine\n"
            f"• `/switch delta` — Switch to Delta Exchange India\n\n"
            f"⚠️ Note: Paper trading and legacy brokers (OANDA, Alpaca, CCXT) have been removed.",
            parse_mode="Markdown",
        )
        return

    mode = args[0].lower()
    if mode in ("paper", "demo", "sim"):
        await update.message.reply_text(
            "❌ **Paper Trading Mode Removed**\n"
            "Paper simulation has been removed. MetaTrader 5 Live and Delta Exchange are active.",
            parse_mode="Markdown",
        )
    elif mode in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
        from delta_broker import DeltaBroker
        key = getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", "")
        secret = getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", "")
        testnet = mode == "delta_testnet" or getattr(config, "DELTA_EXCHANGE_TESTNET", False)
        base_url = getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
        lev = getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0)
        auto_lev = getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True)
        max_lev = getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0)
        try:
            delta_b = DeltaBroker(
                api_key=key,
                api_secret=secret,
                base_url=base_url,
                is_testnet=testnet,
                leverage=lev,
                auto_leverage=auto_lev,
                max_leverage=max_lev,
            )
            engine.broker = delta_b
            if hasattr(engine, "reset_halt"):
                engine.reset_halt()
            config.BROKER_MODE = "delta_live"
            await update.message.reply_text(
                f"✅ **Switched to Delta Exchange India Broker**\n"
                f"• **Environment:** `{'TESTNET' if testnet else 'LIVE'}`\n"
                f"• **Wallet Balance:** `${delta_b.balance:,.2f}`\n"
                f"• **Autotrade Status:** `{'ACTIVE' if config.AUTOTRADE_ENABLED else 'PAUSED'}`\n"
                f"• **Max Leverage:** `{max_lev:.0f}x` (Auto: `{auto_lev}`)\n"
                f"• **Supported Pairs:** BTCUSD, XAUTUSD (Gold), ETHUSD",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ **Delta Exchange Connection Failed:** {e}\n\n"
                f"Configure `DELTA_EXCHANGE_API_KEY` and `DELTA_EXCHANGE_API_SECRET` in `.env`.",
                parse_mode="Markdown",
            )
    elif mode in ("mt5", "mt5_live", "live"):
        from mt5_accounts import switch_broker_mode
        target_id = args[1] if len(args) > 1 else None
        ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif mode in ("oanda", "alpaca", "ccxt"):
        await update.message.reply_text(
            f"⚠️ **Native Live Broker `{mode.upper()}` Removed**\n"
            f"MetaTrader 5 (`/mt5`) is now the exclusive live execution broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
            f"• To connect live MT5: `/mt5 live <LOGIN> <PASSWORD> [SERVER]`\n"
            f"• To list accounts: `/mt5 accounts`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Usage: `/broker` | `/mt5 status|live|accounts` | `/switch <alias|login>`",
            parse_mode="Markdown",
        )


async def oanda_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **OANDA Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.",
        parse_mode="Markdown",
    )


async def alpaca_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **Alpaca Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.",
        parse_mode="Markdown",
    )


async def ccxt_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **CCXT Live Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.",
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
        from mt5_chart import get_mt5_combined_market_context
        market_context = await asyncio.to_thread(get_mt5_combined_market_context, broker=getattr(engine, "broker", None))
    except Exception:
        try:
            info = await asyncio.to_thread(get_price_info, config.DEFAULT_SYMBOL)
            if info.get("ok"):
                market_context = f"{config.DEFAULT_SYMBOL} mark={info.get('mark')} change={info.get('change_pct')}%"
        except Exception:
            pass
    if hasattr(ctx, "bot") and hasattr(ctx.bot, "send_chat_action"):
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
    reply = await asyncio.to_thread(gemini.ask, chat_id=chat_id, user_text=user_text, market_context=market_context)
    try:
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(reply.replace("*", "").replace("`", ""))


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

                from anti_spam import get_anti_spam_limiter
                limiter = get_anti_spam_limiter()

                events = engine.update_market_candle(symbol, candle_dict)
                for ev in events:
                    logger.info(f"Execution Event: {ev['event']} - {ev.get('message')}")
                    etype = ev.get("event")
                    if etype in ("TRADE_CLOSED", "SCALP_SL", "SCALP_TRAIL_HIT", "STOP_LOSS_HIT"):
                        fpnl = float(ev.get("final_pnl", ev.get("realized_pnl", 0.0)) or 0.0)
                        if fpnl < 0:
                            limiter.record_loss(symbol, abs(fpnl))
                        else:
                            limiter.record_win(symbol)
                    elif etype == "TRADE_EXECUTED":
                        pos = ev.get("order")
                        eprice = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or candle_dict["close"])
                        limiter.record_entry(symbol, eprice)
                    if config.TELEGRAM_CHAT_ID:
                        try:
                            await app.bot.send_message(
                                chat_id=config.TELEGRAM_CHAT_ID, text=ev["message"], parse_mode="Markdown"
                            )
                        except Exception:
                            pass

                is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6)) or 6
                open_n = len(engine.active_positions) + len(engine.pending_setups)
                if not autotrade_active:
                    can_open = False
                elif is_scalping:
                    can_open, spam_reason = limiter.check_can_enter(
                        symbol=symbol,
                        current_price=candle_dict["close"],
                        engine=engine,
                        max_open=max_trades,
                    )
                else:
                    can_open = (
                        open_n < max_trades
                        and symbol not in engine.active_positions
                        and symbol not in engine.pending_setups
                    )
                    if can_open:
                        can_open, spam_reason = limiter.check_can_enter(
                            symbol=symbol,
                            current_price=candle_dict["close"],
                            engine=engine,
                            max_open=max_trades,
                        )
                if can_open:
                    sig, why, votes = arbiter.decide(
                        df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr, symbol=symbol, on_demand=True
                    )
                    if not sig:
                        logger.info("Autotrade: no setup — %s", why)
                    if sig:
                        order = engine.register_setup(sig, reason=sig.get("reason", ""))
                        limiter.record_entry(symbol, order.entry_level)
                        lot_mode_tag = " (Manual)" if getattr(order, "lot_mode", "auto") == "manual" else " (Auto)"
                        alert = (
                            f"⚡ **NEW MULTI-AGENT SETUP**\n"
                            f"`{symbol}` **{order.direction}**\n"
                            f"Entry `${order.entry_level:,.2f}` | SL `${order.stop_loss:,.2f}`\n"
                            f"Lots `{order.lots}`{lot_mode_tag} | Lev `{order.leverage:.0f}x`\n"
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
                            etype = ev.get("event")
                            if etype in ("TRADE_CLOSED", "SCALP_SL", "SCALP_TRAIL_HIT", "STOP_LOSS_HIT"):
                                fpnl = float(ev.get("final_pnl", ev.get("realized_pnl", 0.0)) or 0.0)
                                if fpnl < 0:
                                    limiter.record_loss(symbol, abs(fpnl))
                                else:
                                    limiter.record_win(symbol)
                            elif etype == "TRADE_EXECUTED":
                                pos = ev.get("order")
                                eprice = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or candle_dict["close"])
                                limiter.record_entry(symbol, eprice)
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


_background_autotrade_thread: Optional[threading.Thread] = None


def start_background_autotrade_worker() -> Optional[threading.Thread]:
    """Run live_monitoring_worker in a dedicated background thread for CLI/standalone mode."""
    global _background_autotrade_thread
    if _background_autotrade_thread and _background_autotrade_thread.is_alive():
        return _background_autotrade_thread

    try:
        from autonomous_learner import auto_start
        auto_start()
        logger.info("Autonomous learner auto-started in background")
    except Exception as e:
        logger.debug("Autonomous learner start skip: %s", e)

    try:
        from ops_dashboard import start_dashboard_background
        start_dashboard_background(port=8765)
        logger.info("Local Web Companion Dashboard started on http://127.0.0.1:8765")
    except Exception as e:
        logger.debug("Dashboard start skip: %s", e)

    def _worker_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(live_monitoring_worker(None))
        except Exception as e:
            logger.error("Background autotrade worker error: %s", e)
        finally:
            loop.close()

    _background_autotrade_thread = threading.Thread(
        target=_worker_thread, daemon=True, name="AutotradeWorkerThread"
    )
    _background_autotrade_thread.start()
    logger.info("Live autotrade monitoring worker started in background thread")
    return _background_autotrade_thread


def run_headless() -> None:
    """Run full autonomous trading system headlessly (no Telegram, 24/7 autotrade)."""
    print("==================================================")
    print(" [Nimble Darwin] Headless Autonomous Trading Engine")
    print(f" Watchlist: {', '.join(config.get_autotrade_symbols())}")
    print(f" Broker:    {getattr(config, 'BROKER_MODE', 'mt5_live')} (Equity: ${risk_mgr.state.equity:,.2f})")
    print(f" Autotrade: {'ACTIVE' if autotrade_active else 'OFF'}")
    print(f" Dashboard: http://localhost:8765")
    print("==================================================")

    try:
        from autonomous_learner import auto_start
        auto_start()
        logger.info("Autonomous learner auto-started")
    except Exception as e:
        logger.debug("Autonomous learner start skip: %s", e)

    try:
        from ops_dashboard import start_dashboard_background
        start_dashboard_background(port=8765)
    except Exception as e:
        logger.debug("Dashboard start skip: %s", e)

    print("Headless Trading Bot is running. Press Ctrl+C to terminate.")
    try:
        asyncio.run(live_monitoring_worker(None))
    except (KeyboardInterrupt, SystemExit):
        print("\n[Headless] Trading Bot terminated.")
    except Exception as e:
        logger.error("Headless autotrade worker crash: %s", e)
        raise


def run_cli_interactive():
    global autotrade_active
    start_background_autotrade_worker()
    st = "ACTIVE" if autotrade_active else "OFF"
    print("==================================================")
    print(" TradingBot CLI — Terminal Interactive Control Center")
    print(f" Autotrade Worker: {st} | Watchlist: {', '.join(config.get_autotrade_symbols())}")
    print(f" Broker: {getattr(config, 'BROKER_MODE', 'mt5_live')} | Dashboard: http://localhost:8765")
    print(" Type 'help' for commands, 'status', 'signal', 'positions', 'autotrade [on|off]', 'exit'")
    print("==================================================")
    while True:
        try:
            cmd = input("TradingBot> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting CLI.")
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
        elif name in ("chart", "charts"):
            from mt5_chart import get_mt5_live_chart, format_mt5_chart_card, format_combined_mt5_chart_dashboard
            b = getattr(engine, "broker", None)
            arg = parts[1].lower() if len(parts) > 1 else ""
            if any(x in arg for x in ("btc", "bitcoin", "crypto")):
                print(format_mt5_chart_card(get_mt5_live_chart("BTCUSD", broker=b)))
            elif any(x in arg for x in ("gold", "xau", "xauusd")):
                print(format_mt5_chart_card(get_mt5_live_chart("XAUUSD", broker=b)))
            else:
                print(format_combined_mt5_chart_dashboard(broker=b))
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
        elif name in ("risk", "resetrisk", "riskreset"):
            if len(parts) == 1 and name == "risk":
                print(risk_mgr.summary())
            else:
                args = parts[1:]
                args_lower = [a.lower() for a in args]
                is_restart = any(a in ("restart", "start") for a in args_lower)
                if "daily" in args_lower:
                    scope = "daily"
                elif "weekly" in args_lower:
                    scope = "weekly"
                else:
                    scope = "all"
                new_eq = None
                for a in args:
                    try:
                        val = float(a)
                        if val > 0:
                            new_eq = val
                            break
                    except ValueError:
                        pass
                print(reset_risk_limits(scope=scope, restart_autotrade=is_restart, new_equity=new_eq))
        elif name in ("positions", "slots", "maxpositions", "slot"):
            if len(parts) > 1:
                arg1 = parts[1].lower().strip()
                if arg1 in ("auto", "dynamic", "adaptive"):
                    ok, msg = engine.set_slots_mode("auto")
                    print(msg)
                elif arg1 in ("manual", "man", "fixed"):
                    cnt = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else int(getattr(engine, "manual_slots", 6))
                    ok, msg = engine.set_slots_mode("manual", cnt)
                    print(msg)
                elif arg1.isdigit():
                    cnt = int(arg1)
                    ok, msg = engine.set_slots_mode("manual", cnt)
                    print(msg)
                elif arg1 in ("sync", "refresh", "reconcile", "wallet"):
                    if hasattr(engine, "reconcile_positions"):
                        engine.reconcile_positions()
                    if hasattr(engine, "format_positions_detail"):
                        print(engine.format_positions_detail())
                    else:
                        print(engine.get_portfolio_summary())
                else:
                    if hasattr(engine, "format_positions_detail"):
                        print(engine.format_positions_detail())
                    else:
                        print(engine.get_portfolio_summary())
            else:
                if hasattr(engine, "format_positions_detail"):
                    print(engine.format_positions_detail())
                else:
                    print(engine.get_portfolio_summary())
        elif name in ("matrix", "heatmap", "mtf"):
            arg = parts[1].upper() if len(parts) > 1 else ""
            try:
                from mtf_matrix import render_ascii_matrix, render_telegram_multi_matrix
                if not arg or arg in ("ALL", "BOTH", "MULTI"):
                    print(render_telegram_multi_matrix(["XAUUSD", "BTCUSDT"]))
                else:
                    sym = "BTCUSDT" if arg in ("BTC", "BITCOIN", "BTCUSD") else ("XAUUSD" if arg in ("XAU", "GOLD") else arg)
                    print(render_ascii_matrix(sym))
            except Exception as e:
                print(f"Matrix error: {e}")
        elif name in ("dashboard", "web", "dash"):
            arg = parts[1].lower() if len(parts) > 1 else "start"
            try:
                from ops_dashboard import start_dashboard_background, stop_dashboard_background
                if arg in ("stop", "kill"):
                    stop_dashboard_background()
                    print("Web companion dashboard stopped.")
                else:
                    srv, url = start_dashboard_background(
                        host=getattr(config, "DASHBOARD_HOST", "127.0.0.1"),
                        port=getattr(config, "DASHBOARD_PORT", 8765),
                        status_file=getattr(config, "STATUS_FILE", ".runtime/bot_status.json"),
                        token=getattr(config, "DASHBOARD_TOKEN", ""),
                        engine=engine,
                    )
                    print(f"Web companion dashboard active: {url}")
            except Exception as e:
                print(f"Dashboard error: {e}")
        elif name in ("watchlist", "wl", "symbols", "assets"):
            arg = parts[1].lower() if len(parts) > 1 else ""
            if arg in ("btc", "bitcoin", "btcusdt"):
                config.set_autotrade_symbols(["BTCUSDT"])
                print("Watchlist set to: BTCUSDT (Bitcoin Only)")
            elif arg in ("gold", "xau", "xauusd"):
                config.set_autotrade_symbols(["XAUUSD"])
                print("Watchlist set to: XAUUSD (Gold Only)")
            elif arg in ("both", "all", "btc,gold"):
                config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"])
                print("Watchlist set to: BTCUSDT, XAUUSD (Both)")
            else:
                active = config.get_autotrade_symbols()
                print("=== Watchlist Telemetry ===")
                for s in ("BTCUSDT", "XAUUSD"):
                    p = get_price_info(s)
                    st = "ACTIVE" if s in active else "STANDBY"
                    print(f"• {s}: ${float(p.get('mark') or p.get('close') or 0):,.2f} ({p.get('change_pct', 0.0):+.2f}%) [{st}]")
                print(f"Active Selection: {', '.join(active)} | Autotrade: {'ON' if autotrade_active else 'OFF'}")
        elif name == "autotrade":
            args = [p.lower() for p in parts[1:]]
            arg = args[0] if args else ""
            broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
            sym_choice = None
            for a in args:
                if a in ("btc", "bitcoin", "btcusdt"):
                    sym_choice = ["BTCUSDT"]
                    break
                elif a in ("gold", "xau", "xauusd"):
                    sym_choice = ["XAUUSD"]
                    break
                elif a in ("both", "all"):
                    sym_choice = ["BTCUSDT", "XAUUSD"]
                    break
            if sym_choice:
                config.set_autotrade_symbols(sym_choice)
                print(f"Autotrade symbols set to: {', '.join(sym_choice)}")
            if any(a in ("scalp", "spam") for a in args):
                try:
                    from strategy_profiles import apply_profile
                    apply_profile("scalp", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
                except Exception:
                    pass
                autotrade_active = True
                engine.reset_halt()
                print(f"Autotrade ON in RAPID SCALP SPAM MODE ({broker_mode} mode) for {', '.join(config.get_autotrade_symbols())}")
            elif arg in ("on", "1", "true", "enable") or (sym_choice and arg in ("btc", "gold", "both", "all")):
                autotrade_active = True
                engine.reset_halt()
                print(f"Autotrade ON ({broker_mode} mode) for {', '.join(config.get_autotrade_symbols())}")
            elif arg in ("off", "0", "false", "disable", "stop"):
                autotrade_active = False
                print("Autotrade OFF")
            else:
                st = "ON" if autotrade_active else "OFF"
                prof = getattr(config, "CURRENT_PROFILE", "scalp").upper()
                print(f"Autotrade is {st} ({broker_mode}, profile={prof}, symbols={', '.join(config.get_autotrade_symbols())}). Use: autotrade [on|off|scalp] [btc|gold|both]")
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
        elif name in ("lot", "lots", "lotsize"):
            if len(parts) > 1:
                sub = parts[1].lower()
                if sub in ("auto", "dynamic", "risk"):
                    risk_mgr.set_auto_lot()
                    config.set_lot_size_mode("auto")
                    print("Lot sizing mode set to AUTO (dynamic risk-based calculation)")
                elif sub in ("manual", "fixed", "man"):
                    if len(parts) > 2:
                        try:
                            val = float(parts[2])
                            sym = parts[3].upper() if len(parts) > 3 else None
                            ok, msg = risk_mgr.set_manual_lot(val, symbol=sym)
                            if ok:
                                config.set_manual_lot_size(val, symbol=sym)
                                config.set_lot_size_mode("manual")
                                print(f"Manual lot size set to {val:.4f} lots{' for ' + sym if sym else ''}")
                            else:
                                print(f"Error: {msg}")
                        except Exception as e:
                            print(f"Invalid lot size: {e}")
                    else:
                        risk_mgr.set_lot_mode("manual")
                        config.set_lot_size_mode("manual")
                        print(f"Lot sizing mode set to MANUAL ({risk_mgr.get_manual_lot():.4f} lots)")
                else:
                    try:
                        val = float(sub)
                        sym = parts[2].upper() if len(parts) > 2 else None
                        ok, msg = risk_mgr.set_manual_lot(val, symbol=sym)
                        if ok:
                            config.set_manual_lot_size(val, symbol=sym)
                            config.set_lot_size_mode("manual")
                            print(f"Manual lot size set to {val:.4f} lots{' for ' + sym if sym else ''}")
                        else:
                            print(f"Error: {msg}")
                    except ValueError:
                        print("Usage: lot [auto | manual <size> [SYM] | <size> [SYM]]")
            else:
                m, l = risk_mgr.get_lot_mode()
                print(f"Lot Sizing Mode: {m.upper()} (Manual lot: {l:.4f} lots)")
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
                from performance_analytics import PerformanceTracker
                perf = PerformanceTracker()
                sub = parts[1].lower() if len(parts) > 1 else "summary"
                if sub in ("agents", "ranking", "rankings"):
                    print(perf.agent_report())
                elif sub in ("trades", "history", "recent", "closed"):
                    print(perf.recent_trades_report())
                elif sub in ("reset", "clear"):
                    perf.reset()
                    print("🧹 Performance Stats Reset: All closed trade history has been cleared.")
                else:
                    print(perf.telegram_report())
            except Exception as e:
                print(f"Performance error: {e}")
        elif name == "profile":
            try:
                from strategy_profiles import load_profiles, apply_profile, CURRENT_PROFILE
                profiles = load_profiles()
                if len(parts) > 1:
                    pname = parts[1].lower()
                    if pname in profiles:
                        p, summary = apply_profile(pname, arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
                        clean_summary = summary.replace("**", "").replace("*", "").replace("`", "").replace("_", "")
                        print(f"Profile: {p.name} ({p.description})\n" + clean_summary)
                    else:
                        print(f"Unknown profile '{pname}'. Available profiles: {', '.join(profiles.keys())}")
                else:
                    print(f"Available profiles: {', '.join(profiles.keys())} (Active: {CURRENT_PROFILE})")
            except Exception as e:
                print(f"Profile error: {e}")
        elif name in ("scalp", "microscalp"):
            try:
                from strategy_profiles import apply_profile
                args_sub = [p.lower() for p in parts[1:]]
                if any(a in ("off", "stop", "disable", "pause") for a in args_sub):
                    autotrade_active = False
                    print("⚪ Scalp autotrade stopped.")
                else:
                    for a in args_sub:
                        if a in ("gold", "xau", "xauusd", "xaut", "paxg"):
                            config.set_autotrade_symbols(["XAUUSD"])
                            break
                        elif a in ("btc", "bitcoin", "btcusdt", "btcusd", "xbt"):
                            config.set_autotrade_symbols(["BTCUSDT"])
                            break
                        elif a in ("both", "all", "btc,gold", "gold,btc", "btcusdt,xauusd"):
                            config.set_autotrade_symbols(["BTCUSDT", "XAUUSD"])
                            break
                    autotrade_active = True
                    if engine is not None and hasattr(engine, "reset_halt"):
                        engine.reset_halt()
                    config.PROFILE_LOCKED = True
                    if arbiter is not None:
                        arbiter.profile_locked = True
                    p, summary = apply_profile("scalp", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config, lock=True)
                    clean_summary = summary.replace("**", "").replace("*", "").replace("`", "").replace("_", "")
                    active_syms = ", ".join(config.get_autotrade_symbols())
                    print(f"⚡ SCALP PROFILE & AUTOTRADE ACTIVATED (PINNED):\nWatchlist: {active_syms} | Status: 🟢 ACTIVE\n{clean_summary}\n• Min R:R relaxed to 1.0:1 | Asian Session: Allowed | Slippage veto relaxed to 0.85 effective R:R")
            except Exception as e:
                print(f"Scalp activation error: {e}")
        elif name in ("ask", "chat"):
            query = " ".join(parts[1:]).strip()
            if not query:
                print("Usage: ask <question> or chat <message>")
            else:
                market_context = ""
                try:
                    from mt5_chart import get_mt5_combined_market_context
                    market_context = get_mt5_combined_market_context(broker=getattr(engine, "broker", None))
                except Exception:
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
            from mt5_accounts import get_account_manager, switch_broker_mode, connect_and_verify
            mgr = get_account_manager()
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
            elif sub in ("add", "save", "new", "register"):
                if len(parts) < 5:
                    print("Usage: mt5 add <LOGIN> <PASSWORD> <SERVER> [ALIAS] [GATEWAY_URL]")
                else:
                    login_str = parts[2]
                    password = parts[3]
                    server = parts[4]
                    alias = parts[5] if len(parts) > 5 and not parts[5].startswith("http") else ""
                    gw = ""
                    for a in parts[5:]:
                        if a.startswith("http://") or a.startswith("https://"):
                            gw = a
                            break
                    try:
                        acc = mgr.add_account(login_str, password, server, alias=alias, gateway_url=gw, set_active=True)
                        conn_ok, conn_msg, test_broker = connect_and_verify(acc, mode="live")
                        if conn_ok and test_broker:
                            print(f"MT5 Live Account Added & Verified: #{acc.login} ({acc.alias}) on {acc.server} | Balance: ${test_broker.balance:,.2f}")
                        else:
                            print(f"MT5 Account Saved (Verification Note): #{acc.login} ({acc.alias}) on {acc.server} | {conn_msg}")
                    except Exception as e:
                        print(f"Error adding account: {e}")
            elif sub in ("switch", "select", "use"):
                target = parts[2].lower() if len(parts) > 2 else "live"
                sub_id = parts[3] if len(parts) > 3 else None
                ok, msg, _ = switch_broker_mode(target_mode=target, identifier=sub_id, engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            elif sub in ("accounts", "list", "myaccounts"):
                print(mgr.format_account_list().replace("*", "").replace("`", ""))
            elif sub in ("remove", "delete", "rm", "clear"):
                if sub == "clear" or (len(parts) > 2 and parts[2].lower() in ("all", "*", "clear")):
                    count = mgr.clear_accounts()
                    print(f"All MT5 accounts removed ({count} cleared).")
                elif len(parts) < 3:
                    print("Usage: mt5 remove <login|alias|all>")
                else:
                    removed = mgr.remove_account(parts[2])
                    if removed:
                        print(f"MT5 Account Removed: {removed.alias} (#{removed.login} on {removed.server})")
                    else:
                        print(f"Account '{parts[2]}' not found.")
            elif sub in ("balance", "bal", "setbalance"):
                b = engine.broker
                if len(parts) > 2:
                    try:
                        new_bal = float(parts[2])
                        if b and hasattr(b, "balance"):
                            b.balance = new_bal
                            if hasattr(b, "_initial_balance"):
                                b._initial_balance = new_bal
                        if hasattr(risk_mgr, "equity"):
                            risk_mgr.equity = new_bal
                        if hasattr(risk_mgr, "initial_equity"):
                            risk_mgr.initial_equity = new_bal
                        config.INITIAL_EQUITY = new_bal
                        config.MT5_BALANCE = new_bal
                        act = mgr.get_active_account()
                        if act:
                            act.balance = new_bal
                            act.equity = new_bal
                            mgr._save()
                        print(f"MT5 Live Balance Updated: ${new_bal:,.2f} | Risk Equity: ${new_bal:,.2f}")
                    except Exception as e:
                        print(f"Error setting balance: {e}")
                else:
                    bal = float(getattr(b, "balance", 0.0) or 0.0) if b else 0.0
                    eq = float(getattr(b, "equity", bal) or bal) if b else bal
                    act = mgr.get_active_account()
                    act_str = f"#{act.login} ({act.alias})" if act else "None"
                    print(f"MT5 Live Balance ({act_str}): ${bal:,.2f} | Equity: ${eq:,.2f}")
            elif sub in ("paper", "demo"):
                ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            elif sub in ("live", "connect"):
                if len(parts) >= 5:
                    login_str = parts[2]
                    password = parts[3]
                    server = parts[4]
                    gw = parts[5] if len(parts) > 5 and parts[5].startswith("http") else ""
                    alias = parts[5] if len(parts) > 5 and not parts[5].startswith("http") else "live"
                    try:
                        acc = mgr.add_account(login_str, password, server, alias=alias, gateway_url=gw, set_active=True)
                        config.AUTOTRADE_LIVE_OPT_IN = True
                        ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
                        if ok:
                            autotrade_active = True
                            if hasattr(engine, "reset_halt"):
                                engine.reset_halt()
                        print(msg.replace("*", "").replace("`", ""))
                    except Exception as e:
                        print(f"Error: {e}")
                else:
                    target_id = parts[2] if len(parts) > 2 else None
                    config.AUTOTRADE_LIVE_OPT_IN = True
                    ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                    if ok:
                        autotrade_active = True
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                    print(msg.replace("*", "").replace("`", ""))
            elif sub in ("positions", "pos"):
                if engine.broker:
                    print(engine.broker.get_positions())
                else:
                    print("No broker attached.")
            elif sub in ("check", "test", "verify", "diag", "diagnose"):
                b = engine.broker
                if hasattr(b, "check_trading_readiness"):
                    symbols = getattr(config, "get_autotrade_symbols", lambda: ["XAUUSD", "BTCUSDT"])()
                    res = b.check_trading_readiness(symbols)
                    st = "READY FOR TRADING" if res.get("ready") else "TRADING BLOCKED"
                    print(f"MT5 Pre-Flight Diagnostics Check: {st}")
                    print(f"• Server: {res.get('server')} | Login: {res.get('login')}")
                    print(f"• Terminal Algo Trading: {'Enabled' if res.get('terminal_algo_trading') else 'DISABLED (Turn Algo Trading ON in MT5)'}")
                    print(f"• Account Trading: {'Enabled' if res.get('account_trading_allowed') else 'DISABLED (Check if Investor password was used)'}")
                    print(f"• Broker EA Allowed: {'Allowed' if res.get('broker_expert_allowed') else 'DISABLED by broker'}")
                    print(f"• Engine Halt State: {'HALTED (Run mt5 reset to clear)' if getattr(engine, 'execution_halted', False) else 'Active'}")
                    for bl in res.get("blockers", []):
                        print(f"  🚨 BLOCKER: {bl}")
                    for w in res.get("warnings", []):
                        print(f"  ⚠️ WARNING: {w}")
                else:
                    print("Current broker does not support pre-flight diagnostics.")
            elif sub in ("reset", "resethalt", "clearhalt", "unhalt"):
                if hasattr(engine, "reset_halt"):
                    engine.reset_halt()
                print("MT5 execution halt cleared. All trading requests and order placements unblocked.")
            elif sub in ("bridge", "ea"):
                from bridge import ea_client
                b = engine.broker
                magic = getattr(b, "magic", 112233) if b else 112233
                b_action = parts[2].lower() if len(parts) > 2 else "status"
                if b_action in ("ping", "test"):
                    t0 = time.time()
                    res = ea_client.status(magic=magic, timeout=5.0)
                    elapsed = (time.time() - t0) * 1000.0
                    print(f"EA Bridge Ping: {'PONG' if res.get('ok') else 'FAIL'} ({elapsed:.1f}ms) | {res}")
                elif b_action in ("on", "enable", "switch"):
                    config.MT5_BACKEND = "ea_bridge"
                    if b and hasattr(b, "backend"):
                        b.backend = "ea_bridge"
                        b.connect()
                    else:
                        from mt5_broker import MT5Broker
                        engine.broker = MT5Broker(mode="live", backend="ea_bridge", magic=magic)
                    print(f"Switched to EA Bridge backend (directory: {ea_client.BRIDGE_DIR})")
                elif b_action in ("off", "disable", "auto"):
                    config.MT5_BACKEND = "auto"
                    if b and hasattr(b, "backend"):
                        b.backend = "auto"
                        try:
                            b.connect()
                        except Exception:
                            pass
                    else:
                        try:
                            from mt5_broker import MT5Broker
                            engine.broker = MT5Broker(mode="live", backend="auto", magic=magic)
                        except Exception:
                            pass
                    eff_name = getattr(engine.broker, 'effective_backend', 'auto') if engine.broker else 'auto'
                    print(f"Switched to Auto MT5 backend (effective: {eff_name})")
                elif b_action in ("dir", "path", "setdir"):
                    if len(parts) > 3:
                        new_d = " ".join(parts[3:])
                        ea_client.set_bridge_dir(new_d)
                        config.EA_BRIDGE_DIR = new_d
                        print(f"EA Bridge directory updated to: {new_d}")
                    else:
                        print(f"EA Bridge directory: {ea_client.BRIDGE_DIR}")
                else:
                    cur_eff = getattr(b, "effective_backend", "N/A") if b else "N/A"
                    res = ea_client.status(magic=magic, timeout=2.0)
                    print(f"EA Bridge Status: {'Responsive' if res.get('ok') else 'Awaiting MT5 EA'} | Driver: {cur_eff} | Dir: {ea_client.BRIDGE_DIR} | Status: {res}")
            elif sub in ("backend", "driver"):
                b = engine.broker
                if len(parts) > 2:
                    tgt = parts[2].lower()
                    if tgt in ("ea_bridge", "bridge", "ea"):
                        tgt = "ea_bridge"
                    elif tgt in ("gateway", "rest"):
                        tgt = "gateway"
                    elif tgt in ("native",):
                        tgt = "native"
                    elif tgt in ("auto", "default"):
                        tgt = "auto"
                    else:
                        print("Usage: mt5 backend [ea_bridge|native|gateway|auto]")
                        tgt = None
                    if tgt:
                        config.MT5_BACKEND = tgt
                        if b and hasattr(b, "backend"):
                            b.backend = tgt
                            b.connect()
                        print(f"MT5 backend set to: {tgt} (effective driver: {getattr(b, 'effective_backend', 'N/A')})")
                else:
                    print(f"Configured backend: {getattr(config, 'MT5_BACKEND', 'auto')} | Effective driver: {getattr(b, 'effective_backend', 'N/A')}")
            else:
                print("Usage: mt5 [status|add|accounts|balance|switch|remove|live|paper|positions|check|reset|bridge|backend]")
        elif name in ("bridge", "ea"):
            from bridge import ea_client
            b = engine.broker
            magic = getattr(b, "magic", 112233) if b else 112233
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in ("ping", "test"):
                t0 = time.time()
                res = ea_client.status(magic=magic, timeout=5.0)
                elapsed = (time.time() - t0) * 1000.0
                print(f"EA Bridge Ping: {'PONG' if res.get('ok') else 'FAIL'} ({elapsed:.1f}ms) | {res}")
            elif sub in ("on", "enable", "switch"):
                config.MT5_BACKEND = "ea_bridge"
                if b and hasattr(b, "backend"):
                    b.backend = "ea_bridge"
                    b.connect()
                else:
                    from mt5_broker import MT5Broker
                    engine.broker = MT5Broker(mode="live", backend="ea_bridge", magic=magic)
                print(f"Switched to EA Bridge backend (directory: {ea_client.BRIDGE_DIR})")
            elif sub in ("off", "disable", "auto"):
                config.MT5_BACKEND = "auto"
                if b and hasattr(b, "backend"):
                    b.backend = "auto"
                    try:
                        b.connect()
                    except Exception:
                        pass
                else:
                    try:
                        from mt5_broker import MT5Broker
                        engine.broker = MT5Broker(mode="live", backend="auto", magic=magic)
                    except Exception:
                        pass
                eff_name = getattr(engine.broker, 'effective_backend', 'auto') if engine.broker else 'auto'
                print(f"Switched to Auto MT5 backend (effective: {eff_name})")
            elif sub in ("dir", "path", "setdir"):
                if len(parts) > 2:
                    new_d = " ".join(parts[2:])
                    ea_client.set_bridge_dir(new_d)
                    config.EA_BRIDGE_DIR = new_d
                    print(f"EA Bridge directory updated to: {new_d}")
                else:
                    print(f"EA Bridge directory: {ea_client.BRIDGE_DIR}")
            else:
                cur_eff = getattr(b, "effective_backend", "N/A") if b else "N/A"
                res = ea_client.status(magic=magic, timeout=2.0)
                print(f"EA Bridge Status: {'Responsive' if res.get('ok') else 'Awaiting MT5 EA'} | Driver: {cur_eff} | Dir: {ea_client.BRIDGE_DIR} | Status: {res}")
        elif name in ("switch", "switchbroker"):
            from mt5_accounts import get_account_manager, switch_broker_mode
            if len(parts) > 1:
                target = parts[1].lower()
                if target in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
                    from delta_broker import DeltaBroker
                    k = getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", "")
                    s = getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", "")
                    testnet = target == "delta_testnet" or getattr(config, "DELTA_EXCHANGE_TESTNET", False)
                    base_url = getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
                    try:
                        delta_b = DeltaBroker(
                            api_key=k,
                            api_secret=s,
                            base_url=base_url,
                            is_testnet=testnet,
                            leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                            auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                            max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
                        )
                        engine.broker = delta_b
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                        config.BROKER_MODE = "delta_live"
                        print(f"Switched to Delta Exchange India! Balance: ${delta_b.balance:,.2f}")
                    except Exception as e:
                        print(f"Delta connect error: {e}")
                else:
                    sub_id = parts[2] if len(parts) > 2 else None
                    ok, msg, _ = switch_broker_mode(target_mode=target, identifier=sub_id, engine=engine, config_mod=config)
                    print(msg.replace("*", "").replace("`", ""))
            else:
                bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
                mgr = get_account_manager()
                act = mgr.get_active_account()
                act_str = f"#{act.login} ({act.alias})" if act else "None"
                print(f"Current Broker Mode: {bmode} | Active MT5 Account: {act_str}")
                print("Commands: switch delta | switch live | switch <alias|login>")
        elif name in ("live", "livetrade"):
            from mt5_accounts import get_account_manager, switch_broker_mode
            sub_cmd = parts[1].lower() if len(parts) > 1 else ""
            if sub_cmd in ("off", "stop", "pause"):
                autotrade_active = False
                print("Live automated trading PAUSED. Open positions remain protected with broker-side SL/TP brackets.")
            elif sub_cmd in ("paper", "demo"):
                ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            elif sub_cmd in ("on", "start", "enable", "connect") or (len(parts) == 1 and getattr(config, "BROKER_MODE", "mt5_live") != "mt5_live"):
                config.AUTOTRADE_LIVE_OPT_IN = True
                target_id = parts[2] if len(parts) > 2 else None
                ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                if ok:
                    autotrade_active = True
                    if hasattr(engine, "reset_halt"):
                        engine.reset_halt()
                print(msg.replace("*", "").replace("`", ""))
            elif len(parts) >= 4 and parts[1].isdigit():
                mgr = get_account_manager()
                try:
                    acc = mgr.add_account(parts[1], parts[2], parts[3], alias=parts[4] if len(parts) > 4 else "live", set_active=True)
                    config.AUTOTRADE_LIVE_OPT_IN = True
                    ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
                    if ok:
                        autotrade_active = True
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                    print(msg.replace("*", "").replace("`", ""))
                except Exception as e:
                    print(f"Error: {e}")
            else:
                bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
                print(f"Current Mode: {bmode} (Usage: live on | live off | live <login> <password> <server>)")
        elif name in ("balance", "wallet", "equity"):
            from mt5_accounts import get_account_manager
            b = engine.broker
            mgr = get_account_manager()
            if len(parts) > 1:
                try:
                    new_bal = float(parts[1])
                    if b and hasattr(b, "balance"):
                        b.balance = new_bal
                        try:
                            b.equity = new_bal
                        except Exception:
                            pass
                        if hasattr(b, "_initial_balance"):
                            b._initial_balance = new_bal
                    if hasattr(risk_mgr, "state") and hasattr(risk_mgr.state, "equity"):
                        risk_mgr.state.equity = new_bal
                        risk_mgr.state.peak_equity = max(risk_mgr.state.peak_equity, new_bal)
                    if hasattr(risk_mgr, "equity"):
                        risk_mgr.equity = new_bal
                    if hasattr(risk_mgr, "initial_equity"):
                        risk_mgr.initial_equity = new_bal
                    config.INITIAL_EQUITY = new_bal
                    config.MT5_BALANCE = new_bal
                    act = mgr.get_active_account()
                    if act:
                        act.balance = new_bal
                        act.equity = new_bal
                        mgr._save()
                    print(f"Live Balance Updated: ${new_bal:,.2f} | Risk Equity: ${new_bal:,.2f}")
                except Exception as e:
                    print(f"Error setting balance: {e}")
            else:
                bal = float(getattr(b, "balance", 0.0) or 0.0) if b else 0.0
                eq = float(getattr(b, "equity", bal) or bal) if b else bal
                print("⚠️ Paper trading and paper wallet balance have been removed. MetaTrader 5 Live is the sole exclusive broker.")
                print(f"MT5 Live Balance: ${bal:,.2f} | Equity: ${eq:,.2f}")
                print("Usage: balance <amount> to update real live balance.")
        elif name == "delta":
            delta_parts = parts[1:]
            if not delta_parts:
                if engine.broker and type(engine.broker).__name__ in ("DeltaBroker", "MockDeltaBroker"):
                    b = engine.broker
                    env_str = "TESTNET" if getattr(b, "is_testnet", False) else "LIVE"
                    curr = getattr(b, "currency", "USD")
                    print(f"Delta Exchange ({env_str}): Balance: ${b.balance:,.2f} {curr} | Equity: ${b.equity:,.2f} | Free Margin: ${b.free_margin:,.2f}")
                elif engine.broker and hasattr(engine.broker, "get_account_info"):
                    print(engine.broker.get_account_info().summary())
                else:
                    print("Delta Exchange India: use 'delta live <key> <secret>' or 'delta leverage <1-200>'")
            elif delta_parts[0] in ("status", "info"):
                if engine.broker and type(engine.broker).__name__ in ("DeltaBroker", "MockDeltaBroker"):
                    b = engine.broker
                    if hasattr(b, "refresh_balances"):
                        try:
                            b.refresh_balances()
                        except Exception:
                            pass
                    env_str = "TESTNET" if getattr(b, "is_testnet", False) else "LIVE"
                    curr = getattr(b, "currency", "USD")
                    print(f"Delta Exchange ({env_str}): Balance: ${b.balance:,.2f} {curr} | Equity: ${b.equity:,.2f} | Free Margin: ${b.free_margin:,.2f}")
                else:
                    print("Delta Exchange is not active. Connect with 'delta live <key> <secret>'.")
            elif delta_parts[0] in ("live", "connect"):
                k = delta_parts[1] if len(delta_parts) > 1 else (getattr(config, "DELTA_EXCHANGE_API_KEY", "") or os.getenv("DELTA_EXCHANGE_API_KEY", ""))
                s = delta_parts[2] if len(delta_parts) > 2 else (getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or os.getenv("DELTA_EXCHANGE_API_SECRET", ""))
                if k and s:
                    try:
                        from delta_broker import DeltaBroker
                        engine.broker = DeltaBroker(
                            api_key=k,
                            api_secret=s,
                            is_testnet=False,
                            leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                            auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                            max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
                        )
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                        config.DELTA_EXCHANGE_API_KEY = k
                        config.DELTA_EXCHANGE_API_SECRET = s
                        config.BROKER_MODE = "delta_live"
                        print(f"Connected to Delta Exchange India Live! Balance: ${engine.broker.balance:,.2f}")
                    except Exception as exc:
                        print(f"Delta Live connect failed: {exc}")
                else:
                    print("Missing Delta API key or secret. Usage: delta live <key> <secret>")
            elif delta_parts[0] in ("testnet", "demo"):
                k = delta_parts[1] if len(delta_parts) > 1 else (getattr(config, "DELTA_EXCHANGE_API_KEY", "") or os.getenv("DELTA_EXCHANGE_API_KEY", ""))
                s = delta_parts[2] if len(delta_parts) > 2 else (getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or os.getenv("DELTA_EXCHANGE_API_SECRET", ""))
                if k and s:
                    try:
                        from delta_broker import DeltaBroker
                        engine.broker = DeltaBroker(
                            api_key=k,
                            api_secret=s,
                            is_testnet=True,
                            leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                            auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                            max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
                        )
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                        config.DELTA_EXCHANGE_API_KEY = k
                        config.DELTA_EXCHANGE_API_SECRET = s
                        config.BROKER_MODE = "delta_testnet"
                        print(f"Connected to Delta Exchange Testnet! Balance: ${engine.broker.balance:,.2f}")
                    except Exception as exc:
                        print(f"Delta Testnet connect failed: {exc}")
                else:
                    print("Missing Delta API key or secret. Usage: delta testnet <key> <secret>")
            elif delta_parts[0] in ("leverage", "lev"):
                if len(delta_parts) > 1:
                    try:
                        v = float(delta_parts[1].replace("x", ""))
                        risk_mgr.set_leverage(v)
                        if hasattr(engine.broker, "set_leverage"):
                            engine.broker.set_leverage(getattr(config, "DEFAULT_SYMBOL", "BTCUSD"), v)
                        print(f"Leverage set to {v:.0f}x")
                    except Exception as e:
                        print(f"Invalid leverage: {e}")
                else:
                    curr_lev = getattr(engine.broker, "default_leverage", risk_mgr.get_leverage()) if engine.broker else risk_mgr.get_leverage()
                    print(f"Current Leverage: {curr_lev:.0f}x (Usage: delta leverage <1-200>)")
            elif delta_parts[0] in ("auto_leverage", "autolev"):
                sub_arg = delta_parts[1].lower() if len(delta_parts) > 1 else "toggle"
                curr_state = getattr(engine.broker, "auto_leverage", True) if engine.broker else True
                if sub_arg in ("on", "true", "1", "enable"):
                    new_state = True
                elif sub_arg in ("off", "false", "0", "disable"):
                    new_state = False
                else:
                    new_state = not curr_state
                if hasattr(engine.broker, "auto_leverage"):
                    engine.broker.auto_leverage = new_state
                config.DELTA_EXCHANGE_AUTO_LEVERAGE = new_state
                state_str = "ENABLED" if new_state else "DISABLED"
                print(f"Delta Exchange Auto-Leverage: {state_str}")
            elif delta_parts[0] in ("positions", "pos"):
                if engine.broker and hasattr(engine.broker, "get_positions"):
                    positions = engine.broker.get_positions()
                    if positions:
                        for p in positions:
                            print(f"{p.symbol} {p.side.upper()} {p.qty} @ {p.entry_price} | PnL: ${p.unrealized_pnl:+,.2f}")
                    else:
                        print("No active open positions on Delta Exchange.")
                else:
                    print("No broker attached.")
            elif len(delta_parts) >= 2 and delta_parts[0] not in ("close",):
                k, s = delta_parts[0], delta_parts[1]
                try:
                    from delta_broker import DeltaBroker
                    engine.broker = DeltaBroker(api_key=k, api_secret=s)
                    config.DELTA_EXCHANGE_API_KEY = k
                    config.DELTA_EXCHANGE_API_SECRET = s
                    config.BROKER_MODE = "delta_live"
                    print(f"Connected to Delta Exchange India Live! Balance: ${engine.broker.balance:,.2f}")
                except Exception as exc:
                    print(f"Delta Live connect failed: {exc}")
            else:
                print("Usage: delta [status|live <key> <secret>|testnet <key> <secret>|leverage <1-200>|auto_leverage|positions]")
        elif name == "broker":
            if len(parts) > 1:
                bmode = parts[1].lower()
                if bmode in ("paper", "demo", "sim"):
                    print("❌ Paper trading mode has been removed. MetaTrader 5 Live and Delta Exchange are active.")
                elif bmode in ("mt5", "mt5_live", "live"):
                    from mt5_accounts import switch_broker_mode
                    target_id = parts[2] if len(parts) > 2 else None
                    ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                    print(msg.replace("*", "").replace("`", ""))
                elif bmode in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
                    from delta_broker import DeltaBroker
                    k = getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", "")
                    s = getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", "")
                    testnet = bmode == "delta_testnet" or getattr(config, "DELTA_EXCHANGE_TESTNET", False)
                    base_url = getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
                    try:
                        delta_b = DeltaBroker(
                            api_key=k,
                            api_secret=s,
                            base_url=base_url,
                            is_testnet=testnet,
                            leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                            auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                            max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
                        )
                        engine.broker = delta_b
                        if hasattr(engine, "reset_halt"):
                            engine.reset_halt()
                        config.BROKER_MODE = "delta_live"
                        print(f"Switched to Delta Exchange India! Balance: ${delta_b.balance:,.2f}")
                    except Exception as e:
                        print(f"Delta connect error: {e}")
                else:
                    print(f"⚠️ Broker '{bmode}' is not recognized. Use 'broker mt5' or 'broker delta'.")
            else:
                bname = type(engine.broker).__name__ if engine.broker else "None"
                print(f"Active broker: {bname} ({getattr(config, 'BROKER_MODE', 'mt5_live')})")
        elif name in ("oanda", "alpaca", "ccxt"):
            print(f"⚠️ Native live broker '{name.upper()}' has been removed. MetaTrader 5 Live is the sole exclusive broker.")
        elif name in ("help", "commands", "cmd", "menu", "list", "all"):
            print(
                "Commands: signal [SYM], price [SYM], chart [SYM], levels [SYM], news [SYM], quality [SYM], positions, slots [CMD], autotrade on|off, close [SYM], cancel [SYM], closed, delta [CMD], balance [AMOUNT], mt5 [CMD], bridge [CMD], broker [MODE], switch [MODE], live [CMD], status, health, cooldown [CMD], spam [on|off], risk, resetrisk, leverage [LEV], lot [CMD], kelly [SYM], regime [SYM], session, vstops, slippage, kill [on|off], backtest [BARS] [SYM], abacktest [MODE] [BARS] [SYM], performance, profile [NAME], board, journal, agents, tiers, digest, replay [ID], export, setups, abooks, heal, brain, train [heavy|quick], relearn [SYM], ask [TEXT], reset, exit\n\n"
                "Commands by category:\n"
                "📊 Market: price [SYM], chart [SYM], levels [SYM], news [SYM]\n"
                "🎯 Signals: signal [SYM], asignal [SYM] [profile], single [SYM], quality [SYM]\n"
                "⚡ Execution: positions, slots [auto|manual <1-6>], autotrade on|off, close [SYM|all], cancel [SYM], closed, delta [CMD], balance [AMOUNT], broker [MODE], switch [MODE], live [CMD], mt5 [CMD], bridge [CMD], vstops, slippage\n"
                "🛡️ Risk: status, health, cooldown [CMD], spam [on|off], risk, resetrisk, leverage [LEV], lot [auto|manual|<size>], kelly [SYM], regime [SYM], session, kill [on|off]\n"
                "📈 Research: backtest [BARS] [SYM], abacktest [MODE] [BARS] [SYM], performance, profile [NAME], replay [ID], export, setups, abooks, heal\n"
                "🧠 Autonomous AI: board, journal, agents, tiers, digest, brain, train [heavy|quick], relearn [SYM|ALL]\n"
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
                print(_get().digest())
            except Exception as e:
                print(f"Digest error: {e}")
        elif name == "replay":
            if len(parts) > 1:
                try:
                    from telegram_feature_pack_v4 import _get
                    print(_get().replay(parts[1]))
                except Exception as e:
                    print(f"Replay error: {e}")
            else:
                print("Usage: replay <TRADE_ID>")
        elif name == "export":
            try:
                from telegram_feature_pack_v4 import _get
                print(_get().export_csv())
            except Exception as e:
                print(f"Export error: {e}")
        elif name == "setups":
            try:
                from telegram_feature_pack_v4 import _get
                pack = _get()
                print(pack.edge.setup_report() + "\n\n" + pack.edge.calibration_report())
            except Exception as e:
                print(f"Setups error: {e}")
        elif name == "abooks":
            try:
                from telegram_feature_pack_v4 import _get
                print(_get().ab.report())
            except Exception as e:
                print(f"A/B error: {e}")
        elif name == "heal":
            try:
                from telegram_feature_pack_v4 import _get
                h = _get().health
                if len(parts) > 1 and parts[1].lower() in ("reset", "ok", "clear"):
                    h.record_ok()
                    print("Health counter reset.")
                else:
                    print(h.status())
            except Exception as e:
                print(f"Heal error: {e}")
        elif name in ("brain", "learner", "veto"):
            try:
                from autonomous_learner import get_brain
                brain = get_brain(auto_bootstrap=False)
                sub = parts[1].lower() if len(parts) > 1 else "status"
                if sub in ("status", "info"):
                    print(brain.summary())
                elif sub in ("reset", "clear"):
                    res = brain.reset_memory()
                    print(f"Autonomous Learner memory reset to baseline. (min_score={res['min_score']}, min_rr={res['min_rr']})")
                elif sub in ("rules", "filters"):
                    print(brain.format_rules())
                elif sub in ("flaws", "sanctions"):
                    print(brain.format_flaws())
                elif sub in ("on", "enable", "start", "1", "true"):
                    brain.set_enabled(True)
                    print("Autonomous Learner signal filtering: ENABLED")
                elif sub in ("off", "disable", "pause", "stop", "0", "false"):
                    brain.set_enabled(False)
                    print("Autonomous Learner signal filtering: PAUSED")
                elif sub in ("unban", "allow"):
                    target = parts[2].lower() if len(parts) > 2 else ""
                    if target.isdigit():
                        h = int(target) % 24
                        ok = brain.unban_hour(h)
                        print(f"Unbanned UTC hour {h:02d}:00" if ok else f"Hour {h:02d}:00 was not banned.")
                    elif target in ("all", "hours"):
                        cnt = brain.clear_banned_hours()
                        print(f"Cleared all {cnt} banned UTC hours.")
                    elif target:
                        rem = brain.unban_setup(target)
                        print(f"Unbanned setups: {rem}" if rem else f"No setups matching {target}")
                    else:
                        print("Usage: learner unban <hour|hours|setup_name>")
                elif sub in ("clearhours", "clearhour"):
                    cnt = brain.clear_banned_hours()
                    print(f"Cleared all {cnt} banned UTC hours.")
                elif sub in ("clearbans", "clearallbans"):
                    res = brain.clear_bans()
                    print(f"Cleared all learned bans: {res}")
                elif sub in ("relearn", "train"):
                    sym = parts[2].upper() if len(parts) > 2 else "XAUUSD"
                    years = float(parts[3]) if len(parts) > 3 and parts[3].replace('.', '', 1).isdigit() else 5.0
                    print(f"Retraining Autonomous Learner on {sym} ({years:.1f}y)...")
                    summary = brain.bootstrap(symbol=sym, years=years)
                    print(f"Bootstrap complete: {summary}")
                elif sub in ("help", "commands"):
                    print("learner/brain [status|rules|flaws|unban <hour>|clearhours|relearn [SYM] [YEARS]|reset|on|off]")
                else:
                    print(brain.summary())
            except Exception as e:
                print(f"Learner/Brain error: {e}")
        elif name == "train":
            try:
                sub = parts[1].lower() if len(parts) > 1 else "heavy"
                heavy = (sub != "quick")
                print(f"Starting {'Heavy' if heavy else 'Quick'} Bot Training Pipeline across all assets...")
                from heavy_trainer import HeavyTrainer
                trainer = HeavyTrainer()
                summary = trainer.train_all(heavy=heavy)
                print("\n" + trainer.format_markdown_report(summary))
            except Exception as e:
                print(f"Train error: {e}")
        elif name == "relearn":
            try:
                from autonomous_learner import get_brain
                brain = get_brain(auto_bootstrap=False)
                sym = parts[1].upper() if len(parts) > 1 else "XAUUSD"
                years = float(parts[2]) if len(parts) > 2 and parts[2].replace('.', '', 1).isdigit() else 5.0
                if sym in ("ALL", "HEAVY"):
                    print(f"Retraining Autonomous Learner across ALL assets ({years:.1f}y)...")
                    from heavy_trainer import HeavyTrainer
                    trainer = HeavyTrainer(years=years)
                    summary = trainer.train_all(heavy=True)
                    print("\n" + trainer.format_markdown_report(summary))
                else:
                    print(f"Retraining Autonomous Learner on {sym} ({years:.1f}y)...")
                    summary = brain.bootstrap(symbol=sym, years=years)
                    print(f"Bootstrap complete: {summary}")
            except Exception as e:
                print(f"Relearn error: {e}")
        elif name in ("vstops", "vstop"):
            try:
                from virtual_stops import get_virtual_stop_manager
                mgr = get_virtual_stop_manager()
                if len(parts) > 1 and parts[1].lower() in ("clear", "reset", "flush"):
                    cleared = len(mgr.records)
                    mgr.records.clear()
                    mgr._save()
                    print(f"Cleared {cleared} virtual hidden stops.")
                else:
                    active = mgr.active_stops()
                    if active:
                        print(f"Active Virtual Stops ({len(active)}):")
                        for s in active:
                            tp_str = f" | TP1: ${s.virtual_tp1:,.2f}" if s.virtual_tp1 else ""
                            print(f"• #{s.ticket} {s.symbol} {s.direction} @ ${s.entry_price:,.2f} | SL: ${s.virtual_stop:,.2f}{tp_str}")
                    else:
                        print("No active virtual stops registered.")
            except Exception as e:
                print(f"Virtual stops error: {e}")
        elif name in ("slippage", "analytics"):
            try:
                from execution_analytics import get_execution_tracker
                tracker = get_execution_tracker()
                rep = tracker.generate_report()
                print(f"Execution Analytics Grade: {rep.execution_rating} | Fills: {rep.total_executions} | Avg Latency: {rep.avg_latency_ms:.1f}ms | Slippage: {rep.avg_slippage_pts:+.2f} pts")
            except Exception as e:
                print(f"Slippage analytics error: {e}")
        elif name in ("kelly", "var"):
            try:
                from kelly_criterion import calculate_kelly_lot_size
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                eq = float(getattr(risk_mgr.state, "equity", None) or getattr(config, "INITIAL_EQUITY", 10000.0))
                pinfo = get_price_info(sym)
                price = float(pinfo.get("mark") or pinfo.get("close") or (2650.0 if "XAU" in sym else 65000.0))
                sl = price * 0.99
                k_res = calculate_kelly_lot_size(equity=eq, entry_price=price, stop_loss_price=sl, win_rate=0.55, payoff_ratio=1.8)
                print(f"Fractional Kelly Sizing ({sym}): Rec Lots: {k_res.recommended_lots} | Dollar Risk: ${k_res.dollar_risk:,.2f} ({k_res.risk_pct_of_equity:.2f}%)")
            except Exception as e:
                print(f"Kelly sizing error: {e}")
        elif name in ("regime", "switchregime"):
            try:
                from regime_auto_switcher import RegimeAutoSwitcher
                sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
                feed = MarketDataFeed(symbol=sym, source=getattr(config, "DEFAULT_DATA_SOURCE", "demo"))
                data = feed.multi_tf(limit_1m=100)
                df = data.get("1m") if data else None
                if df is not None and not df.empty:
                    switcher = RegimeAutoSwitcher(cooldown_seconds=0.0)
                    res = switcher.evaluate_and_switch(df, symbol=sym, arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
                    print(f"Regime Detection ({sym}): {res.regime_detected} (Profile: {res.current_profile}) | Reason: {res.reason}")
                else:
                    print(f"No market data for {sym}")
            except Exception as e:
                print(f"Regime error: {e}")
        elif name in ("session", "sessions"):
            try:
                from session_reporter import compile_session_summary
                summary = compile_session_summary(open_positions=list(engine.active_positions.values()))
                print(f"Current Session: {summary.session_name} | Net PnL: ${summary.net_pnl_usd:+,.2f} | Open: {summary.open_positions_count}")
            except Exception as e:
                print(f"Session error: {e}")
        elif name in ("antispam", "cooldown", "cooldowns", "spam", "spamtrade"):
            try:
                from anti_spam import get_anti_spam_limiter
                limiter = get_anti_spam_limiter()
                if len(parts) > 1:
                    sub = parts[1].lower().strip()
                    if name in ("spam", "spamtrade") or sub in ("spam", "spamtrade"):
                        action = parts[2].lower().strip() if len(parts) > 2 else (sub if name in ("spam", "spamtrade") else "toggle")
                        if action in ("on", "enable", "1", "true", "active", "activate"):
                            limiter.set_spam_mode(True, max_trades=6, engine=engine)
                            print("Spam Trade Mode ACTIVATED: Max 6 trades concurrent, all cooldowns BYPASSED.")
                        elif action in ("off", "disable", "0", "false", "deactivate", "inactive"):
                            limiter.set_spam_mode(False, engine=engine)
                            print("Spam Trade Mode DEACTIVATED: Reverted to protected mode (max 4 total, strict cooldowns).")
                        else:
                            new_v = not limiter.spam_trade_enabled
                            limiter.set_spam_mode(new_v, max_trades=6, engine=engine)
                            st = "ACTIVATED (Max 6, No Cooldown)" if new_v else "DEACTIVATED (Protected)"
                            print(f"Spam Trade Mode: {st}")
                    elif sub in ("auto", "adaptive", "dynamic"):
                        limiter.set_mode("auto")
                        print("Anti-Spam Mode set to AUTO (Adaptive: profile-aware cooldowns, max 2 trades per currency, max 4 total).")
                    elif sub in ("on", "enable", "manual", "1", "true", "active", "activate", "activated"):
                        limiter.set_mode("on")
                        print("Anti-Spam Mode set to MANUAL ON (Cooldown ACTIVATED: strict cooldowns enforced: max 2 trades per currency, max 4 total).")
                    elif sub in ("off", "disable", "bypass", "0", "false", "deactivate", "deactivated", "inactive"):
                        limiter.set_mode("off")
                        print("Anti-Spam Mode set to OFF (Cooldown DEACTIVATED: cooldowns bypassed. Safety limits retained: max 2 per currency, max 4 total).")
                    elif sub in ("reset", "clear"):
                        sym = parts[2].upper() if len(parts) > 2 else None
                        limiter.reset(sym)
                        target = f"for {sym}" if sym else "globally"
                        print(f"Anti-spam cooldowns and loss streaks reset {target}.")
                    else:
                        print("Usage: cooldown [active|deactivate|auto|reset] or spam [on|off]")
                else:
                    rep = limiter.get_status_report(engine=engine).replace("**", "").replace("*", "").replace("`", "").replace("_", "")
                    print(rep)
            except Exception as e:
                print(f"Anti-spam error: {e}")
        else:
            print("Commands: signal, price, levels, chart, news, quality, positions, slots, matrix, heatmap, dashboard, autotrade, close, cancel, closed, mt5, bridge, broker, switch, live, balance, delta, status, health, cooldown, spam, risk, resetrisk, leverage, lot, kelly, regime, session, vstops, slippage, kill, backtest, abacktest, performance, profile, board, journal, agents, tiers, digest, replay, export, setups, abooks, heal, brain, train, relearn, ask, reset, exit")


def main():
    token = getattr(config, "TELEGRAM_TOKEN", None) or os.getenv("TELEGRAM_TOKEN", "")
    if "--cli" in sys.argv:
        run_cli_interactive()
        return

    if "--headless" in sys.argv or os.getenv("HEADLESS") in ("1", "true", "yes"):
        run_headless()
        return

    if not token or token == "your_telegram_bot_token_here":
        print("TELEGRAM_TOKEN not configured in .env.")
        if sys.stdin.isatty():
            run_cli_interactive()
        else:
            print("Non-interactive / supervisor environment detected. Starting 24/7 autonomous trading engine...")
            run_headless()
        return

    print(f"Starting Telegram Trading Bot on token: {token[:8]}***...")

    async def on_post_init(application):
        logger.info("Initializing live monitoring background worker...")
        asyncio.create_task(live_monitoring_worker(application))

        # Register official Telegram Bot Command list so Telegram UI command menu ('/') works
        try:
            from telegram import BotCommand
            bot_commands = [
                # Navigation & Guide
                BotCommand("start", "Start bot & interactive overview"),
                BotCommand("help", "Full command center & user guide"),
                BotCommand("commands", "Interactive catalog of all bot commands"),
                # Market Data & Charts
                BotCommand("chart", "Institutional MT5 Live Candlestick Chart (BTC & Gold)"),
                BotCommand("price", "Live mark price & 24h market stats"),
                BotCommand("levels", "Key levels (PDH/PDL, pivots, 2Y S/R)"),
                BotCommand("news", "Macro calendar & economic news sentiment"),
                # Signals & Strategy
                BotCommand("signal", "9-Agent consensus & optimal entry zone"),
                BotCommand("asignal", "Advanced SMC + CVD orderflow setup"),
                BotCommand("quality", "Signal quality score & breakdown"),
                # Execution & Positions
                BotCommand("positions", "Active trades, live balance, equity & real-time PnL"),
                BotCommand("slots", "Manage position slots (1-6 positions, Auto / Manual)"),
                BotCommand("matrix", "Multi-Timeframe Trend & Volatility Heatmap Matrix"),
                BotCommand("dashboard", "Local Web Companion Dashboard & Real-Time Terminal"),
                BotCommand("autotrade", "Toggle multi-symbol autotrade (max 6 slots: 3 Gold, 3 BTC)"),
                BotCommand("watchlist", "View & switch active Gold/BTC autotrade watchlist"),
                BotCommand("close", "Close position(s) by symbol or all"),
                BotCommand("cancel", "Cancel pending setup order"),
                BotCommand("closed", "Recently closed trades history & PnL"),
                BotCommand("vstops", "Client-side virtual hidden stop loss manager"),
                BotCommand("slippage", "Broker execution latency & slippage tracker"),
                # Brokers & MT5
                BotCommand("balance", "View or update active MT5 balance, equity & margin"),
                BotCommand("mt5", "MetaTrader 5 live execution engine & dashboard"),
                BotCommand("bridge", "MT5 EA Bridge diagnostics & auto-deploy"),
                BotCommand("delta", "Delta Exchange status & controls (Crypto/Derivatives)"),
                BotCommand("switch", "Switch active broker or MT5 account"),
                BotCommand("live", "MT5 Live execution controls & account telemetry"),
                BotCommand("broker", "View active execution broker & status"),
                # Risk & Safety
                BotCommand("status", "Portfolio equity & margin capacity"),
                BotCommand("health", "System health & background worker telemetry"),
                BotCommand("cooldown", "Anti-spam rate limiting & cooldown status (active/deactivate)"),
                BotCommand("antispam", "Anti-spam concurrency & cooldown controls (auto/on/off)"),
                BotCommand("spam", "Spam trade mode (max 6 trades without cooldown, on/off)"),
                BotCommand("risk", "Risk tier report, sizing & reset"),
                BotCommand("resetrisk", "Reset daily/weekly risk limits & restart"),
                BotCommand("leverage", "Set or view leverage (1-200x, manual/auto)"),
                BotCommand("lot", "Auto / Manual lot size selection"),
                BotCommand("kelly", "Fractional Kelly lot sizing & portfolio VaR"),
                BotCommand("regime", "Market regime detector & auto-switcher"),
                BotCommand("session", "Session summary & killzones report"),
                BotCommand("kill", "Emergency execution circuit breaker switch"),
                # Backtesting & Research
                BotCommand("backtest", "Fast backtest engine on historical data"),
                BotCommand("abacktest", "Advanced backtest suite (WF / MC / sweep)"),
                BotCommand("performance", "Trade metrics, win rate & Sharpe ratio"),
                BotCommand("profile", "View or switch strategy profile"),
                # Autonomous AI & Intelligence
                BotCommand("board", "Strategy board & agent standings"),
                BotCommand("journal", "Trade journal analytics & history"),
                BotCommand("agents", "Agent performance rankings & stats"),
                BotCommand("tiers", "Risk tier status & allocations"),
                BotCommand("digest", "Daily market performance digest"),
                BotCommand("replay", "Replay historical trade setup by ID"),
                BotCommand("export", "Export trade journal to CSV"),
                BotCommand("setups", "Setup expectancy & bans report"),
                BotCommand("abooks", "A/B paper books comparison"),
                BotCommand("heal", "System health counter reset & self-healing"),
                BotCommand("brain", "Autonomous learner intelligence & state"),
                BotCommand("learner", "Autonomous learner veto toggle (on/off/unban)"),
                BotCommand("veto", "Quick toggle for trade veto filtering (on/off)"),
                BotCommand("train", "Deep heavy multi-asset AI & autonomous training"),
                BotCommand("relearn", "Retrain autonomous learner on 5Y data"),
                BotCommand("ask", "AI trading assistant with live MT5 market context"),
                BotCommand("reset", "Clear conversational chat memory"),
            ]
            await application.bot.set_my_commands(bot_commands)
            logger.info("Registered %d commands with Telegram Bot API", len(bot_commands))
        except Exception as cmd_err:
            logger.warning("Failed to sync Bot Commands with Telegram API: %s", cmd_err)

        # Autonomous brain auto-start in background
        try:
            from autonomous_learner import auto_start
            auto_start()
            logger.info("Autonomous learner auto-started in background")
        except Exception as e:
            logger.debug("Autonomous learner start skip: %s", e)

    app = ApplicationBuilder().token(token).post_init(on_post_init).build()

    # Core Navigation & Help
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["help", "commands", "cmd", "menu", "list", "all", "command"], help_command))

    # Market Data
    app.add_handler(CommandHandler("levels", levels_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler(["chart", "charts"], chart_command))
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
    app.add_handler(CommandHandler(["slots", "maxpositions", "slot"], slots_command))
    app.add_handler(CommandHandler(["matrix", "heatmap", "mtf"], matrix_command))
    app.add_handler(CommandHandler(["dashboard", "web", "dash"], dashboard_command))
    app.add_handler(CommandHandler("autotrade", autotrade_command))
    app.add_handler(CommandHandler(["watchlist", "wl", "symbols", "assets"], watchlist_command))
    app.add_handler(CommandHandler(["close", "closeall"], close_command))
    app.add_handler(CommandHandler("closed", closed_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(autotrade_callback_handler))

    # MT5 & Delta Exchange India Live Broker Execution
    app.add_handler(CommandHandler(["balance", "wallet", "equity"], balance_command))
    app.add_handler(CommandHandler(["delta", "deltaindia"], delta_command))
    app.add_handler(CommandHandler("mt5", mt5_command))
    app.add_handler(CommandHandler(["bridge", "ea"], bridge_command))
    app.add_handler(CommandHandler(["switch", "switchbroker"], switch_command))
    app.add_handler(CommandHandler(["live", "livetrade"], live_command))
    app.add_handler(CommandHandler("broker", broker_command))
    app.add_handler(CommandHandler("oanda", oanda_command))
    app.add_handler(CommandHandler("alpaca", alpaca_command))
    app.add_handler(CommandHandler("ccxt", ccxt_command))
    app.add_handler(CommandHandler(["slippage", "analytics"], slippage_command))
    app.add_handler(CommandHandler(["vstops", "vstop"], vstops_command))

    # Risk & Safety
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler(["antispam", "Antyspam", "cooldown", "cooldowns"], cooldown_command))
    app.add_handler(CommandHandler(["spam", "spamtrade", "burst"], spam_command))
    app.add_handler(CommandHandler("risk", risk_command))
    app.add_handler(CommandHandler(["resetrisk", "riskreset"], resetrisk_command))
    app.add_handler(CommandHandler("leverage", leverage_command))
    app.add_handler(CommandHandler(["lot", "lots", "lotsize"], lot_command))
    app.add_handler(CommandHandler(["kelly", "var"], kelly_command))
    app.add_handler(CommandHandler(["regime", "switchregime"], regime_command))
    app.add_handler(CommandHandler(["session", "sessions"], session_command))
    try:
        from telegram_advanced import kill_command
        app.add_handler(CommandHandler("kill", kill_command))
    except Exception:
        pass

    # Backtesting & Research
    app.add_handler(CommandHandler("backtest", backtest_command))
    try:
        from telegram_advanced import performance_command
        app.add_handler(CommandHandler(["performance", "perf"], performance_command))
    except Exception as e:
        logger.exception("Failed to register performance_command: %s", e)

    try:
        from telegram_advanced import abacktest_command, profile_command, scalp_command
        app.add_handler(CommandHandler(["abacktest", "backtestall"], abacktest_command))
        app.add_handler(CommandHandler("profile", profile_command))
        app.add_handler(CommandHandler(["scalp", "microscalp"], scalp_command))
    except Exception as e:
        logger.exception("Failed to register abacktest/profile/scalp handlers: %s", e)

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
        from telegram_brain import brain_command, relearn_command, train_command, learner_command, veto_command
        app.add_handler(CommandHandler("brain", brain_command))
        app.add_handler(CommandHandler("relearn", relearn_command))
        app.add_handler(CommandHandler("train", train_command))
        app.add_handler(CommandHandler(["learner", "autonomous"], learner_command))
        app.add_handler(CommandHandler(["veto", "learnerveto"], veto_command))
    except Exception:
        pass

    # Conversational Assistant & Controls
    app.add_handler(CommandHandler(["ask", "chat"], ask_command))
    app.add_handler(CommandHandler("reset", reset_command))

    # Safely register any additional advanced commands before unknown_command fallback
    try:
        from telegram_advanced import register_all_advanced
        register_all_advanced(app)
    except Exception as adv_err:
        logger.warning("Advanced commands register note: %s", adv_err)

    # Fallback for unrecognized commands (MUST BE REGISTERED LAST)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Conversational chat handler for non-commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))


    async def global_telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = getattr(context, "error", None)
        if not err:
            return
        err_str = str(err)
        if "Conflict" in err_str or "terminated by other getUpdates" in err_str:
            logger.warning("Telegram conflict: another bot instance is polling with this token. Pausing briefly...")
        elif "NetworkError" in err_str or "TimedOut" in err_str or "httpx" in err_str:
            logger.warning("Telegram network glitch (non-fatal): %s", err)
        elif "Can't parse entities" in err_str or "can't find end of" in err_str or "entity" in err_str.lower():
            logger.warning("Telegram markdown entity parse error caught: %s", err)
            if update and isinstance(update, Update):
                try:
                    await safe_reply(update, "⚠️ Message formatting error from Telegram parser. Raw response delivered.", parse_mode=None)
                except Exception:
                    pass
        else:
            logger.error("Telegram error caught: %s", err)

    app.add_error_handler(global_telegram_error_handler)

    print("Trading Bot is running. Press Ctrl+C to terminate.")
    try:
        app.run_polling(drop_pending_updates=True)
    except Exception as poll_err:
        logger.error("Telegram polling terminated: %s", poll_err)
        err_name = type(poll_err).__name__
        err_msg = str(poll_err).lower()
        if "invalidtoken" in err_name.lower() or "rejected by the server" in err_msg:
            print(f"⚠️ TELEGRAM_TOKEN was rejected by Telegram API ({poll_err}).")
            print("Please check TELEGRAM_TOKEN in your .env file.")
            print("Switching to headless autonomous trading engine so trading continues uninterrupted...")
            run_headless()
            return
        raise


if __name__ == "__main__":
    main()
