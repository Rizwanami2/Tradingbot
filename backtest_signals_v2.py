#!/usr/bin/env python3
"""Improved backtest signals — import these in advanced_backtest / auto_backtest."""
from __future__ import annotations
import math
from typing import Dict, Optional
import numpy as np
import pandas as pd


def _true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    prev = c.shift(1)
    return pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14) -> float:
    tr = _true_range(h, l, c)
    atr = tr.rolling(period).mean().iloc[-1]
    if not math.isfinite(float(atr)) or atr <= 0:
        return 0.0
    return float(atr)


def _adx(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14) -> float:
    """Wilder-style ADX approximation (index-aligned)."""
    if len(c) < period * 2:
        return 0.0
    up = h.diff()
    down = (-l.diff())
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=h.index, dtype=float)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=h.index, dtype=float)
    tr = _true_range(h, l, c)
    atr = tr.rolling(period).mean()
    plus_di = 100.0 * plus_dm.rolling(period).mean() / atr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.rolling(period).mean() / atr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    adx = dx.rolling(period).mean().iloc[-1]
    v = float(adx)
    return v if math.isfinite(v) else 0.0


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs.iloc[-1]))
    return float(val) if math.isfinite(float(val)) else 50.0


def ema_cross_signal(window: pd.DataFrame, fast: int = 12, slow: int = 26, atr_mult: float = 2.2) -> Optional[Dict]:
    """Tuned EMA cross: 12/26, ATR×2.2, ADX≥18, RR 2.8, slope filter."""
    if len(window) < slow + 20:
        return None
    c = window["close"].astype(float)
    h = window["high"].astype(float)
    l = window["low"].astype(float)
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    atr = _atr(h, l, c, 14)
    if atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(atr, entry * 0.0008)
    adx = _adx(h, l, c, 14)
    if adx < 18:
        return None
    slope = float(ema_s.iloc[-1] - ema_s.iloc[-5])
    cross_long = float(ema_f.iloc[-2]) <= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) > float(ema_s.iloc[-1])
    cross_short = float(ema_f.iloc[-2]) >= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) < float(ema_s.iloc[-1])
    rr = 2.8
    if cross_long and slope >= 0:
        return {"direction": "long", "entry": entry, "stop": entry - atr * atr_mult, "tp1": entry + atr * atr_mult * rr}
    if cross_short and slope <= 0:
        return {"direction": "short", "entry": entry, "stop": entry + atr * atr_mult, "tp1": entry - atr * atr_mult * rr}
    return None


def trend_pullback_signal(window: pd.DataFrame) -> Optional[Dict]:
    if len(window) < 60:
        return None
    c = window["close"].astype(float)
    h = window["high"].astype(float)
    l = window["low"].astype(float)
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    atr = _atr(h, l, c, 14)
    if atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(atr, entry * 0.0008)
    adx = _adx(h, l, c, 14)
    if adx < 16:
        return None
    e20, e50 = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    slope50 = float(ema50.iloc[-1] - ema50.iloc[-6])
    rsi = _rsi(c, 14)
    band = atr * 0.35
    if e20 > e50 and slope50 > 0 and float(l.iloc[-4:].min()) <= e20 + band and entry >= e20 and 38 <= rsi <= 68:
        stop = min(float(l.iloc[-8:].min()), entry - atr * 1.5)
        if entry - stop < atr * 0.9:
            stop = entry - atr * 1.6
        risk = entry - stop
        return {"direction": "long", "entry": entry, "stop": stop, "tp1": entry + risk * 2.5}
    if e20 < e50 and slope50 < 0 and float(h.iloc[-4:].max()) >= e20 - band and entry <= e20 and 32 <= rsi <= 62:
        stop = max(float(h.iloc[-8:].max()), entry + atr * 1.5)
        if stop - entry < atr * 0.9:
            stop = entry + atr * 1.6
        risk = stop - entry
        return {"direction": "short", "entry": entry, "stop": stop, "tp1": entry - risk * 2.5}
    return None


