#!/usr/bin/env python3
"""Historical multi-horizon AI market predictor (uses full OHLCV history)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Direction(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class AIPrediction:
    direction: Direction
    confidence: float
    expected_move: float
    horizon: int
    summary: str


def _safe(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


class AIPredictor:
    """Full-history OHLCV predictor: multi-scale mom, slopes, SMA50/200, ATR, swings, indicators."""

    def predict(self, df: pd.DataFrame, horizon: int = 22) -> AIPrediction:
        if df is None or getattr(df, "empty", True) or len(df) < 60:
            return AIPrediction(Direction.FLAT, 0.0, 0.0, horizon, "No data")

        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        n = len(c)

        if not np.isfinite(c[-1]) or abs(float(c[-1])) < 1e-12:
            return AIPrediction(Direction.FLAT, 0.0, 0.0, horizon, "Invalid price")

        def mom(bars: int) -> float:
            if n <= bars or abs(float(c[-bars - 1])) < 1e-12:
                return 0.0
            return _safe((c[-1] - c[-bars - 1]) / c[-bars - 1])

        def slope(bars: int) -> float:
            if n < bars:
                return 0.0
            try:
                s = float(np.polyfit(np.arange(bars, dtype=float), c[-bars:], 1)[0] / c[-1])
            except Exception:
                return 0.0
            return _safe(s)

        def sma(bars: int) -> float:
            if n < bars:
                return float(c[-1])
            return _safe(np.mean(c[-bars:]))

        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        atr14 = _safe(np.mean(tr[-14:])) if len(tr) >= 14 else 0.0
        atr_hist = tr[-min(len(tr), 200):] if len(tr) else np.array([0.0])
        atr_pct = 0.5
        if len(atr_hist) >= 20 and float(np.std(atr_hist)) > 1e-12:
            atr_pct = float(np.mean(atr_hist <= atr14))

        horizons = [5, 15, 30, 60, 120, 240]
        weights_m = [3.0, 2.4, 1.8, 1.4, 1.0, 0.7]
        mom_score = 0.0
        w_sum = 0.0
        for hb, w in zip(horizons, weights_m):
            if n > hb + 1:
                mom_score += mom(hb) * w
                w_sum += w
        mom_score = mom_score / w_sum if w_sum else 0.0

        slope_score = slope(50) * 2.5 + slope(100) * 1.8 + slope(min(200, n)) * 1.2

        sma50 = sma(50)
        sma200 = sma(min(200, n))
        px = float(c[-1])
        struct = 0.0
        if px > sma50 > sma200:
            struct += 0.9
        elif px < sma50 < sma200:
            struct -= 0.9
        elif px > sma50:
            struct += 0.35
        elif px < sma50:
            struct -= 0.35
        if n >= 100:
            window = c[-min(200, n):]
            mu = float(np.mean(window))
            sd = float(np.std(window)) + 1e-12
            z = (px - mu) / sd
            if z > 2.0:
                struct -= 0.25
            elif z < -2.0:
                struct += 0.25

        swing = 0.0
        lb = min(80, n - 2)
        if lb >= 20:
            seg = c[-lb:]
            mid = lb // 2
            first_hi, first_lo = float(np.max(seg[:mid])), float(np.min(seg[:mid]))
            sec_hi, sec_lo = float(np.max(seg[mid:])), float(np.min(seg[mid:]))
            if sec_hi > first_hi and sec_lo > first_lo:
                swing += 0.6
            elif sec_hi < first_hi and sec_lo < first_lo:
                swing -= 0.6

        vol_s = 0.0
        if "volume" in df.columns and n >= 40:
            v = df["volume"].astype(float).values
            if np.isfinite(v[-1]) and np.nansum(v[-40:]) > 0:
                short_v = float(np.nanmean(v[-10:]))
                long_v = float(np.nanmean(v[-40:])) + 1e-12
                vol_ratio = short_v / long_v
                if vol_ratio > 1.25:
                    vol_s = 0.25 if mom_score >= 0 else -0.25
                elif vol_ratio < 0.75:
                    vol_s = -0.15 if mom_score >= 0 else 0.15

        ind_s = 0.0
        try:
            from trading_strategy_indicators_pro import IndicatorEngine
            snap = IndicatorEngine().compute(df)
            ind_s = _safe(getattr(snap, "score", 0.0)) / 5.0
        except Exception:
            pass

        regime_w = 1.0
        if atr_pct > 0.85:
            regime_w = 0.75
        elif atr_pct < 0.15:
            regime_w = 0.85

        raw_score = (
            mom_score * 4.5
            + slope_score * 3.0
            + struct * 0.55
            + swing * 0.45
            + vol_s * 0.35
            + ind_s * 0.80
        ) * regime_w
        raw_score = _safe(raw_score)
        tanh_v = float(np.tanh(raw_score * 6.5))
        bull = 0.5 + tanh_v * 0.48
        bear = 1.0 - bull

        if bull > 0.62:
            d, conf = Direction.LONG, bull
        elif bear > 0.62:
            d, conf = Direction.SHORT, bear
        else:
            d, conf = Direction.FLAT, max(bull, bear)

        conf = _safe(conf)
        exp = atr14 * np.sqrt(max(horizon, 1) / 14.0) * (0.55 + conf * 0.85)
        exp = _safe(exp)
        summary = (
            f"AI hist n={n} {d.name} {conf:.0%} "
            f"(mom={mom_score:+.3f} slope={slope_score:+.4f} "
            f"struct={struct:+.2f} ind={ind_s:+.2f} atr%={atr_pct:.0%})"
        )
        return AIPrediction(d, conf, float(exp), horizon, summary)


def patch_into_indicators_module() -> None:
    try:
        import trading_strategy_indicators_pro as tip
        tip.AIPredictor = AIPredictor
        print("[ai_historical] AIPredictor patched into indicators module")
    except Exception as e:
        print("[ai_historical] patch failed:", e)
