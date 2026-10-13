#!/usr/bin/env python3
"""Wire improved signals into advanced_backtest at import time."""
from __future__ import annotations

def apply():
    import advanced_backtest as ab
    try:
        from backtest_signals_v2 import (
            ema_cross_signal,
            adaptive_signal,
            multiagent_proxy_signal,
            trend_pullback_signal,
            mean_reversion_signal,
            ema_growth_signal,
            _adx,
            _atr,
            _rsi,
        )
    except ImportError:
        print("[wire_backtest_v2] backtest_signals_v2 not found")
        return
    ab.ema_cross_signal = trend_pullback_signal
    ab.ema_cross_raw = ema_cross_signal
    ab.adaptive_signal = adaptive_signal
    ab.multiagent_proxy_signal = multiagent_proxy_signal
    ab.trend_pullback_signal = trend_pullback_signal
    ab.ema_growth_signal = ema_growth_signal
    if hasattr(ab, "mean_reversion_signal"):
        ab.mean_reversion_signal = mean_reversion_signal
    ab._adx = _adx
    ab._atr = _atr
    ab._rsi = _rsi
    ab.SimConfig.risk_per_trade = 0.01
    ab.SimConfig.tp_r = 2.5
    ab.SimConfig.commission_pct = 0.00025
    ab.SimConfig.min_bars = 60

    _orig_load = ab.load_data
    def _load_smart(symbol, bars=1000, source="ccxt", csv=None, timeframe="1d"):
        try:
            from historical_2y import fetch_daily_2y
            df = fetch_daily_2y(symbol, limit=max(bars, 500))
            if df is not None and len(df) >= 120:
                print(f"[wire] daily 2Y: {len(df)} bars")
                return df.tail(bars) if bars < len(df) else df
        except Exception:
            pass
        try:
            return _orig_load(symbol, bars, source, csv)
        except TypeError:
            try:
                from realtime_prices import fetch_ohlcv
                for tf in ("1d", "1h", "15m"):
                    df = fetch_ohlcv(symbol, interval=tf, limit=min(bars, 1000))
                    if df is not None and not df.empty and len(df) >= 80:
                        print(f"[wire] realtime {tf}: {len(df)} bars")
                        return df.tail(bars)
            except Exception as e:
                print(f"[wire] realtime fallback: {e}")
            return _orig_load(symbol, bars, source, csv)
    ab.load_data = _load_smart
    print("[wire_backtest_v2] trend-pullback primary + daily load active")
