#!/usr/bin/env python3
"""SMC / ICT Liquidity Structure helpers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


class StructureDir(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


@dataclass
class SMCSetup:
    kind: str
    direction: StructureDir
    strength: float
    reason: str
    bars_ago: int = 0
    price: float = 0.0


def detect_equal_highs_lows(df: pd.DataFrame, lookback: int = 30, tol: float = 0.0008) -> List[SMCSetup]:
    out: List[SMCSetup] = []
    if df is None or len(df) < lookback:
        return out
    window = df.iloc[-lookback:]
    highs = window["high"].values
    lows = window["low"].values
    last_h = float(highs[-1])
    last_l = float(lows[-1])
    # equal highs cluster
    eq_h = np.where(np.abs(highs - last_h) / max(abs(last_h), 1e-9) < tol)[0]
    if len(eq_h) >= 2:
        out.append(SMCSetup("EQUAL_HIGHS", StructureDir.BEARISH, 0.65,
                            f"Equal Highs liquidity pool @ {last_h:.2f}", bars_ago=0, price=last_h))
    eq_l = np.where(np.abs(lows - last_l) / max(abs(last_l), 1e-9) < tol)[0]
    if len(eq_l) >= 2:
        out.append(SMCSetup("EQUAL_LOWS", StructureDir.BULLISH, 0.65,
                            f"Equal Lows liquidity pool @ {last_l:.2f}", bars_ago=0, price=last_l))
    return out


def detect_fair_value_gaps(df: pd.DataFrame, lookback: int = 40) -> List[SMCSetup]:
    out: List[SMCSetup] = []
    if df is None or len(df) < 5:
        return out
    window = df.iloc[-lookback:] if len(df) > lookback else df
    for i in range(2, len(window)):
        c0, c1, c2 = window.iloc[i - 2], window.iloc[i - 1], window.iloc[i]
        # bullish FVG: candle0 high < candle2 low
        if float(c0["high"]) < float(c2["low"]):
            gap = float(c2["low"]) - float(c0["high"])
            if gap > 0:
                out.append(SMCSetup("FVG_BULL", StructureDir.BULLISH, min(0.55 + gap / max(float(c1["close"]), 1e-9) * 50, 0.9),
                                    f"Bullish FVG gap {gap:.4f}", bars_ago=len(window) - 1 - i, price=float(c2["low"])))
        # bearish FVG
        if float(c0["low"]) > float(c2["high"]):
            gap = float(c0["low"]) - float(c2["high"])
            if gap > 0:
                out.append(SMCSetup("FVG_BEAR", StructureDir.BEARISH, min(0.55 + gap / max(float(c1["close"]), 1e-9) * 50, 0.9),
                                    f"Bearish FVG gap {gap:.4f}", bars_ago=len(window) - 1 - i, price=float(c2["high"])))
    return out[-5:]


def detect_order_blocks(df: pd.DataFrame, lookback: int = 30) -> List[SMCSetup]:
    out: List[SMCSetup] = []
    if df is None or len(df) < 8:
        return out
    window = df.iloc[-lookback:]
    for i in range(1, len(window) - 1):
        prev, cur, nxt = window.iloc[i - 1], window.iloc[i], window.iloc[i + 1]
        # bullish OB: down candle then strong up displacement
        if float(cur["close"]) < float(cur["open"]) and float(nxt["close"]) > float(nxt["open"]):
            if float(nxt["close"]) > float(cur["high"]):
                out.append(SMCSetup("OB_BULL", StructureDir.BULLISH, 0.7,
                                    f"Bullish Order Block @ {float(cur['low']):.2f}",
                                    bars_ago=len(window) - 1 - i, price=float(cur["low"])))
        if float(cur["close"]) > float(cur["open"]) and float(nxt["close"]) < float(nxt["open"]):
            if float(nxt["close"]) < float(cur["low"]):
                out.append(SMCSetup("OB_BEAR", StructureDir.BEARISH, 0.7,
                                    f"Bearish Order Block @ {float(cur['high']):.2f}",
                                    bars_ago=len(window) - 1 - i, price=float(cur["high"])))
    return out[-4:]


def detect_bos(df: pd.DataFrame, lookback: int = 20) -> List[SMCSetup]:
    out: List[SMCSetup] = []
    if df is None or len(df) < lookback:
        return out
    window = df.iloc[-lookback:]
    swing_high = float(window["high"].iloc[:-1].max())
    swing_low = float(window["low"].iloc[:-1].min())
    last = window.iloc[-1]
    if float(last["close"]) > swing_high:
        out.append(SMCSetup("BOS_UP", StructureDir.BULLISH, 0.75,
                            f"Break of Structure up through {swing_high:.2f}", 0, float(last["close"])))
    if float(last["close"]) < swing_low:
        out.append(SMCSetup("BOS_DOWN", StructureDir.BEARISH, 0.75,
                            f"Break of Structure down through {swing_low:.2f}", 0, float(last["close"])))
    return out


def detect_pdh_pdl_sweep(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> List[SMCSetup]:
    out: List[SMCSetup] = []
    if daily is None or daily.empty or len(daily) < 2 or df is None or len(df) < 3:
        return out
    try:
        pdh = float(daily.iloc[-2]["high"])
        pdl = float(daily.iloc[-2]["low"])
    except Exception:
        return out
    prev, curr = df.iloc[-2], df.iloc[-1]
    if float(prev["high"]) > pdh and float(curr["close"]) < pdh and float(curr["close"]) < float(curr["open"]):
        out.append(SMCSetup("PDH_SWEEP", StructureDir.BEARISH, 0.85,
                            f"PDH Liquidity Sweep + Rejection @ {pdh:.2f}", 0, pdh))
    if float(prev["low"]) < pdl and float(curr["close"]) > pdl and float(curr["close"]) > float(curr["open"]):
        out.append(SMCSetup("PDL_SWEEP", StructureDir.BULLISH, 0.85,
                            f"PDL Liquidity Sweep + Rejection @ {pdl:.2f}", 0, pdl))
    return out


def scan_smc(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> List[SMCSetup]:
    if df is None or getattr(df, "empty", True) or len(df) < 10:
        return []
    setups: List[SMCSetup] = []
    setups.extend(detect_pdh_pdl_sweep(df, daily))
    setups.extend(detect_bos(df))
    setups.extend(detect_order_blocks(df))
    setups.extend(detect_fair_value_gaps(df))
    setups.extend(detect_equal_highs_lows(df))
    setups.sort(key=lambda s: (-s.strength, s.bars_ago))
    return setups


def best_smc_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> Tuple[StructureDir, float, str]:
    if df is None or getattr(df, "empty", True) or len(df) < 10:
        return StructureDir.NEUTRAL, 0.0, "Insufficient bars for SMC"
    setups = scan_smc(df, daily)
    if not setups:
        return StructureDir.NEUTRAL, 0.0, "No SMC setup"
    recent = [s for s in setups if s.bars_ago <= 3]
    candidate = recent[0] if recent else setups[0]
    return candidate.direction, candidate.strength, f"{candidate.kind}: {candidate.reason}"
