#!/usr/bin/env python3
"""
Gautam Jha–style price-action strategy agent.

Educational price-action rules (not financial advice):
- Top-down daily liquidity: daily open, previous day high/low
- Prefer clear trends; avoid FOMO / chop
- Style A: Break-and-Go (momentum continuation)
- Style B: Retrace-to-level (pullback to daily open / PDH / PDL)
- Style C: Liquidity sweep + rejection

Produces a Vote-compatible signal for the multi-agent Arbiter.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

import numpy as np
import pandas as pd


class GJDir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class GJVote:
    agent: str
    direction: GJDir
    strength: float
    reason: str = ""


def _safe_f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


def _daily_levels(daily: Optional[pd.DataFrame], df: pd.DataFrame) -> dict:
    out = {"pdh": None, "pdl": None, "pdo": None, "day_open": None}
    try:
        if daily is not None and not getattr(daily, "empty", True) and len(daily) >= 2:
            prev = daily.iloc[-2]
            cur = daily.iloc[-1]
            out["pdh"] = _safe_f(prev["high"])
            out["pdl"] = _safe_f(prev["low"])
            out["pdo"] = _safe_f(prev["open"] if "open" in prev else prev["close"])
            out["day_open"] = _safe_f(cur["open"])
            return out
    except Exception:
        pass
    if df is None or len(df) < 50:
        return out
    try:
        n = len(df)
        window = df.iloc[-min(n, 500):]
        out["day_open"] = _safe_f(window["open"].iloc[0])
        mid = len(window) // 2
        if mid >= 10:
            prev = window.iloc[:mid]
            out["pdh"] = _safe_f(prev["high"].max())
            out["pdl"] = _safe_f(prev["low"].min())
            out["pdo"] = _safe_f(prev["open"].iloc[0])
    except Exception:
        pass
    return out


def _trend_structure(df: pd.DataFrame, lookback: int = 40) -> Tuple[GJDir, float, str]:
    if df is None or len(df) < lookback + 5:
        return GJDir.FLAT, 0.0, "Insufficient bars for structure"
    seg = df.iloc[-lookback:]
    c = seg["close"].astype(float).values
    mid = lookback // 2
    first_hi, first_lo = float(np.max(c[:mid])), float(np.min(c[:mid]))
    sec_hi, sec_lo = float(np.max(c[mid:])), float(np.min(c[mid:]))
    last = seg.iloc[-8:]
    green = int((last["close"] > last["open"]).sum())
    red = int((last["close"] < last["open"]).sum())
    if sec_hi > first_hi and sec_lo > first_lo and green >= 5:
        return GJDir.LONG, 0.75, "Uptrend HH+HL clear"
    if sec_hi < first_hi and sec_lo < first_lo and red >= 5:
        return GJDir.SHORT, 0.75, "Downtrend LH+LL clear"
    if sec_hi > first_hi and sec_lo > first_lo:
        return GJDir.LONG, 0.45, "Mild uptrend structure"
    if sec_hi < first_hi and sec_lo < first_lo:
        return GJDir.SHORT, 0.45, "Mild downtrend structure"
    return GJDir.FLAT, 0.0, "Range / chop — no FOMO"


def _break_and_go(df: pd.DataFrame, trend: GJDir) -> Tuple[GJDir, float, str]:
    if trend == GJDir.FLAT or df is None or len(df) < 5:
        return GJDir.FLAT, 0.0, ""
    prev, cur = df.iloc[-2], df.iloc[-1]
    po, ph, pl, pc = map(_safe_f, [prev["open"], prev["high"], prev["low"], prev["close"]])
    co, ch, cl, cc = map(_safe_f, [cur["open"], cur["high"], cur["low"], cur["close"]])
    if trend == GJDir.SHORT and cc < pc and cc < pl and cc < co:
        return GJDir.SHORT, 0.80, "Break-and-Go short (broke prior low)"
    if trend == GJDir.LONG and cc > pc and cc > ph and cc > co:
        return GJDir.LONG, 0.80, "Break-and-Go long (broke prior high)"
    return GJDir.FLAT, 0.0, ""


def _retrace_to_level(df: pd.DataFrame, levels: dict, trend: GJDir) -> Tuple[GJDir, float, str]:
    if trend == GJDir.FLAT or df is None or len(df) < 8:
        return GJDir.FLAT, 0.0, ""
    px = _safe_f(df["close"].iloc[-1])
    cur = df.iloc[-1]
    co, ch, cl, cc = map(_safe_f, [cur["open"], cur["high"], cur["low"], cur["close"]])
    candidates = []
    for name, key in (("daily open", "day_open"), ("PDO", "pdo"), ("PDH", "pdh"), ("PDL", "pdl")):
        lv = levels.get(key)
        if lv is None or lv <= 0:
            continue
        dist = abs(px - lv) / max(px, 1e-9)
        if dist > 0.004:
            continue
        candidates.append((name, lv, dist))
    if not candidates:
        return GJDir.FLAT, 0.0, ""
    candidates.sort(key=lambda x: x[2])
    name, lv, dist = candidates[0]
    if trend == GJDir.LONG and cc > co and cl <= lv * 1.001:
        return GJDir.LONG, 0.85, f"Retrace long at {name} ({lv:.2f})"
    if trend == GJDir.SHORT and cc < co and ch >= lv * 0.999:
        return GJDir.SHORT, 0.85, f"Retrace short at {name} ({lv:.2f})"
    return GJDir.FLAT, 0.0, f"Near {name} but no trend candle"


def _liquidity_sweep(df: pd.DataFrame, levels: dict) -> Tuple[GJDir, float, str]:
    if df is None or len(df) < 5:
        return GJDir.FLAT, 0.0, ""
    prev, cur = df.iloc[-2], df.iloc[-1]
    pdh, pdl = levels.get("pdh"), levels.get("pdl")
    ph, pl = _safe_f(prev["high"]), _safe_f(prev["low"])
    cc, co = _safe_f(cur["close"]), _safe_f(cur["open"])
    ch, cl = _safe_f(cur["high"]), _safe_f(cur["low"])
    if pdh and pdh > 0 and ph > pdh and cc < pdh and cc < co:
        return GJDir.SHORT, 0.88, f"PDH liquidity sweep + rejection @ {pdh:.2f}"
    if pdl and pdl > 0 and pl < pdl and cc > pdl and cc > co:
        return GJDir.LONG, 0.88, f"PDL liquidity sweep + rejection @ {pdl:.2f}"
    try:
        highs = df["high"].astype(float).iloc[-12:-1]
        lows = df["low"].astype(float).iloc[-12:-1]
        if ch > float(highs.max()) * 0.999 and cc < co and cc < float(highs.max()):
            return GJDir.SHORT, 0.70, "Equal-high liquidity grab + rejection"
        if cl < float(lows.min()) * 1.001 and cc > co and cc > float(lows.min()):
            return GJDir.LONG, 0.70, "Equal-low liquidity grab + rejection"
    except Exception:
        pass
    return GJDir.FLAT, 0.0, ""


class GautamJhaAgent:
    name = "GAUTAM-JHA"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> GJVote:
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 30:
                return GJVote(self.name, GJDir.FLAT, 0.0, "Need more bars")
            levels = _daily_levels(daily, df)
            trend, t_str, t_why = _trend_structure(df)
            for label, fn in (
                ("sweep", lambda: _liquidity_sweep(df, levels)),
                ("retrace", lambda: _retrace_to_level(df, levels, trend)),
                ("bag", lambda: _break_and_go(df, trend)),
            ):
                try:
                    d, s, r = fn()
                except Exception:
                    continue
                if d != GJDir.FLAT and s >= 0.65:
                    if label != "sweep" and trend != GJDir.FLAT and d != trend and s < 0.85:
                        continue
                    return GJVote(self.name, d, float(min(s, 1.0)), r or t_why)
            if trend != GJDir.FLAT and t_str >= 0.70:
                return GJVote(self.name, trend, 0.40, f"Structure bias: {t_why}")
            return GJVote(self.name, GJDir.FLAT, 0.0, t_why or "No Gautam Jha setup")
        except Exception as e:
            return GJVote(self.name, GJDir.FLAT, 0.0, f"GJ error: {type(e).__name__}")


def gautam_jha_vote(df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> GJVote:
    return GautamJhaAgent().vote(df, daily)
