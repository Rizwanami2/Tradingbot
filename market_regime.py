"""Research-only market-regime classification for OHLCV data.

The classifier describes recent price/volatility conditions; it does not generate
entries, estimate profit probability, or place orders. Inputs must contain only
completed candles to avoid intrabar repainting. ATR is normalized by close so the
volatility measure is comparable across differently priced instruments.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from backtest_engine import _validated_frame


def classify_market_regime(
    data: pd.DataFrame,
    *,
    fast_ema: int = 12,
    slow_ema: int = 26,
    adx_period: int = 14,
    atr_period: int = 14,
    volatility_lookback: int = 100,
    high_volatility_percentile: float = 0.80,
    trending_adx: float = 25.0,
    ranging_adx: float = 20.0,
) -> Dict[str, Any]:
    """Classify the final completed candle as trending, ranging, high-volatility,
    or transition.

    The high-volatility threshold is calculated from prior ATR/close values only;
    the current observation is excluded from its own reference distribution.
    This function is deterministic and has no exchange or execution side effects.
    """
    if fast_ema < 2 or slow_ema <= fast_ema:
        raise ValueError("Require 2 <= fast_ema < slow_ema")
    if adx_period < 2 or atr_period < 2 or volatility_lookback < 10:
        raise ValueError("Periods must be positive; volatility_lookback must be >= 10")
    if not 0.5 <= high_volatility_percentile < 1.0:
        raise ValueError("high_volatility_percentile must be in [0.5, 1.0)")
    if not 0 <= ranging_adx < trending_adx <= 100:
        raise ValueError("Require 0 <= ranging_adx < trending_adx <= 100")

    frame = _validated_frame(data)
    min_bars = max(slow_ema + 2, 2 * adx_period + 2, atr_period + 2, volatility_lookback + 2)
    if len(frame) < min_bars:
        raise ValueError(f"Not enough candles for regime analysis; need at least {min_bars}")

    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous_close = close.shift(1)

    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    smooth_plus = plus_dm.ewm(alpha=1 / adx_period, adjust=False, min_periods=adx_period).mean()
    smooth_minus = minus_dm.ewm(alpha=1 / adx_period, adjust=False, min_periods=adx_period).mean()
    safe_atr = atr.replace(0, np.nan)
    plus_di = 100 * smooth_plus / safe_atr
    minus_di = 100 * smooth_minus / safe_atr
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / di_sum).fillna(0.0)
    adx = dx.ewm(alpha=1 / adx_period, adjust=False, min_periods=adx_period).mean()

    ema_fast = close.ewm(span=fast_ema, adjust=False, min_periods=fast_ema).mean()
    ema_slow = close.ewm(span=slow_ema, adjust=False, min_periods=slow_ema).mean()
    atr_pct = (atr / close) * 100.0
    historical_volatility = atr_pct.iloc[-(volatility_lookback + 1):-1].dropna()
    if historical_volatility.empty or pd.isna(adx.iloc[-1]) or pd.isna(atr_pct.iloc[-1]):
        raise ValueError("Insufficient valid indicator history for regime analysis")

    volatility_threshold = float(historical_volatility.quantile(high_volatility_percentile))
    current_volatility = float(atr_pct.iloc[-1])
    adx_value = float(adx.iloc[-1])
    fast_value = float(ema_fast.iloc[-1])
    slow_value = float(ema_slow.iloc[-1])
    if fast_value > slow_value:
        direction = "bullish"
    elif fast_value < slow_value:
        direction = "bearish"
    else:
        direction = "neutral"

    if current_volatility > volatility_threshold:
        regime = "high_volatility"
    elif adx_value >= trending_adx:
        regime = "trending"
    elif adx_value <= ranging_adx:
        regime = "ranging"
    else:
        regime = "transition"

    return {
        "regime": regime,
        "trend_direction": direction,
        "adx": round(adx_value, 6),
        "atr": round(float(atr.iloc[-1]), 10),
        "atr_pct": round(current_volatility, 8),
        "historical_atr_pct_threshold": round(volatility_threshold, 8),
        "high_volatility_percentile": high_volatility_percentile,
        "volatility_lookback": volatility_lookback,
        "fast_ema": round(fast_value, 10),
        "slow_ema": round(slow_value, 10),
        "candle_time": frame.index[-1].isoformat(),
        "classification_kind": "descriptive_heuristic_not_prediction",
    }
