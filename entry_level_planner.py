#!/usr/bin/env python3
"""
Best entry-level planner.

Uses all available market data to propose an entry *range* (not only mark price):
  - Daily open / PDH / PDL
  - Swing structure (recent highs/lows)
  - EMA 9/21/50 confluence
  - ATR volatility band
  - Optional SMC / liquidity levels when present
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _safe(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


@dataclass
class EntryPlan:
    direction: str
    mark: float
    best_entry: float
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    confidence: float
    tp3: Optional[float] = None
    rr: float = 0.0
    levels: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    invalid_above: Optional[float] = None
    invalid_below: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _daily_levels(symbol: str, df: Optional[pd.DataFrame]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        from realtime_prices import compute_levels
        data = compute_levels(symbol)
        for k in ("pdh", "pdl", "pdo", "day_open", "open", "prev_high", "prev_low"):
            if isinstance(data, dict) and data.get(k) is not None:
                out[k] = _safe(data[k])
        lv = data.get("levels") if isinstance(data, dict) else None
        if isinstance(lv, dict):
            for k, v in lv.items():
                if v is not None:
                    out[str(k).lower()] = _safe(v)
    except Exception:
        pass
    if df is not None and len(df) >= 50 and not out:
        try:
            w = df.iloc[-min(len(df), 500):]
            mid = len(w) // 2
            out["day_open"] = _safe(w["open"].iloc[0])
            out["pdh"] = _safe(w["high"].iloc[:mid].max())
            out["pdl"] = _safe(w["low"].iloc[:mid].min())
        except Exception:
            pass
    return out


def _swing_levels(df: pd.DataFrame, lookback: int = 40) -> Dict[str, float]:
    if df is None or len(df) < lookback:
        return {}
    seg = df.iloc[-lookback:]
    return {
        "swing_high": _safe(seg["high"].max()),
        "swing_low": _safe(seg["low"].min()),
        "mid": _safe((seg["high"].max() + seg["low"].min()) / 2),
    }


def _ema_levels(df: pd.DataFrame) -> Dict[str, float]:
    if df is None or len(df) < 55:
        return {}
    c = df["close"].astype(float)
    out = {}
    for span in (9, 21, 50, 200):
        try:
            out[f"ema{span}"] = _safe(c.ewm(span=span, adjust=False).mean().iloc[-1])
        except Exception:
            pass
    return out


def _mtf_confluence_levels(symbol: str) -> Tuple[Dict[str, float], float, str]:
    """Extract key HTF levels and directional alignment from MTF Matrix."""
    levels: Dict[str, float] = {}
    score = 0.0
    bias = "NEUTRAL"
    try:
        from mtf_matrix import _MATRIX_CACHE
        sym_key = str(symbol or "").strip().upper()
        snap = None
        if sym_key in _MATRIX_CACHE:
            snap = _MATRIX_CACHE[sym_key][1]
        elif not os.getenv("PYTEST_CURRENT_TEST"):
            from mtf_matrix import analyze_symbol_mtf
            snap = analyze_symbol_mtf(symbol, force_refresh=False)
        if snap and hasattr(snap, "timeframes"):
            score = float(getattr(snap, "overall_score", 0.0) or 0.0)
            bias = str(getattr(snap, "top_bias", "NEUTRAL") or "NEUTRAL").upper()
            tfs = snap.timeframes
            for tf_k in ("15m", "1h", "4h", "daily"):
                if tf_k in tfs:
                    item = tfs[tf_k]
                    if item.ema21 > 0:
                        levels[f"mtf_{tf_k}_ema21"] = _safe(item.ema21)
                    if item.ema50 > 0:
                        levels[f"mtf_{tf_k}_ema50"] = _safe(item.ema50)
                    if item.ema200 > 0:
                        levels[f"mtf_{tf_k}_ema200"] = _safe(item.ema200)
    except Exception:
        pass
    return levels, score, bias


def _institutional_pivot_levels(symbol: str, df: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Extract Floor Pivot points and technical levels from MT5 Chart engine."""
    levels: Dict[str, float] = {}
    try:
        from mt5_chart import compute_technical_levels
        candles = []
        if df is not None and len(df) >= 15:
            for _, r in df.iloc[-60:].iterrows():
                candles.append({
                    "open": _safe(r.get("open", r.get("close", 0.0))),
                    "high": _safe(r.get("high", 0.0)),
                    "low": _safe(r.get("low", 0.0)),
                    "close": _safe(r.get("close", 0.0)),
                })
        if len(candles) >= 15:
            tech = compute_technical_levels(candles)
            if tech:
                for k in ("pivot", "r1", "r2", "s1", "s2"):
                    if tech.get(k) and float(tech[k]) > 0:
                        levels[f"mt5_{k}"] = _safe(tech[k])
    except Exception:
        pass
    return levels


