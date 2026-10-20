#!/usr/bin/env python3
"""
EMA Ribbon (8/21/55) Trend-Pullback Micro-Scalp Strategy.
=========================================================
Engineered specifically for high-win-rate 1m & 5m scalping on Gold (XAUUSD)
and Bitcoin (BTCUSDT).

Core Principles:
1. Triple EMA Ribbon Structure:
   - Fast Signal EMA: 8 period
   - Pullback Value Anchor EMA: 21 period
   - Macro Trend Base EMA: 55 period

2. Ribbon Momentum Alignment:
   - Bullish Trend: EMA 8 > EMA 21 > EMA 55 (Ribbon fanning upward)
   - Bearish Trend: EMA 8 < EMA 21 < EMA 55 (Ribbon fanning downward)

3. Pullback into the "Value Pocket":
   - Does NOT chase overextended prices.
   - Waits for price to retrace into the golden pocket between EMA 8 and EMA 21.

4. Micro-Rejection Trigger:
   - Bullish Scalp: Low dips into the value pocket and rejects upward, candle closes
     firmly green back near/above EMA 8.
   - Bearish Scalp: High enters the value pocket and rejects downward, candle closes
     firmly red back near/below EMA 8.

5. Tight Micro Protection:
   - Stop Loss placed tightly behind EMA 21 + small buffer.
   - Micro Target: 1.2x to 1.6x risk-to-reward ratio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class RibbonDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class RibbonVote:
    agent: str = "RIBBON-SCALP"
    direction: RibbonDir = RibbonDir.FLAT
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


def detect_ribbon_scalp(df: pd.DataFrame) -> Optional[RibbonVote]:
    """
    Detects high-probability 8/21/55 EMA ribbon pullback scalps.
    """
    if df is None or len(df) < 55:
        return None

    c = df["close"].astype(float)
    ema8_series = c.ewm(span=8, adjust=False).mean()
    ema21_series = c.ewm(span=21, adjust=False).mean()
    ema55_series = c.ewm(span=55, adjust=False).mean()

    ema8 = float(ema8_series.iloc[-1])
    ema21 = float(ema21_series.iloc[-1])
    ema55 = float(ema55_series.iloc[-1])

    prev_ema8 = float(ema8_series.iloc[-2])
    prev_ema21 = float(ema21_series.iloc[-2])
    prev_ema55 = float(ema55_series.iloc[-2])

    atr_val = _calculate_atr(df, 14) or (_safe_float(df["close"].iloc[-1]) * 0.0015)
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])
    po, ph, pl, pc = map(_safe_float, [prev["open"], prev["high"], prev["low"], prev["close"]])
    candle_span = max(ch - cl, 1e-9)

    # 1. BULLISH RIBBON SCALP (8 > 21 > 55)
    if ema8 > ema21 and ema21 > ema55:
        # Check if price recently pulled back into the value zone [ema21, ema8]
        retest_low = min(cl, pl)
        in_value_pocket = (retest_low <= ema8 + 0.2 * atr_val) and (retest_low >= ema21 - 0.4 * atr_val)

        if in_value_pocket and cc >= ema8 - 0.2 * atr_val:
            lower_wick = min(co, cc) - cl
            lower_wick_ratio = lower_wick / candle_span
            # Bullish bounce reaction
            if lower_wick_ratio >= 0.18 or cc > co:
                strength = 0.90 if cc > ema8 else 0.85
                stop = round(min(retest_low, ema21) - 0.35 * atr_val, 4)
                risk_pts = max(cc - stop, 0.4 * atr_val)
                tp1 = round(cc + risk_pts * 1.5, 4)
                tp2 = round(cc + risk_pts * 2.2, 4)
                reason = (
                    f"EMA Ribbon Bullish Scalp (8>21>55): Retested 21-EMA value zone @ {retest_low:,.2f}, "
                    f"absorbed with {lower_wick_ratio:.0%} lower wick, closing @ {cc:,.2f}"
                )
                return RibbonVote(
                    agent="RIBBON-SCALP",
                    direction=RibbonDir.LONG,
                    strength=strength,
                    reason=reason,
                    pattern="BULLISH_RIBBON_PULLBACK",
                    stop_price=stop,
                    tp_price=tp1,
                    details={"ema8": ema8, "ema21": ema21, "ema55": ema55, "retest_price": retest_low, "tp2": tp2},
                )

    # 2. BEARISH RIBBON SCALP (8 < 21 < 55)
    elif ema8 < ema21 and ema21 < ema55:
        retest_high = max(ch, ph)
        in_value_pocket = (retest_high >= ema8 - 0.2 * atr_val) and (retest_high <= ema21 + 0.4 * atr_val)

        if in_value_pocket and cc <= ema8 + 0.2 * atr_val:
            upper_wick = ch - max(co, cc)
            upper_wick_ratio = upper_wick / candle_span
            if upper_wick_ratio >= 0.18 or cc < co:
                strength = 0.90 if cc < ema8 else 0.85
                stop = round(max(retest_high, ema21) + 0.35 * atr_val, 4)
                risk_pts = max(stop - cc, 0.4 * atr_val)
                tp1 = round(cc - risk_pts * 1.5, 4)
                tp2 = round(cc - risk_pts * 2.2, 4)
                reason = (
                    f"EMA Ribbon Bearish Scalp (8<21<55): Retested 21-EMA value zone @ {retest_high:,.2f}, "
                    f"rejected with {upper_wick_ratio:.0%} upper wick, closing @ {cc:,.2f}"
                )
                return RibbonVote(
                    agent="RIBBON-SCALP",
                    direction=RibbonDir.SHORT,
                    strength=strength,
                    reason=reason,
                    pattern="BEARISH_RIBBON_PULLBACK",
                    stop_price=stop,
                    tp_price=tp1,
                    details={"ema8": ema8, "ema21": ema21, "ema55": ema55, "retest_price": retest_high, "tp2": tp2},
                )

    return None


class RibbonScalpAgent:
    """
    EMA Ribbon Trend-Pullback Scalper Agent.
    Specialized in fast micro-pullbacks into the 8/21 value pocket in confirmed
    institutional momentum trends.
    """
    name = "RIBBON-SCALP"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> RibbonVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 55:
                return RibbonVote(self.name, RibbonDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return RibbonVote(self.name, RibbonDir.FLAT, 0.0, "Missing OHLC columns")

            setup = detect_ribbon_scalp(df)
            if setup is not None and setup.direction != RibbonDir.FLAT:
                return setup

            return RibbonVote(self.name, RibbonDir.FLAT, 0.0, "No active ribbon pullback scalp setup")
        except Exception as e:
            return RibbonVote(self.name, RibbonDir.FLAT, 0.0, f"Ribbon evaluation note: {type(e).__name__}")


def ribbon_scalp_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> RibbonVote:
    return RibbonScalpAgent().vote(df, daily=daily)
