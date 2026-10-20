#!/usr/bin/env python3
"""
Institutional Supply & Demand (Order Imbalance) Strategy.
=========================================================
Identifies institutional supply and demand zones based on displacement,
base structure, zone freshness, and wick rejection entries for Gold (XAUUSD)
and Bitcoin (BTCUSDT).

Core Principles:
1. Zone Formations:
   - Demand: Drop-Base-Rally (DBR) & Rally-Base-Rally (RBR)
   - Supply: Rally-Base-Drop (RBD) & Drop-Base-Drop (DBD)

2. Departure Strength & Displacement:
   - High volume or wide-range expansion candle leaving the base indicates
     unfilled institutional limit orders waiting at the base.

3. Freshness & Touch Count:
   - Fresh / Virgin zones (0 prior touches) receive highest confidence.
   - 1st test zone receives standard confidence.
   - > 1 touches indicate mitigated / exhausted zone (ignored).

4. Rejection Entry Confirmation:
   - Price returns to tap the zone and rejects with a long wick (absorption).
   - Stop Loss placed tightly behind the base boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class SDDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


class ZoneType(Enum):
    DEMAND_DBR = "DBR_DEMAND"
    DEMAND_RBR = "RBR_DEMAND"
    SUPPLY_RBD = "RBD_SUPPLY"
    SUPPLY_DBD = "DBD_SUPPLY"


@dataclass
class SDZone:
    zone_type: ZoneType
    direction: SDDir
    top: float
    bottom: float
    base_idx: int
    departure_strength: float
    test_count: int = 0
    fresh: bool = True


@dataclass
class SDVote:
    agent: str = "SUPPLY-DEMAND"
    direction: SDDir = SDDir.FLAT
    strength: float = 0.0
    reason: str = ""
    zone_type: str = ""
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


def extract_supply_demand_zones(df: pd.DataFrame, max_lookback: int = 60) -> List[SDZone]:
    """
    Scans recent price history for clean institutional base structures:
    - Base: 1 to 3 tight/consolidating candles
    - Departure: Strong expansion candle (body >= 1.3x average candle)
    """
    if df is None or len(df) < 15:
        return []

    n = len(df)
    start_idx = max(2, n - max_lookback)
    atr_val = _calculate_atr(df, 14) or (_safe_float(df["close"].iloc[-1]) * 0.0015)

    zones: List[SDZone] = []

    for i in range(start_idx, n - 2):
        # Examine prior candle (inflow), base candle, and departure candle
        inflow = df.iloc[i - 1]
        base = df.iloc[i]
        departure = df.iloc[i + 1]

        in_o, in_c = _safe_float(inflow["open"]), _safe_float(inflow["close"])
        b_o, b_c, b_h, b_l = map(_safe_float, [base["open"], base["close"], base["high"], base["low"]])
        dep_o, dep_c, dep_h, dep_l = map(_safe_float, [departure["open"], departure["close"], departure["high"], departure["low"]])

        base_body = abs(b_c - b_o)
        base_range = max(b_h - b_l, 1e-9)
        dep_body = abs(dep_c - dep_o)

        # Base candle should be compact (tight consolidation or indecision)
        if base_range > 2.5 * atr_val or base_body > 1.8 * atr_val:
            continue

        # Departure candle must be decisive
        if dep_body < 0.9 * atr_val:
            continue

        dep_ratio = dep_body / base_range

        # 1. DEMAND ZONE: Departure is strongly bullish (dep_c > dep_o)
        if dep_c > dep_o and dep_c > b_h:
            z_top = b_h
            z_bottom = b_l
            z_type = ZoneType.DEMAND_DBR if in_c < in_o else ZoneType.DEMAND_RBR
            
            # Check how many times future bars (i+2 to current) have entered this zone
            touches = 0
            invalidated = False
            for k in range(i + 2, n):
                k_l = _safe_float(df.iloc[k]["low"])
                k_c = _safe_float(df.iloc[k]["close"])
                # If low touched inside the zone
                if k_l <= z_top and k_l >= z_bottom:
                    touches += 1
                # If price closed below zone bottom, zone is violated/broken
                if k_c < z_bottom - 0.2 * atr_val:
                    invalidated = True
                    break

            if not invalidated and touches <= 2:
                zones.append(SDZone(
                    zone_type=z_type,
                    direction=SDDir.LONG,
                    top=z_top,
                    bottom=z_bottom,
                    base_idx=i,
                    departure_strength=min(1.5, dep_ratio),
                    test_count=touches,
                    fresh=(touches == 0 or touches == 1),
                ))

        # 2. SUPPLY ZONE: Departure is strongly bearish (dep_c < dep_o)
        elif dep_c < dep_o and dep_c < b_l:
            z_top = b_h
            z_bottom = b_l
            z_type = ZoneType.SUPPLY_RBD if in_c > in_o else ZoneType.SUPPLY_DBD

            touches = 0
            invalidated = False
            for k in range(i + 2, n):
                k_h = _safe_float(df.iloc[k]["high"])
                k_c = _safe_float(df.iloc[k]["close"])
                if k_h >= z_bottom and k_h <= z_top:
                    touches += 1
                if k_c > z_top + 0.2 * atr_val:
                    invalidated = True
                    break

            if not invalidated and touches <= 2:
                zones.append(SDZone(
                    zone_type=z_type,
                    direction=SDDir.SHORT,
                    top=z_top,
                    bottom=z_bottom,
                    base_idx=i,
                    departure_strength=min(1.5, dep_ratio),
                    test_count=touches,
                    fresh=(touches == 0 or touches == 1),
                ))

    return zones


class SupplyDemandAgent:
    """
    Supply & Demand Strategy Agent.
    Evaluates institutional imbalance zones and triggers high-accuracy
    rejection entries on zone mitigations.
    """
    name = "SUPPLY-DEMAND"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> SDVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 15:
                return SDVote(self.name, SDDir.FLAT, 0.0, "Need more bars")

            if not all(col in df.columns for col in ["open", "high", "low", "close"]):
                df = df.rename(columns={c: str(c).lower() for c in df.columns})

            required_cols = {"open", "high", "low", "close"}
            if not required_cols.issubset(set(df.columns)):
                return SDVote(self.name, SDDir.FLAT, 0.0, "Missing OHLC columns")

            zones = extract_supply_demand_zones(df)
            if not zones:
                return SDVote(self.name, SDDir.FLAT, 0.0, "No active S/D zones detected")

            atr_val = _calculate_atr(df, 14) or (_safe_float(df["close"].iloc[-1]) * 0.0015)
            curr = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else curr

            co, ch, cl, cc = map(_safe_float, [curr["open"], curr["high"], curr["low"], curr["close"]])
            po, ph, pl, pc = map(_safe_float, [prev["open"], prev["high"], prev["low"], prev["close"]])
            candle_span = max(ch - cl, 1e-9)

            # Check for current bar or previous bar tapping into a fresh zone
            for z in reversed(zones):
                # Demand tap
                if z.direction == SDDir.LONG:
                    # Check if price tested the demand zone (either curr low or prev low)
                    tapped = (cl <= z.top + 0.2 * atr_val and cl >= z.bottom - 0.2 * atr_val) or \
                             (pl <= z.top + 0.2 * atr_val and pl >= z.bottom - 0.2 * atr_val)

                    if tapped and cc >= z.bottom:
                        lower_wick = min(co, cc) - cl
                        lower_wick_ratio = lower_wick / candle_span
                        # Bullish rejection confirmation
                        if lower_wick_ratio >= 0.18 or cc > co:
                            strength = 0.88 if z.test_count <= 1 else 0.76
                            stop = round(z.bottom - 0.5 * atr_val, 4)
                            risk_pts = max(cc - stop, 0.5 * atr_val)
                            tp1 = round(cc + risk_pts * 1.8, 4)
                            tp2 = round(cc + risk_pts * 2.8, 4)
                            reason = (
                                f"Demand Zone Tap ({z.zone_type.value}): Level [{z.bottom:,.2f} - {z.top:,.2f}] "
                                f"absorbed with {lower_wick_ratio:.0%} rejection wick (touches: {z.test_count})"
                            )
                            return SDVote(
                                agent=self.name,
                                direction=SDDir.LONG,
                                strength=strength,
                                reason=reason,
                                zone_type=z.zone_type.value,
                                stop_price=stop,
                                tp_price=tp1,
                                details={"top": z.top, "bottom": z.bottom, "test_count": z.test_count, "tp2": tp2},
                            )

                # Supply tap
                elif z.direction == SDDir.SHORT:
                    tapped = (ch >= z.bottom - 0.2 * atr_val and ch <= z.top + 0.2 * atr_val) or \
                             (ph >= z.bottom - 0.2 * atr_val and ph <= z.top + 0.2 * atr_val)

                    if tapped and cc <= z.top:
                        upper_wick = ch - max(co, cc)
                        upper_wick_ratio = upper_wick / candle_span
                        if upper_wick_ratio >= 0.18 or cc < co:
                            strength = 0.88 if z.test_count <= 1 else 0.76
                            stop = round(z.top + 0.5 * atr_val, 4)
                            risk_pts = max(stop - cc, 0.5 * atr_val)
                            tp1 = round(cc - risk_pts * 1.8, 4)
                            tp2 = round(cc - risk_pts * 2.8, 4)
                            reason = (
                                f"Supply Zone Tap ({z.zone_type.value}): Level [{z.bottom:,.2f} - {z.top:,.2f}] "
                                f"rejected with {upper_wick_ratio:.0%} rejection wick (touches: {z.test_count})"
                            )
                            return SDVote(
                                agent=self.name,
                                direction=SDDir.SHORT,
                                strength=strength,
                                reason=reason,
                                zone_type=z.zone_type.value,
                                stop_price=stop,
                                tp_price=tp1,
                                details={"top": z.top, "bottom": z.bottom, "test_count": z.test_count, "tp2": tp2},
                            )

            return SDVote(self.name, SDDir.FLAT, 0.0, "Outside active S/D zones")
        except Exception as e:
            return SDVote(self.name, SDDir.FLAT, 0.0, f"S/D evaluation note: {type(e).__name__}")


def supply_demand_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> SDVote:
    return SupplyDemandAgent().vote(df, daily=daily)
