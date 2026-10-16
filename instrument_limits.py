"""
Instrument-Specific Leverage & Risk Caps
========================================
Hard limits per asset class to prevent dangerous over-leverage.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# Max leverage by instrument / class
LEVERAGE_CAPS: Dict[str, float] = {
    # Metals
    "XAUUSD": 50.0,
    "XAUTUSD": 100.0,
    "GOLD": 50.0,
    "XAUUSDT": 50.0,
    "GC=F": 50.0,
    # Major Forex
    "EURUSD": 30.0,
    "GBPUSD": 30.0,
    "USDJPY": 30.0,
    "AUDUSD": 30.0,
    "USDCAD": 30.0,
    "USDCHF": 30.0,
    # Crypto (Standard & Delta Exchange India up to 200x)
    "BTCUSD": 200.0,
    "BTCUSDT": 200.0,
    "ETHUSD": 200.0,
    "ETHUSDT": 200.0,
    "PAXGUSD": 100.0,
    "DEFAULT": 20.0,
}

# Delta Exchange India specific leverage caps (up to 200x)
DELTA_LEVERAGE_CAPS: Dict[str, float] = {
    "BTCUSD": 200.0,
    "BTCUSDT": 200.0,
    "BTC": 200.0,
    "ETHUSD": 200.0,
    "ETHUSDT": 200.0,
    "ETH": 200.0,
    "XAUTUSD": 100.0,
    "PAXGUSD": 100.0,
    "SOLUSD": 100.0,
    "DEFAULT": 200.0,
}

# Max risk % of equity per trade by class
RISK_CAPS: Dict[str, float] = {
    "XAUUSD": 0.40,
    "XAUTUSD": 0.40,
    "GOLD": 0.40,
    "BTCUSD": 0.30,
    "BTCUSDT": 0.30,
    "DEFAULT": 0.50,
}


def get_max_leverage(symbol: str, is_delta: bool = False, max_override: Optional[float] = None) -> float:
    if max_override is not None and max_override > 0:
        return float(max_override)
    sym = symbol.upper().replace("/", "").replace("-", "")
    if is_delta:
        if sym in DELTA_LEVERAGE_CAPS:
            return DELTA_LEVERAGE_CAPS[sym]
        if any(x in sym for x in ("BTC", "ETH")):
            return 200.0
        if any(x in sym for x in ("XAU", "GOLD", "PAXG", "XAUT")):
            return 100.0
        return DELTA_LEVERAGE_CAPS["DEFAULT"]

    if sym in LEVERAGE_CAPS:
        return LEVERAGE_CAPS[sym]
    if "BTC" in sym or "ETH" in sym:
        return 200.0
    if "XAU" in sym or "GOLD" in sym:
        return 50.0
    if any(x in sym for x in ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF")):
        return 30.0
    return LEVERAGE_CAPS["DEFAULT"]


def get_max_risk_pct(symbol: str) -> float:
    sym = symbol.upper().replace("/", "")
    return RISK_CAPS.get(sym, RISK_CAPS["DEFAULT"])


def clamp_leverage(symbol: str, requested: float, is_delta: bool = False, max_override: Optional[float] = None) -> Tuple[float, str]:
    """Clamp requested leverage to instrument-safe maximum (supports up to 200x)."""
    cap = get_max_leverage(symbol, is_delta=is_delta, max_override=max_override)
    req = float(requested)
    if req > cap:
        return cap, f"Clamped {req:.0f}x → {cap:.0f}x (instrument cap for {symbol.upper()})"
    if req < 1.0:
        return 1.0, "Leverage raised to minimum 1x"
    return req, f"OK {req:.0f}x (cap {cap:.0f}x)"
