#!/usr/bin/env python3
"""
Institutional Mean Reversion & VWAP Volatility Band Strategy.
============================================================
Quantitative trading rules:
1. Dynamic Volume-Weighted Average Price (VWAP) calculation with standard deviation bands (±1.5σ, ±2.0σ).
2. Relative Volume (RVOL) exhaustion detection on statistical band touches.
3. Candlestick rejection analysis (hammer / shooting star pinbars at band extremes).
4. Volatility squeeze breakout detection when band compression expands with high volume.

Produces an MRVote compatible with the multi-agent Arbiter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


class MRDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class MRVote:
    agent: str
    direction: MRDir
    strength: float
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _safe_f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


def calc_vwap_bands(df: pd.DataFrame, window: int = 100) -> Dict[str, float]:
    """Calculate rolling VWAP and standard deviation bands safely."""
    out = {
        "vwap": 0.0,
        "upper_1": 0.0,
        "lower_1": 0.0,
        "upper_2": 0.0,
        "lower_2": 0.0,
        "bandwidth": 0.0,
        "std": 0.0,
    }
    if df is None or len(df) < 5:
        return out

    try:
        sub = df.iloc[-min(len(df), window):].copy()
        high = sub["high"].astype(float).values
        low = sub["low"].astype(float).values
        close = sub["close"].astype(float).values
        vol = sub["volume"].astype(float).values if "volume" in sub else np.ones(len(sub))

        # Handle zero or flat volume fallback
        if np.sum(vol) <= 0 or not np.all(np.isfinite(vol)):
            vol = np.ones(len(sub))

        typ_price = (high + low + close) / 3.0
        cum_vol = np.cumsum(vol)
        cum_vp = np.cumsum(typ_price * vol)

        safe_cum_vol = np.where(cum_vol <= 0, 1.0, cum_vol)
        vwap_series = cum_vp / safe_cum_vol
        vwap = float(vwap_series[-1])

        # Rolling standard deviation from VWAP
        sq_diff = (typ_price - vwap_series) ** 2
        cum_sq = np.cumsum(vol * sq_diff)
        variance = cum_sq[-1] / safe_cum_vol[-1]
        std = math.sqrt(max(0.0, float(variance)))

        # Fallback to ATR-like std if variance is 0
        if std <= 1e-8:
            std = float((np.max(high) - np.min(low)) * 0.15)

        upper_1 = vwap + 1.5 * std
        lower_1 = vwap - 1.5 * std
        upper_2 = vwap + 2.0 * std
        lower_2 = vwap - 2.0 * std
        bandwidth = ((upper_2 - lower_2) / vwap * 100.0) if vwap > 0 else 0.0

        return {
            "vwap": round(vwap, 4),
            "upper_1": round(upper_1, 4),
            "lower_1": round(lower_1, 4),
            "upper_2": round(upper_2, 4),
            "lower_2": round(lower_2, 4),
            "bandwidth": round(bandwidth, 4),
            "std": round(std, 4),
        }
    except Exception:
        return out


def calc_rvol(df: pd.DataFrame, period: int = 20) -> float:
    """Relative volume of the most recent bar compared to the recent rolling average."""
    if df is None or len(df) < period + 1 or "volume" not in df:
        return 1.0
    try:
        v = df["volume"].astype(float).values
        last_vol = v[-1]
        avg_vol = float(np.mean(v[-period - 1:-1]))
        if avg_vol > 0:
            return round(float(last_vol / avg_vol), 2)
        return 1.0
    except Exception:
        return 1.0


def calc_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """Safe Wilder RSI calculation."""
    if df is None or len(df) < period + 2:
        return 50.0
    try:
        closes = df["close"].astype(float).values
        diffs = np.diff(closes[-(period + 20):])
        if len(diffs) < period:
            return 50.0
        gains = np.where(diffs > 0, diffs, 0.0)
        losses = np.where(diffs < 0, -diffs, 0.0)
        avg_gain = float(np.mean(gains[-period:]))
        avg_loss = float(np.mean(losses[-period:]))
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(float(rsi), 2)
    except Exception:
        return 50.0


def detect_pinbar(df: pd.DataFrame) -> Tuple[bool, bool]:
    """Detect bullish hammer or bearish shooting star rejection pinbars on the latest bar."""
    if df is None or len(df) < 1:
        return False, False
    try:
        last = df.iloc[-1]
        open_p = _safe_f(last["open"])
        high_p = _safe_f(last["high"])
        low_p = _safe_f(last["low"])
        close_p = _safe_f(last["close"])
        bar_range = high_p - low_p
        if bar_range <= 1e-8:
            return False, False

        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        # Bullish hammer / rejection: long lower wick (>= 55% of range), close near high
        is_bull_pin = (
            lower_wick >= 0.55 * bar_range
            and upper_wick <= 0.25 * bar_range
            and (close_p - low_p) / bar_range >= 0.60
        )

        # Bearish shooting star / rejection: long upper wick (>= 55% of range), close near low
        is_bear_pin = (
            upper_wick >= 0.55 * bar_range
            and lower_wick <= 0.25 * bar_range
            and (high_p - close_p) / bar_range >= 0.60
        )

        return bool(is_bull_pin), bool(is_bear_pin)
    except Exception:
        return False, False


class MeanReversionVWAPAgent:
    """
    Mean Reversion & VWAP Volatility Band Agent.
    Evaluates statistical deviations from equilibrium to capture high-probability
    reversion and squeeze-expansion moves.
    """

    def __init__(self, name: str = "MEAN-REVERSION"):
        self.name = name

    def vote(
        self,
        df: Optional[pd.DataFrame],
        daily: Optional[pd.DataFrame] = None,
    ) -> MRVote:
        if df is None or getattr(df, "empty", True) or len(df) < 30:
            return MRVote(self.name, MRDir.FLAT, 0.0, "Need more bars")

        cols = [c.lower() for c in df.columns]
        req = ["open", "high", "low", "close"]
        if not all(r in cols for r in req):
            return MRVote(self.name, MRDir.FLAT, 0.0, "Missing OHLC columns")

        bands = calc_vwap_bands(df, window=100)
        vwap = bands["vwap"]
        upper_1 = bands["upper_1"]
        lower_1 = bands["lower_1"]
        upper_2 = bands["upper_2"]
        lower_2 = bands["lower_2"]

        if vwap <= 0 or upper_1 <= 0 or lower_1 <= 0:
            return MRVote(self.name, MRDir.FLAT, 0.0, "Degraded VWAP computation")

        last = df.iloc[-1]
        close_p = _safe_f(last["close"])
        high_p = _safe_f(last["high"])
        low_p = _safe_f(last["low"])

        rvol = calc_rvol(df, period=20)
        rsi = calc_rsi(df, period=14)
        is_bull_pin, is_bear_pin = detect_pinbar(df)

        details = {
            "vwap": vwap,
            "upper_1": upper_1,
            "lower_1": lower_1,
            "upper_2": upper_2,
            "lower_2": lower_2,
            "rvol": rvol,
            "rsi": rsi,
            "bandwidth": bands["bandwidth"],
        }

        # Case 1: Extreme statistical oversold (touching or below lower 2.0 SD band)
        if low_p <= lower_2 or (close_p <= lower_1 and (is_bull_pin or rsi < 32)):
            strength = 0.70
            reasons = ["VWAP oversold"]
            if low_p <= lower_2:
                strength += 0.12
                reasons.append("extreme -2.0σ touch")
            if is_bull_pin:
                strength += 0.10
                reasons.append("bull pinbar rejection")
            if rvol >= 1.2:
                strength += 0.05
                reasons.append(f"volume spike (RVOL={rvol:.1f}x)")
            if rsi < 30:
                reasons.append(f"RSI oversold ({rsi:.0f})")

            return MRVote(
                agent=self.name,
                direction=MRDir.LONG,
                strength=min(0.95, round(strength, 2)),
                reason=", ".join(reasons),
                details=details,
            )

        # Case 2: Extreme statistical overbought (touching or above upper 2.0 SD band)
        if high_p >= upper_2 or (close_p >= upper_1 and (is_bear_pin or rsi > 68)):
            strength = 0.70
            reasons = ["VWAP overbought"]
            if high_p >= upper_2:
                strength += 0.12
                reasons.append("extreme +2.0σ touch")
            if is_bear_pin:
                strength += 0.10
                reasons.append("bear pinbar rejection")
            if rvol >= 1.2:
                strength += 0.05
                reasons.append(f"volume spike (RVOL={rvol:.1f}x)")
            if rsi > 70:
                reasons.append(f"RSI overbought ({rsi:.0f})")

            return MRVote(
                agent=self.name,
                direction=MRDir.SHORT,
                strength=min(0.95, round(strength, 2)),
                reason=", ".join(reasons),
                details=details,
            )

        # Case 3: Volatility squeeze expansion (breakout after narrow bandwidth)
        if bands["bandwidth"] < 1.0 and rvol >= 1.8:
            if close_p > upper_1:
                return MRVote(
                    agent=self.name,
                    direction=MRDir.LONG,
                    strength=0.72,
                    reason=f"Volatility squeeze expansion LONG (RVOL={rvol:.1f}x, BW={bands['bandwidth']:.2f}%)",
                    details=details,
                )
            elif close_p < lower_1:
                return MRVote(
                    agent=self.name,
                    direction=MRDir.SHORT,
                    strength=0.72,
                    reason=f"Volatility squeeze expansion SHORT (RVOL={rvol:.1f}x, BW={bands['bandwidth']:.2f}%)",
                    details=details,
                )

        return MRVote(
            agent=self.name,
            direction=MRDir.FLAT,
            strength=0.0,
            reason=f"Inside equilibrium VWAP bands (${lower_1:,.2f} - ${upper_1:,.2f})",
            details=details,
        )


def mean_reversion_levels(df: pd.DataFrame) -> Dict[str, float]:
    """Provides key VWAP levels for trade planning and visual indicators."""
    bands = calc_vwap_bands(df)
    return {
        "vwap": bands["vwap"],
        "vwap_upper_1": bands["upper_1"],
        "vwap_lower_1": bands["lower_1"],
        "vwap_upper_2": bands["upper_2"],
        "vwap_lower_2": bands["lower_2"],
        "vwap_bandwidth": bands["bandwidth"],
    }
