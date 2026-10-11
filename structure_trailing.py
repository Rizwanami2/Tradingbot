"""
Structure-Based Trailing Stop
=============================
Trails stop under/above recent swing lows/highs.
Never widens risk; stop always stays on the correct side of entry.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd


def recent_swing_low(df: pd.DataFrame, lookback: int = 8) -> Optional[float]:
    if df is None or len(df) < lookback + 1:
        return None
    if "low" not in df.columns:
        return None
    return float(df["low"].iloc[-(lookback + 1):-1].min())


def recent_swing_high(df: pd.DataFrame, lookback: int = 8) -> Optional[float]:
    if df is None or len(df) < lookback + 1:
        return None
    if "high" not in df.columns:
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
    LONG: stop must stay strictly below entry.
    SHORT: stop must stay strictly above entry.
    """
    direction = str(direction).upper()
    try:
        entry = float(entry)
        current_stop = float(current_stop)
    except (TypeError, ValueError):
        return current_stop, "invalid entry/stop"

    buffer = atr_value * buffer_atr if atr_value and atr_value > 0 else abs(entry) * 0.0003

    if direction in ("LONG", "BUY", "1"):
        swing = recent_swing_low(df, lookback)
        if swing is None:
            return current_stop, "no swing low"
        candidate = swing - buffer
        # Must be tighter than current AND still below entry
        if candidate > current_stop and candidate < entry:
            return round(candidate, 6), f"structure trail under swing low {swing:.2f}"
        return current_stop, "structure not tighter"

    if direction in ("SHORT", "SELL", "-1"):
        swing = recent_swing_high(df, lookback)
        if swing is None:
            return current_stop, "no swing high"
        candidate = swing + buffer
        # Must be tighter than current AND still above entry
        if candidate < current_stop and candidate > entry:
            return round(candidate, 6), f"structure trail above swing high {swing:.2f}"
        return current_stop, "structure not tighter"

    return current_stop, f"unknown direction {direction}"
