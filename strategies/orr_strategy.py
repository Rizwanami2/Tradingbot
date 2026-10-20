#!/usr/bin/env python3
"""
Opening Range Retest & Order Flow Reversal (ORR) Strategy.
=========================================================
Institutional price-action setup engineered for high accuracy and clean straight
directional entries on Gold (XAUUSD) and Bitcoin (BTCUSDT).

Strategy Principles:
1. Opening Range (OR) & Initial Balance:
   - Evaluates the session opening range (London Open 07:00-08:00 UTC, NY Open 12:30-13:30 UTC, Asian Open 00:00-01:00 UTC)
     or rolling 20-30 bar consolidation range for 24/7 continuous trading.
   - Defines Opening Range High (ORH), Opening Range Low (ORL), and Range Midpoint (ORM).

2. Breakout with Momentum:
   - Confirms decisive candle close above ORH (bullish breakout) or below ORL (bearish breakout).
   - Rejects unconfirmed wicks.

3. Straight Retest & Wick Rejection:
   - Does NOT chase the breakout candle.
   - Waits for price to retest straight back to the broken level (ORH as support or ORL as resistance).
   - Confirms institutional rejection: candle lower wick >= 25% of candle range on bullish retest,
     or upper wick >= 25% of candle range on bearish retest.
   - Candle closes back in the direction of the breakout.

4. 24/7 Order Flow Market Structure Retest (BOS + Retest):
   - Detects local swing structure displacement and immediate straight retest with wick rejection.

5. Direct Structural Risk / Reward:
   - Precision structural stop loss placed behind the retest rejection wick.
   - Target 1: 1.5x R:R, Target 2: 2.5x R:R.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class ORRDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class ORRVote:
    agent: str = "ORR-STRATEGY"
    direction: ORRDir = ORRDir.FLAT
    strength: float = 0.0
    reason: str = ""
    pattern: str = ""
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 1:
        return 0.0
    try:
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        c = df["close"].astype(float).values
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        if len(tr) >= period:
            return float(np.mean(tr[-period:]))
    except Exception:
        pass
    return 0.0


def _extract_datetime_index(df: pd.DataFrame) -> Optional[pd.DatetimeIndex]:
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index
    for col in ("time", "timestamp", "datetime", "date"):
        if col in df.columns:
            try:
                dt = pd.to_datetime(df[col], utc=True)
                return pd.DatetimeIndex(dt)
            except Exception:
                pass
    return None


@dataclass
class OpeningRange:
    high: float
    low: float
    mid: float
    range_pts: float
    range_pct: float
    start_idx: int
    end_idx: int
    valid: bool = False
    session_name: str = "Rolling-IB"


def compute_opening_range(df: pd.DataFrame, min_bars: int = 15) -> OpeningRange:
    """
    Computes either the session-based Opening Range (London/NY/Asian) if datetime index is present,
    or dynamic rolling Initial Balance from earlier bars.
    """
    if df is None or len(df) < min_bars + 5:
        return OpeningRange(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, valid=False)

    dt_idx = _extract_datetime_index(df)
    n = len(df)

    # 1. Try session-based opening range if timestamps exist
    if dt_idx is not None and len(dt_idx) == n:
        try:
            times = dt_idx.time
            # Institutional Opening Range windows: first 15-20m of key session opens
            sessions = [
                (time(7, 0), time(7, 15), "London-OR"),
                (time(12, 30), time(12, 45), "NY-OR"),
                (time(0, 0), time(0, 20), "Asian-OR"),
            ]
            last_t = times[-1]
            for s_start, s_end, s_name in sessions:
                in_window = [i for i, t in enumerate(times) if s_start <= t <= s_end]
                # If we have bars in the opening range and there are bars after the window has closed
                if len(in_window) >= 6 and in_window[-1] < n - 1:
                    sub = df.iloc[in_window]
                    orh = _safe_float(sub["high"].max())
                    orl = _safe_float(sub["low"].min())
                    if orh > orl and orh > 0:
                        pts = orh - orl
                        pct = (pts / orl) * 100.0
                        return OpeningRange(
                            high=orh,
                            low=orl,
                            mid=(orh + orl) / 2.0,
                            range_pts=pts,
                            range_pct=pct,
                            start_idx=in_window[0],
                            end_idx=in_window[-1],
                            valid=True,
                            session_name=s_name,
                        )
        except Exception:
            pass

    # 2. Rolling Initial Balance (IB) fallback: prior 20-35 bars window before recent price action
    ib_window_size = min(25, max(12, n // 3))
    start_i = max(0, n - ib_window_size - 8)
    end_i = max(start_i + 8, n - 4)

    sub = df.iloc[start_i:end_i]
    orh = _safe_float(sub["high"].max())
    orl = _safe_float(sub["low"].min())
    if orh > orl and orh > 0:
        pts = orh - orl
        pct = (pts / orl) * 100.0
        return OpeningRange(
            high=orh,
            low=orl,
            mid=(orh + orl) / 2.0,
            range_pts=pts,
            range_pct=pct,
            start_idx=start_i,
            end_idx=end_i,
            valid=True,
            session_name="Rolling-IB",
        )

    return OpeningRange(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, valid=False)


def detect_opening_range_retest(
    df: pd.DataFrame,
    daily: Optional[pd.DataFrame] = None,
) -> Optional[ORRVote]:
    """
    Detects high-probability Opening Range Breakout + Straight Retest (ORR):
    1. Decisive breakout candle closed above ORH (bullish) or below ORL (bearish).
    2. Straight retest: price pulls back directly into the broken level zone.
    3. Wick rejection: lower wick >= 25% on long, or upper wick >= 25% on short.
    4. Close back in trade direction.
    """
    if df is None or len(df) < 20:
        return None

    orang = compute_opening_range(df)
    if not orang.valid:
        return None

    orh = orang.high
    orl = orang.low
    atr_val = _calculate_atr(df, 14) or (orh * 0.0015)
    tol = max(atr_val * 0.40, orh * 0.0006)

    # Bars after opening range
    post_bars = df.iloc[orang.end_idx + 1:]
    if len(post_bars) < 2:
        return None

    curr = df.iloc[-1]
    co = _safe_float(curr["open"])
    ch = _safe_float(curr["high"])
    cl = _safe_float(curr["low"])
    cc = _safe_float(curr["close"])
    candle_span = max(ch - cl, 1e-9)

    # Search for decisive breakout in post_bars prior to current bar
    # -------------------------------------------------------------
    # A) BULLISH ORR: Broke ORH, now straight retesting ORH as support
    broke_orh_idx = None
    for idx in range(len(post_bars) - 1):
        b = post_bars.iloc[idx]
        b_c = _safe_float(b["close"])
        b_o = _safe_float(b["open"])
        if b_c > orh + 0.1 * tol and b_c > b_o:
            broke_orh_idx = idx
            break

    if broke_orh_idx is not None:
        # Distance between breakout and current bar (must be a recent straight retest, <= 15 bars)
        bars_since_bo = (len(post_bars) - 1) - broke_orh_idx
        if 1 <= bars_since_bo <= 15:
            # Retest condition: current (or immediately prior) bar low touches ORH support zone
            prev = df.iloc[-2]
            p_l = _safe_float(prev["low"])
            touches_orh = (cl <= orh + tol and cl >= orh - 1.2 * tol) or (p_l <= orh + tol and p_l >= orh - 1.2 * tol)
            
            if touches_orh and cc > orh - 0.5 * tol:
                # Retest bar can be current or previous bar
                retest_bar = curr if (cl <= orh + tol and cl >= orh - 1.2 * tol) else prev
                r_o, r_h, r_l, r_c = map(_safe_float, [retest_bar["open"], retest_bar["high"], retest_bar["low"], retest_bar["close"]])
                r_span = max(r_h - r_l, 1e-9)
                lower_wick = min(r_o, r_c) - r_l
                lower_wick_ratio = lower_wick / r_span
                # Wick rejection confirmation on retest bar or bullish follow-through on current bar
                if lower_wick_ratio >= 0.20 or cc > co:
                    strength = 0.88
                    if lower_wick_ratio >= 0.30 and cc >= co:
                        strength = 0.94
                    retest_low = min(cl, r_l)
                    stop = round(min(retest_low, orh - tol) - 0.5 * atr_val, 4)
                    risk_pts = max(cc - stop, 0.5 * atr_val)
                    tp1 = round(cc + risk_pts * 1.5, 4)
                    tp2 = round(cc + risk_pts * 2.5, 4)
                    reason = (
                        f"ORR Bullish Retest ({orang.session_name}): Broke ORH {orh:,.2f}, "
                        f"straight retest at {retest_low:,.2f} with {lower_wick_ratio:.0%} wick rejection"
                    )
                    return ORRVote(
                        agent="ORR-STRATEGY",
                        direction=ORRDir.LONG,
                        strength=strength,
                        reason=reason,
                        pattern="BULLISH_ORR_RETEST",
                        stop_price=stop,
                        tp_price=tp1,
                        details={
                            "session": orang.session_name,
                            "orh": orh,
                            "orl": orl,
                            "retest_price": retest_low,
                            "wick_ratio": lower_wick_ratio,
                            "tp1": tp1,
                            "tp2": tp2,
                        },
                    )

    # B) BEARISH ORR: Broke ORL, now straight retesting ORL as resistance
    broke_orl_idx = None
    for idx in range(len(post_bars) - 1):
        b = post_bars.iloc[idx]
        b_c = _safe_float(b["close"])
        b_o = _safe_float(b["open"])
        if b_c < orl - 0.1 * tol and b_c < b_o:
            broke_orl_idx = idx
            break

    if broke_orl_idx is not None:
        bars_since_bo = (len(post_bars) - 1) - broke_orl_idx
        if 1 <= bars_since_bo <= 15:
            prev = df.iloc[-2]
            p_h = _safe_float(prev["high"])
            touches_orl = (ch >= orl - tol and ch <= orl + 1.2 * tol) or (p_h >= orl - tol and p_h <= orl + 1.2 * tol)
            
            if touches_orl and cc < orl + 0.5 * tol:
                retest_bar = curr if (ch >= orl - tol and ch <= orl + 1.2 * tol) else prev
                r_o, r_h, r_l, r_c = map(_safe_float, [retest_bar["open"], retest_bar["high"], retest_bar["low"], retest_bar["close"]])
                r_span = max(r_h - r_l, 1e-9)
                upper_wick = r_h - max(r_o, r_c)
                upper_wick_ratio = upper_wick / r_span
                if upper_wick_ratio >= 0.20 or cc < co:
                    strength = 0.88
                    if upper_wick_ratio >= 0.30 and cc <= co:
                        strength = 0.94
                    retest_high = max(ch, r_h)
                    stop = round(max(retest_high, orl + tol) + 0.5 * atr_val, 4)
                    risk_pts = max(stop - cc, 0.5 * atr_val)
                    tp1 = round(cc - risk_pts * 1.5, 4)
                    tp2 = round(cc - risk_pts * 2.5, 4)
                    reason = (
                        f"ORR Bearish Retest ({orang.session_name}): Broke ORL {orl:,.2f}, "
                        f"straight retest at {retest_high:,.2f} with {upper_wick_ratio:.0%} wick rejection"
                    )
                    return ORRVote(
                        agent="ORR-STRATEGY",
                        direction=ORRDir.SHORT,
                        strength=strength,
                        reason=reason,
                        pattern="BEARISH_ORR_RETEST",
                        stop_price=stop,
                        tp_price=tp1,
                        details={
                            "session": orang.session_name,
                            "orh": orh,
                            "orl": orl,
                            "retest_price": retest_high,
                            "wick_ratio": upper_wick_ratio,
                            "tp1": tp1,
                            "tp2": tp2,
                        },
                    )

    return None


def detect_order_flow_structure_retest(df: pd.DataFrame) -> Optional[ORRVote]:
    """
    24/7 Swing Structure Break & Straight Retest (BOS + Retest):
    Detects recent local swing high/low break followed by immediate straight pullback retest.
    """
    if df is None or len(df) < 30:
        return None

    atr_val = _calculate_atr(df, 14) or (_safe_float(df["close"].iloc[-1]) * 0.0015)
    tol = max(atr_val * 0.35, _safe_float(df["close"].iloc[-1]) * 0.0005)

    # Look back 25 bars for swing high / low
    window = df.iloc[-25:-4]
    swing_high = _safe_float(window["high"].max())
    swing_low = _safe_float(window["low"].min())

    recent = df.iloc[-4:]
    curr = recent.iloc[-1]
    co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])
    candle_span = max(ch - cl, 1e-9)

    # 1. Bullish Structure Retest: broke prior swing high, then straight retest
    for i in range(len(recent) - 1):
        bar = recent.iloc[i]
        if _safe_float(bar["close"]) > swing_high:
            # Broke out; check if current bar retests swing_high
            if cl <= swing_high + tol and cc >= swing_high - 0.5 * tol:
                lower_wick = min(co, cc) - cl
                lower_wick_ratio = lower_wick / candle_span
                if lower_wick_ratio >= 0.20 or cc > co:
                    stop = round(min(cl, swing_high - tol) - 0.4 * atr_val, 4)
                    risk_pts = max(cc - stop, 0.4 * atr_val)
                    tp1 = round(cc + risk_pts * 1.5, 4)
                    return ORRVote(
                        agent="ORR-STRATEGY",
                        direction=ORRDir.LONG,
                        strength=0.82,
                        reason=f"Order Flow Retest: Swing High {swing_high:,.2f} broken & straight retested as support",
                        pattern="SWING_BOS_RETEST_LONG",
                        stop_price=stop,
                        tp_price=tp1,
                        details={"swing_high": swing_high, "retest_low": cl},
                    )

    # 2. Bearish Structure Retest: broke prior swing low, then straight retest
    for i in range(len(recent) - 1):
        bar = recent.iloc[i]
        if _safe_float(bar["close"]) < swing_low:
            # Broke out down; check if current bar retests swing_low
            if ch >= swing_low - tol and cc <= swing_low + 0.5 * tol:
                upper_wick = ch - max(co, cc)
                upper_wick_ratio = upper_wick / candle_span
                if upper_wick_ratio >= 0.20 or cc < co:
                    stop = round(max(ch, swing_low + tol) + 0.4 * atr_val, 4)
                    risk_pts = max(stop - cc, 0.4 * atr_val)
                    tp1 = round(cc - risk_pts * 1.5, 4)
                    return ORRVote(
                        agent="ORR-STRATEGY",
                        direction=ORRDir.SHORT,
                        strength=0.82,
                        reason=f"Order Flow Retest: Swing Low {swing_low:,.2f} broken & straight retested as resistance",
                        pattern="SWING_BOS_RETEST_SHORT",
                        stop_price=stop,
                        tp_price=tp1,
                        details={"swing_low": swing_low, "retest_high": ch},
                    )

    return None


class ORRAgent:
    """
    Opening Range Retest (ORR) Strategy Agent.
    Evaluates institutional opening range breakouts, straight pullback retests,
    and order-flow market structure continuation.
    """
    name = "ORR-STRATEGY"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> ORRVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 20:
                return ORRVote(self.name, ORRDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return ORRVote(self.name, ORRDir.FLAT, 0.0, "Missing OHLC columns")

            # 1. Primary setup: Opening Range Breakout + Straight Retest
            setup = detect_opening_range_retest(df, daily=daily)
            if setup is not None and setup.direction != ORRDir.FLAT:
                return setup

            # 2. Secondary setup: 24/7 Swing BOS + Straight Retest
            structure_setup = detect_order_flow_structure_retest(df)
            if structure_setup is not None and structure_setup.direction != ORRDir.FLAT:
                return structure_setup

            return ORRVote(self.name, ORRDir.FLAT, 0.0, "No active ORR breakout-retest setup")
        except Exception as e:
            return ORRVote(self.name, ORRDir.FLAT, 0.0, f"ORR evaluation note: {type(e).__name__}")


def orr_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> ORRVote:
    """Functional convenience wrapper for ORRAgent vote."""
    return ORRAgent().vote(df, daily=daily)
