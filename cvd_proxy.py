"""
Order-Flow / CVD Proxy from OHLCV
=================================
Approximates cumulative volume delta using candle direction * volume.
Handles missing volume safely.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def _volume_col(df: pd.DataFrame) -> pd.Series:
    if "volume" in df.columns:
        vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
        if float(vol.abs().sum()) > 0:
            return vol
    # Proxy volume from range when real volume missing
    return (df["high"].astype(float) - df["low"].astype(float)).clip(lower=0) + 1e-8


def signed_volume(df: pd.DataFrame) -> pd.Series:
    """+volume on up closes, -volume on down closes."""
    direction = np.sign(df["close"].astype(float) - df["open"].astype(float))
    direction = pd.Series(direction, index=df.index).replace(0, np.nan).ffill().fillna(0)
    return direction * _volume_col(df)


def cvd(df: pd.DataFrame) -> pd.Series:
    return signed_volume(df).cumsum()


def cvd_signal(df: pd.DataFrame, lookback: int = 20) -> Tuple[str, float, str]:
    """
    Returns (direction, strength 0-1, reason).
    direction: LONG / SHORT / FLAT
    """
    if df is None or len(df) < lookback + 5:
        return "FLAT", 0.0, "insufficient bars"
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(c.lower() for c in df.columns)) and not required.issubset(df.columns):
        # try lowercase normalize
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
    if not required.issubset(df.columns):
        return "FLAT", 0.0, "missing OHLC columns"

    sv = signed_volume(df)
    recent = sv.iloc[-lookback:]
    total = float(recent.sum())
    abs_vol = float(recent.abs().sum()) or 1.0
    imbalance = total / abs_vol  # -1 .. +1

    price_chg = float(df["close"].iloc[-1] - df["close"].iloc[-lookback])
    cvd_series = cvd(df)
    cvd_chg = float(cvd_series.iloc[-1] - cvd_series.iloc[-lookback])

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
