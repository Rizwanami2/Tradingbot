#!/usr/bin/env python3
"""
Volatility Squeeze & Momentum Burst Scalp Strategy (TTM Squeeze Engine).
========================================================================
Engineered for rapid 1m / 5m scalp trades on Gold (XAUUSD) and Bitcoin (BTCUSDT).

Core Principles:
1. Volatility Compression (The Squeeze):
   - Bollinger Bands (20, 2.0 std) contract INSIDE Keltner Channels (20, 1.5 ATR).
   - Price consolidates tightly, building institutional pressure.

2. Squeeze Release (The Fire):
   - Bollinger Bands expand OUTSIDE Keltner Channels.
   - Energy is released in an explosive directional impulse move.

3. Momentum Confirmation:
   - Momentum oscillator (linear regression delta from mean) confirms direction.
   - Bullish Fire: Momentum > 0 and expanding upward; candle closes above SMA20.
   - Bearish Fire: Momentum < 0 and expanding downward; candle closes below SMA20.

4. Micro Scalp Targets & Tight Protection:
   - Precision micro-stop: placed at SMA20 / opposite band.
   - Micro target: fast 1.0x - 1.8x risk-to-reward ratio for quick capital capture.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class SqueezeDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class SqueezeVote:
    agent: str = "VOLATILITY-SQUEEZE"
    direction: SqueezeDir = SqueezeDir.FLAT
    strength: float = 0.0
    reason: str = ""
    pattern: str = ""
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


def compute_bollinger_keltner(
    df: pd.DataFrame,
    length: int = 20,
    bb_mult: float = 2.0,
    kc_mult: float = 1.5,
) -> Optional[Dict[str, float]]:
    """
    Computes Bollinger Bands and Keltner Channels on close prices.
    """
    if df is None or len(df) < length:
        return None

    try:
        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values

        # 1. Bollinger Bands
        sma20 = float(np.mean(c[-length:]))
        std20 = float(np.std(c[-length:]))
        bb_upper = sma20 + bb_mult * std20
        bb_lower = sma20 - bb_mult * std20

        # 2. Keltner Channels
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        atr20 = float(np.mean(tr[-length:])) if len(tr) >= length else float(np.mean(tr))
        kc_upper = sma20 + kc_mult * atr20
        kc_lower = sma20 - kc_mult * atr20

        # 3. Momentum Histogram (Deviation from SMA20)
        recent_c = c[-5:]
        x = np.arange(len(recent_c))
        slope = float(np.polyfit(x, recent_c, 1)[0]) if len(recent_c) >= 3 else 0.0
        mom = float((c[-1] - sma20) + slope * 2.0)

        # Squeeze condition
        squeeze_on = (bb_upper < kc_upper) and (bb_lower > kc_lower)
        squeeze_fired = (bb_upper >= kc_upper) or (bb_lower <= kc_lower)

        return {
            "sma20": sma20,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "kc_upper": kc_upper,
            "kc_lower": kc_lower,
            "atr20": atr20,
            "mom": mom,
            "slope": slope,
            "squeeze_on": squeeze_on,
            "squeeze_fired": squeeze_fired,
        }
    except Exception:
        return None


def detect_squeeze_scalp(df: pd.DataFrame) -> Optional[SqueezeVote]:
    """
    Detects high-momentum squeeze fire scalps.
    """
    if df is None or len(df) < 22:
        return None

    curr_data = compute_bollinger_keltner(df)
    prev_data = compute_bollinger_keltner(df.iloc[:-1])

    if curr_data is None:
        return None

    sma20 = curr_data["sma20"]
    bb_upper = curr_data["bb_upper"]
    bb_lower = curr_data["bb_lower"]
    atr20 = curr_data["atr20"] or (_safe_float(df["close"].iloc[-1]) * 0.0015)
    mom = curr_data["mom"]
    slope = curr_data["slope"]

    curr = df.iloc[-1]
    co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])

    # Check if a squeeze was active in the recent 1 to 4 bars and is now firing
    was_squeezing = False
    for lb in range(1, min(6, len(df) - 19)):
        past_data = compute_bollinger_keltner(df.iloc[:-lb])
        if past_data and (past_data["squeeze_on"] or (past_data["bb_upper"] - past_data["bb_lower"]) <= 2.2 * past_data["atr20"]):
            was_squeezing = True
            break

    is_firing = curr_data["squeeze_fired"]

    if was_squeezing and is_firing:
        # A) BULLISH SQUEEZE SCALP
        if mom > 0 and slope > 0 and cc > sma20 and cc > co:
            strength = 0.92 if cc > curr_data["kc_upper"] else 0.86
            stop = round(max(sma20, cc - 0.75 * atr20), 4)
            if stop >= cc:
                stop = round(cc - 0.65 * atr20, 4)
            risk_pts = max(cc - stop, 0.4 * atr20)
            tp1 = round(cc + risk_pts * 1.4, 4)
            tp2 = round(cc + risk_pts * 2.2, 4)
            reason = (
                f"Squeeze Fire Bullish Scalp: BB broke above KC with expanding momentum (+{mom:.2f}), "
                f"slope {slope:+.2f}, price {cc:,.2f} above SMA20"
            )
            return SqueezeVote(
                agent="VOLATILITY-SQUEEZE",
                direction=SqueezeDir.LONG,
                strength=strength,
                reason=reason,
                pattern="BULLISH_SQUEEZE_BURST",
                stop_price=stop,
                tp_price=tp1,
                details={"sma20": sma20, "mom": mom, "slope": slope, "tp2": tp2},
            )

        # B) BEARISH SQUEEZE SCALP
        elif mom < 0 and slope < 0 and cc < sma20 and cc < co:
            strength = 0.92 if cc < curr_data["kc_lower"] else 0.86
            stop = round(min(sma20, cc + 0.75 * atr20), 4)
            if stop <= cc:
                stop = round(cc + 0.65 * atr20, 4)
            risk_pts = max(stop - cc, 0.4 * atr20)
            tp1 = round(cc - risk_pts * 1.4, 4)
            tp2 = round(cc - risk_pts * 2.2, 4)
            reason = (
                f"Squeeze Fire Bearish Scalp: BB broke below KC with expanding momentum ({mom:.2f}), "
                f"slope {slope:+.2f}, price {cc:,.2f} below SMA20"
            )
            return SqueezeVote(
                agent="VOLATILITY-SQUEEZE",
                direction=SqueezeDir.SHORT,
                strength=strength,
                reason=reason,
                pattern="BEARISH_SQUEEZE_BURST",
                stop_price=stop,
                tp_price=tp1,
                details={"sma20": sma20, "mom": mom, "slope": slope, "tp2": tp2},
            )

    return None


class SqueezeScalpAgent:
    """
    Volatility Squeeze & Momentum Burst Scalper Agent.
    Specialized in explosive micro-scalp triggers when price breaks out of
    volatility compression.
    """
    name = "VOLATILITY-SQUEEZE"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> SqueezeVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 22:
                return SqueezeVote(self.name, SqueezeDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return SqueezeVote(self.name, SqueezeDir.FLAT, 0.0, "Missing OHLC columns")

            setup = detect_squeeze_scalp(df)
            if setup is not None and setup.direction != SqueezeDir.FLAT:
                return setup

            return SqueezeVote(self.name, SqueezeDir.FLAT, 0.0, "No active volatility squeeze burst")
        except Exception as e:
            return SqueezeVote(self.name, SqueezeDir.FLAT, 0.0, f"Squeeze evaluation note: {type(e).__name__}")


def squeeze_scalp_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> SqueezeVote:
    return SqueezeScalpAgent().vote(df, daily=daily)
