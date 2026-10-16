#!/usr/bin/env python3
"""
Advanced Backtesting Suite
==========================
- Walk-forward analysis (in-sample / out-of-sample)
- Monte Carlo trade reshuffle + equity path stress
- Multi-agent / custom signal function support
- Enhanced metrics: Sharpe, Sortino, Calmar, SQN, expectancy, MAE/MFE
- Parameter sweep helper
- Realistic fees, slippage, stop-first same-bar rule

Usage:
  python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode walkforward
  python advanced_backtest.py --csv data.csv --mode montecarlo --mc-runs 500
  python advanced_backtest.py --mode multiagent --bars 2000
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from backtest_engine import _validated_frame, BacktestConfig, run_backtest as _ema_backtest
except ImportError:
    _validated_frame = None
    BacktestConfig = None
    _ema_backtest = None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    direction: str
    entry: float
    exit: float
    size: float
    pnl: float
    r_multiple: float
    reason: str
    mae: float = 0.0   # max adverse excursion (R)
    mfe: float = 0.0   # max favorable excursion (R)


@dataclass
class Metrics:
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    calmar: float
    sqn: float
    profit_factor: float
    win_rate: float
    total_trades: int
    avg_r: float
    expectancy_r: float
    avg_mae: float
    avg_mfe: float
    avg_hold_bars: float = 0.0

    def summary(self) -> str:
        return (
            f"\n{'='*62}\n"
            f" ADVANCED BACKTEST METRICS\n"
            f"{'='*62}\n"
            f" Equity          : {self.initial_equity:,.2f} → {self.final_equity:,.2f}\n"
            f" Return          : {self.total_return_pct:+.2f}%\n"
            f" Max Drawdown    : {self.max_drawdown_pct:.2f}%\n"
            f" Sharpe / Sortino: {self.sharpe:.2f} / {self.sortino:.2f}\n"
            f" Calmar / SQN    : {self.calmar:.2f} / {self.sqn:.2f}\n"
            f" Profit Factor   : {self.profit_factor:.2f}\n"
            f" Win Rate        : {self.win_rate:.1f}%  |  Trades: {self.total_trades}\n"
            f" Avg R / Expect  : {self.avg_r:.2f} / {self.expectancy_r:.2f}R\n"
            f" Avg MAE / MFE   : {self.avg_mae:.2f}R / {self.avg_mfe:.2f}R\n"
            f"{'='*62}\n"
        )


def compute_metrics(
    equity_curve: List[float],
    trades: List[TradeRecord],
    initial_equity: float,
    bars_per_year: float = 252 * 24 * 60,
) -> Metrics:
    eq = pd.Series(equity_curve, dtype=float)
    if len(eq) < 2:
        return Metrics(initial_equity, initial_equity, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    rets = eq.pct_change().dropna()
    total_return = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
    dd_series = (eq.cummax() - eq) / eq.cummax()
    max_dd = float(dd_series.max() * 100)

    sharpe = sortino = calmar = 0.0
    if len(rets) > 10 and float(rets.std()) > 0:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(bars_per_year))
        downside = rets[rets < 0]
        if len(downside) and float(downside.std()) > 0:
            sortino = float(rets.mean() / downside.std() * np.sqrt(bars_per_year))
    if max_dd > 0:
        calmar = float(total_return / max_dd)

    rs = [t.r_multiple for t in trades]
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gp = sum(t.pnl for t in wins) if wins else 0.0
    gl = abs(sum(t.pnl for t in losses)) if losses else 1e-12
    pf = gp / gl
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    avg_r = float(np.mean(rs)) if rs else 0.0
    expectancy = avg_r
    avg_mae = float(np.mean([t.mae for t in trades])) if trades else 0.0
    avg_mfe = float(np.mean([t.mfe for t in trades])) if trades else 0.0

    # System Quality Number (Van Tharp)
    sqn = 0.0
    if len(rs) >= 5 and float(np.std(rs)) > 0:
        sqn = float(np.sqrt(len(rs)) * np.mean(rs) / np.std(rs))

    return Metrics(
        initial_equity=initial_equity,
        final_equity=float(eq.iloc[-1]),
        total_return_pct=float(total_return),
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        sqn=sqn,
        profit_factor=float(pf),
        win_rate=float(win_rate),
        total_trades=len(trades),
        avg_r=avg_r,
        expectancy_r=expectancy,
        avg_mae=avg_mae,
        avg_mfe=avg_mfe,
    )


# ---------------------------------------------------------------------------
# Core event-driven simulator
# ---------------------------------------------------------------------------

SignalFunc = Callable[[pd.DataFrame], Optional[Dict[str, Any]]]


@dataclass
class SimConfig:
    initial_equity: float = 10000.0
    risk_per_trade: float = 0.0025
    commission_pct: float = 0.0004
    slippage_pct: float = 0.00015
    tp_r: float = 1.8
    min_bars: int = 80
    allow_short: bool = True
    max_leverage: float = 20.0


class AdvancedSimulator:
    """Bar-by-bar simulator with MAE/MFE tracking and realistic costs."""

    def __init__(self, cfg: Optional[SimConfig] = None):
        self.cfg = cfg or SimConfig()

    def run(
        self,
        df: pd.DataFrame,
        signal_func: SignalFunc,
        symbol: str = "XAUUSD",
    ) -> Tuple[Metrics, List[TradeRecord], List[float]]:
        min_required = max(35, min(self.cfg.min_bars, 50))
        if df is None or len(df) < min_required:
            raise ValueError(f"Need at least {min_required} bars")

        if _validated_frame is not None:
            try:
                df = _validated_frame(df)
            except Exception:
                df = df.copy()
                df.columns = [str(c).lower() for c in df.columns]
        else:
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]

        equity = self.cfg.initial_equity
        curve = [equity]
        trades: List[TradeRecord] = []
        position = None

        start_bar = min(self.cfg.min_bars, max(30, len(df) - 10))
        for i in range(start_bar, len(df)):
            window = df.iloc[: i + 1]
            candle = df.iloc[i]
            high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
            ts = df.index[i]

            if position is not None:
                direction = position["direction"]
                entry = position["entry"]
                stop = position["stop"]
                tp = position["tp"]
                size = position["size"]
                risk = abs(entry - stop) or 1e-12

                # Update MAE / MFE in R
                if direction == "long":
                    adverse = (entry - low) / risk
                    favor = (high - entry) / risk
                else:
                    adverse = (high - entry) / risk
                    favor = (entry - low) / risk
                position["mae"] = max(position.get("mae", 0.0), max(0.0, adverse))
                position["mfe"] = max(position.get("mfe", 0.0), max(0.0, favor))

                exit_price = None
                reason = ""
                max_slip = risk * 0.15
                # Stop-first if both touched
                if direction == "long":
                    if low <= stop:
                        slip = min(stop * self.cfg.slippage_pct, max_slip)
                        exit_price = stop - slip
                        reason = "SL"
                    elif high >= tp:
                        slip = min(tp * self.cfg.slippage_pct, max_slip)
                        exit_price = tp - slip
                        reason = "TP"
                else:
                    if high >= stop:
                        slip = min(stop * self.cfg.slippage_pct, max_slip)
                        exit_price = stop + slip
                        reason = "SL"
                    elif low <= tp:
                        slip = min(tp * self.cfg.slippage_pct, max_slip)
                        exit_price = tp + slip
                        reason = "TP"

                if exit_price is not None:
                    if direction == "long":
                        pnl = (exit_price - entry) * size
                    else:
                        pnl = (entry - exit_price) * size
                    commission = (entry + exit_price) * size * self.cfg.commission_pct
                    pnl -= commission
                    equity += pnl
                    r_mult = pnl / (risk * size) if risk * size > 0 else 0.0
                    trades.append(TradeRecord(
                        entry_time=str(position["time"]),
                        exit_time=str(ts),
                        direction=direction,
                        entry=entry,
                        exit=exit_price,
                        size=size,
                        pnl=round(pnl, 4),
                        r_multiple=round(r_mult, 4),
                        reason=reason,
                        mae=round(position.get("mae", 0.0), 4),
                        mfe=round(position.get("mfe", 0.0), 4),
                    ))
                    position = None

            if position is None:
                sig = signal_func(window)
                if sig is not None:
                    direction = str(sig["direction"]).lower()
                    if direction in ("short", "sell", "-1") and not self.cfg.allow_short:
                        pass
                    else:
                        if direction in ("long", "buy", "1"):
                            direction = "long"
                        else:
                            direction = "short"
                        raw_entry = float(sig["entry"])
                        raw_stop = float(sig["stop"])
                        raw_tp = float(sig.get("tp1", raw_entry + (raw_entry - raw_stop) * self.cfg.tp_r))
                        if direction == "short" and raw_tp > raw_entry:
                            raw_tp = raw_entry - abs(raw_entry - raw_stop) * self.cfg.tp_r

                        stop_dist = abs(raw_entry - raw_stop)
                        tp_dist = abs(raw_tp - raw_entry)
                        min_stop = raw_entry * 0.0005
                        if stop_dist < min_stop:
                            stop_dist = min_stop
                            tp_dist = stop_dist * self.cfg.tp_r

                        risk_amount = equity * self.cfg.risk_per_trade
                        if stop_dist > 0 and raw_entry > 0:
                            max_units = (equity * self.cfg.max_leverage) / raw_entry
                            size = min(risk_amount / stop_dist, max_units)

                            max_slip = stop_dist * 0.15
                            slip = min(raw_entry * self.cfg.slippage_pct, max_slip)

                            if direction == "long":
                                entry = raw_entry + slip
                                stop = entry - stop_dist
                                tp = entry + tp_dist
                            else:
                                entry = raw_entry - slip
                                stop = entry + stop_dist
                                tp = entry - tp_dist

                            position = {
                                "direction": direction,
                                "entry": entry,
                                "stop": stop,
                                "tp": tp,
                                "size": size,
                                "time": ts,
                                "mae": 0.0,
                                "mfe": 0.0,
                            }

            curve.append(equity)

        # Close open position at last close
        if position is not None:
            close = float(df.iloc[-1]["close"])
            entry = position["entry"]
            size = position["size"]
            risk = abs(entry - position["stop"]) or 1e-12
            if position["direction"] == "long":
                pnl = (close - entry) * size
            else:
                pnl = (entry - close) * size
            commission = (entry + close) * size * self.cfg.commission_pct
            pnl -= commission
            equity += pnl
            r_mult = pnl / (risk * size) if risk * size > 0 else 0.0
            trades.append(TradeRecord(
                entry_time=str(position["time"]),
                exit_time=str(df.index[-1]),
                direction=position["direction"],
                entry=entry,
                exit=close,
                size=size,
                pnl=round(pnl, 4),
                r_multiple=round(r_mult, 4),
                reason="EOD",
                mae=round(position.get("mae", 0.0), 4),
                mfe=round(position.get("mfe", 0.0), 4),
            ))
            curve.append(equity)

        metrics = compute_metrics(curve, trades, self.cfg.initial_equity)
        return metrics, trades, curve


# ---------------------------------------------------------------------------
# Built-in signal functions
# ---------------------------------------------------------------------------

def ema_cross_signal(window: pd.DataFrame, fast: int = 12, slow: int = 26, atr_mult: float = 1.5) -> Optional[Dict]:
    if len(window) < slow + 5:
        return None
    c = window["close"].astype(float)
    h = window["high"].astype(float)
    l = window["low"].astype(float)
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if not math.isfinite(float(atr)) or atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(float(atr), entry * 0.0008)
    if float(ema_f.iloc[-2]) <= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) > float(ema_s.iloc[-1]):
        return {"direction": "long", "entry": entry, "stop": entry - atr * atr_mult,
                "tp1": entry + atr * atr_mult * 1.8}
    if float(ema_f.iloc[-2]) >= float(ema_s.iloc[-2]) and float(ema_f.iloc[-1]) < float(ema_s.iloc[-1]):
        return {"direction": "short", "entry": entry, "stop": entry + atr * atr_mult,
                "tp1": entry - atr * atr_mult * 1.8}
    return None


def multiagent_proxy_signal(window: pd.DataFrame) -> Optional[Dict]:
    """Lightweight proxy of multi-agent consensus for fast backtests."""
    if len(window) < 60:
        return None
    c = window["close"].astype(float)
    h, l = window["high"].astype(float), window["low"].astype(float)
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if not math.isfinite(float(atr)) or atr <= 0:
        return None
    entry = float(c.iloc[-1])
    atr = max(float(atr), entry * 0.0008)

    trend_long = float(ema9.iloc[-1]) > float(ema21.iloc[-1]) > float(ema50.iloc[-1])
    trend_short = float(ema9.iloc[-1]) < float(ema21.iloc[-1]) < float(ema50.iloc[-1])

    # Simple BOS proxy
    swing_high = float(h.iloc[-15:-1].max())
    swing_low = float(l.iloc[-15:-1].min())
    bos_long = float(c.iloc[-1]) > swing_high
    bos_short = float(c.iloc[-1]) < swing_low

    rsi = _rsi(c, 14)
    rsi_ok_long = 40 < rsi < 70
    rsi_ok_short = 30 < rsi < 60

    if trend_long and bos_long and rsi_ok_long:
        return {"direction": "long", "entry": entry, "stop": entry - atr * 1.3,
                "tp1": entry + atr * 1.3 * 2.0}
    if trend_short and bos_short and rsi_ok_short:
        return {"direction": "short", "entry": entry, "stop": entry + atr * 1.3,
                "tp1": entry - atr * 1.3 * 2.0}
    return None


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs.iloc[-1]))
    return float(val) if math.isfinite(float(val)) else 50.0


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardResult:
    windows: List[Dict[str, Any]] = field(default_factory=list)
    oos_metrics: Optional[Metrics] = None
    combined_return_pct: float = 0.0
    combined_max_dd_pct: float = 0.0

    def summary(self) -> str:
        lines = ["\n" + "=" * 62, " WALK-FORWARD ANALYSIS", "=" * 62]
        for i, w in enumerate(self.windows, 1):
            lines.append(
                f" Window {i}: IS ret {w['is_return']:+.1f}% | OOS ret {w['oos_return']:+.1f}% "
                f"| OOS DD {w['oos_dd']:.1f}% | OOS trades {w['oos_trades']}"
            )
        if self.oos_metrics:
            lines.append(self.oos_metrics.summary())
        lines.append(f" Combined OOS Return: {self.combined_return_pct:+.2f}%")
        lines.append(f" Combined OOS Max DD: {self.combined_max_dd_pct:.2f}%")
        lines.append("=" * 62 + "\n")
        return "\n".join(lines)


def walk_forward(
    df: pd.DataFrame,
    signal_func: SignalFunc,
    n_windows: int = 5,
    is_ratio: float = 0.7,
    cfg: Optional[SimConfig] = None,
    symbol: str = "XAUUSD",
) -> WalkForwardResult:
    """Anchored-style rolling walk-forward: each window splits IS/OOS."""
    sim = AdvancedSimulator(cfg)
    n = len(df)
    if n < 300:
        raise ValueError("Need at least 300 bars for walk-forward")

    window_size = n // n_windows
    result = WalkForwardResult()
    all_oos_trades: List[TradeRecord] = []
    oos_equity = [cfg.initial_equity if cfg else 10000.0]

    for w in range(n_windows):
        start = w * window_size
        end = min(n, (w + 1) * window_size) if w < n_windows - 1 else n
        chunk = df.iloc[start:end]
        if len(chunk) < 70:
            continue
        split = int(len(chunk) * is_ratio)
        is_df = chunk.iloc[:split]
        oos_df = chunk.iloc[split:]
        if len(is_df) < 35 or len(oos_df) < 25:
            continue

        try:
            is_m, _, _ = sim.run(is_df, signal_func, symbol)
            oos_m, oos_trades, oos_curve = sim.run(oos_df, signal_func, symbol)
        except Exception:
            continue

        result.windows.append({
            "window": w + 1,
            "is_bars": len(is_df),
            "oos_bars": len(oos_df),
            "is_return": is_m.total_return_pct,
            "oos_return": oos_m.total_return_pct,
            "oos_dd": oos_m.max_drawdown_pct,
            "oos_trades": oos_m.total_trades,
            "oos_sharpe": oos_m.sharpe,
        })
        all_oos_trades.extend(oos_trades)
        # stitch equity approximately
        if len(oos_curve) > 1:
            scale = oos_equity[-1] / oos_curve[0] if oos_curve[0] else 1.0
            oos_equity.extend([x * scale for x in oos_curve[1:]])

    if all_oos_trades and len(oos_equity) > 1:
        result.oos_metrics = compute_metrics(oos_equity, all_oos_trades, oos_equity[0])
        result.combined_return_pct = result.oos_metrics.total_return_pct
        result.combined_max_dd_pct = result.oos_metrics.max_drawdown_pct

    return result


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

@dataclass
class MonteCarloResult:
    n_runs: int
    median_return_pct: float
    p5_return_pct: float
    p95_return_pct: float
    median_max_dd_pct: float
    p95_max_dd_pct: float
    prob_profit: float
    prob_dd_over_20: float
    returns: List[float] = field(default_factory=list)
    max_dds: List[float] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*62}\n"
            f" MONTE CARLO ({self.n_runs} runs)\n"
            f"{'='*62}\n"
            f" Median Return     : {self.median_return_pct:+.2f}%\n"
            f" 5th / 95th %ile   : {self.p5_return_pct:+.2f}% / {self.p95_return_pct:+.2f}%\n"
            f" Median Max DD     : {self.median_max_dd_pct:.2f}%\n"
            f" 95th %ile Max DD  : {self.p95_max_dd_pct:.2f}%\n"
            f" P(profit)         : {self.prob_profit*100:.1f}%\n"
            f" P(DD > 20%)       : {self.prob_dd_over_20*100:.1f}%\n"
            f"{'='*62}\n"
        )


def monte_carlo_from_trades(
    trades: List[TradeRecord],
    initial_equity: float = 10000.0,
    n_runs: int = 500,
    seed: int = 42,
) -> MonteCarloResult:
    """Resample trade R-multiples with replacement to stress equity paths."""
    if len(trades) < 5:
        raise ValueError("Need at least 5 trades for Monte Carlo")

    rng = np.random.default_rng(seed)
    # Approximate risk $ from first trade sizing pattern
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    # Convert R to equity fraction assuming constant risk_per_trade ≈ mean |pnl|/|r|
    risk_fractions = []
    for t in trades:
        if abs(t.r_multiple) > 1e-9:
            risk_fractions.append(abs(t.pnl) / (abs(t.r_multiple) * initial_equity))
    risk_pct = float(np.median(risk_fractions)) if risk_fractions else 0.0025
    risk_pct = max(0.0005, min(0.05, risk_pct))

    returns = []
    max_dds = []
    n = len(rs)

    for _ in range(n_runs):
        sample = rng.choice(rs, size=n, replace=True)
        equity = initial_equity
        peak = equity
        max_dd = 0.0
        for r in sample:
            equity *= (1 + r * risk_pct)
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        returns.append((equity / initial_equity - 1) * 100)
        max_dds.append(max_dd)

    returns_a = np.array(returns)
    dds_a = np.array(max_dds)
    return MonteCarloResult(
        n_runs=n_runs,
        median_return_pct=float(np.median(returns_a)),
        p5_return_pct=float(np.percentile(returns_a, 5)),
        p95_return_pct=float(np.percentile(returns_a, 95)),
        median_max_dd_pct=float(np.median(dds_a)),
        p95_max_dd_pct=float(np.percentile(dds_a, 95)),
        prob_profit=float((returns_a > 0).mean()),
        prob_dd_over_20=float((dds_a > 20).mean()),
        returns=list(returns_a),
        max_dds=list(dds_a),
    )


# ---------------------------------------------------------------------------
# Parameter sweep
# ---------------------------------------------------------------------------

def parameter_sweep(
    df: pd.DataFrame,
    fast_list: List[int] = None,
    slow_list: List[int] = None,
    atr_mults: List[float] = None,
    cfg: Optional[SimConfig] = None,
    symbol: str = "XAUUSD",
) -> pd.DataFrame:
    fast_list = fast_list or [8, 12, 16]
    slow_list = slow_list or [21, 26, 34]
    atr_mults = atr_mults or [1.2, 1.5, 2.0]
    sim = AdvancedSimulator(cfg)
    rows = []

    for f in fast_list:
        for s in slow_list:
            if s <= f:
                continue
            for a in atr_mults:
                def sig(window, _f=f, _s=s, _a=a):
                    return ema_cross_signal(window, fast=_f, slow=_s, atr_mult=_a)
                try:
                    m, trades, _ = sim.run(df, sig, symbol)
                    rows.append({
                        "fast": f, "slow": s, "atr_mult": a,
                        "return_pct": round(m.total_return_pct, 2),
                        "max_dd": round(m.max_drawdown_pct, 2),
                        "sharpe": round(m.sharpe, 2),
                        "sqn": round(m.sqn, 2),
                        "trades": m.total_trades,
                        "win_rate": round(m.win_rate, 1),
                        "pf": round(m.profit_factor, 2),
                    })
                except Exception:
                    continue

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("sharpe", ascending=False).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# Data helpers + CLI
# ---------------------------------------------------------------------------

def load_data(symbol: str, bars: int, source: str, csv: Optional[str]) -> pd.DataFrame:
    """REAL DATA FIRST — CSV → CCXT/data_feed → TradingView/multi-agent → synthetic last."""
    # 1) Explicit CSV
    if csv:
        df = pd.read_csv(csv)
        df.columns = [c.lower() for c in df.columns]
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
        elif not isinstance(df.index, pd.DatetimeIndex):
            for col in ("datetime", "date", "time"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], utc=True)
                    df = df.set_index(col)
                    break
        return df.tail(bars)

    errors = []

    # 2) CCXT / data_feed (real exchange history)
    try:
        from data_feed import DataFeed
        import config as _cfg
        feed = DataFeed(
            symbol=symbol,
            source="ccxt",
            exchange_id=getattr(_cfg, "DEFAULT_EXCHANGE", "binance"),
            timeframe="1m",
        )
        df = feed.fetch(limit=bars, timeframe="1m")
        if df is not None and not df.empty and len(df) >= min(100, bars // 2):
            print(f"[AdvancedBacktest] Real data via data_feed/ccxt: {len(df)} bars")
            return df.tail(bars)
    except Exception as e:
        errors.append(f"ccxt:{e}")

    # 3) Requested source if not already ccxt
    if source and source.lower() not in ("demo", "synthetic", "ccxt"):
        try:
            from data_feed import DataFeed
            feed = DataFeed(symbol=symbol, source=source)
            df = feed.fetch(limit=bars)
            if df is not None and not df.empty:
                print(f"[AdvancedBacktest] Real data via source={source}: {len(df)} bars")
                return df.tail(bars)
        except Exception as e:
            errors.append(f"source:{e}")

    # 4) Multi-agent / TradingView path
    try:
        from multi_agent_trading_bot import MarketDataFeed
        feed = MarketDataFeed(symbol=symbol, source=source if source not in ("demo", "synthetic") else "tradingview")
        df = feed.fetch(limit=bars)
        if df is not None and not getattr(df, "empty", True):
            print(f"[AdvancedBacktest] Data via MarketDataFeed: {len(df)} bars")
            return df.tail(bars)
    except Exception as e:
        errors.append(f"tv:{e}")

    # 5) Synthetic last resort (research only)
    print(f"[AdvancedBacktest] WARNING: real data unavailable ({'; '.join(errors) or 'none'}); using synthetic")
    dates = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=bars, freq="1min")
    import numpy as np
    returns = np.random.randn(bars) * 0.3
    p = 2650 + np.cumsum(returns)
    return pd.DataFrame({
        "open": p, "high": p + np.random.uniform(0.1, 1.5, bars),
        "low": p - np.random.uniform(0.1, 1.5, bars),
        "close": p + np.random.randn(bars) * 0.1,
        "volume": np.random.randint(50, 1000, bars),
    }, index=dates)




def main():
    parser = argparse.ArgumentParser(description="Advanced Backtesting Suite")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--source", default="demo")
    parser.add_argument("--csv", default=None)
    parser.add_argument("--mode", choices=["single", "walkforward", "montecarlo", "sweep", "multiagent"], default="single")
    parser.add_argument("--mc-runs", type=int, default=500)
    parser.add_argument("--windows", type=int, default=5)
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.0025)
    args = parser.parse_args()

    df = load_data(args.symbol, args.bars, args.source, args.csv)
    print(f"Loaded {len(df)} bars for {args.symbol}")

    cfg = SimConfig(initial_equity=args.equity, risk_per_trade=args.risk)
    sim = AdvancedSimulator(cfg)

    if args.mode == "single":
        m, trades, curve = sim.run(df, ema_cross_signal, args.symbol)
        print(m.summary())
        print(f"Sample trades: {min(5, len(trades))}")
        for t in trades[:5]:
            print(f"  {t.direction} R={t.r_multiple:+.2f} PnL={t.pnl:+.2f} MAE={t.mae:.2f} MFE={t.mfe:.2f} ({t.reason})")

    elif args.mode == "multiagent":
        m, trades, curve = sim.run(df, multiagent_proxy_signal, args.symbol)
        print(m.summary())

    elif args.mode == "walkforward":
        wf = walk_forward(df, ema_cross_signal, n_windows=args.windows, cfg=cfg, symbol=args.symbol)
        print(wf.summary())

    elif args.mode == "montecarlo":
        m, trades, _ = sim.run(df, ema_cross_signal, args.symbol)
        print(m.summary())
        mc = monte_carlo_from_trades(trades, initial_equity=args.equity, n_runs=args.mc_runs)
        print(mc.summary())

    elif args.mode == "sweep":
        table = parameter_sweep(df, cfg=cfg, symbol=args.symbol)
        print("\nParameter sweep (top 15 by Sharpe):\n")
        print(table.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
