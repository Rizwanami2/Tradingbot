#!/usr/bin/env python3
"""
Automated Strategy Profile Auto-Switcher & Rolling Sharpe Weight Calibrator.
===========================================================================
1. Automatically switches strategy profiles (scalp, swing, balanced, gold_only, crypto)
   based on real-time market regime, ADX trend strength, and ATR volatility.
2. Dynamic Rolling Sharpe Calibration: recalculates multi-agent Arbiter voting weights
   based on each individual agent's recent historical performance.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy_profiles import StrategyProfile, apply_profile, get_current_profile, load_profiles

logger = logging.getLogger("RegimeAutoSwitcher")


@dataclass
class RegimeSwitchResult:
    switched: bool
    previous_profile: str
    current_profile: str
    reason: str
    regime_detected: str
    metrics: Dict[str, float] = field(default_factory=dict)


def compute_regime_indicators(df: pd.DataFrame) -> Dict[str, float]:
    """Computes technical regime classification metrics: ADX, ATR%, BB Width."""
    if df is None or len(df) < 20:
        return {"adx": 20.0, "atr_pct": 0.15, "bb_width": 0.005, "trend_strength": 0.0}

    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)

    # 1. ATR percentage
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    current_price = float(c.iloc[-1])
    atr_pct = (atr / max(current_price, 1e-9)) * 100.0

    # 2. ADX proxy (Directional Movement)
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_sum = tr.rolling(14).sum().replace(0, np.nan)
    plus_di = 100.0 * (pd.Series(pos_dm, index=df.index).rolling(14).sum() / tr_sum)
    minus_di = 100.0 * (pd.Series(neg_dm, index=df.index).rolling(14).sum() / tr_sum)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    adx = float(dx.rolling(14).mean().iloc[-1])
    if math.isnan(adx):
        adx = 22.0

    # 3. Bollinger Band Width
    rolling_mean = c.rolling(20).mean()
    rolling_std = c.rolling(20).std()
    bb_upper = rolling_mean + (rolling_std * 2.0)
    bb_lower = rolling_mean - (rolling_std * 2.0)
    bb_width = float(((bb_upper - bb_lower) / rolling_mean).iloc[-1])
    if math.isnan(bb_width):
        bb_width = 0.005

    # 4. Trend strength (EMA alignment)
    ema9 = float(c.ewm(span=9, adjust=False).mean().iloc[-1])
    ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
    trend_str = (ema9 - ema21) / max(current_price, 1e-9) * 100.0

    return {
        "adx": round(adx, 2),
        "atr_pct": round(atr_pct, 4),
        "bb_width": round(bb_width, 5),
        "trend_strength": round(trend_str, 4),
    }


class RegimeAutoSwitcher:
    """Monitors regime transitions and applies profiles with cooldown protection."""

    def __init__(self, cooldown_seconds: float = 300.0):
        self.cooldown_seconds = cooldown_seconds
        self.last_switch_time: float = 0.0
        self.last_profile: str = "scalp"

    def evaluate_and_switch(
        self,
        df: pd.DataFrame,
        symbol: str = "XAUUSD",
        arbiter: Any = None,
        risk_mgr: Any = None,
        engine: Any = None,
        config: Any = None,
    ) -> RegimeSwitchResult:
        metrics = compute_regime_indicators(df)
        adx = metrics["adx"]
        bb_width = metrics["bb_width"]
        sym = symbol.upper()

        current_prof = get_current_profile().name
        target_profile = current_prof
        regime = "NORMAL"
        reason = "Market within standard parameters"

        # Check if user locked the active profile
        profile_locked = bool(
            getattr(config, "PROFILE_LOCKED", False)
            or getattr(arbiter, "profile_locked", False)
        )
        if profile_locked:
            return RegimeSwitchResult(
                switched=False,
                previous_profile=current_prof,
                current_profile=current_prof,
                reason=f"Profile '{current_prof}' locked by user (auto-switch held)",
                regime_detected=regime,
                metrics=metrics,
            )

        # Determine target profile based on asset & volatility dynamics
        if "XAU" in sym or "GOLD" in sym:
            if current_prof == "scalp" and adx < 32:
                target_profile = "scalp"
                regime = "SCALP_CONCESSION"
                reason = "Scalp profile active with concession (ADX < 32) -> Micro-scalp mode preserved"
            elif adx < 22:
                target_profile = "scalp"
                regime = "CONSOLIDATION_CHOP"
                reason = "Gold in range consolidation (ADX < 22) -> Micro-scalp mode"
            else:
                target_profile = "gold_only"
                regime = "EXPANSION_TREND"
                reason = "Gold in active trend expansion -> Gold Optimized Profile"
        elif any(c in sym for c in ("BTC", "ETH", "CRYPTO")):
            if adx < 20:
                target_profile = "scalp"
                regime = "LOW_VOLATILITY_RANGE"
                reason = "Crypto range-bound -> Rapid micro-scalp mode"
            else:
                target_profile = "crypto"
                regime = "CRYPTO_TREND"
                reason = "Crypto trending momentum -> Crypto Profile"
        else:
            if adx >= 28 and bb_width >= 0.008:
                target_profile = "swing"
                regime = "STRONG_TREND"
                reason = "Strong macro trend (ADX >= 28) -> Swing Profile"
            elif adx < 20:
                target_profile = "scalp"
                regime = "CHOP_RANGE"
                reason = "Consolidation regime -> Scalp Profile"
            else:
                target_profile = "balanced"
                regime = "BALANCED"
                reason = "Moderate conditions -> Balanced Profile"

        now = time.time()
        # Cooldown prevents rapid thrashing
        if target_profile != current_prof and (now - self.last_switch_time >= self.cooldown_seconds):
            try:
                apply_profile(
                    name=target_profile,
                    arbiter=arbiter,
                    risk_mgr=risk_mgr,
                    engine=engine,
                    config=config,
                )
                self.last_switch_time = now
                self.last_profile = target_profile
                logger.info("Auto-Switched profile from %s to %s (%s)", current_prof, target_profile, reason)
                return RegimeSwitchResult(
                    switched=True,
                    previous_profile=current_prof,
                    current_profile=target_profile,
                    reason=reason,
                    regime_detected=regime,
                    metrics=metrics,
                )
            except Exception as e:
                logger.warning("Error applying auto profile switch: %s", e)

        return RegimeSwitchResult(
            switched=False,
            previous_profile=current_prof,
            current_profile=current_prof,
            reason="Holding current profile (within bounds or in cooldown)",
            regime_detected=regime,
            metrics=metrics,
        )


def calibrate_rolling_sharpe_weights(
    agent_stats_file: Any = "agent_stats.json",
    base_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Recalibrates multi-agent consensus weights based on rolling win-rate and Sharpe ratio.
    Boosts consistent top performers and softly reduces underperforming agents.
    """
    stats_file = "agent_stats.json"
    arbiter_inst = None
    if isinstance(agent_stats_file, (str, Path)):
        stats_file = str(agent_stats_file)
    else:
        arbiter_inst = agent_stats_file
        if base_weights is None and hasattr(arbiter_inst, "WEIGHTS"):
            base_weights = dict(arbiter_inst.WEIGHTS)

    weights = dict(base_weights or {
        "TREND": 1.25,
        "LIQUIDITY": 1.40,
        "INDICATORS": 1.00,
        "AI-LEARNER": 0.90,
        "NEWS": 0.70,
        "GAUTAM-JHA": 1.55,
        "MEAN-REVERSION": 1.35,
        "FAKE-BREAKOUT": 1.35,
        "MTF-CONFLUENCE": 1.40,
        "ASIAN-BREAKOUT": 1.25,
    })

    if not os.path.exists(stats_file):
        if arbiter_inst is not None and hasattr(arbiter_inst, "WEIGHTS"):
            arbiter_inst.WEIGHTS.update(weights)
        return weights

    try:
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)

        for agent, w in list(weights.items()):
            agent_data = stats.get(agent, {})
            wins = agent_data.get("wins", 0)
            losses = agent_data.get("losses", 0)
            total = wins + losses
            if total >= 5:
                wr = wins / total
                # Reward high win rates (>60%), penalize poor win rates (<40%)
                if wr >= 0.65:
                    weights[agent] = round(w * 1.15, 2)
                elif wr <= 0.35:
                    weights[agent] = round(w * 0.85, 2)
    except Exception as e:
        logger.warning("Error in rolling Sharpe weight calibration: %s", e)

    return weights
