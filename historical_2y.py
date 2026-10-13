#!/usr/bin/env python3
"""
2-year historical candle analysis.

Fetches ~730 daily bars (2 years) and derives long-term structure:
  - 52-week high / low
  - 2-year high / low
  - Major swing S/R
  - Long-term EMA 50/200 on daily
  - Trend bias from multi-year structure
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import pandas as pd


def _safe(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


@dataclass
class History2Y:
    symbol: str
    bars: int
    years_span: float
    mark: float
    high_2y: float
    low_2y: float
    high_52w: float
    low_52w: float
    ema50: float
    ema200: float
    trend: str
    support: List[float] = field(default_factory=list)
    resistance: List[float] = field(default_factory=list)
    bias_score: float = 0.0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fetch_daily_2y(symbol: str, limit: int = 730) -> pd.DataFrame:
    sym = str(symbol or "").upper()
    if sym in ("BTC", "BTCUSD", "XBT"):
        sym = "BTCUSDT"
    if sym in ("XAU", "GOLD", "XAUT", "XAUTUSD"):
        sym = "XAUUSD"

    try:
        from realtime_prices import fetch_ohlcv
        df = fetch_ohlcv(sym, interval="1d", limit=min(limit, 1000))
        if df is not None and not getattr(df, "empty", True) and len(df) >= 60:
            return df
    except Exception:
        pass

    try:
        from data_feed import DataFeed
        df = DataFeed(symbol=sym, source="ccxt").fetch(limit=min(limit, 1000), timeframe="1d")
        if df is not None and not getattr(df, "empty", True) and len(df) >= 60:
            return df
    except Exception:
        pass

    try:
        import yfinance as yf
        ymap = {
            "BTCUSDT": "BTC-USD",
            "ETHUSDT": "ETH-USD",
            "XAUUSD": "GC=F",
            "PAXGUSDT": "PAXG-USD",
        }
        ysym = ymap.get(sym, sym)
        hist = yf.Ticker(ysym).history(period="2y", interval="1d")
        if hist is not None and len(hist) >= 60:
            df = hist.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
            return df.tail(limit)
    except Exception:
        pass

    return pd.DataFrame()


def analyze_2y(symbol: str, df: Optional[pd.DataFrame] = None) -> History2Y:
    if df is None or getattr(df, "empty", True):
        df = fetch_daily_2y(symbol)
    if df is None or getattr(df, "empty", True) or len(df) < 30:
        return History2Y(
            symbol=symbol, bars=0, years_span=0.0, mark=0.0,
            high_2y=0.0, low_2y=0.0, high_52w=0.0, low_52w=0.0,
            ema50=0.0, ema200=0.0, trend="RANGE",
            summary="Insufficient 2y history",
        )

    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    n = len(df)
    years = n / 365.25
    mark = _safe(c.iloc[-1])
    high_2y = _safe(h.max())
    low_2y = _safe(l.min())
    w52 = min(n, 365)
    high_52w = _safe(h.iloc[-w52:].max())
    low_52w = _safe(l.iloc[-w52:].min())

    ema50 = _safe(c.ewm(span=50, adjust=False).mean().iloc[-1]) if n >= 50 else mark
    ema200 = _safe(c.ewm(span=min(200, n), adjust=False).mean().iloc[-1]) if n >= 50 else mark

    bias = 0.0
    if mark > ema50 > ema200:
        bias += 0.5
    elif mark < ema50 < ema200:
        bias -= 0.5
    rng = high_2y - low_2y
    if rng > 0:
        pos = (mark - low_2y) / rng
        bias += (pos - 0.5) * 0.6

    if bias > 0.25:
        trend = "BULL"
    elif bias < -0.25:
        trend = "BEAR"
    else:
        trend = "RANGE"

    supports: List[float] = []
    resistances: List[float] = []
    try:
        q25 = _safe(c.quantile(0.25))
        q50 = _safe(c.quantile(0.50))
        q75 = _safe(c.quantile(0.75))
        for lv in (low_2y, low_52w, q25, q50):
            if lv < mark * 0.999:
                supports.append(round(lv, 4))
        for lv in (high_2y, high_52w, q75, q50):
            if lv > mark * 1.001:
                resistances.append(round(lv, 4))
        try:
            w = df.resample("W").agg({"high": "max", "low": "min", "close": "last"}).dropna()
            if len(w) >= 8:
                supports.append(round(_safe(w["low"].iloc[-12:].min()), 4))
                resistances.append(round(_safe(w["high"].iloc[-12:].max()), 4))
        except Exception:
            pass
    except Exception:
        pass

    supports = sorted(set(supports))[-5:]
    resistances = sorted(set(resistances))[:5]

    summary = (
        f"2Y n={n} ({years:.1f}y) {trend} bias={bias:+.2f} | "
        f"52w {low_52w:,.0f}-{high_52w:,.0f} | "
        f"EMA50 {ema50:,.0f} EMA200 {ema200:,.0f}"
    )
    return History2Y(
        symbol=symbol,
        bars=n,
        years_span=round(years, 2),
        mark=mark,
        high_2y=high_2y,
        low_2y=low_2y,
        high_52w=high_52w,
        low_52w=low_52w,
        ema50=ema50,
        ema200=ema200,
        trend=trend,
        support=supports,
        resistance=resistances,
        bias_score=round(float(bias), 3),
        summary=summary,
    )


def format_history_2y(h: History2Y) -> str:
    if h.bars < 30:
        return f"\n📅 **2Y History:** _{h.summary}_\n"
    sup = " | ".join(f"${x:,.0f}" for x in h.support[-3:]) or "—"
    res = " | ".join(f"${x:,.0f}" for x in h.resistance[:3]) or "—"
    return (
        f"\n📅 **2Y Candle Analysis** ({h.bars} daily bars ≈ {h.years_span:.1f}y)\n"
        f"• Trend: **{h.trend}** (bias `{h.bias_score:+.2f}`)\n"
        f"• 52w range: `${h.low_52w:,.2f}` – `${h.high_52w:,.2f}`\n"
        f"• 2Y range: `${h.low_2y:,.2f}` – `${h.high_2y:,.2f}`\n"
        f"• Daily EMA50 `${h.ema50:,.2f}` | EMA200 `${h.ema200:,.2f}`\n"
        f"• Support: {sup}\n"
        f"• Resistance: {res}\n"
    )


def history_levels_for_planner(h: History2Y) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if h.bars < 30:
        return out
    out["high_2y"] = h.high_2y
    out["low_2y"] = h.low_2y
    out["high_52w"] = h.high_52w
    out["low_52w"] = h.low_52w
    out["ema50_d"] = h.ema50
    out["ema200_d"] = h.ema200
    for i, s in enumerate(h.support[-3:]):
        out[f"sup2y_{i}"] = s
    for i, r in enumerate(h.resistance[:3]):
        out[f"res2y_{i}"] = r
    return out