def _ote_fibonacci_levels(df: Optional[pd.DataFrame], direction: str) -> Dict[str, float]:
    """Calculate ICT Optimal Trade Entry (OTE) 61.8% - 78.6% Golden Pocket levels."""
    levels: Dict[str, float] = {}
    if df is None or len(df) < 20:
        return levels
    try:
        lookback = min(len(df), 50)
        seg = df.iloc[-lookback:]
        high_val = float(seg["high"].max())
        low_val = float(seg["low"].min())
        span = high_val - low_val
        if span <= 0:
            return levels
        if direction == "LONG":
            # Retracement from high down into discount
            fib_50 = high_val - span * 0.50
            fib_618 = high_val - span * 0.618
            fib_705 = high_val - span * 0.705
            fib_786 = high_val - span * 0.786
            levels["ote_50"] = _safe(fib_50)
            levels["ote_618"] = _safe(fib_618)
            levels["ote_705"] = _safe(fib_705)
            levels["ote_786"] = _safe(fib_786)
            levels["ote_target"] = _safe(high_val + span * 0.27)
        elif direction == "SHORT":
            # Retracement from low up into premium
            fib_50 = low_val + span * 0.50
            fib_618 = low_val + span * 0.618
            fib_705 = low_val + span * 0.705
            fib_786 = low_val + span * 0.786
            levels["ote_50"] = _safe(fib_50)
            levels["ote_618"] = _safe(fib_618)
            levels["ote_705"] = _safe(fib_705)
            levels["ote_786"] = _safe(fib_786)
            levels["ote_target"] = _safe(low_val - span * 0.27)
    except Exception:
        pass
    return levels


def _supply_demand_zone_levels(df: Optional[pd.DataFrame], mark: float, direction: str) -> Dict[str, float]:
    """Extract fresh institutional Supply & Demand boundaries."""
    levels: Dict[str, float] = {}
    if df is None or len(df) < 15:
        return levels
    try:
        from supply_demand_strategy import extract_supply_demand_zones, SDDir
        zones = extract_supply_demand_zones(df)
        for z in zones:
            if not z.fresh:
                continue
            if direction == "LONG" and z.direction == SDDir.LONG:
                if z.top <= mark * 1.002:
                    levels["sd_demand_top"] = _safe(z.top)
                    levels["sd_demand_base"] = _safe(z.bottom)
            elif direction == "SHORT" and z.direction == SDDir.SHORT:
                if z.bottom >= mark * 0.998:
                    levels["sd_supply_bottom"] = _safe(z.bottom)
                    levels["sd_supply_top"] = _safe(z.top)
    except Exception:
        pass
    return levels


