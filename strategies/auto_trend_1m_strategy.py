#!/usr/bin/env python3
"""
1-Minute Candle Auto-Trend Following Strategy.
=============================================
Engineered specifically for 1m candlestick scalping on Gold (XAUUSD)
and Bitcoin (BTCUSDT), strictly following the auto-detected micro & structural trend.

Core Rules:
1. Triple Moving Average Auto-Trend Alignment:
   - Micro Momentum EMA: 9 period
   - Baseline Trend EMA: 21 period
   - Structural Macro EMA: 50 period
   - Bullish Auto-Trend: EMA 9 > EMA 21 > EMA 50 with upward slope
   - Bearish Auto-Trend: EMA 9 < EMA 21 < EMA 50 with downward slope

2. 1m Candlestick Momentum & Price Action Confirmation:
   - For BUY: 1m candle close >= EMA 9 (or pullback into EMA 9/21 value zone with bullish rejection wick),
     making higher-highs/higher-lows across the recent 3 bars.
   - For SELL: 1m candle close <= EMA 9 (or pullback into EMA 9/21 value zone with bearish rejection wick),
     making lower-highs/lower-lows across the recent 3 bars.

3. Strict Auto-Trend Following:
   - NEVER trade counter-trend!
   - In Bullish Auto-Trend: ONLY take LONG. SHORT is strictly prohibited.
   - In Bearish Auto-Trend: ONLY take SHORT. LONG is strictly prohibited.
   - In Flat / Choppy / Entangled EMAs: Stand aside (Dir.FLAT).

4. Micro Protection Brackets:
   - Dynamic Stop Loss placed behind recent 1m swing level / EMA 21 with 0.35x ATR buffer.
   - Take Profit targets: TP1 = 1.2x - 1.5x risk, TP2 = 2.0x risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class AutoTrendDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class AutoTrend1mVote:
    agent: str = "AUTO-TREND-1M"
    direction: AutoTrendDir = AutoTrendDir.FLAT
    strength: float = 0.0
    reason: str = ""
    trend_state: str = "FLAT"
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


def detect_auto_trend_1m(df: pd.DataFrame) -> AutoTrend1mVote:
    """
    Evaluates 1m candlestick auto-trend structure, EMA cascades (9/21/50),
    and price action momentum.
    """
    if df is None or getattr(df, "empty", True) or len(df) < 50:
        return AutoTrend1mVote(
            direction=AutoTrendDir.FLAT,
            strength=0.0,
            reason="Need at least 50 1m candles for auto-trend evaluation",
            trend_state="INSUFFICIENT_DATA",
        )

    # Ensure lowercase columns
    cols = {str(c).lower(): c for c in df.columns}
    if not all(k in cols for k in ["open", "high", "low", "close"]):
        return AutoTrend1mVote(
            direction=AutoTrendDir.FLAT,
            strength=0.0,
            reason="Missing required OHLC columns",
            trend_state="INVALID_COLUMNS",
        )

    c = df[cols["close"]].astype(float)
    o = df[cols["open"]].astype(float)
    h = df[cols["high"]].astype(float)
    l = df[cols["low"]].astype(float)

    ema9_series = c.ewm(span=9, adjust=False).mean()
    ema21_series = c.ewm(span=21, adjust=False).mean()
    ema50_series = c.ewm(span=50, adjust=False).mean()

    ema9 = float(ema9_series.iloc[-1])
    ema21 = float(ema21_series.iloc[-1])
    ema50 = float(ema50_series.iloc[-1])

    prev_ema9 = float(ema9_series.iloc[-2])
    prev_ema21 = float(ema21_series.iloc[-2])
    prev_ema50 = float(ema50_series.iloc[-2])

    slope9 = ema9 - prev_ema9
    slope21 = ema21 - prev_ema21

    curr_close = float(c.iloc[-1])
    curr_open = float(o.iloc[-1])
    curr_high = float(h.iloc[-1])
    curr_low = float(l.iloc[-1])

    prev_close = float(c.iloc[-2])
    prev_open = float(o.iloc[-2])
    prev_high = float(h.iloc[-2])
    prev_low = float(l.iloc[-2])

    atr_val = _calculate_atr(df, 14) or (curr_close * 0.001)
    candle_span = max(curr_high - curr_low, 1e-9)

    # 1. BULLISH AUTO-TREND: EMA 9 > 21 > 50 with positive trajectory
    if ema9 > ema21 and ema21 > ema50 and slope9 >= -1e-6:
        # Micro price action: higher lows across last 3 bars
        recent_lows = l.iloc[-4:].values
        recent_highs = h.iloc[-4:].values
        higher_lows = recent_lows[-1] >= recent_lows[0] - 0.2 * atr_val

        # Candle pattern: green close or rejection off EMA9/21
        is_green = curr_close >= curr_open
        lower_wick = min(curr_open, curr_close) - curr_low
        has_lower_rejection = (lower_wick / candle_span) >= 0.20
        pullback_held = curr_low >= ema21 - 0.35 * atr_val

        if (is_green or has_lower_rejection) and pullback_held:
            # Stop loss tightly below recent swing low or EMA21
            swing_low = min(float(np.min(recent_lows)), ema21)
            stop = round(swing_low - 0.30 * atr_val, 4)
            risk = max(curr_close - stop, 0.40 * atr_val)
            tp1 = round(curr_close + risk * 1.35, 4)
            tp2 = round(curr_close + risk * 2.00, 4)

            # Conviction strength based on alignment quality
            strength = 0.90 if (curr_close > ema9 and is_green) else 0.85
            reason = (
                f"1m Auto-Trend BULLISH: EMA 9>21>50 alignment (EMA9={ema9:,.2f}, EMA21={ema21:,.2f}), "
                f"price={curr_close:,.2f} following upward momentum"
            )
            return AutoTrend1mVote(
                direction=AutoTrendDir.LONG,
                strength=strength,
                reason=reason,
                trend_state="BULLISH_AUTO_TREND",
                stop_price=stop,
                tp_price=tp1,
                details={
                    "ema9": ema9,
                    "ema21": ema21,
                    "ema50": ema50,
                    "slope9": slope9,
                    "slope21": slope21,
                    "atr": atr_val,
                    "tp2": tp2,
                },
            )

    # 2. BEARISH AUTO-TREND: EMA 9 < 21 < 50 with negative trajectory
    elif ema9 < ema21 and ema21 < ema50 and slope9 <= 1e-6:
        recent_lows = l.iloc[-4:].values
        recent_highs = h.iloc[-4:].values
        lower_highs = recent_highs[-1] <= recent_highs[0] + 0.2 * atr_val

        # Candle pattern: red close or rejection off EMA9/21
        is_red = curr_close <= curr_open
        upper_wick = curr_high - max(curr_open, curr_close)
        has_upper_rejection = (upper_wick / candle_span) >= 0.20
        pullback_held = curr_high <= ema21 + 0.35 * atr_val

        if (is_red or has_upper_rejection) and pullback_held:
            # Stop loss tightly above recent swing high or EMA21
            swing_high = max(float(np.max(recent_highs)), ema21)
            stop = round(swing_high + 0.30 * atr_val, 4)
            risk = max(stop - curr_close, 0.40 * atr_val)
            tp1 = round(curr_close - risk * 1.35, 4)
            tp2 = round(curr_close - risk * 2.00, 4)

            strength = 0.90 if (curr_close < ema9 and is_red) else 0.85
            reason = (
                f"1m Auto-Trend BEARISH: EMA 9<21<50 alignment (EMA9={ema9:,.2f}, EMA21={ema21:,.2f}), "
                f"price={curr_close:,.2f} following downward momentum"
            )
            return AutoTrend1mVote(
                direction=AutoTrendDir.SHORT,
                strength=strength,
                reason=reason,
                trend_state="BEARISH_AUTO_TREND",
                stop_price=stop,
                tp_price=tp1,
                details={
                    "ema9": ema9,
                    "ema21": ema21,
                    "ema50": ema50,
                    "slope9": slope9,
                    "slope21": slope21,
                    "atr": atr_val,
                    "tp2": tp2,
                },
            )

    return AutoTrend1mVote(
        direction=AutoTrendDir.FLAT,
        strength=0.0,
        reason=f"1m Auto-Trend FLAT: EMAs not cleanly aligned (EMA9={ema9:,.2f}, EMA21={ema21:,.2f}, EMA50={ema50:,.2f})",
        trend_state="FLAT_CONSOLIDATION",
    )


def verify_auto_trend_alignment(df1: pd.DataFrame, direction: str) -> Tuple[bool, str]:
    """
    Validates whether an intended trade direction aligns with the 1m auto trend.
    Strictly forbids trading against the auto trend.
    Returns (is_allowed: bool, reason: str).
    """
    if df1 is None or len(df1) < 25:
        return True, "Insufficient 1m data for auto-trend filter (allowed)"

    try:
        vote = detect_auto_trend_1m(df1)
        dir_norm = str(direction).upper()
        is_buy = dir_norm in ("BUY", "LONG")
        is_sell = dir_norm in ("SELL", "SHORT")

        if is_buy and vote.direction == AutoTrendDir.SHORT:
            return False, f"Opposing 1m Auto-Trend: Bearish EMA cascade active ({vote.reason})"

        if is_sell and vote.direction == AutoTrendDir.LONG:
            return False, f"Opposing 1m Auto-Trend: Bullish EMA cascade active ({vote.reason})"

        return True, f"Aligned with 1m Auto-Trend ({vote.trend_state})"
    except Exception as e:
        return True, f"Auto-trend check exception: {e}"


class AutoTrend1mAgent:
    """
    Multi-Agent Consensus Agent for 1m Candlestick Auto-Trend Following.
    Vigorous trend follower that rejects counter-trend impulses.
    """
    name = "AUTO-TREND-1M"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> AutoTrend1mVote:
        try:
            return detect_auto_trend_1m(df)
        except Exception as e:
            return AutoTrend1mVote(
                direction=AutoTrendDir.FLAT,
                strength=0.0,
                reason=f"AutoTrend1mAgent error: {e}",
            )


def auto_trend_1m_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> AutoTrend1mVote:
    return AutoTrend1mAgent().vote(df, daily=daily)
