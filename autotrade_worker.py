#!/usr/bin/env python3
"""Multi-symbol autotrade monitoring worker — Gold + BTC only."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List

import pandas as pd

logger = logging.getLogger("autotrade_worker")


async def live_monitoring_worker(
    app,
    *,
    config,
    engine,
    risk_mgr,
    arbiter,
    MarketDataFeed,
    autotrade_active_getter: Callable[[], bool],
    write_runtime_status: Callable[[], None],
    monitoring_health: Dict[str, Any],
    now_iso: Callable[[], str],
):
    """Poll Gold + BTC only; open up to MAX_OPEN_TRADES concurrent setups/positions."""
    try:
        from multi_agent_trading_bot import _norm_trade_symbol
    except Exception:
        def _norm_trade_symbol(s):
            s = str(s or "").upper()
            if s in ("XAUTUSD", "XAUT", "GOLD", "XAU", "PAXGUSDT", "PAXG"):
                return "XAUUSD"
            if s in ("BTC", "BTCUSD", "XBT"):
                return "BTCUSDT"
            return s

    raw_syms = list(getattr(config, "AUTOTRADE_SYMBOLS", None) or ["BTCUSDT", "XAUUSD"])
    _ALLOWED = {
        "BTCUSDT", "BTCUSD", "BTC", "XAUUSD", "XAUTUSD", "XAU", "GOLD", "XAUT", "PAXGUSDT", "PAXG",
    }
    watchlist: List[str] = []
    for s in raw_syms:
        try:
            n = _norm_trade_symbol(s)
        except Exception:
            n = str(s).upper()
        if not n:
            continue
        if n not in _ALLOWED and not any(x in n for x in ("BTC", "XAU", "GOLD", "PAXG", "XAUT")):
            continue
        if "BTC" in n:
            n = "BTCUSDT"
        elif any(x in n for x in ("XAU", "GOLD", "PAXG", "XAUT")):
            n = "XAUUSD"
        else:
            continue
        if n not in watchlist:
            watchlist.append(n)
    if not watchlist:
        watchlist = ["BTCUSDT", "XAUUSD"]

    max_open = int(getattr(config, "MAX_OPEN_TRADES", 10) or 10)
    if getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False):
        max_open = max(max_open, int(getattr(engine, "max_open_trades", 10) or 10), 10)
    try:
        engine.max_open_trades = max_open
    except Exception:
        pass

    feeds = {sym: MarketDataFeed(symbol=sym, source=config.DEFAULT_DATA_SOURCE) for sym in watchlist}
    logger.info("Autotrade watchlist=%s max_open=%s (multi-symbol concurrent limit %s)", watchlist, max_open, max_open)
    write_runtime_status()

    while True:
        monitoring_health["last_poll_at"] = now_iso()
        any_data = False
        autotrade_active = bool(autotrade_active_getter())
        try:
            for symbol in watchlist:
                try:
                    feed = feeds.get(symbol) or MarketDataFeed(symbol=symbol, source=config.DEFAULT_DATA_SOURCE)
                    feeds[symbol] = feed
                    data = feed.multi_tf(limit_1m=500)
                    if not data or "1m" not in data or data["1m"].empty:
                        continue
                    any_data = True
                    df1 = data["1m"]
                    daily = data.get("daily")
                    if daily is None or getattr(daily, "empty", True):
                        daily = pd.DataFrame()
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
                        logger.info("Execution Event: %s - %s", ev["event"], ev.get("message"))
                        if config.TELEGRAM_CHAT_ID:
                            try:
                                await app.bot.send_message(
                                    chat_id=config.TELEGRAM_CHAT_ID, text=ev["message"], parse_mode="Markdown"
                                )
                            except Exception:
                                pass

                    is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                    open_n = len(engine.active_positions) + len(engine.pending_setups)
                    if is_scalping:
                        can_open = autotrade_active and open_n < max_open
                    else:
                        can_open = (
                            autotrade_active
                            and symbol not in engine.active_positions
                            and symbol not in engine.pending_setups
                            and open_n < max_open
                        )
                    if can_open:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr,
                            symbol=symbol, on_demand=True,
                        )
                        if not sig:
                            logger.info("Autotrade %s: no setup — %s", symbol, why)
                        else:
                            try:
                                order = engine.register_setup(sig, reason=sig.get("reason", ""))
                            except RuntimeError as reg_err:
                                logger.info("Autotrade %s: register blocked — %s", symbol, reg_err)
                                continue
                            is_scalp_order = getattr(engine, "scalp_mode", False) or getattr(order, "is_scalp", False)
                            alert = (
                                f"⚡ **NEW SCALP SETUP** ({open_n + 1}/{max_open})\n"
                                f"`{symbol}` **{order.direction}** @ `${order.entry_level:,.2f}`\n"
                                f"Micro SL `${order.stop_loss:,.2f}` | Micro TP `${order.tp1:,.2f}`\n"
                                f"Lots `{order.lots}` | Lev `{order.leverage:.0f}x`\n"
                                f"_{order.setup_reason}_"
                            ) if is_scalp_order else (
                                f"⚡ **NEW MULTI-AGENT SETUP** ({open_n + 1}/{max_open})\n"
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
                            for ev in engine.update_market_candle(symbol, candle_dict):
                                if config.TELEGRAM_CHAT_ID:
                                    try:
                                        await app.bot.send_message(
                                            chat_id=config.TELEGRAM_CHAT_ID, text=ev["message"], parse_mode="Markdown"
                                        )
                                    except Exception:
                                        pass
                except Exception as sym_err:
                    logger.error("Monitoring %s error: %s", symbol, sym_err)

            if any_data:
                monitoring_health["last_success_at"] = now_iso()
                monitoring_health["last_error"] = None
                monitoring_health["consecutive_errors"] = 0
            else:
                monitoring_health["last_error"] = "No market data returned"
                monitoring_health["consecutive_errors"] += 1
        except Exception as e:
            monitoring_health["last_error"] = str(e)
            monitoring_health["consecutive_errors"] += 1
            logger.error("Monitoring worker error: %s", e)

        write_runtime_status()
        is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
        if is_scalping:
            sleep_interval = 1 if (engine.active_positions or engine.pending_setups) else 2
        else:
            sleep_interval = (
                config.FAST_POLL_INTERVAL
                if (engine.active_positions or engine.pending_setups)
                else config.POLL_INTERVAL
            )
        await asyncio.sleep(sleep_interval)
