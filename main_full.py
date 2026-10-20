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
from typing import Dict, Optional

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


async def _to_thread(func, *args, **kwargs):
    if hasattr(asyncio, "to_thread"):
        return await asyncio.to_thread(func, *args, **kwargs)
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


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
        "🤖 **TradingBot — Multi-Agent Paper Assistant**\n\n"
        "Welcome! Use /help for commands. /signal for 5-agent consensus.\n"
        "Setup monitoring tracks levels locally; live exchange execution is separate."
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **TradingBot Guide**\n\n"
        "⚡ **Market & Signals:**\n"
        "• `/price [SYM]` — Live mark price & quote\n"
        "• `/levels [SYM]` — Key levels (PDH/PDL, pivots, swings)\n"
        "• `/signal [SYM]` or `/single [SYM]` — 5-Agent confluence setup\n"
        "• `/asignal [SYM] [profile]` — Advanced SMC + CVD setup\n"
        "• `/news` — Market sentiment & shock detection\n\n"
        "📊 **Risk & Position Management:**\n"
        "• `/status` — Portfolio health & autotrade state\n"
        "• `/positions` — Active open trades\n"
        "• `/risk` — Risk tier & exposure summary\n"
        "• `/leverage [1-20]` — Set maximum leverage\n"
        "• `/close [ID]` & `/cancel [ID]` — Manage trades\n"
        "• `/kill [on|off]` — Emergency execution stop\n\n"
        "🔬 **Backtesting & Research:**\n"
        "• `/backtest [single|auto] [bars] [SYM]` — Fast backtest\n"
        "• `/abacktest [mode] [bars] [SYM]` — Advanced backtest suite\n"
        "• `/profile [name]` — Switch strategy profile (scalp/swing/trend/balanced)\n"
        "• `/performance` — Detailed trade metrics & Sharpe\n"
        "• `/brain` & `/relearn` — Autonomous self-learning memory\n"
        "• `/autotrade on|off` — Toggle setup monitoring & trade tracking\n"
        "• `/reset` — Clear conversation context\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


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
        if sig:
            # Autotrade executes positions when /signal is called
            ok, note = try_paper_execute(engine, sig, candle_price=res.get("price") or sig.get("entry"))
            tag = "✅" if ok else "⚠️"
            if ok:
                global autotrade_active
                autotrade_active = True
                try:
                    engine.reset_halt()
                except Exception:
                    pass
            exec_msg = f"{tag} Autotrade Executed: {note}" if ok else f"{tag} Autotrade: {note}"
            try:
                await update.message.reply_text(exec_msg, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(exec_msg)
        else:
            logger.info("Signal %s: setup was flat or unconfirmed, no trade executed", sym)
    except Exception as e:
        logger.exception("signal_command failed")
        await update.message.reply_text(f"Signal error: `{type(e).__name__}: {e}`", parse_mode="Markdown")



async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = risk_mgr.state
    mode_icon = "🟢" if s.mode == "normal" else ("🟡" if s.mode == "reduced" else "🔴")
    auto_status = "🟢 ACTIVE" if autotrade_active else "⚪ STOPPED"
    summary = engine.get_portfolio_summary()
    msg = (
        f"📈 **Bot Status**\n"
        f"• Monitoring: {auto_status}\n"
        f"• Symbol: `{config.DEFAULT_SYMBOL}`\n"
        f"• Equity: `${summary['equity']:,.2f}` | Lev: `{summary['leverage']:.0f}x`\n"
        f"• Positions: `{summary['active_positions_count']}` | Pending: `{summary['pending_setups_count']}`\n"
        f"• Risk: {mode_icon} `{s.mode.upper()}` | Today PnL: `${s.daily_pnl:+,.2f}`\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def health_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"❤️ **Health**\n"
        f"• Last poll: `{monitoring_health.get('last_poll_at')}`\n"
        f"• Last success: `{monitoring_health.get('last_success_at')}`\n"
        f"• Last error: `{monitoring_health.get('last_error')}`\n"
        f"• Consecutive errors: `{monitoring_health.get('consecutive_errors')}`\n"
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

    s = risk_mgr.state
    risk_pct, reason = risk_mgr.current_risk_pct()
    can, why = risk_mgr.can_trade()
    trade_st = "🟢 Ready" if can else f"⚠️ Blocked ({why})"
    msg = (
        f"🛡️ **Risk**\n"
        f"• Risk/trade: `{risk_pct:.2f}%` ({reason})\n"
        f"• Status: {trade_st}\n"
        f"• Equity: `${s.equity:,.2f}` | Mode: `{s.mode}`\n"
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
    if not arg or arg in ("ALL", "CLOSED", "*"):
        if not engine.active_positions:
            await update.message.reply_text("No open positions to close.")
            return
        closed = []
        if hasattr(engine, "close_all_positions"):
            closed = engine.close_all_positions(reason="Manual close all")
        else:
            for sym in list(engine.active_positions.keys()):
                p = engine.close_position(sym, reason="Manual close all")
                if p:
                    closed.append(p)
        lines = [f"✅ Closed {len(closed)} position(s)"]
        for p in closed:
            lines.append(f"{p.symbol} PnL ${float(getattr(p, 'realized_pnl', 0) or 0):+.2f}")
        await update.message.reply_text("\n".join(lines))
        return
    sym = arg if arg not in ("BTC", "BTCUSD") else "BTCUSDT"
    closed = engine.close_position(sym, reason="Manual close")
    if closed:
        await update.message.reply_text(f"✅ Closed {sym} PnL ${float(getattr(closed, 'realized_pnl', 0) or 0):+.2f}")
    else:
        await update.message.reply_text(f"No position for {sym}. Use /close all")


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


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = (ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL)
    try:
        cancelled = engine.cancel_setup(sym, reason="Manual cancel")
        if cancelled:
            await update.message.reply_text(f"Cancelled pending setup for `{sym}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No pending setup for `{sym}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Cancel failed: {e}")


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

    if any(a in ("scalp",) for a in args):
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
                cur_mode = getattr(config, "get_autotrade_mode", lambda: getattr(config, "AUTOTRADE_MODE", "signal"))().lower()
                if cur_mode == "signal":
                    can_open = False
                elif is_scalping:
                    can_open = (autotrade_active and open_n < max_trades)
                else:
                    can_open = (autotrade_active and symbol not in engine.active_positions and symbol not in engine.pending_setups and open_n < max_trades)
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
        elif name == "help":
            print("Commands: signal [SYM], backtest [BARS] [SYM], status, health, levels [SYM], price [SYM], news [SYM], quality [SYM], risk, leverage [LEV], positions, performance, kill [on|off], profile [NAME], autotrade on|off, close [SYM], cancel [SYM], brain [status|rules|flaws|relearn|reset|on|off], relearn [SYM], ask [TEXT], reset, exit")
        else:
            print("Commands: signal, single, backtest, status, health, levels, price, news, quality, risk, leverage, positions, performance, kill, profile, autotrade on|off, close, cancel, brain, relearn, ask, reset, exit")


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
        # Advanced commands if available
        try:
            from telegram_advanced import register_all_advanced
            register_all_advanced(application)
            print("Advanced commands registered")
        except Exception as adv_err:
            print(f"Advanced commands not loaded: {adv_err}")
        try:
            from telegram_brain import register_brain
            register_brain(application)
            print("Autonomous Learner commands registered")
        except Exception as brain_err:
            print(f"Brain commands not loaded: {brain_err}")

    app = ApplicationBuilder().token(token).post_init(on_post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("levels", levels_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("asignal", signal_command))
    app.add_handler(CommandHandler("single", signal_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("risk", risk_command))
    app.add_handler(CommandHandler(["resetrisk", "riskreset"], resetrisk_command))
    app.add_handler(CommandHandler("leverage", leverage_command))
    app.add_handler(CommandHandler("close", close_command))
    app.add_handler(CommandHandler("closeall", close_command))
    app.add_handler(CommandHandler("closed", closed_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("autotrade", autotrade_command))
    app.add_handler(CommandHandler(["watchlist", "wl", "symbols", "assets"], watchlist_command))
    app.add_handler(CallbackQueryHandler(autotrade_callback_handler))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("reset", reset_command))
    try:
        from telegram_brain import brain_command, relearn_command
        app.add_handler(CommandHandler("brain", brain_command))
        app.add_handler(CommandHandler("relearn", relearn_command))
    except Exception:
        pass
    try:
        from telegram_advanced import performance_command
        app.add_handler(CommandHandler(["performance", "perf"], performance_command))
    except Exception as e:
        logger.exception("Failed to register performance_command: %s", e)
    try:
        from telegram_advanced import abacktest_command, profile_command, kill_command
        app.add_handler(CommandHandler("profile", profile_command))
        app.add_handler(CommandHandler("kill", kill_command))
        app.add_handler(CommandHandler(["abacktest", "auto_backtest"], abacktest_command))
    except Exception as e:
        logger.exception("Failed to register advanced handlers: %s", e)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))

    print("Trading Bot is running. Press Ctrl+C to terminate.")
    app.run_polling()



if __name__ == "__main__":
    main()
