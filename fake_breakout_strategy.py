#!/usr/bin/env python3
"""
Institutional Fake Breakout & Liquidity Sweep Strategy.
======================================================
Comprehensive detection of institutional traps and false breakouts:
1. Swing Failure Pattern (SFP) — Wick sweep of key swing high/low with body close inside.
2. Turtle Soup (Linda Raschke) — 20-bar extreme pierced and reversed back into prior range.
3. Wyckoff Spring & UTAD — Range boundary breakdown/breakout rejection on high volume.
4. Judas Swing — Session Open (London/NY) liquidity run against Asian session range.
5. Equal Highs / Lows (EQH/EQL) Sweep — Sweeping resting buy/sell-side liquidity pools.
6. Failed Break-and-Retest Trap — Retest fails to hold and collapses back through boundary.

Compatible with the Multi-Agent Arbiter consensus voting engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class FBDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class FBVote:
    agent: str = "FAKE-BREAKOUT"
    direction: FBDir = FBDir.FLAT
    strength: float = 0.0
    reason: str = ""
    pattern: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def detect_swing_failure_pattern(
    df: pd.DataFrame,
    lookback: int = 20,
    wick_ratio_min: float = 0.35,
) -> Optional[Dict[str, Any]]:
    """
    1. Swing Failure Pattern (SFP):
    Price wicks beyond a prominent swing high/low, sweeps liquidity/stops,
    and closes back inside the range with a pronounced rejection wick.
    """
    if df is None or len(df) < lookback + 2:
        return None

    # Reference swing window excludes the last 2 candles
    window = df.iloc[-(lookback + 2):-2]
    if len(window) < 5:
        return None

    swing_high = float(window["high"].max())
    swing_low = float(window["low"].min())

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    curr_open = _safe_float(curr["open"])
    curr_high = _safe_float(curr["high"])
    curr_low = _safe_float(curr["low"])
    curr_close = _safe_float(curr["close"])
    curr_range = max(curr_high - curr_low, 1e-9)

    # (A) Bullish SFP: Swept Swing Low
    swept_low = (curr_low < swing_low) or (_safe_float(prev["low"]) < swing_low and curr_low <= swing_low * 1.0005)
    closed_above_low = curr_close > swing_low
    lower_wick = max(0.0, min(curr_open, curr_close) - curr_low)
    lower_wick_ratio = lower_wick / curr_range

    if swept_low and closed_above_low and lower_wick_ratio >= wick_ratio_min:
        overshoot_pct = (swing_low - curr_low) / max(swing_low, 1e-9) * 100.0
        strength = min(0.95, 0.75 + lower_wick_ratio * 0.20)
        return {
            "pattern": "SFP_BULLISH",
            "direction": FBDir.LONG,
            "strength": round(strength, 2),
            "level": swing_low,
            "overshoot_pct": round(overshoot_pct, 3),
            "wick_ratio": round(lower_wick_ratio, 2),
            "reason": f"Bullish SFP: Swept Swing Low ({swing_low:.2f}) with {lower_wick_ratio:.0%} lower wick rejection",
        }

    # (B) Bearish SFP: Swept Swing High
    swept_high = (curr_high > swing_high) or (_safe_float(prev["high"]) > swing_high and curr_high >= swing_high * 0.9995)
    closed_below_high = curr_close < swing_high
    upper_wick = max(0.0, curr_high - max(curr_open, curr_close))
    upper_wick_ratio = upper_wick / curr_range

    if swept_high and closed_below_high and upper_wick_ratio >= wick_ratio_min:
        overshoot_pct = (curr_high - swing_high) / max(swing_high, 1e-9) * 100.0
        strength = min(0.95, 0.75 + upper_wick_ratio * 0.20)
        return {
            "pattern": "SFP_BEARISH",
            "direction": FBDir.SHORT,
            "strength": round(strength, 2),
            "level": swing_high,
            "overshoot_pct": round(overshoot_pct, 3),
            "wick_ratio": round(upper_wick_ratio, 2),
            "reason": f"Bearish SFP: Swept Swing High ({swing_high:.2f}) with {upper_wick_ratio:.0%} upper wick rejection",
        }

    return None


def detect_turtle_soup(
    df: pd.DataFrame,
    lookback: int = 20,
    min_bars_since_prev: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    2. Turtle Soup (Linda Raschke 20-bar Reversal):
    New 20-bar high/low made after at least min_bars_since_prev bars,
    reversing back inside the 20-bar range within 1-2 bars.
    """
    if df is None or len(df) < lookback + 5:
        return None

    # Reference 20 bars prior to the recent test
    ref_window = df.iloc[-(lookback + 2):-2]
    if len(ref_window) < lookback:
        return None

    prior_20_high = float(ref_window["high"].max())
    prior_20_low = float(ref_window["low"].min())

    idx_high = int(np.argmax(ref_window["high"].values))
    idx_low = int(np.argmin(ref_window["low"].values))
    bars_since_high = len(ref_window) - 1 - idx_high
    bars_since_low = len(ref_window) - 1 - idx_low

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    curr_close = _safe_float(curr["close"])
    curr_low = _safe_float(curr["low"])
    curr_high = _safe_float(curr["high"])
    prev_low = _safe_float(prev["low"])
    prev_high = _safe_float(prev["high"])

    # Turtle Soup Buy (Long)
    if bars_since_low >= min_bars_since_prev:
        made_new_low = (curr_low < prior_20_low) or (prev_low < prior_20_low)
        reversal_back = curr_close > prior_20_low and curr_close > _safe_float(prev["open"])
        if made_new_low and reversal_back:
            margin_pct = (prior_20_low - min(curr_low, prev_low)) / max(prior_20_low, 1e-9) * 100.0
            if margin_pct <= 0.80:  # Not an outright freefall
                return {
                    "pattern": "TURTLE_SOUP_BUY",
                    "direction": FBDir.LONG,
                    "strength": 0.85,
                    "level": prior_20_low,
                    "reason": f"Turtle Soup Buy: 20-bar Low trap @ {prior_20_low:.2f} ({margin_pct:.2f}% breach reversed)",
                }

    # Turtle Soup Sell (Short)
    if bars_since_high >= min_bars_since_prev:
        made_new_high = (curr_high > prior_20_high) or (prev_high > prior_20_high)
        reversal_back = curr_close < prior_20_high and curr_close < _safe_float(prev["open"])
        if made_new_high and reversal_back:
            margin_pct = (max(curr_high, prev_high) - prior_20_high) / max(prior_20_high, 1e-9) * 100.0
            if margin_pct <= 0.80:
                return {
                    "pattern": "TURTLE_SOUP_SELL",
                    "direction": FBDir.SHORT,
                    "strength": 0.85,
                    "level": prior_20_high,
                    "reason": f"Turtle Soup Sell: 20-bar High trap @ {prior_20_high:.2f} ({margin_pct:.2f}% breach reversed)",
                }

    return None