def _asian_range_levels(df: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Extract Asian session liquidity extremes."""
    levels: Dict[str, float] = {}
    if df is None or len(df) < 20:
        return levels
    try:
        from asian_range_breakout import compute_asian_range
        ar = compute_asian_range(df)
        if ar and ar.high > 0 and ar.low > 0:
            levels["asian_high"] = _safe(ar.high)
            levels["asian_low"] = _safe(ar.low)
            levels["asian_mid"] = _safe(ar.mid)
    except Exception:
        pass
    return levels


def _brain_learning_insights(symbol: str, direction: str) -> Tuple[float, float, List[str]]:
    """Query Autonomous Learner and Learning Edge for previous trade feedback."""
    conf_boost = 0.0
    min_rr = 1.5
    notes: List[str] = []
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        if brain and getattr(brain.knowledge, "enabled", True):
            k = brain.knowledge
            if k.min_rr > min_rr:
                min_rr = float(k.min_rr)
            setup_tag = f"{direction.lower()}|{symbol.lower()}"
            if setup_tag in getattr(k, "banned_setups", {}):
                conf_boost -= 0.15
                notes.append(f"Brain caution: setup {setup_tag} previously flagged")
    except Exception:
        pass
    try:
        from learning_edge import LearningEdge
        edge = LearningEdge()
        tag = f"{direction.lower()}|{symbol.lower()}"
        if tag in edge.setups:
            stats = edge.setups[tag]
            if stats.trades >= 3:
                if stats.wr >= 60.0 or stats.exp > 0.3:
                    conf_boost += 0.10
                    notes.append(f"Brain edge: {tag} WR={stats.wr:.0f}%, Exp={stats.exp:+.2f}R")
                elif stats.wr < 40.0 and stats.exp < -0.2:
                    conf_boost -= 0.10
                    notes.append(f"Brain drag: {tag} low WR={stats.wr:.0f}%")
    except Exception:
        pass
    return conf_boost, min_rr, notes


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 2:
        return 0.0
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return _safe(np.mean(tr[-period:]))


def _score_level(price: float, mark: float, direction: str, atr: float) -> float:
    if price <= 0 or mark <= 0:
        return 0.0
    dist = abs(price - mark)
    band = max(atr, mark * 0.001)
    if direction == "LONG":
        side = 1.0 if price <= mark else 0.35
    elif direction == "SHORT":
        side = 1.0 if price >= mark else 0.35
    else:
        side = 0.5
    proximity = max(0.0, 1.0 - dist / (2.5 * band + 1e-9))
    return side * proximity


def plan_best_entry(
    symbol: str,
    df: Optional[pd.DataFrame],
    direction: str = "FLAT",
    mark: Optional[float] = None,
    stop_hint: Optional[float] = None,
    risk_r: float = 1.5,
    profile: Optional[str] = None,
    agent_levels: Optional[Dict[str, float]] = None,
    votes: Optional[List[Any]] = None,
) -> EntryPlan:
    direction = str(direction or "FLAT").upper()
    if "LONG" in direction:
        direction = "LONG"
    elif "SHORT" in direction:
        direction = "SHORT"
    else:
        direction = "FLAT"

    if mark is None or mark <= 0:
        if df is not None and len(df):
            mark = _safe(df["close"].iloc[-1])
        else:
            mark = 0.0
    mark = _safe(mark)

    atr = _atr(df) if df is not None else mark * 0.002
    if atr <= 0:
        atr = max(mark * 0.002, 1e-6)

    prof_str = str(profile or "").lower()
    is_swing = "swing" in prof_str
    is_scalp_prof = "scalp" in prof_str
    lookback = 60 if is_swing else 40

    daily = _daily_levels(symbol, df)
    swings = _swing_levels(df, lookback=lookback) if df is not None else {}
    emas = _ema_levels(df) if df is not None else {}

    candidates: List[Tuple[str, float, float]] = []
    all_levels: Dict[str, float] = {"mark": mark, "atr": atr}
    all_levels.update(daily)
    all_levels.update(swings)
    all_levels.update(emas)

    # 1. SMC Order Blocks, Fair Value Gaps & Liquidity Pools
    try:
        from smc_liquidity import detect_order_blocks, detect_fair_value_gaps, detect_equal_highs_lows
        if df is not None and len(df) >= 10:
            for ob in detect_order_blocks(df):
                all_levels[f"ob_{ob.direction.name.lower()}"] = ob.price
            for fvg in detect_fair_value_gaps(df):
                all_levels[f"fvg_{fvg.direction.name.lower()}"] = fvg.price
            for eq in detect_equal_highs_lows(df):
                all_levels[f"liq_{eq.kind.lower()}"] = eq.price
    except Exception:
        pass

    # 2. Multi-Timeframe Matrix Confluence (1m to 1d)
    mtf_levels, mtf_score, mtf_bias = _mtf_confluence_levels(symbol)
    all_levels.update(mtf_levels)

    # 3. Institutional MT5 Chart Floor Pivots & ATR Channels
    pivot_levels = _institutional_pivot_levels(symbol, df)
    all_levels.update(pivot_levels)

    # 4. ICT Optimal Trade Entry (OTE) 61.8% - 78.6% Golden Pocket
    ote_levels = _ote_fibonacci_levels(df, direction)
    all_levels.update(ote_levels)

    # 5. Institutional Supply & Demand Zones
    sd_levels = _supply_demand_zone_levels(df, mark, direction)
    all_levels.update(sd_levels)

    # 6. Asian Session Range Liquidity Extremes
    asian_levels = _asian_range_levels(df)
    all_levels.update(asian_levels)

    # 7. Agent-Proposed Specific Structural Levels (Unified Consensus)
    if agent_levels and isinstance(agent_levels, dict):
        for k, v in agent_levels.items():
            if v and float(v) > 0:
                all_levels[f"agent_{k}"] = _safe(v)

    if votes:
        for v in votes:
            aname = str(getattr(v, "agent", "") or "").lower().replace("-", "_")
            vdir = str(getattr(v, "direction", "")).upper()
            if direction in vdir:
                sp = getattr(v, "stop_price", None)
                tp = getattr(v, "tp_price", None)
                ep = getattr(v, "entry_price", None)
                if sp and float(sp) > 0:
                    all_levels[f"{aname}_stop"] = _safe(sp)
                if tp and float(tp) > 0:
                    all_levels[f"{aname}_tp"] = _safe(tp)
                if ep and float(ep) > 0:
                    all_levels[f"{aname}_entry"] = _safe(ep)

    # 8. Historical 2Y High-Probability Key Levels
    try:
        from historical_2y import analyze_2y, history_levels_for_planner
        h2 = analyze_2y(symbol)
        all_levels.update(history_levels_for_planner(h2))
    except Exception:
        pass

    # 9. Autonomous Brain Learning Insights & Expectancy
    conf_boost, brain_min_rr, brain_notes = _brain_learning_insights(symbol, direction)
    if brain_min_rr > risk_r:
        risk_r = brain_min_rr
    if is_swing and risk_r < 2.5:
        risk_r = 2.5

    sym_u = str(symbol or "").upper()
    is_gold = any(x in sym_u for x in ("XAU", "GOLD", "PAXG"))
    is_btc = any(x in sym_u for x in ("BTC", "BITCOIN", "XBT"))
    if is_gold:
        spread_buf = max(atr * 0.25, 0.40)
    elif is_btc:
        spread_buf = max(atr * 0.25, 12.0)
    else:
        spread_buf = max(atr * 0.22, mark * 0.0004)

    for name, px in list(all_levels.items()):
        if name in ("mark", "atr") or px <= 0:
            continue
        sc = _score_level(px, mark, direction, atr)
        if any(tag in name for tag in ("ote_618", "ote_705", "sd_demand", "sd_supply", "mt5_pivot", "ob_")):
            sc *= 1.25
        if sc > 0.15:
            candidates.append((name, px, sc))

    if direction == "LONG":
        candidates.append(("atr_pullback", mark - atr * 0.5, 0.7))
        candidates.append(("atr_deep", mark - atr * 1.0, 0.55))
    elif direction == "SHORT":
        candidates.append(("atr_pullback", mark + atr * 0.5, 0.7))
        candidates.append(("atr_deep", mark + atr * 1.0, 0.55))

    reasons: List[str] = []
    if not candidates or direction == "FLAT":
        lo, hi = mark - atr * 0.35, mark + atr * 0.35
        return EntryPlan(
            direction="FLAT",
            mark=mark,
            best_entry=mark,
            entry_low=min(lo, hi),
            entry_high=max(lo, hi),
            stop=mark - atr,
            tp1=mark + atr * risk_r,
            tp2=mark + atr * risk_r * 1.6,
            tp3=mark + atr * risk_r * 2.5,
            rr=round(risk_r * 1.6, 2),
            confidence=0.0,
            levels=all_levels,
            reasons=["No directional consensus — range around mark only"],
        )

    candidates.sort(key=lambda x: x[2], reverse=True)
    top = candidates[:6]
    weights = [c[2] for c in top]
    prices = [c[1] for c in top]
    wsum = sum(weights) or 1.0
    best = sum(p * w for p, w in zip(prices, weights)) / wsum

    sw_h = swings.get("swing_high")
    sw_l = swings.get("swing_low")

    demand_base = all_levels.get("sd_demand_base")
    supply_top = all_levels.get("sd_supply_top")
    pivot_s1 = all_levels.get("mt5_s1")
    pivot_r1 = all_levels.get("mt5_r1")

    if direction == "LONG":
        entry_low = min(prices + [best, mark - atr * 0.25])
        entry_high = min(mark, best + atr * 0.15)
        if entry_high < entry_low:
            entry_low, entry_high = mark - atr * 0.4, mark
        best_entry = (entry_low + entry_high) / 2

        # Structure-based Stop Loss: below swing low / demand base / pivot S1
        anchors = [p for p in (sw_l, demand_base, pivot_s1) if p and p < entry_low]
        anchor_l = min(anchors) if anchors else (sw_l if (sw_l and sw_l < entry_low) else entry_low)
        struct_stop = anchor_l - spread_buf
        if stop_hint and stop_hint < entry_low:
            stop = min(stop_hint, struct_stop)
        else:
            stop = struct_stop
        # Guarantee minimum stop distance
        stop = min(stop, best_entry - max(atr * 0.8, spread_buf * 2.0))

        risk = abs(best_entry - stop) or atr
        min_tp1 = best_entry + risk * max(risk_r, 1.5)
        # Target structural resistance or pivot R1/R2
        targs = [p for p in (sw_h, pivot_r1, all_levels.get("mt5_r2")) if p and p >= best_entry + risk * 1.2]
        tp1 = min(targs) if targs else min_tp1
        tp1 = max(min_tp1, tp1)

        tp2 = max(tp1 + risk * 0.8, best_entry + risk * (2.5 if not is_swing else 3.2))
        tp3 = best_entry + risk * (4.0 if not is_swing else 5.0)
        invalid_below = stop
        invalid_above = best_entry + risk * 1.5
    else:
        entry_high = max(prices + [best, mark + atr * 0.25])
        entry_low = max(mark, best - atr * 0.15)
        if entry_high < entry_low:
            entry_low, entry_high = mark, mark + atr * 0.4
        best_entry = (entry_low + entry_high) / 2

        # Structure-based Stop Loss: above swing high / supply ceiling / pivot R1
        anchors = [p for p in (sw_h, supply_top, pivot_r1) if p and p > entry_high]
        anchor_h = max(anchors) if anchors else (sw_h if (sw_h and sw_h > entry_high) else entry_high)
        struct_stop = anchor_h + spread_buf
        if stop_hint and stop_hint > entry_high:
            stop = max(stop_hint, struct_stop)
        else:
            stop = struct_stop
        # Guarantee minimum stop distance
        stop = max(stop, best_entry + max(atr * 0.8, spread_buf * 2.0))

        risk = abs(stop - best_entry) or atr
        min_tp1 = best_entry - risk * max(risk_r, 1.5)
        # Target structural support or pivot S1/S2
        targs = [p for p in (sw_l, pivot_s1, all_levels.get("mt5_s2")) if p and p <= best_entry - risk * 1.2]
        tp1 = max(targs) if targs else min_tp1
        tp1 = min(min_tp1, tp1)

        tp2 = min(tp1 - risk * 0.8, best_entry - risk * (2.5 if not is_swing else 3.2))
        tp3 = best_entry - risk * (4.0 if not is_swing else 5.0)
        invalid_above = stop
        invalid_below = best_entry - risk * 1.5

    for name, px, sc in top[:3]:
        reasons.append(f"{name}@{px:,.2f} (score {sc:.2f})")

    if mtf_bias == direction:
        reasons.append(f"MTF {mtf_bias} aligned (score {mtf_score:+.0f})")
        conf_boost += 0.08
    elif mtf_bias != "NEUTRAL":
        reasons.append(f"MTF divergence: HTF {mtf_bias}")
        conf_boost -= 0.08

    reasons.extend(brain_notes)

    conf = min(0.98, max(0.35, 0.45 + 0.10 * len(top) + 0.15 * (top[0][2] if top else 0) + conf_boost))
    eff_rr = round(abs(tp2 - best_entry) / max(risk, 1e-9), 2)

    return EntryPlan(
        direction=direction,
        mark=mark,
        best_entry=round(best_entry, 6),
        entry_low=round(min(entry_low, entry_high), 6),
        entry_high=round(max(entry_low, entry_high), 6),
        stop=round(stop, 6),
        tp1=round(tp1, 6),
        tp2=round(tp2, 6),
        tp3=round(tp3, 6),
        rr=eff_rr,
        confidence=round(conf, 3),
        levels={k: round(v, 6) for k, v in all_levels.items() if v},
        reasons=reasons,
        invalid_above=round(invalid_above, 6) if invalid_above else None,
        invalid_below=round(invalid_below, 6) if invalid_below else None,
    )


def format_entry_plan(plan: EntryPlan) -> str:
    if plan.direction == "FLAT":
        return (
            f"\n📐 **Entry Plan (wait)**\n"
            f"• Mark `${plan.mark:,.2f}` | Range `${plan.entry_low:,.2f}` – `${plan.entry_high:,.2f}`\n"
            f"• _{plan.reasons[0] if plan.reasons else 'No setup'}_\n"
        )
    inv = ""
    if plan.invalid_below:
        inv = f"• Invalid if below `${plan.invalid_below:,.2f}`\n"
    if plan.invalid_above:
        inv = f"• Invalid if above `${plan.invalid_above:,.2f}`\n"
    why = ", ".join(plan.reasons[:3]) if plan.reasons else "multi-level confluence"
    key_lv = []
    for k in ("day_open", "pdh", "pdl", "ema21", "ema50", "swing_high", "swing_low", "ob_bullish", "ob_bearish", "fvg_bullish", "fvg_bearish"):
        if k in plan.levels and plan.levels[k]:
            key_lv.append(f"{k} `${plan.levels[k]:,.2f}`")
    lv_line = ("• Levels: " + " | ".join(key_lv[:5]) + "\n") if key_lv else ""
    tp3_line = f" | **TP3:** `${plan.tp3:,.2f}`" if plan.tp3 else ""
    return (
        f"\n📐 **Best Entry Plan ({plan.direction})** conf `{plan.confidence:.0%}` | R:R `1:{plan.rr:.1f}`\n"
        f"• **Entry zone:** `${plan.entry_low:,.2f}` – `${plan.entry_high:,.2f}`\n"
        f"• **Best entry:** `${plan.best_entry:,.2f}` (mark `${plan.mark:,.2f}`)\n"
        f"• **Stop:** `${plan.stop:,.2f}` | **TP1:** `${plan.tp1:,.2f}` | **TP2:** `${plan.tp2:,.2f}`{tp3_line}\n"
        f"{inv}"
        f"{lv_line}"
        f"• Why: _{why}_\n"
    )


def attach_plan_to_signal(sig: Dict[str, Any], plan: EntryPlan) -> Dict[str, Any]:
    if not sig or plan.direction == "FLAT":
        sig = dict(sig or {})
        sig["entry_plan"] = plan.to_dict()
        return sig
    out = dict(sig)
    out["entry_plan"] = plan.to_dict()
    try:
        mark = float(plan.mark)
        best = float(plan.best_entry)
        
        # Determine if signal is scalp vs balanced / structural consensus
        is_scalp = bool(sig.get("is_scalp", False))

        if is_scalp:
            # Scalp profile: execute at current market positions
            out["entry"] = mark or float(out.get("entry") or best)
            out["market_price"] = mark
            out["is_scalp"] = True
            out["fast_market_fill"] = True
            out["is_perfect_entry"] = False
            out["entry_type"] = "MARKET"
            out["order_type"] = "MARKET"
        else:
            # Balanced profile / Multi-Agent Consensus: provide perfect entry level signal,
            # never current market position. Must register as pending limit setup.
            valid_best = best > 0 and math.isfinite(best)
            out["entry"] = best if valid_best else float(out.get("entry") or mark)
            out["best_entry"] = best
            out["market_price"] = mark
            out["is_scalp"] = False
            out["fast_market_fill"] = False
            out["is_perfect_entry"] = True
            out["entry_type"] = "LIMIT_PERFECT_LEVEL"
            out["order_type"] = "LIMIT"

        out["entry_low"] = plan.entry_low
        out["entry_high"] = plan.entry_high
        out["best_entry"] = plan.best_entry
        out["plan_stop"] = plan.stop
        out["plan_tp1"] = plan.tp1
        out["plan_tp2"] = plan.tp2

        # In standard consensus setups, adopt the refined structural TP/SL and RR
        if not is_scalp:
            old_entry = float(sig.get("entry") or best)
            old_stop = float(sig.get("stop") or plan.stop)
            old_risk = abs(old_entry - old_stop) or 1.0

            out["stop"] = plan.stop
            out["tp1"] = plan.tp1
            out["tp2"] = plan.tp2
            if plan.tp3:
                out["tp3"] = plan.tp3

            new_risk = abs(float(out["entry"]) - float(out["stop"]))
            if new_risk > 0:
                out["rr"] = round(abs(float(out["tp2"]) - float(out["entry"])) / new_risk, 2)
                # Adjust lot size proportionally so dollar risk remains constant
                ratio = old_risk / new_risk
                if 0.5 <= ratio <= 2.0 and "size" in out:
                    out["size"] = round(float(out["size"]) * ratio, 4)
                    if "lots" in out:
                        out["lots"] = round(float(out["lots"]) * ratio, 4)
                    if "margin_required" in out:
                        out["margin_required"] = round(float(out["margin_required"]) * ratio, 2)
    except Exception:
        pass
    return out
