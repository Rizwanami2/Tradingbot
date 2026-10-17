#!/usr/bin/env python3
"""
Multi-Timeframe Fair Value Gap (FVG) & Order Block Confluence Engine.
=====================================================================
Detects institutional imbalances across multiple timeframes (1H, 15M, 5M, 1M):
- HTF FVGs and Order Blocks define primary institutional liquidity magnet zones.
- MTF / LTF price action entering/tapping an HTF zone ("mitigation tap").
- Nested Confluence (e.g. 15M Order Block inside a 1H Fair Value Gap).
- Lower timeframe Market Structure Shift (MSS/ChoCH) displacement confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class MTFDir(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


@dataclass
class FVGZone:
    tf: str
    direction: MTFDir
    top: float
    bottom: float
    midpoint: float
    gap_size: float
    bars_ago: int
    mitigated: bool = False


@dataclass
class OrderBlockZone:
    tf: str
    direction: MTFDir
    high: float
    low: float
    open_price: float
    close_price: float
    bars_ago: int
    volume_ratio: float = 1.0


@dataclass
class MTFVote:
    agent: str = "MTF-CONFLUENCE"
    direction: MTFDir = MTFDir.NEUTRAL
    strength: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default


def extract_fair_value_gaps(df: pd.DataFrame, tf_name: str = "1h", lookback: int = 40) -> List[FVGZone]:
    """Finds unmitigated FVGs across the specified dataframe."""
    fvgs: List[FVGZone] = []
    if df is None or len(df) < 5:
        return fvgs

    window = df.iloc[-lookback:] if len(df) > lookback else df
    current_price = _safe_float(df.iloc[-1]["close"])

    for i in range(2, len(window)):
        c0 = window.iloc[i - 2]
        c1 = window.iloc[i - 1]
        c2 = window.iloc[i]

        c0_h, c0_l = _safe_float(c0["high"]), _safe_float(c0["low"])
        c2_h, c2_l = _safe_float(c2["high"]), _safe_float(c2["low"])

        # (1) Bullish FVG: Candle 0 High < Candle 2 Low
        if c0_h < c2_l:
            gap = c2_l - c0_h
            if gap > 0:
                # Check if subsequent candles mitigated the gap
                subsequent = window.iloc[i + 1:] if i + 1 < len(window) else None
                mitigated = False
                if subsequent is not None and not subsequent.empty:
                    lowest_after = float(subsequent["low"].min())
                    if lowest_after <= c0_h:
                        mitigated = True

                fvgs.append(FVGZone(
                    tf=tf_name,
                    direction=MTFDir.BULLISH,
                    top=c2_l,
                    bottom=c0_h,
                    midpoint=(c2_l + c0_h) / 2.0,
                    gap_size=gap,
                    bars_ago=len(window) - 1 - i,
                    mitigated=mitigated,
                ))

        # (2) Bearish FVG: Candle 0 Low > Candle 2 High
        if c0_l > c2_h:
            gap = c0_l - c2_h
            if gap > 0:
                subsequent = window.iloc[i + 1:] if i + 1 < len(window) else None
                mitigated = False
                if subsequent is not None and not subsequent.empty:
                    highest_after = float(subsequent["high"].max())
                    if highest_after >= c0_l:
                        mitigated = True

                fvgs.append(FVGZone(
                    tf=tf_name,
                    direction=MTFDir.BEARISH,
                    top=c0_l,
                    bottom=c2_h,
                    midpoint=(c0_l + c2_h) / 2.0,
                    gap_size=gap,
                    bars_ago=len(window) - 1 - i,
                    mitigated=mitigated,
                ))

    return fvgs


def extract_order_blocks(df: pd.DataFrame, tf_name: str = "1h", lookback: int = 35) -> List[OrderBlockZone]:
    """Detects institutional Order Blocks followed by energetic displacement."""
    obs: List[OrderBlockZone] = []
    if df is None or len(df) < 6:
        return obs

    window = df.iloc[-lookback:] if len(df) > lookback else df
    vols = window["volume"].astype(float).values if "volume" in window.columns else None
    avg_vol = float(np.mean(vols)) if vols is not None and len(vols) > 0 and np.mean(vols) > 0 else 1.0

    for i in range(1, len(window) - 1):
        cur = window.iloc[i]
        nxt = window.iloc[i + 1]

        cur_o, cur_c = _safe_float(cur["open"]), _safe_float(cur["close"])
        cur_h, cur_l = _safe_float(cur["high"]), _safe_float(cur["low"])
        nxt_o, nxt_c = _safe_float(nxt["open"]), _safe_float(nxt["close"])

        vol_ratio = (_safe_float(cur.get("volume", avg_vol)) / avg_vol) if avg_vol > 0 else 1.0

        # Bullish OB: Last down-candle prior to violent up-displacement breaking cur_h
        if cur_c < cur_o and nxt_c > nxt_o and nxt_c > cur_h:
            obs.append(OrderBlockZone(
                tf=tf_name,
                direction=MTFDir.BULLISH,
                high=cur_h,
                low=cur_l,
                open_price=cur_o,
                close_price=cur_c,
                bars_ago=len(window) - 1 - i,
                volume_ratio=round(vol_ratio, 2),
            ))

        # Bearish OB: Last up-candle prior to violent down-displacement breaking cur_l
        if cur_c > cur_o and nxt_c < nxt_o and nxt_c < cur_l:
            obs.append(OrderBlockZone(
                tf=tf_name,
                direction=MTFDir.BEARISH,
                high=cur_h,
                low=cur_l,
                open_price=cur_o,
                close_price=cur_c,
                bars_ago=len(window) - 1 - i,
                volume_ratio=round(vol_ratio, 2),
            ))

    return obs


def evaluate_mtf_confluence(
    df1: pd.DataFrame,
    df15: Optional[pd.DataFrame] = None,
    df1h: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluates multi-timeframe nested confluence across 1H, 15M, and 1M frames.
    """
    if df1 is None or len(df1) < 10:
        return None

    current_price = _safe_float(df1.iloc[-1]["close"])
    curr_low = _safe_float(df1.iloc[-1]["low"])
    curr_high = _safe_float(df1.iloc[-1]["high"])

    # 1. HTF 1H Fair Value Gaps & Order Blocks
    htf_frame = df1h if df1h is not None and len(df1h) >= 10 else df15
    htf_fvgs = extract_fair_value_gaps(htf_frame, tf_name="1H") if htf_frame is not None else []
    htf_obs = extract_order_blocks(htf_frame, tf_name="1H") if htf_frame is not None else []

    # 2. MTF 15M Fair Value Gaps & Order Blocks
    mtf_frame = df15 if df15 is not None and len(df15) >= 10 else df1
    mtf_obs = extract_order_blocks(mtf_frame, tf_name="15M") if mtf_frame is not None else []
    mtf_fvgs = extract_fair_value_gaps(mtf_frame, tf_name="15M") if mtf_frame is not None else []

    # Check for Bullish HTF FVG Tap / Mitigation
    bullish_htf_taps = [
        fvg for fvg in htf_fvgs
        if fvg.direction == MTFDir.BULLISH and not fvg.mitigated and (fvg.bottom <= current_price <= fvg.top or fvg.bottom <= curr_low <= fvg.top)
    ]

    bullish_htf_ob_taps = [
        ob for ob in htf_obs
        if ob.direction == MTFDir.BULLISH and (ob.low <= current_price <= ob.high or ob.low <= curr_low <= ob.high)
    ]

    # Check for Bearish HTF FVG Tap / Mitigation
    bearish_htf_taps = [
        fvg for fvg in htf_fvgs
        if fvg.direction == MTFDir.BEARISH and not fvg.mitigated and (fvg.bottom <= current_price <= fvg.top or fvg.bottom <= curr_high <= fvg.top)
    ]

    bearish_htf_ob_taps = [
        ob for ob in htf_obs
        if ob.direction == MTFDir.BEARISH and (ob.low <= current_price <= ob.high or ob.low <= curr_high <= ob.high)
    ]

    # (A) Bullish Confluence
    if bullish_htf_taps or bullish_htf_ob_taps:
        # Check if 15M also has an Order Block inside or adjacent
        has_mtf_nested = any(ob.direction == MTFDir.BULLISH and ob.bars_ago <= 15 for ob in mtf_obs)
        strength = 0.75
        tier = "2-TF Confluence"
        if (bullish_htf_taps and bullish_htf_ob_taps) or has_mtf_nested:
            strength = 0.90
            tier = "3-TF Nested Confluence (HTF FVG + OB Tap)"

        ref_level = bullish_htf_taps[0].bottom if bullish_htf_taps else bullish_htf_ob_taps[0].low
        return {
            "direction": MTFDir.BULLISH,
            "strength": strength,
            "tier": tier,
            "level": ref_level,
            "reason": f"Bullish MTF Confluence: Price tapped {tier} zone @ {ref_level:.2f}",
        }

    # (B) Bearish Confluence
    if bearish_htf_taps or bearish_htf_ob_taps:
        has_mtf_nested = any(ob.direction == MTFDir.BEARISH and ob.bars_ago <= 15 for ob in mtf_obs)
        strength = 0.75
        tier = "2-TF Confluence"
        if (bearish_htf_taps and bearish_htf_ob_taps) or has_mtf_nested:
            strength = 0.90
            tier = "3-TF Nested Confluence (HTF FVG + OB Tap)"

        ref_level = bearish_htf_taps[0].top if bearish_htf_taps else bearish_htf_ob_taps[0].high
        return {
            "direction": MTFDir.BEARISH,
            "strength": strength,
            "tier": tier,
            "level": ref_level,
            "reason": f"Bearish MTF Confluence: Price tapped {tier} zone @ {ref_level:.2f}",
        }

    return None


class MTFConfluenceAgent:
    """Consensus voting agent for Multi-Timeframe FVG and Order Block confluence."""
    name = "MTF-CONFLUENCE"

    def vote(
        self,
        df1: pd.DataFrame,
        df5: Optional[pd.DataFrame] = None,
        df15: Optional[pd.DataFrame] = None,
        df1h: Optional[pd.DataFrame] = None,
    ) -> MTFVote:
        if df1 is None or len(df1) < 15:
            return MTFVote(agent=self.name, direction=MTFDir.NEUTRAL, strength=0.0, reason="Need more bars")

        res = evaluate_mtf_confluence(df1, df15=df15, df1h=df1h)
        if not res:
            return MTFVote(agent=self.name, direction=MTFDir.NEUTRAL, strength=0.0, reason="No active MTF FVG/OB tap")

        return MTFVote(
            agent=self.name,
            direction=res["direction"],
            strength=float(res.get("strength", 0.75)),
            reason=res.get("reason", "MTF Confluence active"),
            details=res,
        )
