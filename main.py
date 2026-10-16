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


async def _to_thread(func, *args, **kwargs):
    if hasattr(asyncio, "to_thread"):
        return await asyncio.to_thread(func, *args, **kwargs)
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


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

gemini = GeminiClient()
risk_mgr = RiskManager(equity=config.INITIAL_EQUITY, symbol=config.DEFAULT_SYMBOL)
risk_mgr.set_leverage(config.DEFAULT_LEVERAGE)
arbiter = Arbiter(allow_asian=True)
try:
    from mt5_accounts import get_account_manager
    _active_acc = get_account_manager().get_active_account()
    if _active_acc and _active_acc.login:
        config.MT5_LOGIN = _active_acc.login
        config.MT5_PASSWORD = _active_acc.password
        config.MT5_SERVER = _active_acc.server
        config.MT5_GATEWAY_URL = _active_acc.gateway_url
except Exception as _sync_err:
    pass

from execution import create_broker_fail_closed

try:
    _broker = create_broker_fail_closed(
        broker_mode=getattr(config, "BROKER_MODE", "mt5_live"),
        api_key=getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", ""),
        api_secret=getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", ""),
    )
except Exception as _broker_err:
    import os
    if "PYTEST_CURRENT_TEST" in os.environ or getattr(config, "TESTING", False):
        b_m = str(getattr(config, "BROKER_MODE", "")).lower()
        if "delta" in b_m:
            from delta_broker import MockDeltaBroker
            _broker = MockDeltaBroker()
        else:
            from mt5_broker import MockMT5Broker
            _broker = MockMT5Broker()
    else:
        logger.exception(
            "Broker initialization failed; refusing to start the trading bot."
        )
        raise SystemExit(1) from _broker_err
engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)
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
    elif cat in ("mt5", "broker", "brokers", "balance", "wallet", "equity", "oanda", "ccxt", "alpaca", "switch", "live", "delta"):
        text = (
            "🏦 **MetaTrader 5 & Broker Accounts:**\n"
            "• `/switch [delta|live|<login>|<alias>]` — Fast switcher between brokers & MT5 accounts\n"
            "• `/live [on|off|<login> <pass> <server>]` — Quick MT5 Live trading toggle & connect\n"
            "• `/mt5` — Interactive MT5 dashboard & controls\n"
            "• `/mt5 add <login> <pass> <server> [alias]` — Add & save MT5 Live account\n"
            "• `/mt5 switch [<login>|<alias>]` — Switch active MT5 account\n"
            "• `/mt5 accounts` — List registered MT5 accounts\n"
            "• `/mt5 remove <login|alias>` — Remove a saved MT5 account\n"
            "• `/balance` — View active broker balance, equity, and margin\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker [mt5|delta]` — Switch active execution broker engine"
        )
    elif cat in ("risk", "safety", "limits"):
        text = (
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/risk reset [daily|weekly|all]` — Reset loss limits & unblock trading\n"
            "• `/resetrisk [daily|weekly|all|restart]` — Reset risk & restart autotrade\n"
            "• `/leverage [1-200|auto]` — Set manual leverage or dynamic auto-leverage (up to 200x)\n"
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
            "• `/autotrade [on|off|scalp|btc|gold|both]` — Multi-symbol autotrading (Gold/BTC)\n"
            "• `/watchlist [btc|gold|both]` — View & switch active Gold/BTC autotrade watchlist\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL\n\n"
            "🏦 **MetaTrader 5 & Execution Broker:**\n"
            "• `/switch [live|paper]` — Instant toggle between Live & Paper modes\n"
            "• `/live [on|off]` — Quick MT5 Live trading switch\n"
            "• `/mt5` — Interactive MT5 dashboard & controls\n"
            "• `/mt5 add <login> <pass> <server> [alias]` — Save live MT5 trading account\n"
            "• `/mt5 accounts` — List registered MT5 accounts\n"
            "• `/balance [amount]` — View or custom-set paper wallet balance (e.g. `/balance 50000`)\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker paper` — Switch execution broker to paper simulation\n"
            "🛡️ **Risk & Safety Management:**\n"
            "• `/status` — Portfolio equity, drawdown & active slot capacity\n"
            "• `/health` — Real-time telemetry & background worker heartbeat\n"
            "• `/risk` — Risk tier report, margin capacity & lot sizing\n"
            "• `/risk reset [daily|weekly|all]` — Reset loss limits & unblock trading\n"
            "• `/resetrisk [daily|weekly|all|restart]` — Reset risk & restart autotrade\n"
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
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2))
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


