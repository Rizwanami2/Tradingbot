#!/usr/bin/env python3
"""Reliable signal + paper autotrade pipeline (leverage, entry plan, micro size)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    aliases = {
        "BTC": "BTCUSDT", "BITCOIN": "BTCUSDT", "BTCUSD": "BTCUSDT", "XBT": "BTCUSDT",
        "GOLD": "XAUUSD", "XAU": "XAUUSD", "XAUT": "XAUUSD", "XAUTUSD": "XAUUSD",
        "PAXG": "XAUUSD", "PAXGUSDT": "XAUUSD",
    }
    return aliases.get(s, s)


def run_signal(
    symbol: str,
    *,
    equity: float = 1000.0,
    leverage: float = 20.0,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    import config
    from multi_agent_trading_bot import evaluate_symbol, Dir
    from risk_manager import RiskManager

    sym = _norm_symbol(symbol)
    if hasattr(config, "is_trading_allowed") and not config.is_trading_allowed(sym):
        return {
            "symbol": sym,
            "price": 0.0,
            "status": f"Trading disabled for {sym}. Only Gold (XAUUSD) and Bitcoin (BTCUSDT) are permitted.",
            "signal": None,
            "votes": [],
            "error": f"Trading {sym} is disabled. Only Gold and BTC are permitted.",
        }
    src = (source or getattr(config, "DEFAULT_DATA_SOURCE", "ccxt")).lower()
    eq = float(equity if equity is not None else getattr(config, "INITIAL_EQUITY", 1000.0))
    lev = float(leverage if leverage is not None else getattr(config, "DEFAULT_LEVERAGE", 20.0))

    risk = RiskManager(equity=eq, symbol=sym)
    try:
        risk.set_leverage(lev)
    except Exception:
        pass

    try:
        try:
            res = evaluate_symbol(
                symbol=sym, source=src, leverage=risk.get_leverage(),
                on_demand=True, equity=eq,
            )
        except TypeError:
            res = evaluate_symbol(
                symbol=sym, source=src, leverage=risk.get_leverage(), on_demand=True,
            )
    except Exception as e:
        return {
            "symbol": sym,
            "price": 0.0,
            "status": f"Eval error: {type(e).__name__}: {e}",
            "signal": None,
            "votes": [],
            "error": str(e),
        }

    sig = res.get("signal")
    if sig:
        try:
            from strategy_profiles import get_current_profile
            cur_prof = get_current_profile()
            is_scalp_prof = bool(getattr(cur_prof, "is_scalp", False))
            sig["is_scalp"] = is_scalp_prof
            sig["fast_market_fill"] = is_scalp_prof
        except Exception:
            pass
        try:
            if not sig.get("leverage"):
                sig["leverage"] = risk.get_leverage()
            lots = float(sig.get("lots") or 0)
            size = float(sig.get("size") or 0)
            if lots <= 0:
                plan = risk.position_size(
                    entry=float(sig["entry"]),
                    stop=float(sig["stop"]),
                    equity=eq,
                    symbol=sym,
                )
                if plan.lots > 0:
                    sig["lots"] = plan.lots
                    sig["size"] = max(plan.size, plan.lots)
                    sig["margin_required"] = plan.margin_required
                    sig["risk_amount"] = plan.risk_amount
                    sig["leverage"] = plan.leverage or lev
                    sig["lot_mode"] = getattr(plan, "lot_mode", "auto")
                    lots, size = float(sig["lots"]), float(sig["size"])
            if size <= 0 and lots > 0:
                sig["size"] = lots
            d = sig.get("direction")
            if isinstance(d, str):
                u = d.upper()
                sig["direction"] = Dir.LONG if "LONG" in u else (Dir.SHORT if "SHORT" in u else Dir.FLAT)
        except Exception as e:
            res["status"] = f"{res.get('status','')} | size-fix: {e}"
            res["signal"] = None
            sig = None

        if sig:
            try:
                entry = float(sig["entry"])
                stop = float(sig["stop"])
                tp1 = float(sig["tp1"])
                tp2 = float(sig["tp2"])
                dname = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])
                risk_dist = abs(entry - stop) or entry * 0.003
                if "LONG" in dname.upper():
                    if not (stop < entry):
                        sig["stop"] = entry - risk_dist
                    if tp1 <= entry:
                        sig["tp1"] = entry + risk_dist * 1.5
                        tp1 = sig["tp1"]
                    if tp2 < tp1:
                        sig["tp2"] = tp1 + risk_dist * 0.5
                elif "SHORT" in dname.upper():
                    if not (stop > entry):
                        sig["stop"] = entry + risk_dist
                    if tp1 >= entry:
                        sig["tp1"] = entry - risk_dist * 1.5
                        tp1 = sig["tp1"]
                    if tp2 > tp1:
                        sig["tp2"] = tp1 - risk_dist * 0.5
                
                is_scalp_sig = bool(sig.get("is_scalp"))
                target_tp = tp1 if is_scalp_sig else (tp2 if tp2 else tp1)
                computed_rr = round(abs(target_tp - entry) / risk_dist, 2) if risk_dist > 0 else 0.0
                if not is_scalp_sig:
                    sig["rr"] = max(float(sig.get("rr") or 0.0), computed_rr)
                else:
                    sig["rr"] = float(sig.get("rr") or computed_rr)
            except Exception:
                pass

    # Best entry-level plan from all data sources
    try:
        from entry_level_planner import plan_best_entry, attach_plan_to_signal
        dname = "FLAT"
        sig = res.get("signal")
        if sig is not None:
            d = sig.get("direction")
            dname = d.name if hasattr(d, "name") else str(d)
        else:
            longs = shorts = 0
            for v in res.get("votes") or []:
                dd = str(v.get("direction", "")).upper()
                if dd == "LONG":
                    longs += 1
                elif dd == "SHORT":
                    shorts += 1
            if longs > shorts and longs >= 2:
                dname = "LONG"
            elif shorts > longs and shorts >= 2:
                dname = "SHORT"
        df = None
        try:
            from multi_agent_trading_bot import MarketDataFeed
            feed = MarketDataFeed(symbol=sym, source=src)
            data = feed.multi_tf(limit_1m=500)
            if data and "1m" in data:
                df = data["1m"]
        except Exception:
            pass
        mark = float(res.get("price") or 0) or None
        stop_hint = float(sig["stop"]) if sig and sig.get("stop") else None
        plan = plan_best_entry(sym, df, direction=dname, mark=mark, stop_hint=stop_hint)
        res["entry_plan"] = plan.to_dict()
        if sig is not None:
            res["signal"] = attach_plan_to_signal(sig, plan)
    except Exception as _ep_err:
        res["entry_plan_error"] = str(_ep_err)

    try:
        from historical_2y import analyze_2y
        h2 = analyze_2y(sym)
        res["history_2y"] = h2.to_dict()
        if res.get("signal") and h2.bars >= 60:
            d = res["signal"].get("direction")
            dname = d.name if hasattr(d, "name") else str(d)
            if "LONG" in dname.upper() and h2.bias_score < -0.35:
                res["status"] = str(res.get("status", "")) + " | 2Y bias caution (bearish structure)"
            elif "SHORT" in dname.upper() and h2.bias_score > 0.35:
                res["status"] = str(res.get("status", "")) + " | 2Y bias caution (bullish structure)"
    except Exception as _h2_err:
        res["history_2y_error"] = str(_h2_err)

    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        sig = res.get("signal")
        if sig:
            d = sig.get("direction")
            dname = d.name if hasattr(d, "name") else str(d)
            regime = str(sig.get("regime") or "")
            score = float(sig.get("score") or 0.0)
            rr = float(sig.get("rr") or 0.0)
            votes = res.get("votes") or []
            agree = len([v for v in votes if str(v.get("direction", "")).upper() == dname.upper()])
            conv_val = 0.6
            if isinstance(sig.get("conviction"), dict):
                conv_val = float(sig["conviction"].get("score", 0.6) or 0.6)
            elif sig.get("conviction") is not None:
                try:
                    conv_val = float(sig["conviction"])
                except Exception:
                    conv_val = 0.6
            setup_tag = f"{dname.lower()}|{regime or 'unknown'}"
            from strategy_profiles import get_current_profile
            cur_prof = get_current_profile()
            prof_name = getattr(cur_prof, "name", "scalp")
            is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(cur_prof, "is_scalp", False)
            sig["is_scalp"] = is_scalp_sig
            ok, reason, size_mult = brain.advise_signal(
                direction=dname,
                score=score,
                rr=rr,
                agreement=agree,
                conviction=conv_val,
                regime=regime,
                setup=setup_tag,
                is_scalp=is_scalp_sig,
                profile=prof_name,
            )
            # Pure Multi-Agent mode: consensus signals are approved; Brain optimizes regime sizing
            res["brain_allowed"] = True
            res["brain_reason"] = reason
            res["brain_size_mult"] = size_mult
            sig["vetoed"] = False
            if size_mult > 0.0 and size_mult != 1.0:
                cur_lots = float(sig.get("lots") or 0.0)
                cur_size = float(sig.get("size") or 0.0)
                if cur_lots > 0:
                    sig["lots"] = max(0.01, round(cur_lots * size_mult, 4))
                if cur_size > 0:
                    sig["size"] = max(0.01, round(cur_size * size_mult, 4))
                res["status"] = f"{res.get('status', '')} | 🧠 Sized {size_mult:.0%} by Brain"
    except Exception:
        pass

    if not res.get("mt5_chart"):
        try:
            from mt5_chart import get_mt5_live_chart
            ch_data = get_mt5_live_chart(sym, timeframe="1m")
            if ch_data and ch_data.get("ok", True):
                res["mt5_chart"] = ch_data
        except Exception:
            pass

    res["symbol"] = sym
    res["equity"] = eq
    res["leverage"] = risk.get_leverage()
    return res


def format_signal_message(res: Dict[str, Any], show_agent_votes: bool = False) -> str:
    sym = res.get("symbol", "?")
    price_val = float(res.get("price") or 0)
    votes = res.get("votes") or []
    sig = res.get("signal")
    status = res.get("status", "No consensus")
    lev = res.get("leverage", 20)

    vote_lines = []
    for v in votes:
        d = str(v.get("direction", "FLAT")).upper()
        icon = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        reason = str(v.get("reason", ""))[:80].replace("`", "'")
        vote_lines.append(f"{icon} **{v.get('agent','?')}:** {d} ({reason})")

    if sig:
        dname = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])
        dir_icon = "🟢 LONG" if "LONG" in dname.upper() else "🔴 SHORT"
        try:
            entry = float(sig["entry"]); stop = float(sig["stop"])
            tp1 = float(sig["tp1"]); tp2 = float(sig["tp2"])
            lots = float(sig.get("lots") or 0)
            margin = float(sig.get("margin_required") or 0)
            rr = float(sig.get("rr") or 0)
            slev = float(sig.get("leverage") or lev)
        except Exception:
            entry = stop = tp1 = tp2 = lots = margin = rr = slev = 0.0
        
        veto_banner = ""
        if sig.get("vetoed"):
            clean_vr = str(sig.get("veto_reason", "Brain Veto")).replace("`", "'")
            veto_banner = f"⚠️ *VETOED BY AUTONOMOUS LEARNER:* `{clean_vr}`\n"

        is_scalp_msg = bool(sig.get("is_scalp"))
        if is_scalp_msg:
            sig_box = (
                f"\n{veto_banner}"
                f"⚡ **TRADE SETUP (SCALP — CURRENT MARKET | {dir_icon}):**\n"
                f"• **Entry:** `${entry:,.2f}` (Market Order)\n"
                f"• **Stop Loss:** `${stop:,.2f}`\n"
                f"• **Target 1:** `${tp1:,.2f}` | **Target 2:** `${tp2:,.2f}`\n"
                f"• **Lots:** `{lots}` | **Lev:** `{slev:.0f}x`\n"
                f"• **Margin:** `${margin:,.2f}` | **R:R:** `1:{rr:.1f}`\n"
                f"• **Equity:** `${float(res.get('equity') or 0):,.2f}` (paper)\n"
            )
        else:
            dist_pct = ((entry - price_val) / price_val * 100.0) if price_val > 0 else 0.0
            dist_str = f" (Pullback Distance: `{dist_pct:+.2f}%`)" if abs(dist_pct) > 0.01 else ""
            sig_box = (
                f"\n{veto_banner}"
                f"🎯 **TRADE SETUP (BALANCED — PERFECT ENTRY LEVEL | {dir_icon}):**\n"
                f"• **Perfect Entry:** `${entry:,.2f}` (Pending Limit Setup)\n"
                f"• **Current Market:** `${price_val:,.2f}`{dist_str}\n"
                f"• **Stop Loss:** `${stop:,.2f}`\n"
                f"• **Target 1:** `${tp1:,.2f}` | **Target 2:** `${tp2:,.2f}`\n"
                f"• **Lots:** `{lots}` | **Lev:** `{slev:.0f}x`\n"
                f"• **Margin:** `${margin:,.2f}` | **R:R:** `1:{rr:.1f}`\n"
                f"• **Equity:** `${float(res.get('equity') or 0):,.2f}` (paper)\n"
            )

    else:
        sig_box = "\n🛡️ **Stay Flat.** Waiting for confluence.\n"

    plan_txt = ""
    try:
        from entry_level_planner import EntryPlan, format_entry_plan
        ep = res.get("entry_plan")
        if ep:
            plan = EntryPlan(
                direction=str(ep.get("direction", "FLAT")),
                mark=float(ep.get("mark") or 0),
                best_entry=float(ep.get("best_entry") or 0),
                entry_low=float(ep.get("entry_low") or 0),
                entry_high=float(ep.get("entry_high") or 0),
                stop=float(ep.get("stop") or 0),
                tp1=float(ep.get("tp1") or 0),
                tp2=float(ep.get("tp2") or 0),
                confidence=float(ep.get("confidence") or 0),
                levels=ep.get("levels") or {},
                reasons=ep.get("reasons") or [],
                invalid_above=ep.get("invalid_above"),
                invalid_below=ep.get("invalid_below"),
            )
            plan_txt = format_entry_plan(plan)
    except Exception:
        plan_txt = ""

    hist_txt = ""
    try:
        from historical_2y import History2Y, format_history_2y
        hraw = res.get("history_2y")
        if hraw:
            h2 = History2Y(
                symbol=str(hraw.get("symbol", sym)),
                bars=int(hraw.get("bars") or 0),
                years_span=float(hraw.get("years_span") or 0),
                mark=float(hraw.get("mark") or 0),
                high_2y=float(hraw.get("high_2y") or 0),
                low_2y=float(hraw.get("low_2y") or 0),
                high_52w=float(hraw.get("high_52w") or 0),
                low_52w=float(hraw.get("low_52w") or 0),
                ema50=float(hraw.get("ema50") or 0),
                ema200=float(hraw.get("ema200") or 0),
                trend=str(hraw.get("trend") or "RANGE"),
                support=list(hraw.get("support") or []),
                resistance=list(hraw.get("resistance") or []),
                bias_score=float(hraw.get("bias_score") or 0),
                summary=str(hraw.get("summary") or ""),
            )
            hist_txt = format_history_2y(h2)
    except Exception:
        hist_txt = ""

    matrix_txt = ""
    try:
        from consensus_visualizer import format_agent_consensus_matrix
        matrix_txt = format_agent_consensus_matrix(votes)
    except Exception:
        pass

    chart_txt = ""
    ch = res.get("mt5_chart")
    if ch and isinstance(ch, dict):
        try:
            t = ch.get("technicals", {})
            bias = str(t.get("trend_bias", "NEUTRAL"))
            ema_sig = str(t.get("ema_signal", ""))
            rsi = float(t.get("rsi", 50.0))
            atr_v = float(t.get("atr", 0.0))
            p = float(t.get("pivot", 0.0))
            r1 = float(t.get("r1", 0.0))
            r2 = float(t.get("r2", 0.0))
            s1 = float(t.get("s1", 0.0))
            s2 = float(t.get("s2", 0.0))
            spread = float(ch.get("spread", 0.0))
            server = str(ch.get("server", "MetaQuotes-Demo"))
            b_mode = str(ch.get("broker_mode", "LIVE"))
            chart_txt = (
                f"\n📈 **MT5 Institutional Chart Telemetry:**\n"
                f"• **Structure Bias:** {bias}\n"
                f"• **Moving Averages:** `{ema_sig}` (EMA9 `${t.get('ema9', 0):,.2f}` / EMA21 `${t.get('ema21', 0):,.2f}`)\n"
                f"• **Floor Pivots:** `R2: ${r2:,.2f}` | `R1: ${r1:,.2f}` | `P: ${p:,.2f}` | `S1: ${s1:,.2f}` | `S2: ${s2:,.2f}`\n"
                f"• **Oscillators & Vol:** `RSI(14): {rsi:.1f}` | `ATR(14): ${atr_v:,.2f}` | `Spread: ${spread:.2f}`\n"
                f"• **MT5 Feed:** `{server}` | Mode: `{b_mode}`\n"
            )
        except Exception:
            chart_txt = ""

    votes_txt = "\n".join(vote_lines) if vote_lines else "_No votes_"
    matrix_section = f"\n{matrix_txt}\n" if matrix_txt else f"\n**Agent Votes:**\n{votes_txt}\n"
    votes_section = matrix_section if (show_agent_votes or res.get("show_agent_votes")) else ""

    return (
        f"📊 **Multi-Agent Evaluation for {sym}**\n"
        f"Mark: `${price_val:,.2f}` | Lev `{float(lev):.0f}x` | ⚡ **Consensus:** `{status}`\n"
        f"{votes_section}"
        f"{sig_box}"
        f"{chart_txt}"
        f"{plan_txt}"
        f"{hist_txt}"
    )


def try_paper_execute(engine, sig: Dict[str, Any], candle_price: Optional[float] = None) -> Tuple[bool, str]:
    if not sig or not engine:
        return False, "No signal or engine"
    if sig.get("vetoed"):
        vr = sig.get("veto_reason", "Vetoed")
        import re
        m_hr = re.search(r"banned hour (\d+)", str(vr), re.IGNORECASE)
        m_wd = re.search(r"banned weekday (\d+)", str(vr), re.IGNORECASE)
        wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        if m_wd:
            w_idx = int(m_wd.group(1)) % 7
            w_name = wd_names[w_idx]
            unban_note = f" | Unban: `/learner unban {w_name.lower()}`"
        elif m_hr:
            unban_note = f" | Unban: `/learner unban {m_hr.group(1)}`"
        return False, (
            f"Trade execution blocked by Autonomous Learner: {vr}\n"
            f"💡 _AI & Veto Filter active. Toggle: `/ai off` or `/veto off` or `/learner off`{unban_note}_"
        )
    if getattr(engine, "execution_halted", False):
        if not getattr(engine, "active_positions", None) and not getattr(engine, "pending_setups", None):
            try:
                engine.reset_halt()
            except Exception:
                pass
        else:
            return False, (
                "Execution halted after uncertain broker state. "
                "Reconcile broker positions, then resume with `/autotrade on` or `/risk restart`. "
                "Verify live MT5 connection with `/mt5 status`."
            )
    px = float(candle_price if candle_price is not None else sig.get("entry", 0.0))
    dname = sig.get("direction")
    if hasattr(dname, "name"):
        dname = dname.name
    else:
        dname = str(dname).upper()
    order_sym = sig.get("symbol", "")
    max_open = int(getattr(engine, "max_open_trades", 6) or 6)

    # Cooldown & Anti-Spam protection: applies to both auto and manual trades
    try:
        from anti_spam import get_anti_spam_limiter
        limiter = get_anti_spam_limiter()
        can_enter, reason = limiter.check_can_enter(
            symbol=order_sym,
            current_price=px,
            engine=engine,
            max_open=max_open,
            direction=dname,
            bypass_cooldowns=False,
        )
        if not can_enter:
            return False, f"⏳ Trade blocked by Cooldown & Anti-Spam: {reason}\n_Applies to both auto and manual trades._"
    except Exception as _lim_err:
        pass

    # Ensure brackets strictly satisfy ordering before registration
    try:
        e_val = float(sig.get("entry", 0.0))
        s_val = float(sig.get("stop", 0.0))
        t1_val = float(sig.get("tp1", 0.0))
        t2_val = float(sig.get("tp2", 0.0))
        min_buf = max(e_val * 0.0005, 0.1)

        if "LONG" in dname or "BUY" in dname:
            if not (s_val < e_val < t1_val <= t2_val):
                raw_risk = abs(e_val - s_val)
                risk = raw_risk if raw_risk >= min_buf else min_buf
                if s_val >= e_val:
                    sig["stop"] = round(e_val - risk, 4)
                if t1_val <= e_val:
                    sig["tp1"] = round(e_val + risk * 1.5, 4)
                if t2_val < float(sig.get("tp1", t1_val)):
                    sig["tp2"] = round(float(sig["tp1"]) + risk * 0.5, 4)
        elif "SHORT" in dname or "SELL" in dname:
            if not (s_val > e_val > t1_val >= t2_val):
                raw_risk = abs(s_val - e_val)
                risk = raw_risk if raw_risk >= min_buf else min_buf
                if s_val <= e_val:
                    sig["stop"] = round(e_val + risk, 4)
                if t1_val >= e_val:
                    sig["tp1"] = round(e_val - risk * 1.5, 4)
                if t2_val > float(sig.get("tp1", t1_val)):
                    sig["tp2"] = round(float(sig["tp1"]) - risk * 0.5, 4)
    except Exception as _bracket_err:
        logger.debug("try_paper_execute bracket sanitization note: %s", _bracket_err)

    try:
        order = engine.register_setup(sig, reason=sig.get("reason", "Multi-Agent Consensus"))
    except Exception as e:
        return False, f"Register failed: {e}"
    px = float(candle_price if candle_price is not None else sig.get("market_price", sig["entry"]))
    try:
        events = engine.update_market_candle(
            order.symbol,
            {"open": px, "high": px * 1.0005, "low": px * 0.9995, "close": px},
        )
    except Exception as e:
        return True, f"Setup registered (fill pending): {e}"
    msgs = [ev.get("message") for ev in events if ev.get("message")]
    failed_evs = [ev for ev in events if ev.get("event") in ("BROKER_EXECUTION_FAILED", "BROKER_CLOSE_FAILED")]
    if failed_evs:
        last_fail = failed_evs[-1].get("message") or "Broker execution failed"
        return False, last_fail

    try:
        from anti_spam import get_anti_spam_limiter
        limiter = get_anti_spam_limiter()
        limiter.record_entry(order.symbol, px)
    except Exception:
        pass

    if order.symbol in getattr(engine, "active_positions", {}):
        return True, msgs[-1] if msgs else f"Trade filled: {order.symbol} {order.direction}"
    if order.id in getattr(engine, "pending_setups", {}):
        return True, msgs[-1] if msgs else f"Setup pending: {order.symbol} @ {order.entry_level:,.2f} (waiting for perfect entry pullback)"
    return False, msgs[-1] if msgs else f"Setup not executed: {order.symbol}"
