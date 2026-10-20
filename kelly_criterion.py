#!/usr/bin/env python3
"""
Quantitative Risk & Money Management Mathematics Engine.
========================================================
1. Fractional Kelly Criterion Dynamic Lot Sizing:
   f* = (p * (b + 1) - 1) / b
   Supports Full, Half (0.50), and Quarter (0.25) Kelly fractions with hard safety caps.

2. Value at Risk (VaR 95% & 99%) & Conditional Value at Risk (CVaR / Expected Shortfall):
   Parametric (Gaussian) and Historical simulation models for single-asset and portfolio risk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class KellyResult:
    raw_kelly_fraction: float
    applied_kelly_fraction: float
    recommended_lots: float
    dollar_risk: float
    risk_pct_of_equity: float
    win_rate: float
    payoff_ratio: float
    fraction_mode: str  # e.g. "Quarter-Kelly (0.25x)"


@dataclass
class VaRResult:
    confidence_level: float  # 0.95 or 0.99
    horizon_days: int
    parametric_var_usd: float
    parametric_var_pct: float
    historical_var_usd: float
    historical_var_pct: float
    expected_shortfall_cvar_usd: float
    portfolio_equity: float


def calculate_kelly_fraction(
    win_rate: float,
    payoff_ratio: float,
    fraction_mult: float = 0.25,
    max_risk_cap: float = 0.03,
) -> Tuple[float, float]:
    """
    Computes raw and safe fractional Kelly sizing fraction.
    f* = (p * (b + 1) - 1) / b
    """
    p = float(win_rate)
    b = float(payoff_ratio)

    if p > 1.0 and p <= 100.0:
        p = p / 100.0

    if p <= 0 or b <= 0:
        return 0.0, 0.0

    # Cap probability at 0.99 for finite sample uncertainty
    p = min(p, 0.99)

    raw_f = (p * (b + 1.0) - 1.0) / b
    if raw_f <= 0:
        return 0.0, 0.0

    applied_f = min(raw_f * fraction_mult, max_risk_cap)
    return round(raw_f, 4), round(applied_f, 4)


def calculate_kelly_lot_size(
    equity: float,
    entry_price: float,
    stop_loss_price: float,
    win_rate: float = 0.55,
    payoff_ratio: float = 1.80,
    fraction_mult: float = 0.25,
    max_risk_cap: float = 0.025,
    point_value: float = 1.0,
    lot_step: float = 0.01,
    min_lot: float = 0.01,
    max_leverage: float = 20.0,
) -> KellyResult:
    """Calculates position lot size scaled by the Fractional Kelly Criterion."""
    raw_f, applied_f = calculate_kelly_fraction(
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        fraction_mult=fraction_mult,
        max_risk_cap=max_risk_cap,
    )

    dollar_risk = equity * applied_f
    stop_distance = abs(entry_price - stop_loss_price)

    if stop_distance <= 0 or entry_price <= 0:
        return KellyResult(raw_f, applied_f, min_lot, dollar_risk, applied_f * 100.0, win_rate, payoff_ratio, "Zero SL")

    # Units = Risk / (Stop Distance * Point Value)
    raw_lots = (dollar_risk) / (stop_distance * point_value * 100.0 if "XAU" in str(entry_price) else stop_distance)
    # Check max leverage constraint
    max_notional = equity * max_leverage
    max_lots = max_notional / max(entry_price, 1e-9)

    safe_lots = max(min_lot, min(raw_lots, max_lots))
    rounded_lots = round(round(safe_lots / lot_step) * lot_step, 2)

    mode_label = f"Quarter-Kelly ({fraction_mult:.2f}x)" if fraction_mult == 0.25 else f"Fractional Kelly ({fraction_mult:.2f}x)"

    return KellyResult(
        raw_kelly_fraction=raw_f,
        applied_kelly_fraction=applied_f,
        recommended_lots=rounded_lots,
        dollar_risk=round(dollar_risk, 2),
        risk_pct_of_equity=round(applied_f * 100.0, 2),
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        fraction_mode=mode_label,
    )


def compute_value_at_risk(
    returns: pd.Series | List[float],
    portfolio_equity: float,
    confidence_level: float = 0.99,
    horizon_days: int = 1,
) -> VaRResult:
    """
    Computes 95% or 99% Value at Risk (VaR) and Expected Shortfall (CVaR).
    """
    ret_series = pd.Series(returns, dtype=float).dropna()
    if len(ret_series) < 10:
        # Fallback conservative default
        default_var_pct = 0.02 if confidence_level < 0.98 else 0.035
        return VaRResult(
            confidence_level=confidence_level,
            horizon_days=horizon_days,
            parametric_var_usd=round(portfolio_equity * default_var_pct, 2),
            parametric_var_pct=round(default_var_pct * 100.0, 2),
            historical_var_usd=round(portfolio_equity * default_var_pct, 2),
            historical_var_pct=round(default_var_pct * 100.0, 2),
            expected_shortfall_cvar_usd=round(portfolio_equity * default_var_pct * 1.3, 2),
            portfolio_equity=portfolio_equity,
        )

    mu = float(ret_series.mean())
    sigma = float(ret_series.std())

    # Z-scores for confidence levels
    # 95% = 1.6449, 99% = 2.3263
    z_score = 2.3263 if confidence_level >= 0.985 else 1.6449
    sqrt_t = math.sqrt(horizon_days)

    # Parametric VaR (Gaussian)
    parametric_pct = max(0.0, (z_score * sigma - mu) * sqrt_t)
    parametric_usd = portfolio_equity * parametric_pct

    # Historical Simulation VaR
    quantile_alpha = 1.0 - confidence_level
    hist_loss_quantile = float(ret_series.quantile(quantile_alpha))
    hist_pct = max(0.0, abs(min(0.0, hist_loss_quantile)) * sqrt_t)
    hist_usd = portfolio_equity * hist_pct

    # Expected Shortfall (CVaR): Average loss beyond VaR threshold
    tail_losses = ret_series[ret_series <= hist_loss_quantile]
    if len(tail_losses) > 0:
        cvar_pct = abs(float(tail_losses.mean())) * sqrt_t
    else:
        cvar_pct = hist_pct * 1.25
    cvar_usd = portfolio_equity * cvar_pct

    return VaRResult(
        confidence_level=confidence_level,
        horizon_days=horizon_days,
        parametric_var_usd=round(parametric_usd, 2),
        parametric_var_pct=round(parametric_pct * 100.0, 2),
        historical_var_usd=round(hist_usd, 2),
        historical_var_pct=round(hist_pct * 100.0, 2),
        expected_shortfall_cvar_usd=round(cvar_usd, 2),
        portfolio_equity=round(portfolio_equity, 2),
    )
