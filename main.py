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
arbiter = Arbiter()
engine = ExecutionEngine(risk_manager=risk_mgr)

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

    logger.error("Live quote unavailable for %s from TradingView and Delta Exchange", sym)
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
        "🤖 **Institutional Multi-Agent Trading Assistant**\n\n"
        "Welcome! Use /help for commands. /signal for 5-agent consensus.\n"
        "Setup monitoring tracks levels locally; live exchange execution is separate."
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **Command Guide**\n"
        "• `/price [SYMBOL]` `/signal [SYMBOL]` `/news`\n"
        "• `/leverage [1-20]` `/positions` `/close` `/cancel`\n"
        "• `/status` `/health` `/risk` `/autotrade on|off` `/backtest`\n"
        "• `/reset` — clear AI memory\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def price_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    info = get_price_info(sym)
    await update.message.reply_text(format_price_string(info), parse_mode="Markdown")


async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    await update.message.reply_text(f"⏳ Evaluating **{sym}** across all 5 agents...", parse_mode="Markdown")
    res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE, leverage=risk_mgr.get_leverage())
    price_val = res.get("price", 0.0) or 0.0
    sig = res.get("signal")
    votes = res.get("votes", [])
    vote_lines = []
    for v in votes:
        icon = "🟢" if v["direction"] == "LONG" else ("🔴" if v["direction"] == "SHORT" else "⚪")
        vote_lines.append(f"{icon} **{v['agent']}:** {v['direction']} ({v['reason']})")
    status_str = f"⚡ **Consensus:** `{res.get('status', 'No consensus')}`"
    if sig:
        dir_name = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])
        dir_icon = "🟢 LONG" if "LONG" in dir_name.upper() else "🔴 SHORT"
        sig_box = (
            f"\n🎯 **TRADE SETUP ({dir_icon}):**\n"
            f"• **Entry:** `${sig['entry']:,.2f}`\n"
            f"• **Stop Loss:** `${sig['stop']:,.2f}`\n"
            f"• **Target 1:** `${sig['tp1']:,.2f}` | **Target 2:** `${sig['tp2']:,.2f}`\n"
            f"• **Lots:** `{sig['lots']}` | **Lev:** `{sig.get('leverage', risk_mgr.get_leverage()):.0f}x`\n"
            f"• **Margin:** `${sig.get('margin_required', 0.0):,.2f}` | **R:R:** `1:{sig.get('rr', 0):.1f}`\n"
        )
    else:
        sig_box = "\n🛡️ **Stay Flat.** Waiting for confluence.\n"
    msg = (
        f"📊 **Multi-Agent Evaluation for {sym}**\n"
        f"Mark: `${float(price_val):,.2f}`\n\n"
        f"**Agent Votes:**\n" + "\n".join(vote_lines) + f"\n\n{status_str}\n{sig_box}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


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


async def risk_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = risk_mgr.state
    risk_pct, reason = risk_mgr.current_risk_pct()
    msg = (
        f"🛡️ **Risk**\n"
        f"• Risk/trade: `{risk_pct:.2f}%` ({reason})\n"
        f"• Equity: `${s.equity:,.2f}` | Mode: `{s.mode}`\n"
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
    summary = engine.get_portfolio_summary()
    active = summary.get("active_positions_count", 0)
    pending = summary.get("pending_setups_count", 0)
    msg = f"📋 **Positions**\n• Open: `{active}` | Pending setups: `{pending}`\n• Unrealized: `${summary.get('unrealized_pnl', 0):+,.2f}`"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def close_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = (ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL)
    try:
        engine.close_position(sym)
        await update.message.reply_text(f"Close requested for `{sym}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Close failed: {e}")


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = (ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL)
    try:
        engine.cancel_setup(sym, reason="Manual cancel")
        await update.message.reply_text(f"Cancelled pending setup for `{sym}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Cancel failed: {e}")


async def news_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        agent = NewsAgent()
        v = agent.vote()
        await update.message.reply_text(f"📰 **News**\n{v.direction.name}: {v.reason}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"News error: {e}")


async def autotrade_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global autotrade_active
    arg = (ctx.args[0].lower() if ctx.args else "")
    if arg in ("on", "1", "true", "enable"):
        autotrade_active = True
        await update.message.reply_text("🟢 Autotrade **ON** (setup monitoring)", parse_mode="Markdown")
    elif arg in ("off", "0", "false", "disable"):
        autotrade_active = False
        await update.message.reply_text("⚪ Autotrade **OFF**", parse_mode="Markdown")
    else:
        st = "ON" if autotrade_active else "OFF"
        await update.message.reply_text(f"Autotrade is **{st}**. Use `/autotrade on|off`", parse_mode="Markdown")


async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bars = 1000
    if ctx.args:
        try:
            bars = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text("Usage: /backtest [bars]")
            return
    await update.message.reply_text(f"⏳ Backtest {bars} bars...")
    try:
        from data_feed import DataFeed
        feed = DataFeed(symbol=config.DEFAULT_SYMBOL, source="ccxt", exchange_id=config.DEFAULT_EXCHANGE, timeframe="1h")
        historical = feed.fetch(limit=bars, timeframe="1h")
        if historical is None or historical.empty:
            raise ValueError("No historical candles")
        stats = run_backtest(historical.tail(bars), config=BacktestConfig(initial_equity=config.INITIAL_EQUITY), symbol=config.DEFAULT_SYMBOL, timeframe="1h")
        await update.message.reply_text(f"```\n{stats}\n```", parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Backtest failed: %s", exc)
        await update.message.reply_text(f"Backtest failed: `{type(exc).__name__}: {exc}`", parse_mode="Markdown")


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
    """Background task that polls candles and tracks setups/positions."""
    feed = MarketDataFeed(symbol=config.DEFAULT_SYMBOL, source=config.DEFAULT_DATA_SOURCE)
    symbol = config.DEFAULT_SYMBOL
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
                        df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr, symbol=symbol
                    )
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
    print("TradingBot CLI — type /signal, /status, /price, exit")
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
        if name == "signal":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            res = evaluate_symbol(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
            print(res.get("status"), res.get("signal"))
        elif name == "status":
            print(engine.get_portfolio_summary())
        elif name == "price":
            sym = parts[1].upper() if len(parts) > 1 else config.DEFAULT_SYMBOL
            print(get_price_info(sym))
        else:
            print("Commands: signal, status, price, exit")


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

    app = ApplicationBuilder().token(token).post_init(on_post_init).build()
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))

    print("Trading Bot is running. Press Ctrl+C to terminate.")
    app.run_polling()


if __name__ == "__main__":
    main()
