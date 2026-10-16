"""
Volatility-Adjusted Position Sizing
===================================
Scales risk % by current ATR percentile / regime so size shrinks in high vol
and expands modestly in calm trending conditions.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr_percentile(df: pd.DataFrame, period: int = 14, lookback: int = 100) -> float:
    """Return current ATR/close percentile vs recent history (0-100)."""
    if len(df) < period + lookback:
        return 50.0
    atr = atr_series(df, period)
    atr_pct = (atr / df["close"]) * 100.0
    hist = atr_pct.iloc[-(lookback + 1):-1].dropna()
    if hist.empty or pd.isna(atr_pct.iloc[-1]):
        return 50.0
    current = float(atr_pct.iloc[-1])
    return float((hist < current).mean() * 100.0)


def volatility_risk_multiplier(
    df: pd.DataFrame,
    regime: Optional[str] = None,
    period: int = 14,
    lookback: int = 100,
) -> Tuple[float, str]:
    """
    Returns (multiplier, reason).
    High vol → smaller size; low vol + trending → slightly larger.
    """
    pct = atr_percentile(df, period, lookback)
    mult = 1.0
    reasons = []

    if pct >= 85:
        mult *= 0.45
        reasons.append(f"extreme-vol p{pct:.0f}")
    elif pct >= 70:
        mult *= 0.65
        reasons.append(f"high-vol p{pct:.0f}")
    elif pct <= 25:
        mult *= 1.15
        reasons.append(f"low-vol p{pct:.0f}")
    else:
        reasons.append(f"vol-p{pct:.0f}")

    if regime == "ranging":
        mult *= 0.70
        reasons.append("ranging")
    elif regime == "high_volatility":
        mult *= 0.55
        reasons.append("hv-regime")
    elif regime == "trending":
        mult *= 1.10
        reasons.append("trending")

    mult = max(0.25, min(1.35, mult))
    return round(mult, 3), " | ".join(reasons)


def adjust_risk_pct(base_risk_pct: float, df: pd.DataFrame, regime: Optional[str] = None) -> Tuple[float, str]:
    mult, reason = volatility_risk_multiplier(df, regime)
    return round(base_risk_pct * mult, 4), reason
