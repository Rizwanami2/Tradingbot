"""
Signal Quality Filters (47-51)
==============================
- Liquidity / imbalance proxy
- Session volume-node awareness
- Spread + slippage veto
- Relative strength rank helper
- Candle quality (doji / wick / range) filter
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass
class QualityResult:
    allowed: bool
    score: float  # 0..1
    reasons: List[str]


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    if df is None or len(df) < n + 1:
        return 0.0
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-n:])) if len(tr) >= n else 0.0


def liquidity_imbalance_score(df: pd.DataFrame, lookback: int = 40) -> Tuple[float, str]:
    """
    Proxy for FVG / imbalance density.
    Higher score when recent candles left unfilled gaps (bullish or bearish).
    """
    if df is None or len(df) < lookback + 3:
        return 0.5, "insufficient data"
    h = df["high"].values[-lookback:]
    l = df["low"].values[-lookback:]
    gaps = 0
    for i in range(2, len(h)):
        # classic 3-candle FVG proxies
        if l[i] > h[i - 2]:  # bullish gap
            gaps += 1
        if h[i] < l[i - 2]:  # bearish gap
            gaps += 1
    density = gaps / max(1, lookback - 2)
    score = float(min(1.0, density * 4.0))
    return score, f"imbalance density {density:.2f}"


def session_volume_node_ok(df: pd.DataFrame, direction: str, lookback: int = 60) -> Tuple[bool, str]:
    """
    Simple volume-profile proxy: require breakout candle volume > median of recent bars.
    """
    if df is None or len(df) < lookback or "volume" not in df.columns:
        return True, "no volume data — skip"
    vol = df["volume"].values[-lookback:]
    med = float(np.median(vol[:-1])) if len(vol) > 1 else 0.0
    last = float(vol[-1])
    if med <= 0:
        return True, "flat volume"
    if last < med * 0.85:
        return False, f"breakout volume {last:.0f} < median {med:.0f}"
    return True, f"volume ok ({last:.0f} vs med {med:.0f})"


def spread_slippage_veto(
    spread: float,
    atr: float,
    expected_rr: float = 2.0,
    max_spread_atr: float = 0.18,
    slippage_atr: float = 0.05,
) -> Tuple[bool, str]:
    """
    Reject if spread is too wide relative to ATR or if slippage would kill R:R.
    spread / atr should be in price units same as ATR.
    """
    if atr <= 0:
        return True, "no atr"
    ratio = spread / atr
    if ratio > max_spread_atr:
        return False, f"spread {ratio:.2%} ATR > limit {max_spread_atr:.0%}"
    # rough R:R degradation
    effective_rr = expected_rr * (1.0 - (slippage_atr + ratio))
    if effective_rr < 1.4:
        return False, f"slippage+spread kills R:R to ~{effective_rr:.2f}"
    return True, f"spread ok ({ratio:.2%} ATR)"


def relative_strength_rank(
    series_map: Dict[str, pd.Series],
    lookback: int = 20,
) -> List[Tuple[str, float]]:
    """
    Rank symbols by momentum (last return over lookback).
    series_map: symbol -> close series
    """
    ranks = []
    for sym, s in series_map.items():
        if s is None or len(s) < lookback + 1:
            ranks.append((sym, 0.0))
            continue
        denom = float(s.iloc[-lookback - 1])
        if denom == 0 or np.isnan(denom):
            ranks.append((sym, 0.0))
            continue
        ret = float(s.iloc[-1] / denom - 1.0)
        ranks.append((sym, ret))
    ranks.sort(key=lambda x: x[1], reverse=True)
    return ranks


def candle_quality(df: pd.DataFrame) -> Tuple[bool, str, float]:
    """
    Reject poor candles: doji, excessive wick, abnormal range.
    Returns (ok, reason, quality_score 0..1)
    """
    if df is None or len(df) < 5:
        return True, "insufficient", 0.5
    o = float(df["open"].iloc[-1])
    h = float(df["high"].iloc[-1])
    l = float(df["low"].iloc[-1])
    c = float(df["close"].iloc[-1])
    rng = max(h - l, 1e-12)
    body = abs(c - o)
    body_ratio = body / rng
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # recent average range
    avg_rng = float((df["high"] - df["low"]).tail(20).mean()) or rng

    if body_ratio < 0.12:
        return False, "doji / tiny body", 0.2
    if upper_wick / rng > 0.65 and body_ratio < 0.3:
        return False, "heavy upper wick", 0.25
    if lower_wick / rng > 0.65 and body_ratio < 0.3:
        return False, "heavy lower wick", 0.25
    if rng > avg_rng * 3.5:
        return False, "abnormal range spike", 0.3

    score = min(1.0, 0.4 + body_ratio * 0.6)
    return True, "candle ok", score


def evaluate_signal_quality(
    df: pd.DataFrame,
    direction: str,
    spread: float = 0.0,
    expected_rr: float = 2.0,
) -> QualityResult:
    reasons: List[str] = []
    scores: List[float] = []

    atr = _atr(df)
    ok_s, msg_s = spread_slippage_veto(spread, atr, expected_rr=expected_rr)
    if not ok_s:
        return QualityResult(False, 0.0, [msg_s])
    reasons.append(msg_s)

    ok_c, msg_c, sc_c = candle_quality(df)
    if not ok_c:
        return QualityResult(False, sc_c, [msg_c])
    reasons.append(msg_c)
    scores.append(sc_c)

    ok_v, msg_v = session_volume_node_ok(df, direction)
    if not ok_v:
        return QualityResult(False, 0.3, [msg_v])
    reasons.append(msg_v)

    liq, msg_l = liquidity_imbalance_score(df)
    scores.append(liq)
    reasons.append(msg_l)

    final = float(np.mean(scores)) if scores else 0.5
    return QualityResult(True, final, reasons)