def multiagent_proxy_signal(window: pd.DataFrame) -> Optional[Dict]:
    if len(window) < 80:
        return None
    c = window["close"].astype(float)
    h, l = window["high"].astype(float), window["low"].astype(float)
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    atr = _atr(h, l, c, 14)
    if atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(atr, entry * 0.0012)
    adx = _adx(h, l, c, 14)
    if adx < 18:
        return None
    trend_long = float(ema9.iloc[-1]) > float(ema21.iloc[-1]) > float(ema50.iloc[-1])
    trend_short = float(ema9.iloc[-1]) < float(ema21.iloc[-1]) < float(ema50.iloc[-1])
    swing_high = float(h.iloc[-20:-1].max())
    swing_low = float(l.iloc[-20:-1].min())
    rsi = _rsi(c, 14)
    if trend_long and entry > swing_high and 45 <= rsi <= 72:
        return {"direction": "long", "entry": entry, "stop": entry - atr * 1.6, "tp1": entry + atr * 1.6 * 2.6}
    if trend_short and entry < swing_low and 28 <= rsi <= 55:
        return {"direction": "short", "entry": entry, "stop": entry + atr * 1.6, "tp1": entry - atr * 1.6 * 2.6}
    return None


def mean_reversion_signal(window: pd.DataFrame) -> Optional[Dict]:
    if len(window) < 50:
        return None
    c = window["close"].astype(float)
    h, l = window["high"].astype(float), window["low"].astype(float)
    atr = _atr(h, l, c, 14)
    if atr <= 0:
        return None
    adx = _adx(h, l, c, 14)
    if adx > 12:
        return None
    entry = float(c.iloc[-1])
    mid = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    rsi = _rsi(c, 14)
    atr = max(atr, entry * 0.0008)
    prev = float(c.iloc[-2])
    if entry < mid - atr * 1.5 and rsi < 28 and entry > prev:
        return {"direction": "long", "entry": entry, "stop": entry - atr * 1.3, "tp1": max(mid, entry + atr * 1.5)}
    if entry > mid + atr * 1.5 and rsi > 72 and entry < prev:
        return {"direction": "short", "entry": entry, "stop": entry + atr * 1.3, "tp1": min(mid, entry - atr * 1.5)}
    return None


def adaptive_signal(window: pd.DataFrame) -> Optional[Dict]:
    """Use tuned EMA cross in trend regime; flat in chop."""
    if len(window) < 60:
        return None
    c = window["close"].astype(float)
    h, l = window["high"].astype(float), window["low"].astype(float)
    adx = _adx(h, l, c, 14)
    if adx >= 18:
        return ema_cross_signal(window)
    return None


def ema_growth_signal(window: pd.DataFrame, fast: int = 10, slow: int = 22, atr_mult: float = 2.0) -> Optional[Dict]:
    """
    Growth profile signal (targets higher annualized return):
      EMA 10/22 cross + ADX≥14 + mild slope filter + RR 2.8
    Tuned on daily XAU; use with risk_per_trade ≈ 0.04–0.06 and daily bars.
    """
    if len(window) < slow + 20:
        return None
    c = window["close"].astype(float)
    h = window["high"].astype(float)
    l = window["low"].astype(float)
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    atr = _atr(h, l, c, 14)
    if atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(atr, entry * 0.002)
    adx = _adx(h, l, c, 14)
    if adx < 14:
        return None
    slope = float(ema_s.iloc[-1] - ema_s.iloc[-5])
    cross_long = float(ema_f.iloc[-2]) <= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) > float(ema_s.iloc[-1])
    cross_short = float(ema_f.iloc[-2]) >= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) < float(ema_s.iloc[-1])
    rr = 2.8
    if cross_long and slope >= -atr * 0.1:
        return {
            "direction": "long",
            "entry": entry,
            "stop": entry - atr * atr_mult,
            "tp1": entry + atr * atr_mult * rr,
        }
    if cross_short and slope <= atr * 0.1:
        return {
            "direction": "short",
            "entry": entry,
            "stop": entry + atr * atr_mult,
            "tp1": entry - atr * atr_mult * rr,
        }
    return None
