"""
Order-Flow / CVD Proxy from OHLCV
=================================
Approximates cumulative volume delta using candle direction * volume.
Useful confirmation when true tick order-flow is unavailable.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd


def signed_volume(df: pd.DataFrame) -> pd.Series:
    """+volume on up closes, -volume on down closes."""
    direction = np.sign(df["close"] - df["open"])
    direction = direction.replace(0, np.nan).ffill().fillna(0)
    return direction * df["volume"].astype(float)


def cvd(df: pd.DataFrame) -> pd.Series:
    return signed_volume(df).cumsum()


def cvd_signal(df: pd.DataFrame, lookback: int = 20) -> Tuple[str, float, str]:
    """
    Returns (direction, strength 0-1, reason).
    direction: LONG / SHORT / FLAT
    """
    if len(df) < lookback + 5:
        return "FLAT", 0.0, "insufficient bars"
    sv = signed_volume(df)
    recent = sv.iloc[-lookback:]
    total = float(recent.sum())
    abs_vol = float(recent.abs().sum()) or 1.0
    imbalance = total / abs_vol  # -1 .. +1

    # Divergence check: price up but CVD down (or reverse)
    price_chg = float(df["close"].iloc[-1] - df["close"].iloc[-lookback])
    cvd_chg = float(cvd(df).iloc[-1] - cvd(df).iloc[-lookback])

    strength = min(1.0, abs(imbalance))
    if imbalance > 0.25 and cvd_chg > 0:
        return "LONG", strength, f"CVD bullish imbalance {imbalance:+.2f}"
    if imbalance < -0.25 and cvd_chg < 0:
        return "SHORT", strength, f"CVD bearish imbalance {imbalance:+.2f}"
    if price_chg > 0 and cvd_chg < 0:
        return "SHORT", 0.55, "Bearish CVD divergence (price up, CVD down)"
    if price_chg < 0 and cvd_chg > 0:
        return "LONG", 0.55, "Bullish CVD divergence (price down, CVD up)"
    return "FLAT", abs(imbalance), f"CVD neutral {imbalance:+.2f}"
