#!/usr/bin/env python3
"""Multi-symbol autotrade monitoring worker — Gold + BTC only."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List

import pandas as pd

logger = logging.getLogger("autotrade_worker")


async def send_telegram_alert(app: Any, config: Any, text: str) -> bool:
    """Send Telegram alert with automatic plain text fallback on Markdown parse errors."""
    if not getattr(config, "TELEGRAM_CHAT_ID", None) or not text or not getattr(app, "bot", None):
        return False
    try:
        await app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")
        return True
    except Exception:
        try:
            clean = text.replace("*", "").replace("`", "").replace("_", "")
            await app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=clean)
            return True
        except Exception:
            return False


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
    async def _send_alert(text: str) -> None:
        await send_telegram_alert(app, config, text)
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

    _ALLOWED = {
        "BTCUSDT", "BTCUSD", "BTC", "XAUUSD", "XAUTUSD", "XAU", "GOLD", "XAUT", "PAXGUSDT", "PAXG",
    }

    def resolve_watchlist() -> List[str]:
        raw_syms = list(getattr(config, "AUTOTRADE_SYMBOLS", None) or ["BTCUSDT", "XAUUSD"])
        res: List[str] = []
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
            if n not in res:
                res.append(n)
        return res or ["BTCUSDT", "XAUUSD"]

    watchlist: List[str] = resolve_watchlist()

    max_open = int(getattr(config, "MAX_OPEN_TRADES", 2) or 2)
    if getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False):
        max_open = int(getattr(engine, "max_open_trades", max_open) or max_open)
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

        # Dynamically refresh watchlist selection from config / runtime state
        current_watchlist = resolve_watchlist()
        if set(current_watchlist) != set(watchlist):
            logger.info("Autotrade active selection changed: %s (previously %s)", current_watchlist, watchlist)
            removed = set(watchlist) - set(current_watchlist)
            for r in removed:
                if r in engine.pending_setups:
                    engine.cancel_setup(r, reason=f"Symbol {r} removed from active autotrade selection")
            watchlist = list(current_watchlist)
        monitoring_health["watchlist"] = list(watchlist)

        # Automated End-of-Session Performance Report trigger
        try:
            from session_reporter import check_and_generate_transition_report
            transition = check_and_generate_transition_report(
                open_positions=list(engine.active_positions.values())
            )
            if transition:
                _, session_msg = transition
                await _send_alert(session_msg)
        except Exception as _sess_err:
            logger.debug("Session report check error: %s", _sess_err)

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

                    from anti_spam import get_anti_spam_limiter
                    limiter = get_anti_spam_limiter()

                    events = engine.update_market_candle(symbol, candle_dict)
                    for ev in events:
                        logger.info("Execution Event: %s - %s", ev["event"], ev.get("message"))
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

                        await _send_alert(ev.get("message", ""))

                    is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                    open_n = len(engine.active_positions) + len(engine.pending_setups)
                    if not autotrade_active:
                        can_open = False
                    elif is_scalping:
                        can_open, spam_reason = limiter.check_can_enter(
                            symbol=symbol,
                            current_price=candle_dict["close"],
                            engine=engine,
                            max_open=max_open,
                        )
                        if not can_open:
                            logger.debug("Autotrade %s: scalp entry blocked by anti-spam — %s", symbol, spam_reason)
                    else:
                        can_open = (
                            open_n < max_open
                            and symbol not in engine.active_positions
                            and symbol not in engine.pending_setups
                        )
                        if can_open:
                            can_open, spam_reason = limiter.check_can_enter(
                                symbol=symbol,
                                current_price=candle_dict["close"],
                                engine=engine,
                                max_open=max_open,
                            )
                            if not can_open:
                                logger.debug("Autotrade %s: entry blocked by anti-spam — %s", symbol, spam_reason)
                    if can_open:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr,
                            symbol=symbol, on_demand=True,
                        )
                        if not sig:
                            logger.info("Autotrade %s: no setup — %s", symbol, why)
                        else:
                            try:
                                from autonomous_learner import get_brain
                                brain = get_brain(auto_bootstrap=False)
                                d = sig.get("direction")
                                dname = d.name if hasattr(d, "name") else str(d)
                                agree = len([v for v in (votes or []) if str(getattr(v, "direction", v.get("direction", "") if isinstance(v, dict) else "")).upper() == dname.upper()])
                                conv_val = 0.6
                                if isinstance(sig.get("conviction"), dict):
                                    conv_val = float(sig["conviction"].get("score", 0.6) or 0.6)
                                elif sig.get("conviction") is not None:
                                    try:
                                        conv_val = float(sig["conviction"])
                                    except Exception:
                                        conv_val = 0.6
                                is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(engine, "scalp_mode", False)
                                ok, reason, size_mult = brain.advise_signal(
                                    direction=dname,
                                    score=float(sig.get("score") or 0.0),
                                    rr=float(sig.get("rr") or 0.0),
                                    agreement=agree,
                                    conviction=conv_val,
                                    regime=str(sig.get("regime") or ""),
                                    setup=f"{dname.lower()}|{sig.get('regime') or 'unknown'}",
                                    is_scalp=is_scalp_sig,
                                )
                                if not ok:
                                    logger.info("Autotrade %s: vetoed by Autonomous Learner — %s", symbol, reason)
                                    continue
                                if size_mult < 1.0 and sig.get("lot_mode", "auto") != "manual":
                                    if sig.get("lots"):
                                        sig["lots"] = max(0.01, round(float(sig["lots"]) * size_mult, 4))
                                    if sig.get("size"):
                                        sig["size"] = max(0.01, round(float(sig["size"]) * size_mult, 4))
                            except Exception as _b_err:
                                logger.debug("Autotrade brain check error: %s", _b_err)

                            try:
                                order = engine.register_setup(sig, reason=sig.get("reason", ""))
                                limiter.record_entry(symbol, order.entry_level)
                            except RuntimeError as reg_err:
                                logger.info("Autotrade %s: register blocked — %s", symbol, reg_err)
                                continue
                            is_scalp_order = getattr(engine, "scalp_mode", False) or getattr(order, "is_scalp", False)
                            lot_tag = " (Manual)" if getattr(order, "lot_mode", "auto") == "manual" else " (Auto)"
                            alert = (
                                f"⚡ **NEW SCALP SETUP** ({open_n + 1}/{max_open})\n"
                                f"`{symbol}` **{order.direction}** @ `${order.entry_level:,.2f}`\n"
                                f"Micro SL `${order.stop_loss:,.2f}` | Micro TP `${order.tp1:,.2f}`\n"
                                f"Lots `{order.lots}`{lot_tag} | Lev `{order.leverage:.0f}x`\n"
                                f"_{order.setup_reason}_"
                            ) if is_scalp_order else (
                                f"⚡ **NEW MULTI-AGENT SETUP** ({open_n + 1}/{max_open})\n"
                                f"`{symbol}` **{order.direction}**\n"
                                f"Entry `${order.entry_level:,.2f}` | SL `${order.stop_loss:,.2f}`\n"
                                f"Lots `{order.lots}`{lot_tag} | Lev `{order.leverage:.0f}x`\n"
                                f"_{order.setup_reason}_"
                            )
                            try:
                                from gemini_client import GeminiClient
                                _gem = GeminiClient()
                                rationale = _gem.generate_trade_rationale({
                                    "symbol": symbol,
                                    "direction": order.direction,
                                    "entry": order.entry_level,
                                    "stop_loss": order.stop_loss,
                                    "take_profit": order.tp1,
                                    "lots": order.lots,
                                    "reason": order.setup_reason,
                                    "regime": str(sig.get("regime") or "Trending"),
                                    "is_scalp": is_scalp_order,
                                })
                                if rationale:
                                    alert += f"\n\n{rationale}"
                            except Exception:
                                pass
                            await _send_alert(alert)
                            for ev in engine.update_market_candle(symbol, candle_dict):
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
                                await _send_alert(ev.get("message", ""))
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
