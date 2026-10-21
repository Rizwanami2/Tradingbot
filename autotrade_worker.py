#!/usr/bin/env python3
"""Multi-symbol autotrade monitoring worker — Gold + BTC only. Combined with AI Advanced Suite."""
from __future__ import annotations

import asyncio
import os
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


def _should_notify_veto(symbol: str, reason_key: str, cooldown_sec: float = 120.0) -> bool:
    now = time.time()
    prev = _last_ai_veto.get(symbol)
    if prev and prev[0] == reason_key and (now - prev[1]) < cooldown_sec:
        return False
    _last_ai_veto[symbol] = (reason_key, now)
    return True


def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):
    """AI Advanced Suite hard gates. Returns (allowed, sig, reasons)."""
    try:
        from ai_advanced_suite import evaluate_signal_dict, get_ai_suite
        suite = get_ai_suite()
        if not getattr(suite, "enabled", True):
            return True, sig, []
        gate = evaluate_signal_dict(sig, profile=str(profile or "scalp"))
        reasons = list(getattr(gate, "reasons", None) or [])
        if not getattr(gate, "allowed", True):
            return False, sig, reasons
        try:
            sm = float(getattr(gate, "size_mult", 1.0) or 1.0)
            if sm > 0 and sm != 1.0:
                sig = dict(sig)
                sig["ai_size_mult"] = sm
        except Exception:
            pass
        if hasattr(gate, "scorecard"):
            sig = dict(sig)
            sig["ai_scorecard"] = getattr(gate, "scorecard", None)
        return True, sig, reasons
    except Exception as e:
        logger.debug("AI suite gate error (fail-open): %s", e)
        return True, sig, []


def _ai_on_open(symbol: str, direction: str, meta=None):
    try:
        from ai_advanced_suite import get_ai_suite
        get_ai_suite().on_trade_opened(symbol, direction, meta=meta or {})
    except Exception:
        pass


