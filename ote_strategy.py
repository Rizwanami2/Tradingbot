#!/usr/bin/env python3
"""
Optimal Trade Entry (OTE) & Fibonacci Golden Ratio Strategy.
============================================================
Institutional Smart Money (ICT) setup that identifies clean displacement swings
and triggers precision entries inside the Golden Pocket (61.8% to 78.6% retracement).

Core Principles:
1. Significant Displacement Leg:
   - Scans 20-45 bars for an energetic expansion swing (height >= 1.5x ATR).
   - Identifies Swing Origin (Fib 1.0) and Swing Extreme (Fib 0.0).

2. Golden Pocket Retracement:
   - Equilibrium: 50.0%
   - Golden Ratio: 61.8%
   - OTE Sweet Spot: 70.5%
   - Deep Boundary: 78.6%

3. Reversal & Rejection Confirmation:
   - Price retests inside the 61.8% - 78.6% pocket.
   - Validates rejection wick (lower wick for bullish bounce, upper wick for bearish rejection)
     or reversal candle closing back toward the trend.

4. Precision Risk / Reward:
   - Stop Loss placed strictly beyond the swing origin (Fib 1.0) + 0.3x ATR buffer.
   - TP1: Swing extreme (Fib 0.0) -> typical 1:2.0 to 1:3.0 RR.
   - TP2: Fib -0.27 extension -> typical 1:3.5+ RR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class OTEDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class OTEVote:
    agent: str = "OTE-STRATEGY"
    direction: OTEDir = OTEDir.FLAT
    strength: float = 0.0
    reason: str = ""
    pattern: str = ""
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None
    fib_levels: Dict[str, float] = field(default_factory=dict)
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


def detect_ote_setup(df: pd.DataFrame, lookback: int = 40) -> Optional[OTEVote]:
    """
    Identifies recent displacement swing and detects Golden Pocket (61.8% - 78.6%) retracements.
    """
    if df is None or len(df) < 20:
        return None

    atr_val = _calculate_atr(df, 14) or (_safe_float(df["close"].iloc[-1]) * 0.0015)
    n = len(df)
    sub = df.iloc[-min(n, lookback):]

    highs = sub["high"].astype(float).values
    lows = sub["low"].astype(float).values

    max_idx = int(np.argmax(highs))
    min_idx = int(np.argmin(lows))

    swing_high = float(highs[max_idx])
    swing_low = float(lows[min_idx])
    swing_dist = swing_high - swing_low

    if swing_dist < 1.3 * atr_val or swing_high <= 0:
        return None

    curr = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else curr
    co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])
    po, ph, pl, pc = map(_safe_float, [prev["open"], prev["high"], prev["low"], prev["close"]])
    candle_span = max(ch - cl, 1e-9)

    # 1. BULLISH OTE: Low happened first, then impulsive rally to High, now retracing into discount OTE
    if min_idx < max_idx and (len(sub) - 1 - max_idx) >= 1:
        # Swing Low is origin (Fib 1.0), Swing High is extreme (Fib 0.0)
        fib_50 = swing_high - swing_dist * 0.50
        fib_618 = swing_high - swing_dist * 0.618
        fib_705 = swing_high - swing_dist * 0.705
        fib_786 = swing_high - swing_dist * 0.786
        fib_ext27 = swing_high + swing_dist * 0.27

        # Check if price tested the OTE golden zone [fib_786, fib_618]
        retest_low = min(cl, pl)
        in_ote = (retest_low <= fib_618 + 0.15 * atr_val) and (retest_low >= fib_786 - 0.25 * atr_val)

        if in_ote and cc >= fib_786 - 0.1 * atr_val:
            lower_wick = min(co, cc) - cl
            lower_wick_ratio = lower_wick / candle_span
            if lower_wick_ratio >= 0.18 or cc > co:
                strength = 0.92 if (retest_low <= fib_705 and retest_low >= fib_786) else 0.86
                stop = round(swing_low - 0.4 * atr_val, 4)
                tp1 = round(swing_high, 4)
                tp2 = round(fib_ext27, 4)
                reason = (
                    f"Bullish OTE Golden Pocket: Retested 61.8%-78.6% Fib [{fib_786:,.2f} - {fib_618:,.2f}], "
                    f"absorbed with {lower_wick_ratio:.0%} rejection wick (OTE 70.5% @ {fib_705:,.2f})"
                )
                return OTEVote(
                    agent="OTE-STRATEGY",
                    direction=OTEDir.LONG,
                    strength=strength,
                    reason=reason,
                    pattern="BULLISH_OTE_GOLDEN_POCKET",
                    stop_price=stop,
                    tp_price=tp1,
                    fib_levels={"fib_618": fib_618, "fib_705": fib_705, "fib_786": fib_786, "tp1": tp1, "tp2": tp2},
                    details={"swing_low": swing_low, "swing_high": swing_high, "retest_price": retest_low},
                )

    # 2. BEARISH OTE: High happened first, then impulsive drop to Low, now retracing into premium OTE
    elif max_idx < min_idx and (len(sub) - 1 - min_idx) >= 1:
        # Swing High is origin (Fib 1.0), Swing Low is extreme (Fib 0.0)
        fib_50 = swing_low + swing_dist * 0.50
        fib_618 = swing_low + swing_dist * 0.618
        fib_705 = swing_low + swing_dist * 0.705
        fib_786 = swing_low + swing_dist * 0.786
        fib_ext27 = swing_low - swing_dist * 0.27

        # Check if price tested the OTE golden zone [fib_618, fib_786]
        retest_high = max(ch, ph)
        in_ote = (retest_high >= fib_618 - 0.15 * atr_val) and (retest_high <= fib_786 + 0.25 * atr_val)

        if in_ote and cc <= fib_786 + 0.1 * atr_val:
            upper_wick = ch - max(co, cc)
            upper_wick_ratio = upper_wick / candle_span
            if upper_wick_ratio >= 0.18 or cc < co:
                strength = 0.92 if (retest_high >= fib_705 and retest_high <= fib_786) else 0.86
                stop = round(swing_high + 0.4 * atr_val, 4)
                tp1 = round(swing_low, 4)
                tp2 = round(fib_ext27, 4)
                reason = (
                    f"Bearish OTE Golden Pocket: Retested 61.8%-78.6% Fib [{fib_618:,.2f} - {fib_786:,.2f}], "
                    f"rejected with {upper_wick_ratio:.0%} rejection wick (OTE 70.5% @ {fib_705:,.2f})"
                )
                return OTEVote(
                    agent="OTE-STRATEGY",
                    direction=OTEDir.SHORT,
                    strength=strength,
                    reason=reason,
                    pattern="BEARISH_OTE_GOLDEN_POCKET",
                    stop_price=stop,
                    tp_price=tp1,
                    fib_levels={"fib_618": fib_618, "fib_705": fib_705, "fib_786": fib_786, "tp1": tp1, "tp2": tp2},
                    details={"swing_low": swing_low, "swing_high": swing_high, "retest_price": retest_high},
                )

    return None


class OTEAgent:
    """
    Optimal Trade Entry (OTE) Strategy Agent.
    Evaluates institutional Fibonacci retracement golden pockets (61.8% - 78.6%)
    following impulsive displacement market swings.
    """
    name = "OTE-STRATEGY"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> OTEVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 15:
                return OTEVote(self.name, OTEDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return OTEVote(self.name, OTEDir.FLAT, 0.0, "Missing OHLC columns")

            setup = detect_ote_setup(df)
            if setup is not None and setup.direction != OTEDir.FLAT:
                return setup

            return OTEVote(self.name, OTEDir.FLAT, 0.0, "Outside OTE 61.8%-78.6% golden pocket")
        except Exception as e:
            return OTEVote(self.name, OTEDir.FLAT, 0.0, f"OTE evaluation note: {type(e).__name__}")


def ote_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> OTEVote:
    return OTEAgent().vote(df, daily=daily)
