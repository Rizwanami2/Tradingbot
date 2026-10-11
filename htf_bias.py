"""
Higher-Timeframe Bias Filter
============================
Requires daily / 4H / 1H structure alignment before allowing lower-TF entries.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple

import pandas as pd


class Bias(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


def _ema_bias(df: pd.DataFrame, fast: int = 21, slow: int = 50) -> Bias:
    if df is None or len(df) < slow + 2:
        return Bias.NEUTRAL
    close = df["close"].astype(float)
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    if float(ema_f.iloc[-1]) > float(ema_s.iloc[-1]) and float(close.iloc[-1]) > float(ema_f.iloc[-1]):
        return Bias.BULLISH
    if float(ema_f.iloc[-1]) < float(ema_s.iloc[-1]) and float(close.iloc[-1]) < float(ema_f.iloc[-1]):
        return Bias.BEARISH
    return Bias.NEUTRAL


def _structure_bias(df: pd.DataFrame, lb: int = 10) -> Bias:
    if df is None or len(df) < lb + 2:
        return Bias.NEUTRAL
    recent = df.iloc[-lb:]
    if float(recent["close"].iloc[-1]) > float(recent["high"].iloc[:-1].max()):
        return Bias.BULLISH
    if float(recent["close"].iloc[-1]) < float(recent["low"].iloc[:-1].min()):
        return Bias.BEARISH
    return Bias.NEUTRAL


def htf_bias(data: Dict[str, pd.DataFrame]) -> Tuple[Bias, str]:
    """
    Combine 1h + daily bias.
    data keys expected: '1h', 'daily' (optional '4h').
    """
    votes = []
    reasons = []

    for tf, weight in [("daily", 2), ("1h", 1), ("4h", 1)]:
        df = data.get(tf)
        if df is None or df.empty:
            continue
        b1 = _ema_bias(df)
        b2 = _structure_bias(df)
        if b1 != Bias.NEUTRAL:
            votes.extend([b1] * weight)
            reasons.append(f"{tf}-EMA:{b1.name}")
        if b2 != Bias.NEUTRAL:
            votes.extend([b2] * weight)
            reasons.append(f"{tf}-STR:{b2.name}")

    if not votes:
        return Bias.NEUTRAL, "no HTF data"

    bull = sum(1 for v in votes if v == Bias.BULLISH)
    bear = sum(1 for v in votes if v == Bias.BEARISH)
    if bull > bear and bull >= 2:
        return Bias.BULLISH, " | ".join(reasons)
    if bear > bull and bear >= 2:
        return Bias.BEARISH, " | ".join(reasons)
    return Bias.NEUTRAL, "mixed HTF | " + " | ".join(reasons)


def allows_direction(desired: str, data: Dict[str, pd.DataFrame], strict: bool = True) -> Tuple[bool, str]:
    """Return whether lower-TF direction is allowed by HTF bias."""
    bias, reason = htf_bias(data)
    desired = desired.upper()
    if bias == Bias.NEUTRAL:
        if strict:
            return False, f"HTF neutral – blocked ({reason})"
        return True, f"HTF neutral – allowed (non-strict) ({reason})"
    if desired in ("LONG", "BUY", "1") and bias == Bias.BULLISH:
        return True, f"HTF bullish aligned ({reason})"
    if desired in ("SHORT", "SELL", "-1") and bias == Bias.BEARISH:
        return True, f"HTF bearish aligned ({reason})"
    return False, f"HTF conflict: want {desired}, bias={bias.name} ({reason})"
