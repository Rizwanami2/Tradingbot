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
        delta_api_key=getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", ""),
        delta_api_secret=getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", ""),
        delta_base_url=getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange"),
        delta_testnet=getattr(config, "DELTA_EXCHANGE_TESTNET", False),
        delta_leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
        delta_auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
        delta_max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
    )
except Exception as _broker_err:
    print("[boot] broker:", _broker_err)
    _broker = None
engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)
try:
    from strategy_profiles import apply_profile
    apply_profile(getattr(config, "CURRENT_PROFILE", "scalp"), arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
except Exception as _p_boot_err:
    print("[boot] profile init:", _p_boot_err)

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
    elif cat in ("delta", "mt5", "broker", "brokers", "balance", "wallet", "equity", "oanda", "ccxt", "alpaca"):
        text = (
            "🏦 **MetaTrader 5 & Broker Accounts (Paper, Delta India & MT5):**\n"
            "• `/delta [status|live|testnet|leverage|auto_leverage|positions]` — Delta Exchange India Live REST broker (Max 200x)\n"
            "• `/mt5 [status|paper|live|positions]` — MetaTrader 5 Simulation or Live Terminal Bridge\n"
            "• `/balance [amount]` — View or custom-set paper wallet balance (e.g. `/balance 50000`)\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker [paper|delta|mt5]` — Switch execution broker engine"
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
            "• `/autotrade on|off` — Multi-symbol autotrading (up to 5 concurrent active trades)\n"
            "• `/close [SYM|all]` — Instantly close position(s)\n"
            "• `/cancel [SYM]` — Cancel pending order setup\n"
            "• `/closed` — Recently closed trades history & realized PnL\n\n"
            "🏦 **MetaTrader 5 & Paper Trade Wallet:**\n"
            "• `/balance [amount]` — View or custom-set paper wallet balance (e.g. `/balance 50000`)\n"
            "• `/broker` — View active broker environment & telemetry\n"
            "• `/broker paper` — Switch execution broker to paper simulation\n"
            "• `/mt5 [status|paper|live|positions]` — MetaTrader 5 Simulation or Live Terminal Bridge\n\n"
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
        f"• **Circuit Breakers:** 🟢 Cleared (Engine halt lifted, Kill switch deactivated)",
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


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global autotrade_active
    arg = (ctx.args[0].lower() if ctx.args else "")
    broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
    max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2))
    symbols_str = ", ".join(getattr(config, "AUTOTRADE_SYMBOLS", ["BTCUSDT", "XAUUSD"]))
    if arg in ("scalp", "spam") or (len(ctx.args) > 1 and ctx.args[1].lower() in ("scalp", "spam")):
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
            f"• **Active Limit:** `{max_trades}` concurrent trades | Watchlist: `{symbols_str}`",
            parse_mode="Markdown",
        )
    elif arg in ("on", "1", "true", "enable"):
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
        await update.message.reply_text(
            "🏦 **MetaTrader 5 Commands:**\n"
            "• `/mt5 status` — Check MT5 account telemetry & connection\n"
            "• `/mt5 paper` — Switch to MT5 Paper simulation engine\n"
            "• `/mt5 live [login] [pass] [server] [gateway_url]` — Connect live MT5 account\n"
            "• `/mt5 positions` — Show all active MT5 broker positions",
            parse_mode="Markdown",
        )


