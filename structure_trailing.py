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
    df: Optional[pd.DataFrame] = None,
    atr_value: float = 0.0,
    buffer_atr: float = 0.15,
    lookback: int = 8,
    current_price: Optional[float] = None,
    recent_lows: Optional[list] = None,
    recent_highs: Optional[list] = None,
) -> Tuple[float, str]:
    """
    Propose a new stop based on market structure (swing lows/highs).
    Only moves stop in the favorable direction (never widens risk).
    Supports both pre-BE risk reduction AND locking in profit above entry (for LONG)
    or below entry (for SHORT).
    """
    direction = str(direction).upper()
    try:
        entry = float(entry)
        current_stop = float(current_stop)
    except (TypeError, ValueError):
        return current_stop, "invalid entry/stop"

    buffer = atr_value * buffer_atr if atr_value and atr_value > 0 else abs(entry) * 0.0003

    if current_price is None and df is not None and len(df) and "close" in df.columns:
        try:
            current_price = float(df["close"].iloc[-1])
        except Exception:
            current_price = None

    if direction in ("LONG", "BUY", "1"):
        swing = None
        if df is not None and len(df) >= lookback + 1 and "low" in df.columns:
            swing = recent_swing_low(df, lookback)
        elif recent_lows and len(recent_lows) >= 3:
            window = recent_lows[-lookback:] if len(recent_lows) > lookback else recent_lows
            swing = min(window[:-1]) if len(window) > 1 else min(window)

        if swing is None:
            return current_stop, "no swing low"

        candidate = swing - buffer
        # Ensure trailing stop stays safely below current market price
        if current_price is not None and candidate >= current_price:
            candidate = current_price - max(buffer, abs(entry) * 0.0005)

        # Must strictly ratchet in favorable direction (never widen risk)
        if candidate > current_stop:
            if candidate >= entry:
                profit = candidate - entry
                return round(candidate, 6), f"structure trail locked profit +{profit:,.2f} under swing low {swing:,.2f}"
            return round(candidate, 6), f"structure trail tightened under swing low {swing:,.2f}"
        return current_stop, "structure not tighter"

    if direction in ("SHORT", "SELL", "-1"):
        swing = None
        if df is not None and len(df) >= lookback + 1 and "high" in df.columns:
            swing = recent_swing_high(df, lookback)
        elif recent_highs and len(recent_highs) >= 3:
            window = recent_highs[-lookback:] if len(recent_highs) > lookback else recent_highs
            swing = max(window[:-1]) if len(window) > 1 else max(window)

        if swing is None:
            return current_stop, "no swing high"

        candidate = swing + buffer
        # Ensure trailing stop stays safely above current market price
        if current_price is not None and candidate <= current_price:
            candidate = current_price + max(buffer, abs(entry) * 0.0005)

        # Must strictly ratchet in favorable direction (never widen risk)
        if candidate < current_stop:
            if candidate <= entry:
                profit = entry - candidate
                return round(candidate, 6), f"structure trail locked profit +{profit:,.2f} above swing high {swing:,.2f}"
            return round(candidate, 6), f"structure trail tightened above swing high {swing:,.2f}"
        return current_stop, "structure not tighter"

    return current_stop, f"unknown direction {direction}"


def hybrid_trail_stop(
    direction: str,
    entry: float,
    current_stop: float,
    highest_price: float,
    lowest_price: float,
    current_price: float,
    df: Optional[pd.DataFrame] = None,
    atr_value: float = 0.0,
    trail_atr_mult: float = 2.0,
    trail_r_mult: float = 1.5,
    initial_risk: float = 0.0,
    lookback: int = 8,
    recent_lows: Optional[list] = None,
    recent_highs: Optional[list] = None,
) -> Tuple[float, str]:
    """
    Hybrid Trailing Stop combining swing structure and Chandelier ATR trail.
    Selects the most protective (tightest) valid trailing level without choking the trade.
    """
    struct_stop, struct_reason = structure_trail_stop(
        direction=direction,
        entry=entry,
        current_stop=current_stop,
        df=df,
        atr_value=atr_value,
        lookback=lookback,
        current_price=current_price,
        recent_lows=recent_lows,
        recent_highs=recent_highs,
    )

    is_long = str(direction).upper() in ("LONG", "BUY", "1")
    dist = (atr_value * trail_atr_mult) if (atr_value and atr_value > 0) else (initial_risk * trail_r_mult)
    if dist <= 0:
        dist = abs(entry) * 0.003

    best_stop = current_stop
    best_reason = struct_reason

    if is_long:
        chandelier_stop = highest_price - dist
        if current_price and chandelier_stop >= current_price:
            chandelier_stop = current_price - (atr_value * 0.5 if atr_value else abs(entry) * 0.001)

        # Compare structure vs chandelier stop
        candidates = [s for s in (struct_stop, chandelier_stop) if s > current_stop]
        if candidates:
            best_stop = max(candidates)
            if best_stop == chandelier_stop:
                best_reason = f"chandelier ATR trail ({dist:,.2f} dist from peak {highest_price:,.2f})"
            else:
                best_reason = struct_reason
    else:
        chandelier_stop = lowest_price + dist
        if current_price and chandelier_stop <= current_price:
            chandelier_stop = current_price + (atr_value * 0.5 if atr_value else abs(entry) * 0.001)

        candidates = [s for s in (struct_stop, chandelier_stop) if s < current_stop]
        if candidates:
            best_stop = min(candidates)
            if best_stop == chandelier_stop:
                best_reason = f"chandelier ATR trail ({dist:,.2f} dist from trough {lowest_price:,.2f})"
            else:
                best_reason = struct_reason

    return round(best_stop, 6), best_reason
