"""Unit tests for advanced accuracy / risk / backtest modules."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd


def _ohlcv(n: int = 200, start: float = 2650.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="1min")
    rets = rng.normal(0, 0.2, n)
    p = start + np.cumsum(rets)
    high = p + rng.uniform(0.2, 1.2, n)
    low = p - rng.uniform(0.2, 1.2, n)
    close = p + rng.normal(0, 0.1, n)
    high = np.maximum(high, np.maximum(p, close))
    low = np.minimum(low, np.minimum(p, close))
    return pd.DataFrame(
        {"open": p, "high": high, "low": low, "close": close, "volume": rng.integers(50, 500, n)},
        index=dates,
    )


def test_smc_scan_runs():
    from smc_liquidity import scan_smc, best_smc_vote
    df = _ohlcv(120)
    setups = scan_smc(df)
    assert isinstance(setups, list)
    d, s, r = best_smc_vote(df)
    assert s >= 0.0


def test_session_filter():
    from session_filter import get_session_info, is_tradeable_session
    info = get_session_info()
    assert info.name
    ok, reason = is_tradeable_session()
    assert isinstance(ok, bool)


def test_instrument_leverage_caps():
    from instrument_limits import clamp_leverage, get_max_leverage
    assert get_max_leverage("XAUUSD") <= 50
    assert get_max_leverage("BTCUSDT") <= 200
    lev, msg = clamp_leverage("XAUUSD", 500)
    assert lev <= 50


def test_volatility_sizing():
    from volatility_sizing import adjust_risk_pct, atr_percentile
    df = _ohlcv(150)
    pct = atr_percentile(df)
    assert 0 <= pct <= 100
    risk, reason = adjust_risk_pct(0.25, df, regime="trending")
    assert risk > 0


def test_htf_bias():
    from htf_bias import htf_bias, allows_direction
    df = _ohlcv(300)
    data = {"1h": df.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(),
            "daily": df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()}
    bias, reason = htf_bias(data)
    ok, msg = allows_direction("LONG", data, strict=False)
    assert isinstance(ok, bool)


def test_cvd_proxy():
    from cvd_proxy import cvd_signal
    d, s, r = cvd_signal(_ohlcv(80))
    assert d in ("LONG", "SHORT", "FLAT")


def test_kill_switch():
    from kill_switch import KillSwitch
    ks = KillSwitch(max_drawdown_pct=5.0, max_consecutive_losses=3)
    active, _ = ks.is_active(equity=10000, peak=10000, consecutive_losses=0)
    assert active is False
    active, msg = ks.is_active(equity=9000, peak=10000, consecutive_losses=0)
    assert active is True
    ks.deactivate()


def test_portfolio_risk():
    from portfolio_risk import PortfolioRisk, Position
    pr = PortfolioRisk(equity=10000)
    ok, _ = pr.add(Position("XAUUSD", "LONG", risk_amount=50, notional=5000))
    assert ok
    ok2, msg = pr.add(Position("GOLD", "LONG", risk_amount=50, notional=5000))
    assert ok2 is False  # same sector same direction


def test_strategy_profiles():
    from strategy_profiles import get_profile, load_profiles
    p = get_profile("swing")
    assert p.min_rr >= 1.5
    assert "balanced" in load_profiles()


def test_structure_trail():
    from structure_trailing import structure_trail_stop
    df = _ohlcv(50)
    stop, reason = structure_trail_stop("LONG", 2650.0, 2640.0, df, atr_value=2.0)
    assert math.isfinite(stop)


def test_advanced_backtest_single():
    from advanced_backtest import AdvancedSimulator, SimConfig, ema_cross_signal
    df = _ohlcv(400)
    sim = AdvancedSimulator(SimConfig(initial_equity=10000, risk_per_trade=0.002))
    metrics, trades, curve = sim.run(df, ema_cross_signal, "XAUUSD")
    assert metrics.initial_equity == 10000
    assert len(curve) > 0


def test_monte_carlo_requires_trades():
    from advanced_backtest import TradeRecord, monte_carlo_from_trades
    trades = [
        TradeRecord("t0", "t1", "long", 1, 2, 1, 10, 1.0, "TP"),
        TradeRecord("t0", "t1", "long", 1, 2, 1, -5, -0.5, "SL"),
        TradeRecord("t0", "t1", "short", 1, 2, 1, 8, 0.8, "TP"),
        TradeRecord("t0", "t1", "long", 1, 2, 1, -3, -0.3, "SL"),
        TradeRecord("t0", "t1", "long", 1, 2, 1, 12, 1.2, "TP"),
    ]
    mc = monte_carlo_from_trades(trades, n_runs=50, seed=1)
    assert mc.n_runs == 50
    assert math.isfinite(mc.median_return_pct)


def test_session_filter_crypto_weekend():
    from session_filter import get_session_info, is_tradeable_session
    sunday_ts = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
    ok_forex, _ = is_tradeable_session(sunday_ts, symbol="XAUUSD")
    assert ok_forex is False
    ok_crypto, _ = is_tradeable_session(sunday_ts, symbol="BTCUSDT")
    assert ok_crypto is True
    info_btc = get_session_info(sunday_ts, symbol="BTCUSD")
    assert info_btc.is_tradeable is True
    assert info_btc.name == "CRYPTO_24_7"


def test_smc_equal_highs_lows_bounds():
    from smc_liquidity import detect_equal_highs_lows
    df = pd.DataFrame({
        "open": [100.0] * 20,
        "high": [105.0] * 20,
        "low": [95.0] * 20,
        "close": [100.0] * 20,
        "volume": [100] * 20,
    })
    setups = detect_equal_highs_lows(df, lookback=40)
    assert len(setups) > 0
    for s in setups:
        assert 0 <= s.bars_ago < len(df)


def test_signal_quality_zero_division():
    from signal_quality import relative_strength_rank
    s_zero = pd.Series([0.0] * 25)
    s_nan = pd.Series([np.nan] * 25)
    s_ok = pd.Series([float(i) for i in range(1, 26)])
    ranks = relative_strength_rank({"Z": s_zero, "N": s_nan, "OK": s_ok})
    assert len(ranks) == 3
    assert ranks[0][0] == "OK"


def test_multi_agent_bot_entrypoints():
    import multi_agent_trading_bot
    assert hasattr(multi_agent_trading_bot, "run_live")
    assert hasattr(multi_agent_trading_bot, "run_backtest")
    # Quick backtest invocation with small bars
    report = multi_agent_trading_bot.run_backtest({"symbol": "XAUUSD", "bars": 100, "source": "demo"})
    assert report is not None