def set_paper_wallet_balance(amount: Any) -> Tuple[bool, str, Dict[str, Any]]:
    import math
    if isinstance(amount, str):
        cleaned = amount.replace("$", "").replace(",", "").strip().lower()
        multiplier = 1.0
        if cleaned.endswith("k"):
            multiplier = 1000.0
            cleaned = cleaned[:-1]
        elif cleaned.endswith("m"):
            multiplier = 1000000.0
            cleaned = cleaned[:-1]
        try:
            amt = float(cleaned) * multiplier
        except Exception:
            return False, f"Invalid balance amount: {amount}. Must be a positive finite number.", {}
    else:
        try:
            amt = float(amount)
        except Exception:
            return False, f"Invalid balance amount: {amount}. Must be a positive finite number.", {}

    if not math.isfinite(amt) or amt <= 0:
        return False, f"Invalid balance amount: {amount}. Must be a positive finite number.", {}

    # 1. Update risk manager equity and peak equity
    if hasattr(risk_mgr, "set_equity"):
        try:
            risk_mgr.set_equity(amt)
        except Exception:
            pass
    if hasattr(risk_mgr, "state") and risk_mgr.state:
        risk_mgr.state.equity = amt
        risk_mgr.state.peak_equity = max(float(risk_mgr.state.peak_equity or 0.0), amt)
    if hasattr(risk_mgr, "_save_state"):
        try:
            risk_mgr._save_state()
        except Exception:
            pass

    # 2. Update engine risk manager if attached
    if hasattr(engine, "risk_manager") and engine.risk_manager:
        try:
            if hasattr(engine.risk_manager, "set_equity"):
                engine.risk_manager.set_equity(amt)
            if hasattr(engine.risk_manager, "state") and engine.risk_manager.state:
                engine.risk_manager.state.equity = amt
                engine.risk_manager.state.peak_equity = max(float(engine.risk_manager.state.peak_equity or 0.0), amt)
            if hasattr(engine.risk_manager, "_save_state"):
                engine.risk_manager._save_state()
        except Exception:
            pass

    # 3. Update active broker balance and equity if PaperBroker or MT5Broker in paper mode
    broker_updated = False
    broker_name = "None"
    if hasattr(engine, "broker") and engine.broker:
        broker_name = type(engine.broker).__name__
        if hasattr(engine.broker, "balance"):
            try:
                engine.broker.balance = amt
                broker_updated = True
            except Exception:
                pass
        if hasattr(engine.broker, "equity"):
            try:
                engine.broker.equity = amt
            except Exception:
                pass
        if hasattr(engine.broker, "_initial_balance"):
            try:
                engine.broker._initial_balance = amt
            except Exception:
                pass
        if hasattr(engine.broker, "_save_state"):
            try:
                engine.broker._save_state()
            except Exception:
                pass

    # 4. Update config default
    config.INITIAL_EQUITY = amt

    risk_pct = float(getattr(risk_mgr.config, "base_risk_pct", 0.20))
    info = {
        "balance": amt,
        "equity": amt,
        "peak_equity": float(risk_mgr.state.peak_equity),
        "leverage": float(risk_mgr.get_leverage()),
        "risk_pct": risk_pct,
        "risk_amount": amt * (risk_pct / 100.0),
        "broker": broker_name,
        "broker_updated": broker_updated,
    }
    return True, f"Paper wallet balance successfully set to ${amt:,.2f}", info


