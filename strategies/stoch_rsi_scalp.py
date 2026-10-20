#!/usr/bin/env python3
"""
Stochastic RSI Quick Cross Reversal Micro-Scalp Strategy.
=========================================================
Engineered for ultra-fast, high-win-rate 1m & 5m scalping on Gold (XAUUSD)
and Bitcoin (BTCUSDT).

Core Principles:
1. Stochastic RSI (%K, %D with 14, 14, 3, 3):
   - Measures pure momentum exhaustion and rapid turning points on LTF candles.
   - Oversold threshold: <= 18
   - Overbought threshold: >= 82

2. Micro-Trend Harmony (EMA 20 Filter):
   - Bullish Scalp: Price above or bouncing off 20 EMA while StochRSI hooks up from oversold.
   - Bearish Scalp: Price below or rejecting 20 EMA while StochRSI hooks down from overbought.

3. Quick Momentum Cross Trigger:
   - Long: %K crosses above %D from oversold territory with green follow-through bar.
   - Short: %K crosses below %D from overbought territory with red follow-through bar.

4. Micro Scalp Targets & Tight Protection:
   - Tight micro-stop anchored to recent 2-3 bar extreme + ATR buffer.
   - Fast 1.2x to 1.5x micro R:R target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class StochDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class StochVote:
    agent: str = "STOCH-RSI-SCALP"
    direction: StochDir = StochDir.FLAT
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


def compute_stoch_rsi(
    df: pd.DataFrame,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> Optional[Dict[str, float]]:
    """
    Computes StochRSI %K and %D lines.
    """
    needed = rsi_period + stoch_period + k_smooth + d_smooth
    if df is None or len(df) < needed:
        return None

    try:
        c = df["close"].astype(float)
        delta = c.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / rsi_period, min_periods=rsi_period, adjust=False).mean()

        rs = avg_gain / (avg_loss + 1e-12)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Stoch of RSI
        rsi_min = rsi.rolling(stoch_period).min()
        rsi_max = rsi.rolling(stoch_period).max()
        stoch = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-12) * 100.0

        k = stoch.rolling(k_smooth).mean()
        d = k.rolling(d_smooth).mean()

        # 20-period EMA
        ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])

        return {
            "k": float(k.iloc[-1]),
            "d": float(d.iloc[-1]),
            "prev_k": float(k.iloc[-2]),
            "prev_d": float(d.iloc[-2]),
            "prev2_k": float(k.iloc[-3]) if len(k) >= 3 else float(k.iloc[-2]),
            "prev2_d": float(d.iloc[-3]) if len(d) >= 3 else float(d.iloc[-2]),
            "rsi": float(rsi.iloc[-1]),
            "ema20": ema20,
        }
    except Exception:
        return None


def detect_stoch_rsi_scalp(df: pd.DataFrame) -> Optional[StochVote]:
    """
    Detects high-win-rate StochRSI oversold/overbought crosses aligned with micro-trend.
    """
    if df is None or len(df) < 35:
        return None

    metrics = compute_stoch_rsi(df)
    if metrics is None:
        return None

    k, d = metrics["k"], metrics["d"]
    pk, pd_val = metrics["prev_k"], metrics["prev_d"]
    p2k = metrics["prev2_k"]
    ema20 = metrics["ema20"]

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])
    po, ph, pl, pc = map(_safe_float, [prev["open"], prev["high"], prev["low"], prev["close"]])
    atr_val = _calculate_atr(df, 14) or (cc * 0.0015)

    # Recent 3-bar swing levels for tight micro-stops
    recent_lows = df["low"].iloc[-4:].astype(float).min()
    recent_highs = df["high"].iloc[-4:].astype(float).max()

    # 1. BULLISH STOCH-RSI SCALP
    # (%K was recently <= 20 and now crossed above %D, with bullish bar)
    was_oversold = (pk <= 22 or p2k <= 22 or k <= 22)
    bullish_cross = (k > d and (pk <= pd_val or p2k <= metrics["prev2_d"] or (k - d) >= 3.0))

    if was_oversold and bullish_cross and (cc >= co or cc >= pc):
        # Micro trend confirmation: close above ema20 or bouncing off ema20
        trend_aligned = cc >= ema20 - 0.3 * atr_val
        if trend_aligned:
            strength = 0.92 if (cc > ema20 and cc > co) else 0.86
            stop = round(recent_lows - 0.30 * atr_val, 4)
            if stop >= cc:
                stop = round(cc - 0.50 * atr_val, 4)
            risk_pts = max(cc - stop, 0.35 * atr_val)
            tp1 = round(cc + risk_pts * 1.4, 4)
            tp2 = round(cc + risk_pts * 2.2, 4)
            reason = (
                f"StochRSI Bullish Scalp: Hook from oversold (%K:{k:.1f} > %D:{d:.1f}), "
                f"price {cc:,.2f} supported near 20-EMA"
            )
            return StochVote(
                agent="STOCH-RSI-SCALP",
                direction=StochDir.LONG,
                strength=strength,
                reason=reason,
                pattern="BULLISH_STOCH_RSI_CROSS",
                stop_price=stop,
                tp_price=tp1,
                details={"k": k, "d": d, "ema20": ema20, "tp2": tp2},
            )

    # 2. BEARISH STOCH-RSI SCALP
    # (%K was recently >= 80 and now crossed below %D, with bearish bar)
    was_overbought = (pk >= 78 or p2k >= 78 or k >= 78)
    bearish_cross = (k < d and (pk >= pd_val or p2k >= metrics["prev2_d"] or (d - k) >= 3.0))

    if was_overbought and bearish_cross and (cc <= co or cc <= pc):
        trend_aligned = cc <= ema20 + 0.3 * atr_val
        if trend_aligned:
            strength = 0.92 if (cc < ema20 and cc < co) else 0.86
            stop = round(recent_highs + 0.30 * atr_val, 4)
            if stop <= cc:
                stop = round(cc + 0.50 * atr_val, 4)
            risk_pts = max(stop - cc, 0.35 * atr_val)
            tp1 = round(cc - risk_pts * 1.4, 4)
            tp2 = round(cc - risk_pts * 2.2, 4)
            reason = (
                f"StochRSI Bearish Scalp: Reversal from overbought (%K:{k:.1f} < %D:{d:.1f}), "
                f"price {cc:,.2f} rejected below 20-EMA"
            )
            return StochVote(
                agent="STOCH-RSI-SCALP",
                direction=StochDir.SHORT,
                strength=strength,
                reason=reason,
                pattern="BEARISH_STOCH_RSI_CROSS",
                stop_price=stop,
                tp_price=tp1,
                details={"k": k, "d": d, "ema20": ema20, "tp2": tp2},
            )

    return None


class StochRSIScalpAgent:
    """
    Stochastic RSI Quick Cross Reversal Scalper Agent.
    Triggers rapid momentum entries from extreme overbought/oversold levels.
    """
    name = "STOCH-RSI-SCALP"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> StochVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 35:
                return StochVote(self.name, StochDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return StochVote(self.name, StochDir.FLAT, 0.0, "Missing OHLC columns")

            setup = detect_stoch_rsi_scalp(df)
            if setup is not None and setup.direction != StochDir.FLAT:
                return setup

            return StochVote(self.name, StochDir.FLAT, 0.0, "No active StochRSI scalp trigger")
        except Exception as e:
            return StochVote(self.name, StochDir.FLAT, 0.0, f"StochRSI evaluation note: {type(e).__name__}")


def stoch_rsi_scalp_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> StochVote:
    return StochRSIScalpAgent().vote(df, daily=daily)
