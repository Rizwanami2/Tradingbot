#!/usr/bin/env python3
"""Multi-symbol autotrade monitoring worker — Gold + BTC only. Combined with AI Advanced Suite."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

logger = logging.getLogger("autotrade_worker")


async def send_telegram_alert(app: Any, config: Any, text: str) -> bool:
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


_last_ai_veto: Dict[str, Tuple[str, float]] = {}


def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):
    """Run AI Advanced Suite; return (allow, sig_updated). Never raises."""
    try:
        from ai_advanced_suite import evaluate_signal_dict, get_ai_suite
        suite = get_ai_suite()
        if not getattr(suite, "enabled", True):
            return True, sig
        if not sig.get("direction") and dname:
            sig["direction"] = dname
        if not sig.get("agreement"):
            sig["agreement"] = agree
        if sig.get("conviction") is None:
            sig["conviction"] = conv_val
        sig.setdefault("symbol", symbol)
        gate = evaluate_signal_dict(sig, profile=str(profile or "scalp"))
        if not gate.allow:
            reasons_str = "; ".join(gate.reasons)
            now_ts = time.time()
            last_r, last_ts = _last_ai_veto.get(symbol, ("", 0.0))
            if reasons_str != last_r or (now_ts - last_ts) >= 60.0:
                logger.info("Autotrade %s: AI suite veto — %s", symbol, reasons_str)
                _last_ai_veto[symbol] = (reasons_str, now_ts)
            return False, sig
        if gate.size_mult < 1.0 and sig.get("lot_mode", "auto") != "manual":
            for k in ("lots", "size"):
                if sig.get(k):
                    sig[k] = max(0.01, round(float(sig[k]) * gate.size_mult, 4))
            try:
                sig["lots"] = suite.apply_lot_ceiling(float(sig.get("lots") or sig.get("size") or 0.01))
            except Exception:
                pass
        sig["ai_scorecard"] = gate.scorecard
        sig["ai_gate_reasons"] = list(gate.reasons)
        return True, sig
    except Exception as e:
        logger.debug("AI suite gate error: %s", e)
        return True, sig


def _ai_on_open(symbol: str, direction: str, meta=None):
    try:
        from ai_advanced_suite import get_ai_suite
        get_ai_suite().on_trade_opened(symbol, direction, meta=meta or {})
    except Exception:
        pass


def _ai_on_close(symbol: str, fpnl: float, ev: dict = None):
    ev = ev or {}
    r_mult = float(ev.get("r_multiple", ev.get("r", 0.0)) or 0.0)
    if r_mult == 0.0 and fpnl != 0.0:
        r_mult = 1.0 if fpnl > 0 else -1.0
    try:
        from ai_advanced_suite import get_ai_suite
        get_ai_suite().on_trade_closed(symbol=symbol, won=fpnl >= 0, r_multiple=r_mult)
    except Exception:
        pass
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        pos = ev.get("order") if ev else None
        agents = getattr(pos, "agents", []) if pos else []
        regime = getattr(pos, "regime", "") if pos else ""
        session = getattr(pos, "session", "") if pos else ""
        setup = f"{getattr(pos, 'direction', '').lower()}|{symbol.lower()}" if pos else f"live|{symbol.lower()}"
        brain.on_trade_closed(
            r_multiple=r_mult,
            won=fpnl >= 0,
            setup=setup,
            regime=regime,
            session=session,
            agents=agents,
            conf=getattr(pos, "conviction", 0.6) if pos else 0.6,
        )
    except Exception:
        pass


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
    is_running_getter: Optional[Callable[[], bool]] = None,
):
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
    max_open = int(getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6)) or 6)
    if getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False):
        max_open = int(getattr(engine, "max_open_trades", max_open) or max_open)
    try:
        engine.max_open_trades = max_open
    except Exception:
        pass

    feeds = {sym: MarketDataFeed(symbol=sym, source=config.DEFAULT_DATA_SOURCE) for sym in watchlist}
    logger.info("Autotrade watchlist=%s max_open=%s (combined AI suite on)", watchlist, max_open)
    write_runtime_status()

    while (is_running_getter() if is_running_getter is not None else True):
        monitoring_health["last_poll_at"] = now_iso()
        any_data = False
        autotrade_active = bool(autotrade_active_getter())

        if hasattr(engine, "update_auto_slots") and getattr(engine, "slots_mode", "auto") == "auto":
            try:
                engine.update_auto_slots()
            except Exception:
                pass

        max_open = int(getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6)) or 6)
        current_profile_name = getattr(config, "CURRENT_PROFILE", "scalp")
        try:
            from strategy_profiles import get_profile
            _active_prof = get_profile(current_profile_name)
            arbiter.MIN_SCORE = _active_prof.min_score
            arbiter.MIN_AGREEMENT = _active_prof.min_agreement
            arbiter.min_rr = getattr(_active_prof, "min_rr", 1.9)
            arbiter.strict_htf = getattr(_active_prof, "strict_htf", False)
            arbiter.allow_asian = getattr(_active_prof, "allow_asian", True)
            arbiter.require_killzone = getattr(_active_prof, "require_killzone", False)
            arbiter.is_scalp = getattr(_active_prof, "is_scalp", False)
            arbiter.fast_market_fill = getattr(_active_prof, "fast_market_fill", False)
            arbiter.scalp_sl_pct = getattr(_active_prof, "scalp_sl_pct", 0.12)
            arbiter.scalp_tp_pct = getattr(_active_prof, "scalp_tp_pct", 0.10)
            arbiter.scalp_tp_usd = getattr(_active_prof, "scalp_tp_usd", 2.50)
            arbiter.scalp_sl_usd = getattr(_active_prof, "scalp_sl_usd", 3.00)
            arbiter.min_effective_rr = 0.85 if getattr(_active_prof, "is_scalp", False) else 1.4
            if hasattr(engine, "scalp_mode"):
                engine.scalp_mode = getattr(_active_prof, "is_scalp", False)
            if hasattr(engine, "fast_market_fill"):
                engine.fast_market_fill = getattr(_active_prof, "fast_market_fill", False)
        except Exception:
            pass

        current_watchlist = resolve_watchlist()
        if set(current_watchlist) != set(watchlist):
            logger.info("Autotrade selection changed: %s", current_watchlist)
            for r in set(watchlist) - set(current_watchlist):
                if r in engine.pending_setups:
                    engine.cancel_setup(r, reason=f"Symbol {r} removed from watchlist")
            watchlist = list(current_watchlist)
        monitoring_health["watchlist"] = list(watchlist)
        cur_auto_mode = getattr(config, "get_autotrade_mode", lambda: getattr(config, "AUTOTRADE_MODE", "signal"))().lower()
        monitoring_health["autotrade_mode"] = cur_auto_mode

        try:
            from session_reporter import check_and_generate_transition_report
            transition = check_and_generate_transition_report(
                open_positions=list(engine.active_positions.values())
            )
            if transition:
                _, session_msg = transition
                await _send_alert(session_msg)
        except Exception:
            pass
        # MT5 Live Position Synchronization & Stale Halt Auto-Heal
        try:
            if hasattr(engine, "reconcile_positions"):
                engine.reconcile_positions()
                if getattr(engine, "execution_halted", False) and not engine.active_positions and not engine.pending_setups:
                    logger.info("[AutotradeWorker] Auto-cleared stale execution_halted state after clean reconciliation.")
                    engine.reset_halt()
        except Exception as _sync_err:
            logger.debug("[Autotrade] Broker position reconcile notice: %s", _sync_err)

        try:
            for symbol in watchlist:
                try:
                    broker = getattr(engine, "broker", None)
                    data = None
                    if broker is not None and hasattr(broker, "get_candles"):
                        try:
                            df1 = broker.get_candles(symbol, timeframe="1m", limit=500)
                            if df1 is not None and not df1.empty and len(df1) >= 3:
                                data = {"1m": df1}
                                for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
                                    try:
                                        data[tf] = df1.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                                    except Exception:
                                        data[tf] = pd.DataFrame()
                                try:
                                    data["daily"] = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                                except Exception:
                                    data["daily"] = pd.DataFrame()
                        except Exception as _b_err:
                            logger.debug("Broker candles error: %s", _b_err)

                    if data is None:
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
                        "_history": df1,
                    }

                    try:
                        from mt5_chart import fetch_mt5_live_quote
                        mt5_q = fetch_mt5_live_quote(symbol, broker=broker)
                        if mt5_q.get("ok") and float(mt5_q.get("mark") or 0.0) > 0.0:
                            mt5_mark = float(mt5_q["mark"])
                            df1.loc[df1.index[-1], "close"] = mt5_mark
                            df1.loc[df1.index[-1], "high"] = max(float(df1.loc[df1.index[-1], "high"]), mt5_mark)
                            df1.loc[df1.index[-1], "low"] = min(float(df1.loc[df1.index[-1], "low"]), mt5_mark)
                            candle_dict["close"] = mt5_mark
                            candle_dict["high"] = max(candle_dict["high"], mt5_mark)
                            candle_dict["low"] = min(candle_dict["low"], mt5_mark)
                    except Exception:
                        pass

                    if not autotrade_active and symbol in engine.pending_setups:
                        engine.cancel_setup(symbol, reason="Autotrade disabled; pending entry cancelled")

                    try:
                        profile_locked = bool(
                            getattr(config, "PROFILE_LOCKED", False)
                            or getattr(arbiter, "profile_locked", False)
                        )
                        if not profile_locked and len(watchlist) == 1:
                            from regime_auto_switcher import RegimeAutoSwitcher
                            if not hasattr(arbiter, "_regime_switcher") or arbiter._regime_switcher is None:
                                arbiter._regime_switcher = RegimeAutoSwitcher(cooldown_seconds=300.0)
                            switch_res = arbiter._regime_switcher.evaluate_and_switch(
                                df1, symbol=symbol, arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config
                            )
                            if switch_res.switched:
                                await _send_alert(
                                    f"REGIME AUTO-SWITCH: {symbol} {switch_res.previous_profile} -> {switch_res.current_profile} ({switch_res.reason})"
                                )
                    except Exception:
                        pass

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
                            _ai_on_close(symbol, fpnl, ev)
                        elif etype == "TRADE_EXECUTED":
                            pos = ev.get("order")
                            eprice = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or candle_dict["close"])
                            limiter.record_entry(symbol, eprice)
                        await _send_alert(ev.get("message", ""))

                    is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                    open_n = len(engine.active_positions) + len(engine.pending_setups)
                    cur_mode = getattr(config, "get_autotrade_mode", lambda: getattr(config, "AUTOTRADE_MODE", "signal"))().lower()
                    if not autotrade_active:
                        can_open = False
                    elif cur_mode == "signal":
                        # In signal-driven mode, autotrade only executes when /signal is called;
                        # background worker manages open positions (TP/SL/trailing) but does not open new trades.
                        can_open = False
                    elif is_scalping:
                        # Deterministic scalp slot management: total capacity & per-currency limits (cooldowns bypassed)
                        can_open, limit_reason = limiter.check_can_enter(symbol=symbol, current_price=candle_dict["close"], engine=engine, max_open=max_open, bypass_cooldowns=True)
                    else:
                        can_open = open_n < max_open and symbol not in engine.active_positions and symbol not in engine.pending_setups
                        if can_open:
                            can_open, limit_reason = limiter.check_can_enter(symbol=symbol, current_price=candle_dict["close"], engine=engine, max_open=max_open)

                    if can_open:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr,
                            symbol=symbol, on_demand=True,
                        )
                        if not sig:
                            logger.info("Autotrade %s: no setup — %s", symbol, why)
                            monitoring_health[f"{symbol}_eval"] = f"Waiting: {why}"
                        else:
                            monitoring_health[f"{symbol}_eval"] = f"Setup: {getattr(sig.get('direction'), 'name', str(sig.get('direction')))}"
                            dname = ""
                            agree = 0
                            conv_val = 0.6
                            try:
                                from autonomous_learner import get_brain
                                brain = get_brain(auto_bootstrap=False)
                                d = sig.get("direction")
                                dname = d.name if hasattr(d, "name") else str(d)
                                agree = len([v for v in (votes or []) if str(getattr(v, "direction", v.get("direction", "") if isinstance(v, dict) else "")).upper() == dname.upper()])
                                if isinstance(sig.get("conviction"), dict):
                                    conv_val = float(sig["conviction"].get("score", 0.6) or 0.6)
                                elif sig.get("conviction") is not None:
                                    try:
                                        conv_val = float(sig["conviction"])
                                    except Exception:
                                        conv_val = 0.6
                                is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                                ok, reason, size_mult = brain.advise_signal(
                                    direction=dname,
                                    score=float(sig.get("score") or 0.0),
                                    rr=float(sig.get("rr") or 0.0),
                                    agreement=agree,
                                    conviction=conv_val,
                                    regime=str(sig.get("regime") or ""),
                                    setup=f"{dname.lower()}|{sig.get('regime') or 'unknown'}",
                                    is_scalp=is_scalp_sig,
                                    profile=current_profile_name,
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

                            # Combined AI Advanced Suite (automatic)
                            ok_ai, sig = _ai_gate_signal(sig, symbol, current_profile_name, dname=dname, agree=agree, conv_val=conv_val)
                            if not ok_ai:
                                continue

                            d = sig.get("direction")
                            dname = d.name if hasattr(d, "name") else str(d)
                            is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)

                            # Multi-Timeframe Trend & Volatility Confluence Matrix check
                            try:
                                from mtf_matrix import check_mtf_confluence
                                mtf_ok, mtf_reason, mtf_mult = check_mtf_confluence(symbol, dname, is_scalp=is_scalp_sig)
                                if not mtf_ok:
                                    logger.info("Autotrade %s: vetoed by MTF Confluence Matrix — %s", symbol, mtf_reason)
                                    continue
                                if mtf_mult > 1.0 and "score" in sig:
                                    sig["score"] = float(sig["score"]) * mtf_mult
                            except Exception as _mtf_err:
                                logger.debug("Autotrade MTF check note: %s", _mtf_err)

                            # MT5 live chart micro-trend bias check for scalps (reject counter-trend scalps)
                            if is_scalp_sig:
                                try:
                                    from mt5_chart import get_mt5_live_chart
                                    _cdata = get_mt5_live_chart(symbol, timeframe="1m")
                                    if _cdata and _cdata.get("ok", True):
                                        _tbias = str(_cdata.get("technicals", {}).get("trend_bias", "")).upper()
                                        if "BEARISH" in _tbias and ("BUY" in dname.upper() or "LONG" in dname.upper()):
                                            logger.info("Autotrade %s: scalp vetoed by opposing MT5 live chart trend (%s)", symbol, _tbias)
                                            continue
                                        elif "BULLISH" in _tbias and ("SELL" in dname.upper() or "SHORT" in dname.upper()):
                                            logger.info("Autotrade %s: scalp vetoed by opposing MT5 live chart trend (%s)", symbol, _tbias)
                                            continue
                                except Exception as _mt5_err:
                                    logger.debug("Autotrade scalp MT5 chart check note: %s", _mt5_err)

                            # Signal Quality Filter (candle shape, wicks, volume profile)
                            try:
                                from signal_quality import evaluate_signal_quality
                                sq_res = evaluate_signal_quality(
                                    df1,
                                    direction=dname,
                                    spread=0.0,
                                    expected_rr=float(sig.get("rr", 1.0 if is_scalp_sig else 2.0) or (1.0 if is_scalp_sig else 2.0)),
                                    is_scalp=is_scalp_sig,
                                )
                                if not sq_res.allowed:
                                    logger.info("Autotrade %s: vetoed by Signal Quality — %s", symbol, ", ".join(sq_res.reasons))
                                    continue
                            except Exception as _sq_err:
                                logger.debug("Autotrade signal quality note: %s", _sq_err)

                            try:
                                order = engine.register_setup(sig, reason=sig.get("reason", ""))
                                order.agents = list(sig.get("agents") or [])
                                order.regime = str(sig.get("regime") or "")
                                order.score = float(sig.get("score") or 0.0)
                                order.conviction = float(sig.get("conviction") or 0.6)
                                _ai_on_open(symbol, str(getattr(order, "direction", "") or dname), meta={"scorecard": sig.get("ai_scorecard")})
                                limiter.record_entry(symbol, order.entry_level)
                            except RuntimeError as reg_err:
                                logger.info("Autotrade %s: register blocked — %s", symbol, reg_err)
                                continue

                            is_scalp_order = getattr(engine, "scalp_mode", False) or getattr(order, "is_scalp", False)
                            lot_tag = " (Manual)" if getattr(order, "lot_mode", "auto") == "manual" else " (Auto)"
                            sc = sig.get("ai_scorecard")
                            sc_tag = f" | AI {sc:.0f}" if sc is not None else ""
                            alert = (
                                f"NEW SCALP SETUP ({open_n + 1}/{max_open})\n"
                                f"{symbol} {order.direction} @ {order.entry_level:,.2f}\n"
                                f"SL {order.stop_loss:,.2f} | TP {order.tp1:,.2f}\n"
                                f"Lots {order.lots}{lot_tag} | Lev {order.leverage:.0f}x{sc_tag}\n"
                                f"{order.setup_reason}"
                            ) if is_scalp_order else (
                                f"NEW MULTI-AGENT SETUP ({open_n + 1}/{max_open})\n"
                                f"{symbol} {order.direction}\n"
                                f"Entry {order.entry_level:,.2f} | SL {order.stop_loss:,.2f}\n"
                                f"Lots {order.lots}{lot_tag} | Lev {order.leverage:.0f}x{sc_tag}\n"
                                f"{order.setup_reason}"
                            )
                            try:
                                from mt5_chart import get_mt5_live_chart
                                _mt5_c = get_mt5_live_chart(symbol, timeframe="1m")
                                if _mt5_c and _mt5_c.get("ok", True):
                                    _t = _mt5_c.get("technicals", {})
                                    alert += (
                                        f"\n📊 MT5 Chart: {_t.get('trend_bias', 'NEUTRAL')} | "
                                        f"Pivot ${_t.get('pivot', 0):,.2f} (R1 ${_t.get('r1', 0):,.2f} / S1 ${_t.get('s1', 0):,.2f}) | "
                                        f"RSI {_t.get('rsi', 50):.1f}"
                                    )
                            except Exception:
                                pass
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
                                    _ai_on_close(symbol, fpnl, ev)
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