def reset_risk_limits(scope: str = "all", restart_autotrade: bool = False, new_equity: Optional[float] = None) -> str:
    """
    Manually reset daily or weekly risk management limits, loss streaks, circuit breakers,
    and optionally restart automated trading.
    """
    global autotrade_active
    s_scope = (scope or "all").lower().strip()
    if s_scope in ("day", "daily", "d"):
        res = risk_mgr.reset_daily_risk()
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
        res = risk_mgr.reset_weekly_risk()
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
        res = risk_mgr.reset_all_risk(new_equity=new_equity)
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
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2))
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
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2))
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
        f"• **Slots:** `{open_count}/{max_trades}` active (`{slots}` free)\n\n"
        f"💡 **Switch Selection:**\n"
        f"• `/watchlist btc` — Trade BTC only\n"
        f"• `/watchlist gold` — Trade Gold only\n"
        f"• `/watchlist both` — Trade both BTC + Gold\n"
        f"• `/autotrade on [btc|gold|both]`\n"
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
    broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2))

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
            f"• **Active Watchlist:** `{symbols_str}`",
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
            f"• **Active Watchlist:** `{symbols_str}`",
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

    if data in ("auto:on", "auto:scalp"):
        broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
        is_live = "LIVE" in broker_mode or (hasattr(engine, "broker") and getattr(engine.broker, "is_live", False))
        if is_live and not getattr(config, "AUTOTRADE_LIVE_OPT_IN", False):
            await query.message.reply_text(
                "⚠️ **Live Trading Protection**: Autotrading on a LIVE account requires explicit opt-in.\n"
                "Run `/autotrade on confirm` or set `BOT_AUTOTRADE_LIVE_OPT_IN=true`.",
                parse_mode="Markdown"
            )
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
        elif data == "mt5:refresh":
            pass

        text, markup = build_mt5_dashboard()
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

    text = (
        f"🏦 **MetaTrader 5 Dashboard**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Execution Mode:** {mode_icon}\n"
        f"• **Active Account:** `{login_str}`{alias_str}\n"
        f"• **Broker Server:** `{srv}`\n"
        f"• **Balance:** `${bal_f:,.2f}` | **Equity:** `${eq_f:,.2f}`\n"
        f"• **Margin:** `${margin_f:,.2f}` | **Free Margin:** `${free_m_f:,.2f}`\n"
        f"• **Leverage:** `{lev_f:.0f}x` | **Open Positions:** `{active_positions_count}`\n"
        f"• **Saved Accounts:** `{accounts_count}`\n\n"
        f"⚡ **Quick Controls:**\n"
        f"• `/mt5 switch live` — Connect / switch active live MT5 account\n"
        f"• `/mt5 switch <alias|login>` — Switch to a saved live account\n"
        f"• `/mt5 add <login> <pass> <server> [alias]` — Save live account\n"
        f"• `/mt5 accounts` — List all saved accounts\n"
        f"• `/switch <alias|login>` | `/live`"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Connect / Switch Live", callback_data="mt5:switch_live"),
            InlineKeyboardButton("📋 My Accounts", callback_data="mt5:list_accounts"),
        ],
        [
            InlineKeyboardButton("➕ Add Account", callback_data="mt5:help_add"),
            InlineKeyboardButton("📊 Positions", callback_data="mt5:positions"),
        ],
        [
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

    elif sub in ("remove", "delete", "rm"):
        if len(args) < 2:
            await update.message.reply_text("Usage: `/mt5 remove <login|alias>`", parse_mode="Markdown")
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

    else:
        text, markup = build_mt5_dashboard()
        try:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await update.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=markup)


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
    from mt5_accounts import get_account_manager, switch_broker_mode
    args = ctx.args or []
    cmd = args[0].lower() if args else ""

    if cmd in ("off", "stop"):
        await update.message.reply_text(
            "⚠️ **Live Execution is Exclusive**\n"
            "Paper trading has been removed from this system. MT5 Live is the sole execution engine.\n\n"
            "• To pause automated order placement: `/autotrade off`\n"
            "• To trigger the emergency circuit breaker: `/kill on`\n"
            "• To close open positions: `/close all`",
            parse_mode="Markdown",
        )
    elif cmd in ("paper", "demo"):
        ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif cmd in ("on", "start", "enable", "connect") or (len(args) == 0 and getattr(config, "BROKER_MODE", "paper") != "mt5_live"):
        target_id = args[1] if len(args) > 1 else None
        ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
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
            ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
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
    """Notice that paper trading wallet balance has been removed."""
    await update.message.reply_text(
        "⚠️ **Paper Trading Wallet Removed**\n"
        "Paper trading and manual wallet balance simulation have been removed.\n"
        "MetaTrader 5 Live is the sole exclusive broker.\n\n"
        "• Use `/mt5 status` or `/live` to view your live MT5 account balance, equity, and margin.\n"
        "• Use `/mt5 accounts` to manage your live accounts.",
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

                is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2)) or 2
                open_n = len(engine.active_positions) + len(engine.pending_setups)
                can_open = (autotrade_active and open_n < max_trades) if is_scalping else (autotrade_active and symbol not in engine.active_positions and symbol not in engine.pending_setups and open_n < max_trades)
                if can_open:
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
        elif name == "positions":
            if hasattr(engine, "format_positions_detail"):
                print(engine.format_positions_detail())
            else:
                print(engine.get_portfolio_summary())
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
            elif sub in ("remove", "delete", "rm"):
                if len(parts) < 3:
                    print("Usage: mt5 remove <login|alias>")
                else:
                    removed = mgr.remove_account(parts[2])
                    if removed:
                        print(f"MT5 Account Removed: {removed.alias} (#{removed.login} on {removed.server})")
                    else:
                        print(f"Account '{parts[2]}' not found.")
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
                        ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
                        print(msg.replace("*", "").replace("`", ""))
                    except Exception as e:
                        print(f"Error: {e}")
                else:
                    target_id = parts[2] if len(parts) > 2 else None
                    ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                    print(msg.replace("*", "").replace("`", ""))
            elif sub in ("positions", "pos"):
                if engine.broker:
                    print(engine.broker.get_positions())
                else:
                    print("No broker attached.")
            else:
                print("Usage: mt5 [status|add|accounts|switch|remove|live|paper|positions]")
        elif name in ("switch", "switchbroker"):
            from mt5_accounts import get_account_manager, switch_broker_mode
            if len(parts) > 1:
                target = parts[1].lower()
                sub_id = parts[2] if len(parts) > 2 else None
                ok, msg, _ = switch_broker_mode(target_mode=target, identifier=sub_id, engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            else:
                bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
                mgr = get_account_manager()
                act = mgr.get_active_account()
                act_str = f"#{act.login} ({act.alias})" if act else "None"
                print(f"Current Broker Mode: {bmode} | Active MT5 Account: {act_str}")
                print("Commands: switch live | switch <alias|login>")
        elif name in ("live", "livetrade"):
            from mt5_accounts import get_account_manager, switch_broker_mode
            sub_cmd = parts[1].lower() if len(parts) > 1 else ""
            if sub_cmd in ("off", "stop"):
                print("Live execution is exclusive. Paper trading has been removed. Use 'autotrade off' or 'kill on' to stop trading.")
            elif sub_cmd in ("paper", "demo"):
                ok, msg, _ = switch_broker_mode("paper", engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            elif sub_cmd in ("on", "start", "enable", "connect") or (len(parts) == 1 and getattr(config, "BROKER_MODE", "mt5_live") != "mt5_live"):
                target_id = parts[2] if len(parts) > 2 else None
                ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                print(msg.replace("*", "").replace("`", ""))
            elif len(parts) >= 4 and parts[1].isdigit():
                mgr = get_account_manager()
                try:
                    acc = mgr.add_account(parts[1], parts[2], parts[3], alias=parts[4] if len(parts) > 4 else "live", set_active=True)
                    ok, msg, _ = switch_broker_mode("live", identifier=acc.login, engine=engine, config_mod=config)
                    print(msg.replace("*", "").replace("`", ""))
                except Exception as e:
                    print(f"Error: {e}")
            else:
                bmode = getattr(config, "BROKER_MODE", "mt5_live").upper()
                print(f"Current Mode: {bmode} (Usage: live on | live <login> <password> <server>)")
        elif name in ("balance", "wallet", "equity"):
            print("⚠️ Paper trading and paper wallet balance have been removed. MetaTrader 5 Live is the sole exclusive broker.")
        elif name == "broker":
            if len(parts) > 1:
                bmode = parts[1].lower()
                if bmode in ("paper", "demo", "sim"):
                    print("❌ Paper trading mode has been removed. MetaTrader 5 Live is the sole exclusive broker.")
                elif bmode in ("mt5", "mt5_live", "live"):
                    from mt5_accounts import switch_broker_mode
                    target_id = parts[2] if len(parts) > 2 else None
                    ok, msg, _ = switch_broker_mode("live", identifier=target_id, engine=engine, config_mod=config)
                    print(msg.replace("*", "").replace("`", ""))
                else:
                    print(f"⚠️ Broker '{bmode}' has been removed. MetaTrader 5 Live is the sole exclusive broker. Use 'mt5 live'.")
            else:
                bname = type(engine.broker).__name__ if engine.broker else "None"
                print(f"Active broker: {bname} ({getattr(config, 'BROKER_MODE', 'mt5_live')})")
        elif name in ("oanda", "alpaca", "ccxt"):
            print(f"⚠️ Native live broker '{name.upper()}' has been removed. MetaTrader 5 Live is the sole exclusive broker.")
        elif name in ("help", "commands", "cmd", "menu", "list", "all"):
            print(
                "Commands: signal [SYM], price [SYM], levels [SYM], news [SYM], quality [SYM], positions, autotrade on|off, close [SYM], cancel [SYM], closed, balance [AMOUNT], mt5 [CMD], broker [MODE], status, health, risk, leverage [LEV], kill [on|off], backtest [BARS] [SYM], abacktest [MODE] [BARS] [SYM], performance, profile [NAME], board, journal, agents, tiers, digest, brain, relearn [SYM], ask [TEXT], reset, exit\n\n"
                "Commands by category:\n"
                "📊 Market: price [SYM], levels [SYM], news [SYM]\n"
                "🎯 Signals: signal [SYM], asignal [SYM] [profile], single [SYM], quality [SYM]\n"
                "⚡ Execution: positions, autotrade on|off, close [SYM|all], cancel [SYM], closed, balance [AMOUNT], broker [MODE], mt5 [CMD]\n"
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
        elif name == "brain":
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
                elif sub in ("relearn", "train"):
                    sym = parts[2].upper() if len(parts) > 2 else "XAUUSD"
                    years = float(parts[3]) if len(parts) > 3 and parts[3].replace('.', '', 1).isdigit() else 5.0
                    print(f"Retraining Autonomous Learner on {sym} ({years:.1f}y)...")
                    summary = brain.bootstrap(symbol=sym, years=years)
                    print(f"Bootstrap complete: {summary}")
                elif sub in ("help", "commands"):
                    print("brain [status|rules|flaws|relearn [SYM] [YEARS]|reset|on|off]")
                else:
                    print(brain.summary())
            except Exception as e:
                print(f"Brain error: {e}")
        elif name == "relearn":
            try:
                from autonomous_learner import get_brain
                brain = get_brain(auto_bootstrap=False)
                sym = parts[1].upper() if len(parts) > 1 else "XAUUSD"
                years = float(parts[2]) if len(parts) > 2 and parts[2].replace('.', '', 1).isdigit() else 5.0
                print(f"Retraining Autonomous Learner on {sym} ({years:.1f}y)...")
                summary = brain.bootstrap(symbol=sym, years=years)
                print(f"Bootstrap complete: {summary}")
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
                BotCommand("watchlist", "View & switch active Gold/BTC autotrade watchlist"),
                BotCommand("close", "Close position(s) by symbol or all"),
                BotCommand("cancel", "Cancel pending setup order"),
                BotCommand("closed", "Recently closed trades history & PnL"),
                BotCommand("status", "Portfolio equity & margin capacity"),
                BotCommand("health", "System health & background worker telemetry"),
                BotCommand("risk", "Risk tier report, sizing & reset"),
                BotCommand("resetrisk", "Reset daily/weekly risk limits & restart"),
                BotCommand("leverage", "Set or view leverage (1-200x, manual/auto)"),
                BotCommand("kill", "Emergency execution circuit breaker switch"),
                BotCommand("mt5", "MetaTrader 5 live execution engine & dashboard"),
                BotCommand("switch", "Switch active MT5 live account"),
                BotCommand("live", "MT5 Live execution controls & account telemetry"),
                BotCommand("broker", "View MT5 Live execution engine & status"),
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
    app.add_handler(CommandHandler(["watchlist", "wl", "symbols", "assets"], watchlist_command))
    app.add_handler(CommandHandler(["close", "closeall"], close_command))
    app.add_handler(CommandHandler("closed", closed_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(autotrade_callback_handler))

    # MT5 Live Broker Execution
    app.add_handler(CommandHandler(["balance", "wallet", "equity"], balance_command))
    app.add_handler(CommandHandler("mt5", mt5_command))
    app.add_handler(CommandHandler(["switch", "switchbroker"], switch_command))
    app.add_handler(CommandHandler(["live", "livetrade"], live_command))
    app.add_handler(CommandHandler("broker", broker_command))
    app.add_handler(CommandHandler("oanda", oanda_command))
    app.add_handler(CommandHandler("alpaca", alpaca_command))
    app.add_handler(CommandHandler("ccxt", ccxt_command))

    # Risk & Safety
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("risk", risk_command))
    app.add_handler(CommandHandler(["resetrisk", "riskreset"], resetrisk_command))
    app.add_handler(CommandHandler("leverage", leverage_command))
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
        from telegram_advanced import abacktest_command, profile_command
        app.add_handler(CommandHandler(["abacktest", "backtestall"], abacktest_command))
        app.add_handler(CommandHandler("profile", profile_command))
    except Exception as e:
        logger.exception("Failed to register abacktest/profile handlers: %s", e)

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


    async def global_telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = getattr(context, "error", None)
        if not err:
            return
        err_str = str(err)
        if "Conflict" in err_str or "terminated by other getUpdates" in err_str:
            logger.warning("Telegram conflict: another bot instance is polling with this token. Pausing briefly...")
        elif "NetworkError" in err_str or "TimedOut" in err_str or "httpx" in err_str:
            logger.warning("Telegram network glitch (non-fatal): %s", err)
        else:
            logger.error("Telegram error caught: %s", err)

    app.add_error_handler(global_telegram_error_handler)

    print("Trading Bot is running. Press Ctrl+C to terminate.")
    try:
        app.run_polling(drop_pending_updates=True)
    except Exception as poll_err:
        logger.error("Telegram polling terminated: %s", poll_err)
        raise


if __name__ == "__main__":
    main()
