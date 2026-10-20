#!/usr/bin/env python3
"""
Asian Range & London Open Breakout Module.
=========================================
Calculates the Asian Session consolidation range (00:00 - 07:00 UTC)
and detects two primary institutional London Open (07:00 - 10:00 UTC) setups:
1. London Judas Swing: False breakout sweeping Asian High/Low, reverting inside.
2. London Trend Expansion: Energetic displacement breakout targeting 1.5x - 2.0x Asian Range.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class AsianDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class AsianRange:
    high: float
    low: float
    mid: float
    range_pts: float
    range_pct: float
    valid: bool = False


@dataclass
class AsianVote:
    agent: str = "ASIAN-BREAKOUT"
    direction: AsianDir = AsianDir.FLAT
    strength: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def compute_asian_range(df: pd.DataFrame, target_date: Optional[datetime.date] = None) -> AsianRange:
    """Computes the Asian Range (00:00 to 07:00 UTC) from intraday bars."""
    if df is None or len(df) < 10:
        return AsianRange(0.0, 0.0, 0.0, 0.0, 0.0, valid=False)

    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("time", "timestamp", "datetime", "date"):
            if col in df.columns:
                try:
                    df = df.set_index(pd.to_datetime(df[col]))
                    break
                except Exception:
                    pass
        if not isinstance(df.index, pd.DatetimeIndex):
            return AsianRange(0.0, 0.0, 0.0, 0.0, 0.0, valid=False)

    tz_index = df.index.tz_convert(timezone.utc) if df.index.tz is not None else df.index.tz_localize(timezone.utc)
    if target_date is None:
        target_date = tz_index[-1].date()

    # Filter bars between 00:00 and 07:00 UTC for target_date
    mask = (tz_index.date == target_date) & (tz_index.time >= time(0, 0)) & (tz_index.time < time(7, 0))
    asian_df = df.iloc[mask]

    if len(asian_df) < 5:
        return AsianRange(0.0, 0.0, 0.0, 0.0, 0.0, valid=False)

    high = float(asian_df["high"].max())
    low = float(asian_df["low"].min())
    mid = (high + low) / 2.0
    range_pts = high - low
    range_pct = (range_pts / max(low, 1e-9)) * 100.0

    return AsianRange(
        high=high,
        low=low,
        mid=mid,
        range_pts=round(range_pts, 3),
        range_pct=round(range_pct, 3),
        valid=range_pts > 0,
    )


def detect_asian_london_setup(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Evaluates London Open (07:00 - 10:00 UTC) setups against the Asian Range."""
    if df is None or len(df) < 20:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("time", "timestamp", "datetime", "date"):
            if col in df.columns:
                try:
                    df = df.set_index(pd.to_datetime(df[col]))
                    break
                except Exception:
                    pass
        if not isinstance(df.index, pd.DatetimeIndex):
            return None

    tz_index = df.index.tz_convert(timezone.utc) if df.index.tz is not None else df.index.tz_localize(timezone.utc)
    now_dt = tz_index[-1]
    now_t = now_dt.time()

    # Only active during London Open Killzone: 07:00 to 10:30 UTC
    if not (time(7, 0) <= now_t <= time(10, 30)):
        return None

    asian = compute_asian_range(df, target_date=now_dt.date())
    if not asian.valid or asian.range_pct < 0.08 or asian.range_pct > 3.0:
        return None

    curr = df.iloc[-1]
    curr_o = float(curr["open"])
    curr_h = float(curr["high"])
    curr_l = float(curr["low"])
    curr_c = float(curr["close"])

    # (1) London Judas Swing (Sweep & Reject):
    # Bullish Judas: Spiked below Asian Low by <= 0.4%, but closed back above Asian Low
    if curr_l < asian.low and curr_c > asian.low:
        overshoot = (asian.low - curr_l) / asian.low * 100.0
        if 0.01 <= overshoot <= 0.50:
            target = asian.mid
            return {
                "pattern": "LONDON_JUDAS_BULLISH",
                "direction": AsianDir.LONG,
                "strength": 0.85,
                "entry": curr_c,
                "stop": curr_l - (asian.range_pts * 0.15),
                "tp1": target,
                "reason": f"London Judas Swing: Swept Asian Low ({asian.low:.2f}) by {overshoot:.2f}% and rejected upward",
            }

    # Bearish Judas: Spiked above Asian High by <= 0.4%, but closed back below Asian High
    if curr_h > asian.high and curr_c < asian.high:
        overshoot = (curr_h - asian.high) / asian.high * 100.0
        if 0.01 <= overshoot <= 0.50:
            target = asian.mid
            return {
                "pattern": "LONDON_JUDAS_BEARISH",
                "direction": AsianDir.SHORT,
                "strength": 0.85,
                "entry": curr_c,
                "stop": curr_h + (asian.range_pts * 0.15),
                "tp1": target,
                "reason": f"London Judas Swing: Swept Asian High ({asian.high:.2f}) by {overshoot:.2f}% and rejected downward",
            }

    # (2) London Trend Expansion Breakout:
    # Bullish Breakout: Candle closed firmly above Asian High with displacement
    if curr_c > asian.high * 1.0008:
        tp = asian.high + (asian.range_pts * 1.5)
        return {
            "pattern": "LONDON_EXPANSION_BULLISH",
            "direction": AsianDir.LONG,
            "strength": 0.80,
            "entry": curr_c,
            "stop": asian.mid,
            "tp1": tp,
            "reason": f"London Trend Expansion: Broke above Asian High ({asian.high:.2f}), targeting 1.5x extension ({tp:.2f})",
        }

    # Bearish Breakout: Candle closed firmly below Asian Low with displacement
    if curr_c < asian.low * 0.9992:
        tp = asian.low - (asian.range_pts * 1.5)
        return {
            "pattern": "LONDON_EXPANSION_BEARISH",
            "direction": AsianDir.SHORT,
            "strength": 0.80,
            "entry": curr_c,
            "stop": asian.mid,
            "tp1": tp,
            "reason": f"London Trend Expansion: Broke below Asian Low ({asian.low:.2f}), targeting 1.5x extension ({tp:.2f})",
        }

    return None


class AsianBreakoutAgent:
    """Agent evaluating Asian session range liquidity and London expansion setups."""
    name = "ASIAN-BREAKOUT"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> AsianVote:
        if df is None or len(df) < 20:
            return AsianVote(agent=self.name, direction=AsianDir.FLAT, strength=0.0, reason="Need more bars")

        setup = detect_asian_london_setup(df)
        if not setup:
            return AsianVote(agent=self.name, direction=AsianDir.FLAT, strength=0.0, reason="No active Asian/London breakout")

        return AsianVote(
            agent=self.name,
            direction=setup["direction"],
            strength=float(setup.get("strength", 0.80)),
            reason=setup.get("reason", "Asian breakout active"),
            details=setup,
        )
