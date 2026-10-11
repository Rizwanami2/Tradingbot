"""
Advanced Smart Money Concepts (SMC / ICT) Liquidity Engine
=========================================================
Detects:
- Fair Value Gaps (FVG) - 3-candle imbalance
- Order Blocks (last opposing candle before impulsive move)
- Equal Highs / Equal Lows (liquidity pools)
- Previous Day High/Low sweeps with rejection
- Break of Structure (BOS) / Change of Character (CHoCH)

Designed to be called by LiquidityAgent or Arbiter for higher-accuracy setups.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class StructureDir(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


@dataclass
class SMCSetup:
    kind: str  # "FVG", "ORDER_BLOCK", "EQUAL_HIGHS", "EQUAL_LOWS", "PDH_SWEEP", "PDL_SWEEP", "BOS"
    direction: StructureDir
    strength: float  # 0.0 - 1.0
    price_level: float
    top: float
    bottom: float
    reason: str
    bars_ago: int = 0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-period:]))


def detect_fair_value_gaps(df: pd.DataFrame, lookback: int = 40, min_gap_atr: float = 0.15) -> List[SMCSetup]:
    """3-candle Fair Value Gaps (imbalance)."""
    setups: List[SMCSetup] = []
    if len(df) < 5:
        return setups
    atr_val = _atr(df)
    if atr_val <= 0:
        return setups
    min_gap = atr_val * min_gap_atr

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    for i in range(max(2, n - lookback), n - 1):
        # Bullish FVG: candle[i-2].high < candle[i].low  (gap up)
        if lows[i] - highs[i - 2] > min_gap and closes[i] > closes[i - 1]:
            top = float(lows[i])
            bottom = float(highs[i - 2])
            mid = (top + bottom) / 2
            strength = min(1.0, (top - bottom) / (atr_val * 1.5))
            setups.append(SMCSetup(
                kind="FVG",
                direction=StructureDir.BULLISH,
                strength=0.55 + 0.35 * strength,
                price_level=mid,
                top=top,
                bottom=bottom,
                reason=f"Bullish FVG ({top - bottom:.2f} gap)",
                bars_ago=n - 1 - i,
            ))
        # Bearish FVG: candle[i-2].low > candle[i].high  (gap down)
        if lows[i - 2] - highs[i] > min_gap and closes[i] < closes[i - 1]:
            top = float(lows[i - 2])
            bottom = float(highs[i])
            mid = (top + bottom) / 2
            strength = min(1.0, (top - bottom) / (atr_val * 1.5))
            setups.append(SMCSetup(
                kind="FVG",
                direction=StructureDir.BEARISH,
                strength=0.55 + 0.35 * strength,
                price_level=mid,
                top=top,
                bottom=bottom,
                reason=f"Bearish FVG ({top - bottom:.2f} gap)",
                bars_ago=n - 1 - i,
            ))
    return setups


def detect_order_blocks(df: pd.DataFrame, lookback: int = 30, impulse_atr: float = 1.2) -> List[SMCSetup]:
    """Simple Order Block detection: last opposing candle before strong impulse."""
    setups: List[SMCSetup] = []
    if len(df) < 10:
        return setups
    atr_val = _atr(df)
    if atr_val <= 0:
        return setups

    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    closes = df["close"].values
    n = len(df)

    for i in range(max(3, n - lookback), n - 2):
        # Bullish OB: strong up move after a down candle
        move = closes[i + 1] - closes[i]
        if move > atr_val * impulse_atr and closes[i] < opens[i]:
            top = float(max(opens[i], closes[i]))
            bottom = float(lows[i])
            setups.append(SMCSetup(
                kind="ORDER_BLOCK",
                direction=StructureDir.BULLISH,
                strength=0.75,
                price_level=(top + bottom) / 2,
                top=top,
                bottom=bottom,
                reason="Bullish Order Block (last down candle before impulse)",
                bars_ago=n - 1 - i,
            ))
        # Bearish OB
        move = closes[i] - closes[i + 1]
        if move > atr_val * impulse_atr and closes[i] > opens[i]:
            top = float(highs[i])
            bottom = float(min(opens[i], closes[i]))
            setups.append(SMCSetup(
                kind="ORDER_BLOCK",
                direction=StructureDir.BEARISH,
                strength=0.75,
                price_level=(top + bottom) / 2,
                top=top,
                bottom=bottom,
                reason="Bearish Order Block (last up candle before impulse)",
                bars_ago=n - 1 - i,
            ))
    return setups


def detect_equal_highs_lows(df: pd.DataFrame, lookback: int = 40, tolerance_atr: float = 0.12) -> List[SMCSetup]:
    """Equal highs / equal lows = liquidity pools."""
    setups: List[SMCSetup] = []
    if len(df) < 15:
        return setups
    atr_val = _atr(df)
    tol = atr_val * tolerance_atr if atr_val > 0 else 0.5

    window = df.iloc[-lookback:]
    highs = window["high"].values
    lows = window["low"].values

    # Simple pairwise near-equal detection near the right edge
    for i in range(len(highs) - 8, len(highs) - 1):
        for j in range(i + 3, len(highs)):
            if abs(highs[i] - highs[j]) <= tol:
                level = float((highs[i] + highs[j]) / 2)
                setups.append(SMCSetup(
                    kind="EQUAL_HIGHS",
                    direction=StructureDir.BEARISH,  # liquidity above → often sell-side target then reverse
                    strength=0.65,
                    price_level=level,
                    top=level + tol,
                    bottom=level - tol,
                    reason=f"Equal Highs liquidity pool @ {level:.2f}",
                    bars_ago=len(df) - 1 - (len(df) - lookback + j),
                ))
                break
    for i in range(len(lows) - 8, len(lows) - 1):
        for j in range(i + 3, len(lows)):
            if abs(lows[i] - lows[j]) <= tol:
                level = float((lows[i] + lows[j]) / 2)
                setups.append(SMCSetup(
                    kind="EQUAL_LOWS",
                    direction=StructureDir.BULLISH,
                    strength=0.65,
                    price_level=level,
                    top=level + tol,
                    bottom=level - tol,
                    reason=f"Equal Lows liquidity pool @ {level:.2f}",
                    bars_ago=len(df) - 1 - (len(df) - lookback + j),
                ))
                break
    return setups


def detect_pdh_pdl_sweep(df: pd.DataFrame, daily: pd.DataFrame) -> List[SMCSetup]:
    """Previous Day High/Low liquidity sweep + rejection."""
    setups: List[SMCSetup] = []
    if len(df) < 5 or len(daily) < 2:
        return setups
    pdh = float(daily.iloc[-2]["high"])
    pdl = float(daily.iloc[-2]["low"])
    prev, curr = df.iloc[-2], df.iloc[-1]

    # PDH sweep + bearish rejection
    if float(prev["high"]) > pdh and float(curr["close"]) < pdh and curr["close"] < curr["open"]:
        setups.append(SMCSetup(
            kind="PDH_SWEEP",
            direction=StructureDir.BEARISH,
            strength=0.88,
            price_level=pdh,
            top=float(prev["high"]),
            bottom=pdh,
            reason="PDH Liquidity Swept + Bearish Rejection",
            bars_ago=0,
        ))
    # PDL sweep + bullish rejection
    if float(prev["low"]) < pdl and float(curr["close"]) > pdl and curr["close"] > curr["open"]:
        setups.append(SMCSetup(
            kind="PDL_SWEEP",
            direction=StructureDir.BULLISH,
            strength=0.88,
            price_level=pdl,
            top=pdl,
            bottom=float(prev["low"]),
            reason="PDL Liquidity Swept + Bullish Rejection",
            bars_ago=0,
        ))
    return setups


def detect_bos(df: pd.DataFrame, lookback: int = 12) -> List[SMCSetup]:
    """Simple Break of Structure."""
    setups: List[SMCSetup] = []
    if len(df) < lookback + 2:
        return setups
    recent = df.iloc[-(lookback + 1):-1]
    curr = df.iloc[-1]
    swing_high = float(recent["high"].max())
    swing_low = float(recent["low"].min())

    if float(curr["close"]) > swing_high:
        setups.append(SMCSetup(
            kind="BOS",
            direction=StructureDir.BULLISH,
            strength=0.72,
            price_level=swing_high,
            top=float(curr["high"]),
            bottom=swing_high,
            reason="Bullish Break of Structure",
            bars_ago=0,
        ))
    if float(curr["close"]) < swing_low:
        setups.append(SMCSetup(
            kind="BOS",
            direction=StructureDir.BEARISH,
            strength=0.72,
            price_level=swing_low,
            top=swing_low,
            bottom=float(curr["low"]),
            reason="Bearish Break of Structure",
            bars_ago=0,
        ))
    return setups


def scan_smc(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> List[SMCSetup]:
    """Run all SMC detectors and return sorted by strength (descending)."""
    setups: List[SMCSetup] = []
    setups.extend(detect_fair_value_gaps(df))
    setups.extend(detect_order_blocks(df))
    setups.extend(detect_equal_highs_lows(df))
    setups.extend(detect_bos(df))
    if daily is not None and not daily.empty:
        setups.extend(detect_pdh_pdl_sweep(df, daily))
    setups.sort(key=lambda s: s.strength, reverse=True)
    return setups


def best_smc_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> Tuple[StructureDir, float, str]:
    """Convenience: return strongest recent directional SMC signal."""
    setups = scan_smc(df, daily)
    if not setups:
        return StructureDir.NEUTRAL, 0.0, "No SMC setup"
    # Prefer very recent high-strength setups
    recent = [s for s in setups if s.bars_ago <= 3]
    candidate = recent[0] if recent else setups[0]
    return candidate.direction, candidate.strength, f"{candidate.kind}: {candidate.reason}"
