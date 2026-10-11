"""
Structure-Based Trailing Stop
=============================
Trails stop under/above recent swing lows/highs or last opposing structure
instead of pure ATR, capturing more of strong trends.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd


def recent_swing_low(df: pd.DataFrame, lookback: int = 8) -> Optional[float]:
    if len(df) < lookback + 1:
        return None
    return float(df["low"].iloc[-(lookback + 1):-1].min())


def recent_swing_high(df: pd.DataFrame, lookback: int = 8) -> Optional[float]:
    if len(df) < lookback + 1:
        return None
    return float(df["high"].iloc[-(lookback + 1):-1].max())


def structure_trail_stop(
    direction: str,
    entry: float,
    current_stop: float,
    df: pd.DataFrame,
    atr_value: float = 0.0,
    buffer_atr: float = 0.15,
    lookback: int = 8,
) -> Tuple[float, str]:
    """
    Propose a new stop based on market structure.
    Only moves stop in the favorable direction (never widens risk).
    """
    direction = direction.upper()
    buffer = atr_value * buffer_atr if atr_value > 0 else abs(entry) * 0.0003

    if direction in ("LONG", "BUY", "1"):
        swing = recent_swing_low(df, lookback)
        if swing is None:
            return current_stop, "no swing low"
        candidate = swing - buffer
        if candidate > current_stop and candidate < entry * 1.5:
            return round(candidate, 6), f"structure trail under swing low {swing:.2f}"
        return current_stop, "structure not tighter"

    # SHORT
    swing = recent_swing_high(df, lookback)
    if swing is None:
        return current_stop, "no swing high"
    candidate = swing + buffer
    if candidate < current_stop and candidate > entry * 0.5:
        return round(candidate, 6), f"structure trail above swing high {swing:.2f}"
    return current_stop, "structure not tighter"
