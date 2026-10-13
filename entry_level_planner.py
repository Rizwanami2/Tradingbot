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
    for span in (9, 21, 50):
        try:
            out[f"ema{span}"] = _safe(c.ewm(span=span, adjust=False).mean().iloc[-1])
        except Exception:
            pass
    return out


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

    daily = _daily_levels(symbol, df)
    swings = _swing_levels(df) if df is not None else {}
    emas = _ema_levels(df) if df is not None else {}

    candidates: List[Tuple[str, float, float]] = []
    all_levels: Dict[str, float] = {"mark": mark, "atr": atr}
    all_levels.update(daily)
    all_levels.update(swings)
    all_levels.update(emas)
    try:
        from historical_2y import analyze_2y, history_levels_for_planner
        h2 = analyze_2y(symbol)
        all_levels.update(history_levels_for_planner(h2))
    except Exception:
        pass

    for name, px in list(all_levels.items()):
        if name in ("mark", "atr") or px <= 0:
            continue
        sc = _score_level(px, mark, direction, atr)
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
            confidence=0.0,
            levels=all_levels,
            reasons=["No directional consensus — range around mark only"],
        )

    candidates.sort(key=lambda x: x[2], reverse=True)
    top = candidates[:5]
    weights = [c[2] for c in top]
    prices = [c[1] for c in top]
    wsum = sum(weights) or 1.0
    best = sum(p * w for p, w in zip(prices, weights)) / wsum

    if direction == "LONG":
        entry_low = min(prices + [best, mark - atr * 0.25])
        entry_high = min(mark, best + atr * 0.15)
        if entry_high < entry_low:
            entry_low, entry_high = mark - atr * 0.4, mark
        best_entry = (entry_low + entry_high) / 2
        stop = stop_hint if stop_hint and stop_hint < entry_low else entry_low - atr * 1.0
        risk = abs(best_entry - stop) or atr
        tp1 = best_entry + risk * risk_r
        tp2 = best_entry + risk * risk_r * 1.6
        invalid_below = stop
        invalid_above = None
    else:
        entry_high = max(prices + [best, mark + atr * 0.25])
        entry_low = max(mark, best - atr * 0.15)
        if entry_high < entry_low:
            entry_low, entry_high = mark, mark + atr * 0.4
        best_entry = (entry_low + entry_high) / 2
        stop = stop_hint if stop_hint and stop_hint > entry_high else entry_high + atr * 1.0
        risk = abs(stop - best_entry) or atr
        tp1 = best_entry - risk * risk_r
        tp2 = best_entry - risk * risk_r * 1.6
        invalid_above = stop
        invalid_below = None

    for name, px, sc in top[:3]:
        reasons.append(f"{name}@{px:,.2f} (score {sc:.2f})")

    conf = min(0.95, 0.45 + 0.12 * len(top) + 0.15 * (top[0][2] if top else 0))

    return EntryPlan(
        direction=direction,
        mark=mark,
        best_entry=round(best_entry, 6),
        entry_low=round(min(entry_low, entry_high), 6),
        entry_high=round(max(entry_low, entry_high), 6),
        stop=round(stop, 6),
        tp1=round(tp1, 6),
        tp2=round(tp2, 6),
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
    for k in ("day_open", "pdh", "pdl", "ema21", "ema50", "swing_high", "swing_low"):
        if k in plan.levels and plan.levels[k]:
            key_lv.append(f"{k} `${plan.levels[k]:,.2f}`")
    lv_line = ("• Levels: " + " | ".join(key_lv[:5]) + "\n") if key_lv else ""
    return (
        f"\n📐 **Best Entry Plan ({plan.direction})** conf `{plan.confidence:.0%}`\n"
        f"• **Entry zone:** `${plan.entry_low:,.2f}` – `${plan.entry_high:,.2f}`\n"
        f"• **Best entry:** `${plan.best_entry:,.2f}` (mark `${plan.mark:,.2f}`)\n"
        f"• **Stop:** `${plan.stop:,.2f}` | **TP1:** `${plan.tp1:,.2f}` | **TP2:** `${plan.tp2:,.2f}`\n"
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
        if abs(best - mark) / max(mark, 1e-9) < 0.01:
            out["entry"] = best
        else:
            out["entry"] = float(out.get("entry") or mark)
        out["entry_low"] = plan.entry_low
        out["entry_high"] = plan.entry_high
        out["best_entry"] = plan.best_entry
        out["plan_stop"] = plan.stop
        out["plan_tp1"] = plan.tp1
        out["plan_tp2"] = plan.tp2
    except Exception:
        pass
    return out