def detect_wyckoff_spring_utad(
    df: pd.DataFrame,
    range_lookback: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    3. Wyckoff Spring & UTAD (Upthrust After Distribution):
    Spring: Range support breakdown test, rejected back into consolidation with volume.
    UTAD: Range resistance breakout test, rejected back into distribution range.
    """
    if df is None or len(df) < range_lookback + 3:
        return None

    range_df = df.iloc[-range_lookback:-2]
    tr_high = float(range_df["high"].quantile(0.90))
    tr_low = float(range_df["low"].quantile(0.10))
    tr_width = (tr_high - tr_low) / max(tr_low, 1e-9)

    # Require a discernible trading range (e.g. 0.3% - 4.0% range)
    if not (0.003 <= tr_width <= 0.05):
        return None

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    curr_close = _safe_float(curr["close"])
    curr_low = _safe_float(curr["low"])
    curr_high = _safe_float(curr["high"])

    # Volume comparison if available
    vol_confirm = 1.0
    if "volume" in df.columns:
        avg_vol = _safe_float(range_df["volume"].mean(), 1.0)
        curr_vol = _safe_float(curr["volume"], 1.0)
        if avg_vol > 0 and curr_vol > avg_vol * 1.15:
            vol_confirm = 1.10

    # (A) Wyckoff Spring (Phase C Accumulation Trap)
    dipped_below_tr = (curr_low < tr_low) or (_safe_float(prev["low"]) < tr_low)
    reclaimed_tr = curr_close > tr_low and curr_close > _safe_float(curr["open"])
    if dipped_below_tr and reclaimed_tr:
        strength = min(0.92, 0.80 * vol_confirm)
        return {
            "pattern": "WYCKOFF_SPRING",
            "direction": FBDir.LONG,
            "strength": round(strength, 2),
            "level": tr_low,
            "reason": f"Wyckoff Spring: TR support ({tr_low:.2f}) shakeout reclaimed on volume",
        }

    # (B) Wyckoff UTAD (Upthrust After Distribution Trap)
    popped_above_tr = (curr_high > tr_high) or (_safe_float(prev["high"]) > tr_high)
    rejected_back_tr = curr_close < tr_high and curr_close < _safe_float(curr["open"])
    if popped_above_tr and rejected_back_tr:
        strength = min(0.92, 0.80 * vol_confirm)
        return {
            "pattern": "WYCKOFF_UTAD",
            "direction": FBDir.SHORT,
            "strength": round(strength, 2),
            "level": tr_high,
            "reason": f"Wyckoff UTAD: TR resistance ({tr_high:.2f}) upthrust rejected back inside",
        }

    return None


def detect_judas_swing(
    df: pd.DataFrame,
    daily: Optional[pd.DataFrame] = None,
    asian_bars: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    4. The Judas Swing (Session Open False Breakout):
    Spike in opposite direction of trend at London/NY open to sweep Asian/pre-session liquidity.
    """
    if df is None or len(df) < asian_bars + 5:
        return None

    # Reference Asian/pre-session range
    pre_window = df.iloc[-(asian_bars + 5):-5]
    if len(pre_window) < 15:
        return None

    asian_high = float(pre_window["high"].max())
    asian_low = float(pre_window["low"].min())

    # Recent 5 bars = Session open expansion
    recent = df.iloc[-5:]
    recent_high = float(recent["high"].max())
    recent_low = float(recent["low"].min())

    curr = df.iloc[-1]
    curr_close = _safe_float(curr["close"])
    curr_open = _safe_float(curr["open"])

    # (A) Bullish Judas (Bear Trap): Pierced Asian Low during open, now closing back above
    if recent_low < asian_low and curr_close > asian_low and curr_close > curr_open:
        wick = _safe_float(curr["close"]) - _safe_float(curr["low"])
        c_range = max(_safe_float(curr["high"]) - _safe_float(curr["low"]), 1e-9)
        if wick / c_range >= 0.30:
            return {
                "pattern": "JUDAS_SWING_BULLISH",
                "direction": FBDir.LONG,
                "strength": 0.88,
                "level": asian_low,
                "reason": f"Judas Swing Bullish: Asian Low ({asian_low:.2f}) swept at session open + reversed",
            }

    # (B) Bearish Judas (Bull Trap): Pierced Asian High during open, now closing back below
    if recent_high > asian_high and curr_close < asian_high and curr_close < curr_open:
        wick = _safe_float(curr["high"]) - _safe_float(curr["close"])
        c_range = max(_safe_float(curr["high"]) - _safe_float(curr["low"]), 1e-9)
        if wick / c_range >= 0.30:
            return {
                "pattern": "JUDAS_SWING_BEARISH",
                "direction": FBDir.SHORT,
                "strength": 0.88,
                "level": asian_high,
                "reason": f"Judas Swing Bearish: Asian High ({asian_high:.2f}) swept at session open + reversed",
            }

    return None


def detect_equal_highs_lows_sweep(
    df: pd.DataFrame,
    lookback: int = 30,
    tol: float = 0.0008,
) -> Optional[Dict[str, Any]]:
    """
    5. Equal Highs / Equal Lows (EQH/EQL) Liquidity Sweep:
    Sweeps resting buy-side liquidity (BSL) or sell-side liquidity (SSL) clustered around double tops/bottoms.
    """
    if df is None or len(df) < lookback:
        return None

    window = df.iloc[-lookback:-1]
    highs = window["high"].values
    lows = window["low"].values

    curr = df.iloc[-1]
    curr_high = _safe_float(curr["high"])
    curr_low = _safe_float(curr["low"])
    curr_close = _safe_float(curr["close"])

    # Search for EQH clusters in history
    for i in range(len(highs) - 3):
        h1 = float(highs[i])
        matches = [float(h2) for h2 in highs[i + 1:] if abs(h1 - h2) / max(h1, 1e-9) < tol]
        if len(matches) >= 1:
            eqh_level = float(np.mean([h1] + matches))
            # Sweep: current candle poked above EQH by up to 0.4%, but closed below it
            if curr_high > eqh_level and curr_close < eqh_level:
                overshoot = (curr_high - eqh_level) / eqh_level * 100.0
                if 0.01 <= overshoot <= 0.60:
                    return {
                        "pattern": "EQH_LIQUIDITY_SWEEP",
                        "direction": FBDir.SHORT,
                        "strength": 0.85,
                        "level": eqh_level,
                        "reason": f"Equal Highs (EQH) Liquidity Swept @ {eqh_level:.2f} (BSL Hunted)",
                    }

    # Search for EQL clusters in history
    for i in range(len(lows) - 3):
        l1 = float(lows[i])
        matches = [float(l2) for l2 in lows[i + 1:] if abs(l1 - l2) / max(l1, 1e-9) < tol]
        if len(matches) >= 1:
            eql_level = float(np.mean([l1] + matches))
            # Sweep: current candle poked below EQL by up to 0.4%, but closed above it
            if curr_low < eql_level and curr_close > eql_level:
                overshoot = (eql_level - curr_low) / eql_level * 100.0
                if 0.01 <= overshoot <= 0.60:
                    return {
                        "pattern": "EQL_LIQUIDITY_SWEEP",
                        "direction": FBDir.LONG,
                        "strength": 0.85,
                        "level": eql_level,
                        "reason": f"Equal Lows (EQL) Liquidity Swept @ {eql_level:.2f} (SSL Hunted)",
                    }

    return None


def detect_failed_break_and_retest(
    df: pd.DataFrame,
    lookback: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    6. Failed Break-and-Retest Trap:
    Breakout occurred 3 to 10 bars ago, but the retest fails to hold and price crashes back inside.
    """
    if df is None or len(df) < lookback:
        return None

    # Reference structure level prior to breakout (bars -25 to -10)
    base_window = df.iloc[-lookback:-10]
    if len(base_window) < 10:
        return None

    res_level = float(base_window["high"].max())
    sup_level = float(base_window["low"].min())

    break_window = df.iloc[-10:-2]
    curr = df.iloc[-1]
    curr_close = _safe_float(curr["close"])

    # (A) Failed Bullish Retest (Bull Trap)
    # Broke out above res_level during break_window
    broke_above = float(break_window["high"].max()) > res_level * 1.0005
    # Retest was attempted, but current bar closed firmly back BELOW the broken resistance
    if broke_above and curr_close < res_level * 0.9995:
        return {
            "pattern": "FAILED_RETEST_BULL_TRAP",
            "direction": FBDir.SHORT,
            "strength": 0.82,
            "level": res_level,
            "reason": f"Failed Break & Retest (Bull Trap): Broke {res_level:.2f} but collapsed back inside",
        }

    # (B) Failed Bearish Retest (Bear Trap)
    # Broke down below sup_level during break_window
    broke_below = float(break_window["low"].min()) < sup_level * 0.9995
    # Retest was attempted, but current bar closed firmly back ABOVE the broken support
    if broke_below and curr_close > sup_level * 1.0005:
        return {
            "pattern": "FAILED_RETEST_BEAR_TRAP",
            "direction": FBDir.LONG,
            "strength": 0.82,
            "level": sup_level,
            "reason": f"Failed Break & Retest (Bear Trap): Broke {sup_level:.2f} but recovered back inside",
        }

    return None


def detect_all_fake_breakouts(
    df: pd.DataFrame,
    daily: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    """Run all 6 fake breakout detectors and return list of detected setups."""
    if df is None or getattr(df, "empty", True) or len(df) < 15:
        return []

    setups: List[Dict[str, Any]] = []

    sfp = detect_swing_failure_pattern(df)
    if sfp:
        setups.append(sfp)

    turtle = detect_turtle_soup(df)
    if turtle:
        setups.append(turtle)

    wyckoff = detect_wyckoff_spring_utad(df)
    if wyckoff:
        setups.append(wyckoff)

    judas = detect_judas_swing(df, daily)
    if judas:
        setups.append(judas)

    eq_sweep = detect_equal_highs_lows_sweep(df)
    if eq_sweep:
        setups.append(eq_sweep)

    retest_trap = detect_failed_break_and_retest(df)
    if retest_trap:
        setups.append(retest_trap)

    # Sort by strength descending
    setups.sort(key=lambda s: float(s.get("strength", 0.0)), reverse=True)
    return setups


class FakeBreakoutAgent:
    """
    Multi-Agent Consensus Agent: Fake Breakout & Liquidity Sweep Hunter.
    Evaluates 6 institutional false breakout patterns and casts a vote.
    """
    name = "FAKE-BREAKOUT"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> FBVote:
        if df is None or getattr(df, "empty", True) or len(df) < 15:
            return FBVote(agent=self.name, direction=FBDir.FLAT, strength=0.0, reason="Need more bars")

        setups = detect_all_fake_breakouts(df, daily)
        if not setups:
            return FBVote(agent=self.name, direction=FBDir.FLAT, strength=0.0, reason="No fake breakout active")

        top = setups[0]
        direction = top.get("direction", FBDir.FLAT)
        strength = float(top.get("strength", 0.75))
        reason = top.get("reason", "Fake breakout detected")
        pattern = top.get("pattern", "")

        # If multiple patterns agree in the same direction, boost strength for confluence
        matching_dirs = [s for s in setups if s.get("direction") == direction]
        if len(matching_dirs) >= 2:
            strength = min(1.0, strength + 0.10)
            reasons = " + ".join([s.get("pattern", "") for s in matching_dirs[:2]])
            reason = f"Confluence ({reasons}): {reason}"

        return FBVote(
            agent=self.name,
            direction=direction,
            strength=round(strength, 2),
            reason=reason,
            pattern=pattern,
            details=top,
        )


if __name__ == "__main__":
    # Self-test with demo synthetic dataframe
    np.random.seed(42)
    n = 60
    prices = [2650.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + np.random.normal(0, 1.0))
    df_demo = pd.DataFrame({
        "open": prices,
        "high": [p + abs(np.random.normal(0.5, 0.5)) for p in prices],
        "low": [p - abs(np.random.normal(0.5, 0.5)) for p in prices],
        "close": [p + np.random.normal(0, 0.8) for p in prices],
        "volume": [1000 + np.random.randint(0, 500) for _ in prices],
    })
    agent = FakeBreakoutAgent()
    v = agent.vote(df_demo)
    print("Demo Vote:", v)