async def balance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View current paper trading wallet balance or custom set / manually modify it."""
    args = ctx.args or []
    if not args:
        eq = float(getattr(risk_mgr.state, "equity", 0.0) or config.INITIAL_EQUITY)
        peak = float(getattr(risk_mgr.state, "peak_equity", eq) or eq)
        lev = float(risk_mgr.get_leverage())
        risk_pct = float(getattr(risk_mgr.config, "base_risk_pct", 0.20))
        risk_amt = eq * (risk_pct / 100.0)
        bname = type(engine.broker).__name__ if engine.broker else "PaperBroker"
        bmode = getattr(config, "BROKER_MODE", "paper")

        await update.message.reply_text(
            f"💼 **Paper Trading Wallet & Balance**\n"
            f"• **Current Balance:** `${eq:,.2f}`\n"
            f"• **Equity:** `${eq:,.2f}`\n"
            f"• **Peak Equity:** `${peak:,.2f}`\n"
            f"• **Active Leverage:** `{lev:.0f}x`\n"
            f"• **Risk per Trade ({risk_pct:.2f}%):** `${risk_amt:,.2f}`\n"
            f"• **Execution Engine:** `{bname}` (`{bmode}`)\n\n"
            f"✏️ **Modify / Custom Set Balance:**\n"
            f"• Usage: `/balance <amount>`\n"
            f"• Example: `/balance 5000` or `/balance 25000`\n"
            f"• Aliases: `/wallet`, `/equity`",
            parse_mode="Markdown",
        )
        return

    raw_val = args[0].strip()
    ok, msg, info = set_paper_wallet_balance(raw_val)
    if ok:
        await update.message.reply_text(
            f"✅ **Paper Wallet Balance Updated**\n"
            f"• **New Balance / Equity:** `${info['balance']:,.2f}`\n"
            f"• **Peak Equity:** `${info['peak_equity']:,.2f}`\n"
            f"• **Risk Amount / Trade:** `${info['risk_amount']:,.2f}` (at `{info['risk_pct']:.2f}%` risk)\n"
            f"• **Active Leverage:** `{info['leverage']:.0f}x`\n"
            f"• **Broker Synced:** `{info['broker']}`\n\n"
            f"_All future paper orders and autotrades will size according to this wallet balance._",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"❌ Failed to set balance: {msg}\n"
            f"Example: `/balance 5000` or `/balance 50k` or `/balance 100000`",
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
            config.DELTA_EXCHANGE_API_KEY = key
            config.DELTA_EXCHANGE_API_SECRET = secret
            if new_broker.is_testnet:
                config.BROKER_MODE = "delta_testnet"
                await update.message.reply_text(
                    f"🧪 **Delta Exchange Testnet Connected Successfully**\n"
                    f"• **Environment:** `TESTNET (Simulation)`\n"
                    f"• **Endpoint:** `{new_broker.base_url}`\n"
                    f"• **Wallet Balance:** `${new_broker.balance:,.2f}` {new_broker.currency}\n"
                    f"• **Equity:** `${new_broker.equity:,.2f}` {new_broker.currency}\n"
                    f"• **Available Margin:** `${new_broker.free_margin:,.2f}`\n"
                    f"• **Max Leverage:** `200x`\n"
                    f"• **Status:** Active orders will now execute on Delta Exchange testnet.",
                    parse_mode="Markdown",
                )
            else:
                config.BROKER_MODE = "delta_live"
                await update.message.reply_text(
                    f"🟢 **Delta Exchange India LIVE Connected Successfully**\n"
                    f"• **Environment:** `LIVE (Delta Exchange India)`\n"
                    f"• **Endpoint:** `{new_broker.base_url}`\n"
                    f"• **Wallet Balance:** `${new_broker.balance:,.2f}` {new_broker.currency}\n"
                    f"• **Equity:** `${new_broker.equity:,.2f}` {new_broker.currency}\n"
                    f"• **Available Margin:** `${new_broker.free_margin:,.2f}`\n"
                    f"• **Max Leverage:** `200x`\n"
                    f"• **Status:** Live deal execution active with server-side brackets & leverage.",
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

    # 1. No arguments or status
    if not args or args[0].lower() in ("status", "info"):
        if isinstance(engine.broker, DeltaBroker) or (hasattr(engine.broker, "get_account_info") and "Delta" in type(engine.broker).__name__):
            info = engine.broker.get_account_info()
            await update.message.reply_text(info.summary(), parse_mode="Markdown")
            return
        elif env_key and env_secret:
            # Credentials present in environment — auto-connect immediately!
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

    # 2. Command word with zero extra args: /delta live, /delta testnet, /delta connect, /broker delta
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

    # 3. /delta live [KEY] [SECRET]
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

    # 4. /delta testnet [KEY] [SECRET]
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

    # 5. Direct keys: /delta <API_KEY> <API_SECRET> (user omitted the word "live" or "testnet")
    if len(args) >= 2 and sub not in ("leverage", "lev", "auto_leverage", "autolev", "positions", "pos"):
        key = args[0].strip()
        secret = args[1].strip()
        await _connect_and_reply(key, secret, is_testnet=False)
        return

    # 6. Single argument with delimiter (e.g. KEY:SECRET or KEY,SECRET)
    if len(args) == 1 and any(delim in args[0] for delim in (":", ",", "|")):
        for delim in (":", ",", "|"):
            if delim in args[0]:
                parts = args[0].split(delim, 1)
                await _connect_and_reply(parts[0].strip(), parts[1].strip(), is_testnet=False)
                return

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

    else:
        await update.message.reply_text(
            "🇮🇳 **Delta Exchange India Commands:**\n"
            "• `/delta status` — Check Delta account telemetry & connection\n"
            "• `/delta live [API_KEY] [API_SECRET]` — Connect live Delta Exchange India account\n"
            "• `/delta testnet [API_KEY] [API_SECRET]` — Connect Delta testnet account\n"
            "• `/delta leverage <1-200> [SYMBOL]` — Manually set leverage (up to 200x)\n"
            "• `/delta auto_leverage [on|off]` — Toggle auto-scaling dynamic leverage (max 200x)\n"
            "• `/delta positions` — Show all active Delta Exchange positions",
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
            f"🏦 **Execution Broker Environment**\n"
            f"• **Active Broker:** `{bname}`\n"
            f"• **Active Mode:** `{bmode}`{summary}\n\n"
            f"🇮🇳 **Delta Exchange India (Live Crypto & Derivatives Broker):**\n"
            f"• `/delta status` — Check Delta Exchange wallet, equity & leverage\n"
            f"• `/delta live <API_KEY> <API_SECRET>` — Connect live Delta Exchange India account\n"
            f"• `/delta leverage <1-200>` — Manually set leverage (up to 200x)\n"
            f"• `/delta auto_leverage [on|off]` — Auto-scale leverage dynamically (max 200x)\n"
            f"• `/broker delta` — Switch active broker to Delta Exchange India\n\n"
            f"🖥️ **MetaTrader 5 (Exclusive Live Execution Engine):**\n"
            f"• `/mt5 paper` — High-fidelity MT5 simulation engine\n"
            f"• `/mt5 live <LOGIN> <PASSWORD> [SERVER]` — Live MT5 terminal execution\n"
            f"• `/mt5 status` — Check MT5 account balance & margin\n\n"
            f"💼 **Paper Trade Wallet:**\n"
            f"• `/balance <amount>` — Custom set / manually modify paper wallet balance\n"
            f"• `/broker paper` — Switch engine to paper execution",
            parse_mode="Markdown",
        )
        return

    mode = args[0].lower()
    if mode in ("paper", "demo", "sim"):
        from execution import PaperBroker
        eq = float(risk_mgr.state.equity or config.INITIAL_EQUITY)
        new_b = PaperBroker(initial_balance=eq)
        engine.broker = new_b
        config.BROKER_MODE = "paper"
        await update.message.reply_text(
            f"✅ **Broker Switched to PAPER Simulation**\n"
            f"• **Instance:** `PaperBroker`\n"
            f"• **Balance:** `${eq:,.2f}`\n"
            f"• **Status:** Local paper execution active",
            parse_mode="Markdown",
        )
    elif mode in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
        if len(args) > 1:
            ctx.args = list(args[1:])
        await delta_command(update, ctx)
    elif mode in ("mt5", "mt5_paper", "mt5_live"):
        await mt5_command(update, ctx)
    elif mode in ("oanda", "alpaca", "ccxt", "live"):
        await update.message.reply_text(
            f"⚠️ **Native Live Broker `{mode.upper()}` Removed**\n"
            f"MetaTrader 5 (`/mt5`) is now the exclusive live execution broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
            f"• To connect live Delta Exchange India: `/delta live <API_KEY> <API_SECRET>`\n"
            f"• To connect live MT5: `/mt5 live <LOGIN> <PASSWORD> [SERVER]`\n"
            f"• To run paper MT5: `/mt5 paper`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Usage: `/broker` | `/broker paper` | `/delta status|live|leverage` | `/mt5 status|paper|live` | `/balance <amount>`",
            parse_mode="Markdown",
        )


async def oanda_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **OANDA Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.\n"
        "• Use `/balance <amount>` to set your paper trading wallet balance.",
        parse_mode="Markdown",
    )


async def alpaca_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **Alpaca Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.\n"
        "• Use `/balance <amount>` to set your paper trading wallet balance.",
        parse_mode="Markdown",
    )


async def ccxt_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ **CCXT Live Broker Removed**\n"
        "Native live brokers have been removed from the platform. "
        "MetaTrader 5 (`/mt5`) is the sole live broker for Gold (XAUUSD) and Bitcoin (BTCUSDT).\n\n"
        "• Use `/mt5` to connect and manage your MT5 trading terminal.\n"
        "• Use `/balance <amount>` to set your paper trading wallet balance.",
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
        elif name == "autotrade":
            arg = parts[1].lower() if len(parts) > 1 else ""
            broker_mode = getattr(config, "BROKER_MODE", "paper").upper()
            if arg in ("scalp", "spam") or (len(parts) > 2 and parts[2].lower() in ("scalp", "spam")):
                try:
                    from strategy_profiles import apply_profile
                    apply_profile("scalp", arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
                except Exception:
                    pass
                autotrade_active = True
                engine.reset_halt()
                print(f"Autotrade ON in RAPID SCALP SPAM MODE ({broker_mode} mode)")
            elif arg in ("on", "1", "true", "enable"):
                autotrade_active = True
                engine.reset_halt()
                print(f"Autotrade ON ({broker_mode} mode)")
            elif arg in ("off", "0", "false", "disable"):
                autotrade_active = False
                print("Autotrade OFF")
            else:
                st = "ON" if autotrade_active else "OFF"
                prof = getattr(config, "CURRENT_PROFILE", "scalp").upper()
                print(f"Autotrade is {st} ({broker_mode}, profile={prof}). Use: autotrade on|off|scalp")
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
        elif name in ("balance", "wallet", "equity"):
            if len(parts) > 1:
                ok, msg, info = set_paper_wallet_balance(parts[1])
                print(msg)
            else:
                eq = float(getattr(risk_mgr.state, "equity", config.INITIAL_EQUITY) or config.INITIAL_EQUITY)
                init_eq = float(getattr(config, "INITIAL_EQUITY", 10000.0))
                peak = float(getattr(risk_mgr.state, "peak_equity", eq) or eq)
                bname = type(engine.broker).__name__ if engine.broker else "PaperBroker"
                print(
                    f"Paper Trade Wallet:\n"
                    f"• Current Balance/Equity: ${eq:,.2f}\n"
                    f"• Initial Balance: ${init_eq:,.2f}\n"
                    f"• Peak Equity: ${peak:,.2f}\n"
                    f"• Active Broker: {bname}\n\n"
                    f"To modify balance: balance <amount> (e.g. balance 50000 or balance 50k)"
                )
        elif name == "broker":
            if len(parts) > 1:
                bmode = parts[1].lower()
                if bmode in ("paper", "demo", "sim"):
                    from execution import PaperBroker
                    eq = float(risk_mgr.state.equity or config.INITIAL_EQUITY)
                    new_b = PaperBroker(initial_balance=eq)
                    engine.broker = new_b
                    config.BROKER_MODE = "paper"
                    print(f"Broker switched to paper (PaperBroker) with balance ${eq:,.2f}")
                elif bmode in ("delta", "delta_live", "delta_india", "delta_india_live"):
                    print("To configure Delta: delta live [api_key] [api_secret] | delta leverage <1-200>")
                elif bmode in ("mt5", "mt5_paper", "mt5_live"):
                    print("To configure MT5: mt5 paper | mt5 live [login] [pass] [server] [gateway_url]")
                elif bmode in ("oanda", "alpaca", "ccxt", "live"):
                    print(
                        f"⚠️ Native live broker '{bmode}' has been replaced.\n"
                        f"Delta Exchange India (delta) and MetaTrader 5 (mt5) are the active live broker interfaces.\n"
                        f"Use 'delta live' or 'mt5 live'."
                    )
                else:
                    print(f"Unknown broker mode: {bmode}. Available: paper, delta, mt5")
            else:
                bname = type(engine.broker).__name__ if engine.broker else "None"
                print(f"Active broker: {bname} ({getattr(config, 'BROKER_MODE', 'paper')})")
        elif name == "delta":
            if not parts:
                if engine.broker and hasattr(engine.broker, "get_account_info"):
                    print(engine.broker.get_account_info().summary())
                else:
                    print("Delta Exchange India: use 'delta live <key> <secret>' or 'delta leverage <1-200>'")
            elif parts[0] in ("live", "connect"):
                k = parts[1] if len(parts) > 1 else getattr(config, "DELTA_EXCHANGE_API_KEY", "")
                s = parts[2] if len(parts) > 2 else getattr(config, "DELTA_EXCHANGE_API_SECRET", "")
                if k and s:
                    try:
                        from delta_broker import DeltaBroker
                        engine.broker = DeltaBroker(api_key=k, api_secret=s)
                        config.BROKER_MODE = "delta_live"
                        print(f"Connected to Delta Exchange India Live! Balance: ${engine.broker.balance:,.2f}")
                    except Exception as exc:
                        print(f"Delta Live connect failed: {exc}")
                else:
                    print("Missing Delta API key or secret. Usage: delta live <key> <secret>")
            elif parts[0] in ("leverage", "lev"):
                if len(parts) > 1:
                    try:
                        v = float(parts[1].replace("x", ""))
                        risk_mgr.set_leverage(v)
                        if hasattr(engine.broker, "set_leverage"):
                            engine.broker.set_leverage(getattr(config, "DEFAULT_SYMBOL", "BTCUSD"), v)
                        print(f"Leverage set to {v:.0f}x")
                    except Exception as e:
                        print(f"Invalid leverage: {e}")
        elif name in ("oanda", "alpaca", "ccxt"):
            print(
                f"⚠️ Native live broker '{name.upper()}' has been removed.\n"
                f"Delta Exchange India (delta) and MetaTrader 5 (mt5) are active live brokers.\n"
                f"• Use 'delta live' or 'mt5 live' for live execution.\n"
                f"• Use 'balance <amount>' to set your paper trade wallet balance."
            )
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
                BotCommand("close", "Close position(s) by symbol or all"),
                BotCommand("cancel", "Cancel pending setup order"),
                BotCommand("closed", "Recently closed trades history & PnL"),
                BotCommand("status", "Portfolio equity & margin capacity"),
                BotCommand("health", "System health & background worker telemetry"),
                BotCommand("risk", "Risk tier report, sizing & reset"),
                BotCommand("resetrisk", "Reset daily/weekly risk limits & restart"),
                BotCommand("leverage", "Set or view leverage (1-200x, manual/auto)"),
                BotCommand("kill", "Emergency execution circuit breaker switch"),
                BotCommand("balance", "View or custom set paper wallet balance"),
                BotCommand("delta", "Delta Exchange India live broker & leverage (max 200x)"),
                BotCommand("mt5", "MetaTrader 5 live & paper engine"),
                BotCommand("broker", "View or switch active broker engine"),
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

    # MT5, Delta Exchange India & Paper Broker Execution
    app.add_handler(CommandHandler(["balance", "wallet", "equity"], balance_command))
    app.add_handler(CommandHandler("delta", delta_command))
    app.add_handler(CommandHandler("mt5", mt5_command))
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
