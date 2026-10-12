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
    ab.ema_cross_signal = ema_cross_signal
    ab.adaptive_signal = adaptive_signal
    ab.multiagent_proxy_signal = multiagent_proxy_signal
    ab.trend_pullback_signal = trend_pullback_signal
    ab.ema_growth_signal = ema_growth_signal
    if hasattr(ab, "mean_reversion_signal"):
        ab.mean_reversion_signal = mean_reversion_signal
    ab._adx = _adx
    ab._atr = _atr
    ab._rsi = _rsi
    ab.SimConfig.risk_per_trade = 0.005
    ab.SimConfig.tp_r = 2.8
    ab.SimConfig.commission_pct = 0.00025

    _orig_load = ab.load_data
    def _load15(symbol, bars, source, csv, timeframe="15m"):
        try:
            return _orig_load(symbol, bars, source, csv, timeframe=timeframe)
        except TypeError:
            try:
                from realtime_prices import fetch_ohlcv
                for tf in (timeframe, "15m", "1h", "1m"):
                    df = fetch_ohlcv(symbol, interval=tf, limit=min(bars, 1000))
                    if df is not None and not df.empty and len(df) >= 80:
                        print(f"[wire] realtime_prices {tf}: {len(df)} bars")
                        return df.tail(bars)
            except Exception as e:
                print(f"[wire] realtime fallback: {e}")
            return _orig_load(symbol, bars, source, csv)
    ab.load_data = _load15
    print("[wire_backtest_v2] improved signals + defaults + 15m load active")


def apply_growth():
    """Wire growth signal + aggressive risk defaults for ~40% annual target profile."""
    apply()
    import advanced_backtest as ab
    from backtest_signals_v2 import ema_growth_signal
    ab.ema_growth_signal = ema_growth_signal
    ab.SimConfig.risk_per_trade = 0.05
    ab.SimConfig.tp_r = 2.8
    ab.SimConfig.max_leverage = 15.0
    print("[wire_backtest_v2] GROWTH profile active (5% risk, daily preferred)")


def apply_target25():
    """~25% annual target: same growth EMA, moderate 3.2% risk, daily bars."""
    apply()
    import advanced_backtest as ab
    from backtest_signals_v2 import ema_growth_signal
    ab.ema_growth_signal = ema_growth_signal
    ab.SimConfig.risk_per_trade = 0.032
    ab.SimConfig.tp_r = 2.8
    ab.SimConfig.max_leverage = 12.0
    print("[wire_backtest_v2] TARGET25 profile active (3.2% risk, daily preferred)")

if __name__ == "__main__":
    apply()
