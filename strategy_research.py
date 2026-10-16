"""Research-only utilities: multi-timeframe alignment, journaling, and comparisons.

This module does not place orders and does not claim that a quality score is a
probability of profit. Callers should supply completed candles from comparable
data sources and keep out-of-sample periods separate from tuning periods.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import numpy as np
import pandas as pd

from backtest_engine import _validated_frame


_TIMEFRAME_WEIGHTS = {"1m": 1.0, "5m": 1.5, "15m": 2.0, "30m": 2.5,
                      "1h": 3.0, "4h": 4.0, "1d": 5.0, "daily": 5.0}


def analyze_timeframes(
    frames: Mapping[str, pd.DataFrame],
    *,
    fast_ema: int = 12,
    slow_ema: int = 26,
    atr_period: int = 14,
) -> Dict[str, Any]:
    """Return an explainable, non-predictive multi-timeframe trend summary.

    EMA and ATR values are calculated from supplied OHLC data. The most recent
    row is treated as completed; callers must remove any still-forming candle.
    """
    if fast_ema < 2 or slow_ema <= fast_ema or atr_period < 2:
        raise ValueError("Require 2 <= fast_ema < slow_ema and atr_period >= 2")
    if not frames:
        raise ValueError("At least one timeframe is required")

    details: Dict[str, Dict[str, Any]] = {}
    weighted_direction = 0.0
    available_weight = 0.0
    strengths = []
    directions = []

    for timeframe, data in frames.items():
        frame = _validated_frame(data)
        if len(frame) < max(slow_ema, atr_period + 1):
            continue
        close = frame["close"].astype(float)
        fast = close.ewm(span=fast_ema, adjust=False, min_periods=fast_ema).mean()
        slow = close.ewm(span=slow_ema, adjust=False, min_periods=slow_ema).mean()
        previous_close = close.shift(1)
        tr = pd.concat([
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(atr_period, min_periods=atr_period).mean()
        if pd.isna(fast.iloc[-1]) or pd.isna(slow.iloc[-1]) or pd.isna(atr.iloc[-1]):
            continue

        f, s, a, last_close = float(fast.iloc[-1]), float(slow.iloc[-1]), float(atr.iloc[-1]), float(close.iloc[-1])
        direction = 1 if f > s else -1 if f < s else 0
        trend = "bullish" if direction > 0 else "bearish" if direction < 0 else "neutral"
        normalized_gap = abs(f - s) / a if a > 0 else 0.0
        strength = min(normalized_gap / 2.0, 1.0)
        weight = _TIMEFRAME_WEIGHTS.get(str(timeframe).lower(), 1.0)
        weighted_direction += direction * weight
        available_weight += weight
        strengths.append(strength)
        directions.append(direction)
        details[str(timeframe)] = {
            "trend": trend,
            "direction": direction,
            "weight": weight,
            "close": last_close,
            "ema_fast": f,
            "ema_slow": s,
            "atr": a,
            "normalized_ema_gap": normalized_gap,
            "last_candle_time": frame.index[-1].isoformat(),
        }

    if not details:
        raise ValueError("No timeframe has enough valid candles for analysis")

    direction = 1 if weighted_direction > 0 else -1 if weighted_direction < 0 else 0
    total_weight = sum(_TIMEFRAME_WEIGHTS.get(str(k).lower(), 1.0) for k in frames)
    alignment = abs(weighted_direction) / available_weight if available_weight else 0.0
    coverage = min(len(details) / 3.0, 1.0)
    strength_score = float(np.mean(strengths)) if strengths else 0.0
    # This is a heuristic research score, not a win probability.
    quality_score = round(100.0 * (0.65 * alignment + 0.35 * strength_score) * coverage, 2)
    return {
        "direction": "LONG" if direction > 0 else "SHORT" if direction < 0 else "NEUTRAL",
        "quality_score": quality_score,
        "score_kind": "heuristic_alignment_not_probability",
        "timeframes_used": len(details),
        "timeframe_details": details,
        "alignment": round(alignment, 4),
        "coverage": round(coverage, 4),
        "conflicting_timeframes": sum(1 for d in directions if d not in (0, direction)),
        "available_weight": available_weight,
        "configured_weight": total_weight,
    }


def append_trade_journal(path: str | Path, trade: Mapping[str, Any]) -> None:
    """Append one JSON trade record; rejects records without core identifiers."""
    required = ("strategy", "symbol", "timeframe", "entry_time", "exit_time", "net_pnl")
    missing = [key for key in required if key not in trade]
    if missing:
        raise ValueError("Trade journal record missing: " + ", ".join(missing))
    try:
        pnl = float(trade["net_pnl"])
    except (TypeError, ValueError) as exc:
        raise ValueError("net_pnl must be a finite number") from exc
    if not math.isfinite(pnl):
        raise ValueError("net_pnl must be a finite number")
    record = dict(trade)
    record["net_pnl"] = pnl
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False, default=str) + "\n")


def summarize_trade_journal(path: str | Path) -> Dict[str, Any]:
    """Aggregate closed-trade records by strategy, symbol, and timeframe."""
    destination = Path(path)
    groups: Dict[tuple, list] = defaultdict(list)
    if not destination.exists():
        return {"trades": 0, "groups": []}
    with destination.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = (str(row["strategy"]), str(row["symbol"]), str(row["timeframe"]))
                pnl = float(row["net_pnl"])
                if not math.isfinite(pnl):
                    raise ValueError("non-finite P&L")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid trade journal row {line_number}: {exc}") from exc
            groups[key].append(pnl)

    summaries = []
    for (strategy, symbol, timeframe), pnls in groups.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        summaries.append({
            "strategy": strategy, "symbol": symbol, "timeframe": timeframe,
            "trades": len(pnls), "net_pnl": round(sum(pnls), 8),
            "win_rate_pct": round(100.0 * len(wins) / len(pnls), 4),
            "profit_factor": (gross_profit / gross_loss if gross_loss else
                              (math.inf if gross_profit else 0.0)),
            "average_pnl": sum(pnls) / len(pnls),
        })
    summaries.sort(key=lambda item: (item["strategy"], item["symbol"], item["timeframe"]))
    return {"trades": sum(len(p) for p in groups.values()), "groups": summaries}


def compare_backtest_results(results: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    """Normalize comparable backtest summaries and rank by return/drawdown ratio.

    All results should use the same symbol, timeframe, test period, costs, and
    sizing assumptions. The ranking is descriptive and not a guarantee.
    """
    rows = []
    for result in results:
        name = str(result.get("strategy", result.get("name", "unnamed")))
        try:
            ret = float(result["total_return_pct"])
            drawdown = float(result["max_drawdown_pct"])
            trades = int(result.get("trades", result.get("total_trades", 0)))
            win_rate = float(result.get("win_rate_pct", result.get("win_rate", 0.0)))
            profit_factor = float(result.get("profit_factor", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid backtest summary for {name}: {exc}") from exc
        if not all(math.isfinite(v) for v in (ret, drawdown, win_rate, profit_factor)):
            raise ValueError(f"Non-finite metrics in backtest summary for {name}")
        if drawdown < 0 or trades < 0 or not 0 <= win_rate <= 100 or profit_factor < 0:
            raise ValueError(f"Out-of-range metrics in backtest summary for {name}")
        rows.append({
            "strategy": name, "total_return_pct": ret, "max_drawdown_pct": drawdown,
            "trades": trades, "win_rate_pct": win_rate, "profit_factor": profit_factor,
            "return_drawdown_ratio": ret / max(drawdown, 1.0),
        })
    return sorted(rows, key=lambda row: row["return_drawdown_ratio"], reverse=True)