def _ai_on_close(symbol: str, fpnl: float, ev: dict = None) -> str:
    """Auto-learn on every close + live analysis text."""
    ev = ev or {}
    r_mult = float(ev.get("r_multiple", ev.get("r", 0.0)) or 0.0)
    if r_mult == 0.0 and fpnl != 0.0:
        r_mult = 1.0 if fpnl > 0 else -1.0
    won = fpnl >= 0
    pos = ev.get("order") if ev else None
    agents = list(getattr(pos, "agents", []) or []) if pos else []
    regime = str(getattr(pos, "regime", "") or "") if pos else ""
    session = str(getattr(pos, "session", "") or "") if pos else ""
    direction = str(getattr(pos, "direction", "") or "") if pos else ""
    setup = f"{direction.lower()}|{symbol.lower()}" if direction else f"live|{symbol.lower()}"
    conf = float(getattr(pos, "conviction", 0.6) or 0.6) if pos else 0.6
    try:
        from ai_advanced_suite import get_ai_suite
        get_ai_suite().on_trade_closed(symbol=symbol, won=won, r_multiple=r_mult)
    except Exception as e:
        logger.debug("suite close: %s", e)
    live_n = 0
    banned = ""
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        brain.on_trade_closed(
            r_multiple=r_mult, won=won, setup=setup, regime=regime,
            session=session, agents=agents, conf=conf,
        )
        live_n = int(getattr(getattr(brain, "knowledge", None), "total_live_trades", 0) or 0)
        if setup in getattr(brain.knowledge, "banned_setups", {}):
            banned = str(brain.knowledge.banned_setups.get(setup, ""))
    except Exception as e:
        logger.debug("brain close: %s", e)
    try:
        import csv
        from pathlib import Path as _P
        from datetime import datetime, timezone
        jpath = _P("data") / "live_trade_journal.csv"
        jpath.parent.mkdir(parents=True, exist_ok=True)
        nf = not jpath.exists()
        with jpath.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nf:
                w.writerow(["ts", "symbol", "direction", "pnl", "r", "won", "setup", "regime", "session", "agents"])
            w.writerow([
                datetime.now(timezone.utc).isoformat(), symbol, direction,
                round(fpnl, 4), round(r_mult, 4), int(won), setup, regime, session,
                "|".join(str(a) for a in agents),
            ])
    except Exception as e:
        logger.debug("journal: %s", e)
    reg_disp = regime or "-"
    lines = [
        f"{emoji} Learn+Close {symbol} {direction}",
        f"PnL: {fpnl:+.2f} | R: {r_mult:+.2f} | Live: {live_n}",
        f"Setup: {setup} | Regime: {reg_disp}",
    ]
    if banned:
        lines.append(f"Banned: {banned}")
    lines.append("Learned from this close")
    return chr(10).join(lines)



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
            acc_on = str(getattr(config, "ACCURACY_MODE", "") or os.getenv("BOT_ACCURACY_MODE", "")).lower() in ("true", "1", "yes", "on")
            if getattr(_active_prof, "is_scalp", False):
                arbiter.min_effective_rr = 1.15 if acc_on else max(0.85, float(getattr(_active_prof, "min_rr", 1.0) or 1.0) - 0.15)
            else:
                arbiter.min_effective_rr = 1.4
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
        cur_auto_mode = getattr(config, "get_autotrade_mode", lambda: getattr(config, "AUTOTRADE_MODE", "continuous"))().lower()
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

                    live_spread = 0.0
                    try:
                        from mt5_chart import fetch_mt5_live_quote
                        mt5_q = fetch_mt5_live_quote(symbol, broker=broker)
                        if mt5_q.get("ok") and float(mt5_q.get("mark") or 0.0) > 0.0:
                            mt5_mark = float(mt5_q["mark"])
                            live_spread = float(mt5_q.get("spread", 0.0) or 0.0)
                            df1.loc[df1.index[-1], "close"] = mt5_mark
                            df1.loc[df1.index[-1], "high"] = max(float(df1.loc[df1.index[-1], "high"]), mt5_mark)
                            df1.loc[df1.index[-1], "low"] = min(float(df1.loc[df1.index[-1], "low"]), mt5_mark)
                            candle_dict["close"] = mt5_mark
                            candle_dict["high"] = max(candle_dict["high"], mt5_mark)
                            candle_dict["low"] = min(candle_dict["low"], mt5_mark)
                            candle_dict["spread"] = live_spread
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
                            _analysis = _ai_on_close(symbol, fpnl, ev)
                            if _analysis:
                                await _send_alert(_analysis)
                        elif etype == "TRADE_EXECUTED":
                            pos = ev.get("order")
                            eprice = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or candle_dict["close"])
                            limiter.record_entry(symbol, eprice)
                        await _send_alert(ev.get("message", ""))

                    is_scalping = getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                    open_n = len(engine.active_positions) + len(engine.pending_setups)
                    cur_mode = getattr(config, "get_autotrade_mode", lambda: getattr(config, "AUTOTRADE_MODE", "continuous"))().lower()
                    if not autotrade_active:
                        can_open = False
                        monitoring_health[f"{symbol}_eval"] = "Autotrade paused"
                    elif cur_mode == "signal":
                        # In signal-driven mode, autotrade only executes when /signal is called;
                        # background worker manages open positions (TP/SL/trailing) but does not open new trades.
                        can_open = False
                        monitoring_health[f"{symbol}_eval"] = "Signal-only mode (/signal executes)"
                    elif is_scalping:
                        # Deterministic scalp slot management: capacity & per-currency limits with cooldown protection (auto & manual)
                        can_open, limit_reason = limiter.check_can_enter(symbol=symbol, current_price=candle_dict["close"], engine=engine, max_open=max_open, bypass_cooldowns=False)
                        if not can_open:
                            monitoring_health[f"{symbol}_eval"] = f"Slot limit: {limit_reason}"
                    else:
                        can_open = open_n < max_open and symbol not in engine.active_positions and symbol not in engine.pending_setups
                        if can_open:
                            can_open, limit_reason = limiter.check_can_enter(symbol=symbol, current_price=candle_dict["close"], engine=engine, max_open=max_open)
                        if not can_open:
                            monitoring_health[f"{symbol}_eval"] = f"Capacity limit: {open_n}/{max_open}"

                    if can_open:
                        sig, why, votes = arbiter.decide(
                            df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk_mgr,
                            symbol=symbol, on_demand=True, engine=engine,
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
                                # Brain (Autonomous Learner): optimize regime lot sizing without vetoing consensus signals
                                _ok_brain, _brain_why, size_mult = brain.advise_signal(
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
                                _live_veto = str(os.getenv("BOT_LEARN_LIVE_VETO", "true")).lower() in ("true", "1", "yes", "on")
                                if not _ok_brain and _live_veto:
                                    logger.info("Autotrade %s: LEARN VETO — %s", symbol, _brain_why)
                                    monitoring_health[f"{symbol}_eval"] = f"Learn veto: {_brain_why}"
                                    if _should_notify_veto(symbol, f"learn:{str(_brain_why)[:80]}"):
                                        await _send_alert(f"Learn veto {symbol}: {_brain_why}")
                                    continue

                                # Pure Multi-Agent mode: apply Brain regime sizing multiplier without dropping trades
                                if size_mult > 0.0 and size_mult != 1.0 and sig.get("lot_mode", "auto") != "manual":
                                    if sig.get("lots"):
                                        sig["lots"] = max(0.01, round(float(sig["lots"]) * size_mult, 4))
                                    if sig.get("size"):
                                        sig["size"] = max(0.01, round(float(sig["size"]) * size_mult, 4))
                            except Exception as _b_err:
                                logger.debug("Autotrade brain check error: %s", _b_err)

                            # Pure Multi-Agent mode: secondary AI gating and vetoing removed; consensus signals flow directly
                            d = sig.get("direction")
                            dname = d.name if hasattr(d, "name") else str(d)
                            is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(engine, "scalp_mode", False) or getattr(arbiter, "is_scalp", False)
                            # Ensure fast market execution for autotrade consensus setups
                            sig["fast_market_fill"] = True

                            try:
                                size_mult
                            except NameError:
                                size_mult = 1.0

                            # CombinedBotAgent (FIT skills: session/spread/risk/structure)
                            try:
                                import os as _os_cba
                                if _os_cba.getenv("COMBINED_AGENT_ENABLED", "true").lower() not in ("true", "1", "yes", "on"):
                                    raise RuntimeError("combined agent disabled")
                                from combined_bot_agent import CombinedBotAgent
                                if not hasattr(live_monitoring_worker, "_combined_agent"):
                                    live_monitoring_worker._combined_agent = CombinedBotAgent()
                                _cba = live_monitoring_worker._combined_agent
                                _spread = float(candle_dict.get("spread") or 0.0)
                                try:
                                    if _spread <= 0 and "ask" in candle_dict and "bid" in candle_dict:
                                        _spread = abs(float(candle_dict["ask"]) - float(candle_dict["bid"]))
                                except Exception:
                                    pass
                                _ok_cba, sig, _cba_reasons = _cba.evaluate(
                                    sig,
                                    {
                                        "spread": _spread,
                                        "open_trades": open_n,
                                        "open_for_symbol": 1 if symbol in getattr(engine, "active_positions", {}) else 0,
                                        "htf_bias": str(sig.get("regime") or dname),
                                        "m1_direction": dname,
                                        "setup": str(sig.get("setup") or sig.get("reason") or "pullback"),
                                        "is_live": bool(getattr(config, "BOT_AUTOTRADE_LIVE_OPT_IN", False) or getattr(config, "AUTOTRADE_LIVE_OPT_IN", False)),
                                        "entry": float(candle_dict.get("close") or 0.0),
                                    },
                                )
                                if not _ok_cba:
                                    _why = ", ".join(_cba_reasons) if _cba_reasons else "combined agent"
                                    logger.info("Autotrade %s: COMBINED AGENT VETO — %s", symbol, _why)
                                    monitoring_health[f"{symbol}_eval"] = f"Combined veto: {_why}"
                                    continue
                                if sig.get("combined_agent_size_mult"):
                                    try:
                                        size_mult = float(size_mult) * float(sig["combined_agent_size_mult"])
                                        size_mult = max(0.25, min(2.0, size_mult))
                                    except Exception:
                                        pass
                            except Exception as _cba_err:
                                logger.debug("CombinedBotAgent note: %s", _cba_err)

                            # 1. Strict Single-Direction Rule & Cooldown: Do not buy and sell positions at the same time

                            try:
                                if getattr(config, "SAFETY_REQUIRE_RISK_CAN_TRADE", True):
                                    _rok, _rwhy = risk_mgr.can_trade()
                                    if not _rok:
                                        logger.info("Autotrade %s: RISK VETO — %s", symbol, _rwhy)
                                        monitoring_health[f"{symbol}_eval"] = f"Risk veto: {_rwhy}"
                                        if _should_notify_veto(symbol, f"risk:{_rwhy}"):
                                            await _send_alert(f"Risk veto {symbol}: {_rwhy}")
                                        continue
                            except Exception as _re:
                                logger.debug("risk note: %s", _re)
                            try:
                                _prof = str(getattr(config, "CURRENT_PROFILE", "scalp") or "scalp")
                                _ok_ai, sig, _ai_reasons = _ai_gate_signal(sig, symbol, _prof, dname, agree, conv_val)
                                if not _ok_ai:
                                    _why = ", ".join(_ai_reasons) if _ai_reasons else "AI veto"
                                    logger.info("Autotrade %s: AI VETO — %s", symbol, _why)
                                    monitoring_health[f"{symbol}_eval"] = f"AI veto: {_why}"
                                    if _should_notify_veto(symbol, f"ai:{_why[:80]}"):
                                        await _send_alert(f"AI veto {symbol}: {_why}")
                                    continue
                            except Exception as _ae:
                                logger.debug("ai gate note: %s", _ae)

                            can_enter_dir, dir_reason = limiter.check_can_enter(
                                symbol=symbol,
                                current_price=candle_dict["close"],
                                engine=engine,
                                max_open=max_open,
                                bypass_cooldowns=False,
                                direction=dname,
                            )
                            if not can_enter_dir:
                                logger.info("Autotrade %s: direction veto — %s", symbol, dir_reason)
                                monitoring_health[f"{symbol}_eval"] = f"Direction veto: {dir_reason}"
                                continue

                            # 2. Strict 1m Candle Auto-Trend Following: Reject counter-trend trades
                            try:
                                from auto_trend_1m_strategy import verify_auto_trend_alignment
                                trend_ok, trend_why = verify_auto_trend_alignment(df1, dname)
                                if not trend_ok:
                                    logger.info("Autotrade %s: vetoed by 1m Auto-Trend Filter — %s", symbol, trend_why)
                                    monitoring_health[f"{symbol}_eval"] = f"Auto-Trend veto: {trend_why}"
                                    continue
                            except Exception as _at_err:
                                logger.debug("Autotrade 1m auto-trend note: %s", _at_err)

                            # Multi-Timeframe Trend & Volatility Confluence Matrix check
                            try:
                                from mtf_matrix import check_mtf_confluence
                                mtf_ok, mtf_reason, mtf_mult = check_mtf_confluence(symbol, dname, is_scalp=is_scalp_sig)
                                if not mtf_ok:
                                    score_val = float(sig.get("score") or 0.0)
                                    if agree >= 2 or score_val >= 1.0:
                                        logger.info("Autotrade %s: MTF Confluence warning (%s) overridden by Arbiter consensus (score=%.2f, votes=%d)", symbol, mtf_reason, score_val, agree)
                                    else:
                                        logger.info("Autotrade %s: vetoed by MTF Confluence Matrix — %s", symbol, mtf_reason)
                                        continue
                                if mtf_mult > 1.0 and "score" in sig:
                                    sig["score"] = float(sig["score"]) * mtf_mult
                            except Exception as _mtf_err:
                                logger.debug("Autotrade MTF check note: %s", _mtf_err)

                            # MT5 live chart micro-trend bias check for scalps (strictly reject counter-trend scalps)
                            if is_scalp_sig:
                                try:
                                    from mt5_chart import get_mt5_live_chart
                                    _cdata = get_mt5_live_chart(symbol, timeframe="1m")
                                    if _cdata and _cdata.get("ok", True):
                                        _tbias = str(_cdata.get("technicals", {}).get("trend_bias", "")).upper()
                                        is_opposing = ("BEARISH" in _tbias and ("BUY" in dname.upper() or "LONG" in dname.upper())) or \
                                                      ("BULLISH" in _tbias and ("SELL" in dname.upper() or "SHORT" in dname.upper()))
                                        if is_opposing:
                                            logger.info("Autotrade %s: scalp vetoed by opposing MT5 live chart trend (%s)", symbol, _tbias)
                                            monitoring_health[f"{symbol}_eval"] = f"Opposing MT5 trend: {_tbias}"
                                            continue
                                except Exception as _mt5_err:
                                    logger.debug("Autotrade scalp MT5 chart check note: %s", _mt5_err)

                            # Signal Quality Filter (candle shape, wicks, volume profile)
                            try:
                                from signal_quality import evaluate_signal_quality
                                sq_res = evaluate_signal_quality(
                                    df1,
                                    direction=dname,
                                    spread=live_spread,
                                    expected_rr=float(sig.get("rr", 1.0 if is_scalp_sig else 2.0) or (1.0 if is_scalp_sig else 2.0)),
                                    is_scalp=is_scalp_sig,
                                )
                                if not sq_res.allowed:
                                    score_val = float(sig.get("score") or 0.0)
                                    if agree >= 2 or score_val >= 1.0:
                                        logger.info("Autotrade %s: Signal Quality warning (%s) overridden by Arbiter consensus (score=%.2f, votes=%d)", symbol, ", ".join(sq_res.reasons), score_val, agree)
                                    else:
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

                            # Immediate broker market fill at live spread / market tick before any outbound network calls
                            exec_events = engine.update_market_candle(symbol, candle_dict)
                            for ev in exec_events:
                                logger.info("Execution Event: %s - %s", ev["event"], ev.get("message"))
                                etype = ev.get("event")
                                if etype in ("TRADE_CLOSED", "SCALP_SL", "SCALP_TRAIL_HIT", "STOP_LOSS_HIT"):
                                    fpnl = float(ev.get("final_pnl", ev.get("realized_pnl", 0.0)) or 0.0)
                                    if fpnl < 0:
                                        limiter.record_loss(symbol, abs(fpnl))
                                    else:
                                        limiter.record_win(symbol)
                                    _analysis = _ai_on_close(symbol, fpnl, ev)
                                    if _analysis:
                                        await _send_alert(_analysis)
                                elif etype == "TRADE_EXECUTED":
                                    pos = ev.get("order")
                                    eprice = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or candle_dict["close"])
                                    limiter.record_entry(symbol, eprice)
                                await _send_alert(ev.get("message", ""))

                            is_scalp_order = getattr(engine, "scalp_mode", False) or getattr(order, "is_scalp", False)
                            lot_tag = " (Manual)" if getattr(order, "lot_mode", "auto") == "manual" else " (Auto)"
                            sc = sig.get("ai_scorecard")
                            sc_tag = f" | AI {sc:.0f}" if sc is not None else ""
                            disp_entry = float(getattr(order, "executed_price", 0.0) or getattr(order, "entry_level", 0.0))
                            alert = (
                                f"NEW SCALP SETUP ({open_n + 1}/{max_open})\n"
                                f"{symbol} {order.direction} @ {disp_entry:,.2f}\n"
                                f"SL {order.stop_loss:,.2f} | TP {order.tp1:,.2f}\n"
                                f"Lots {order.lots}{lot_tag} | Lev {order.leverage:.0f}x{sc_tag}\n"
                                f"{order.setup_reason}"
                            ) if is_scalp_order else (
                                f"NEW MULTI-AGENT SETUP ({open_n + 1}/{max_open})\n"
                                f"{symbol} {order.direction}\n"
                                f"Entry {disp_entry:,.2f} | SL {order.stop_loss:,.2f}\n"
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
                                    "entry": disp_entry,
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
