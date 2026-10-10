#!/usr/bin/env python3
"""
Improved Backtest Engine
========================
Realistic backtesting with commission, slippage, equity curve,
and key performance metrics.

Usage:
    python backtest_engine.py --symbol XAUUSD --bars 3000 --source demo
"""

import argparse
import math
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

try:
    from data_feed import DataFeed
except ImportError:
    DataFeed = None

try:
    from execution import PaperBroker
except ImportError:
    PaperBroker = None


@dataclass
class BacktestResult:
    symbol: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    profit_factor: float
    win_rate: float
    total_trades: int
    avg_r: float
    equity_curve: List[float] = field(default_factory=list)
    trades: List[Dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f" BACKTEST RESULT — {self.symbol}\n"
            f"{'='*60}\n"
            f" Initial Equity   : {self.initial_equity:,.2f}\n"
            f" Final Equity     : {self.final_equity:,.2f}\n"
            f" Total Return     : {self.total_return_pct:+.2f}%\n"
            f" Max Drawdown     : {self.max_drawdown_pct:.2f}%\n"
            f" Sharpe Ratio     : {self.sharpe:.2f}\n"
            f" Sortino Ratio    : {self.sortino:.2f}\n"
            f" Profit Factor    : {self.profit_factor:.2f}\n"
            f" Win Rate         : {self.win_rate:.1f}%\n"
            f" Total Trades     : {self.total_trades}\n"
            f" Average R        : {self.avg_r:.2f}\n"
            f"{'='*60}\n"
        )


class SimpleBacktester:
    def __init__(
        self,
        initial_equity: float = 10000.0,
        commission_pct: float = 0.0004,
        slippage_pct: float = 0.0001,
        risk_per_trade: float = 0.0025,
    ):
        self.initial_equity = initial_equity
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.risk_per_trade = risk_per_trade

    def run(self, df: pd.DataFrame, signal_func, symbol: str = "XAUUSD") -> BacktestResult:
        if df is None or len(df) < 100:
            raise ValueError("Need at least 100 bars for backtest")

        equity = self.initial_equity
        equity_curve = [equity]
        trades = []
        position = None
        peak = equity

        for i in range(80, len(df)):
            window = df.iloc[: i + 1]
            candle = df.iloc[i]
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            ts = df.index[i]

            if position is not None:
                direction = position["direction"]
                entry = position["entry"]
                stop = position["stop"]
                tp1 = position["tp1"]
                size = position["size"]
                risk = abs(entry - stop)

                exit_price = None
                reason = ""

                if direction == "long":
                    if low <= stop:
                        exit_price = stop * (1 - self.slippage_pct)
                        reason = "SL"
                    elif high >= tp1:
                        exit_price = tp1 * (1 - self.slippage_pct)
                        reason = "TP1"
                else:
                    if high >= stop:
                        exit_price = stop * (1 + self.slippage_pct)
                        reason = "SL"
                    elif low <= tp1:
                        exit_price = tp1 * (1 + self.slippage_pct)
                        reason = "TP1"

                if exit_price is not None:
                    if direction == "long":
                        pnl = (exit_price - entry) * size
                    else:
                        pnl = (entry - exit_price) * size

                    commission = (entry + exit_price) * size * self.commission_pct
                    pnl -= commission
                    equity += pnl
                    r_multiple = pnl / (risk * size) if risk * size > 0 else 0.0

                    trades.append({
                        "entry_time": str(position["time"]),
                        "exit_time": str(ts),
                        "direction": direction,
                        "entry": entry,
                        "exit": exit_price,
                        "size": size,
                        "pnl": round(pnl, 2),
                        "r": round(r_multiple, 2),
                        "reason": reason,
                    })
                    position = None

            if position is None:
                sig = signal_func(window)
                if sig is not None:
                    direction = sig["direction"]
                    entry = float(sig["entry"])
                    stop = float(sig["stop"])
                    tp1 = float(sig.get("tp1", entry + (entry - stop) * 1.5))

                    risk_amount = equity * self.risk_per_trade
                    stop_dist = abs(entry - stop)
                    if stop_dist <= 0:
                        continue
                    size = risk_amount / stop_dist

                    if direction == "long":
                        entry *= (1 + self.slippage_pct)
                    else:
                        entry *= (1 - self.slippage_pct)

                    position = {
                        "direction": direction,
                        "entry": entry,
                        "stop": stop,
                        "tp1": tp1,
                        "size": size,
                        "time": ts,
                    }

            equity_curve.append(equity)
            peak = max(peak, equity)

        return self._compute_metrics(symbol, equity_curve, trades)

    def _compute_metrics(self, symbol: str, equity_curve: List[float], trades: List[Dict]) -> BacktestResult:
        eq = pd.Series(equity_curve)
        rets = eq.pct_change().dropna()

        total_return = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
        max_dd = ((eq.cummax() - eq) / eq.cummax()).max() * 100

        sharpe = 0.0
        sortino = 0.0
        if len(rets) > 10 and rets.std() > 0:
            sharpe = float(rets.mean() / rets.std() * np.sqrt(252 * 24 * 60))
            downside = rets[rets < 0]
            if len(downside) > 0 and downside.std() > 0:
                sortino = float(rets.mean() / downside.std() * np.sqrt(252 * 24 * 60))

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_profit = sum(t["pnl"] for t in wins) if wins else 0.0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1e-9
        profit_factor = gross_profit / gross_loss
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
        avg_r = float(np.mean([t["r"] for t in trades])) if trades else 0.0

        return BacktestResult(
            symbol=symbol,
            initial_equity=self.initial_equity,
            final_equity=float(eq.iloc[-1]),
            total_return_pct=float(total_return),
            max_drawdown_pct=float(max_dd),
            sharpe=sharpe,
            sortino=sortino,
            profit_factor=float(profit_factor),
            win_rate=float(win_rate),
            total_trades=len(trades),
            avg_r=avg_r,
            equity_curve=equity_curve,
            trades=trades,
        )


def demo_signal(window: pd.DataFrame) -> Optional[Dict]:
    if len(window) < 50:
        return None
    c = window["close"].values
    ret = (c[-1] - c[-20]) / c[-20]
    atr = np.mean(np.maximum(
        window["high"].values[-14:] - window["low"].values[-14:],
        np.abs(window["high"].values[-14:] - window["close"].values[-15:-1])
    ))
    if ret > 0.003:
        entry = float(c[-1])
        stop = entry - atr * 1.2
        return {"direction": "long", "entry": entry, "stop": stop, "tp1": entry + (entry - stop) * 1.8}
    if ret < -0.003:
        entry = float(c[-1])
        stop = entry + atr * 1.2
        return {"direction": "short", "entry": entry, "stop": stop, "tp1": entry - (stop - entry) * 1.8}
    return None




@dataclass(frozen=True)
class BacktestConfig:
    """Parameters for the isolated OHLCV simulator."""
    initial_equity: float = 10000.0
    risk_per_trade: float = 0.01
    max_leverage: float = 5.0
    fee_rate: float = 0.0004
    slippage_bps: float = 2.0
    fast_ema: int = 12
    slow_ema: int = 26
    atr_period: int = 14
    stop_atr: float = 2.0
    reward_risk: float = 2.0
    allow_short: bool = True

    def validate(self):
        for name in ("initial_equity", "risk_per_trade", "max_leverage", "stop_atr", "reward_risk"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.risk_per_trade > 0.05:
            raise ValueError("risk_per_trade must be <= 0.05 (5%)")
        if not math.isfinite(self.fee_rate) or self.fee_rate < 0:
            raise ValueError("fee_rate must be finite and >= 0")
        if not math.isfinite(self.slippage_bps) or not 0 <= self.slippage_bps <= 500:
            raise ValueError("slippage_bps must be between 0 and 500")
        if self.fast_ema < 2 or self.slow_ema <= self.fast_ema:
            raise ValueError("EMA windows must satisfy 2 <= fast_ema < slow_ema")
        if self.atr_period < 2:
            raise ValueError("atr_period must be >= 2")


def _validated_frame(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("Historical OHLCV data is empty")
    frame = data.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    missing = {"open", "high", "low", "close"} - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {', '.join(sorted(missing))}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        col = next((c for c in ("timestamp", "datetime", "date", "time") if c in frame.columns), None)
        if col is None:
            raise ValueError("Data must use a DatetimeIndex or include a timestamp column")
        frame.index = pd.to_datetime(frame.pop(col), utc=True, errors="coerce")
    if frame.index.isna().any():
        raise ValueError("Historical data contains invalid timestamps")
    if frame.index.has_duplicates:
        raise ValueError("Historical data contains duplicate timestamps")
    frame = frame.sort_index()
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    vals = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(vals).all() or (vals <= 0).any():
        raise ValueError("OHLC values must be finite and positive")
    invalid = ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) |
               (frame["low"] > frame[["open", "close", "high"]].min(axis=1)))
    if invalid.any():
        raise ValueError("OHLC candle has inconsistent high/low values")
    return frame


def _indicators(frame: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    out = frame.copy()
    out["ema_fast"] = out["close"].ewm(span=cfg.fast_ema, adjust=False, min_periods=cfg.fast_ema).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.slow_ema, adjust=False, min_periods=cfg.slow_ema).mean()
    prev_close = out["close"].shift(1)
    tr = pd.concat([out["high"] - out["low"],
                    (out["high"] - prev_close).abs(),
                    (out["low"] - prev_close).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(cfg.atr_period, min_periods=cfg.atr_period).mean()
    up = (out["ema_fast"].shift(1) <= out["ema_slow"].shift(1)) & (out["ema_fast"] > out["ema_slow"])
    down = (out["ema_fast"].shift(1) >= out["ema_slow"].shift(1)) & (out["ema_fast"] < out["ema_slow"])
    out["signal"] = np.select([up, down], [1, -1], default=0)
    out.loc[out["atr"].isna() | out["ema_slow"].isna(), "signal"] = 0
    return out


def run_backtest(data: pd.DataFrame, config: Optional[BacktestConfig] = None,
                 symbol: str = "UNKNOWN", timeframe: str = "unknown") -> Dict:
    """Run an EMA crossover simulation; signals fill on the next candle open.

    If stop and target are both touched in one candle, the stop is assumed first.
    Fees and slippage are applied at entry and exit. No broker calls are made.
    """
    cfg = config or BacktestConfig()
    cfg.validate()
    frame = _indicators(_validated_frame(data), cfg)
    if len(frame) < max(cfg.slow_ema + 2, cfg.atr_period + 2):
        raise ValueError("Not enough candles for the configured EMA/ATR windows")

    equity = float(cfg.initial_equity)
    peak = equity
    max_dd = 0.0
    curve = []
    trades = []
    position = None
    slip = cfg.slippage_bps / 10000.0

    def close_position(ts, raw_exit, reason):
        nonlocal equity, position
        pos = position
        direction = pos["direction"]
        exit_price = raw_exit * (1 - slip if direction == 1 else 1 + slip)
        gross = (exit_price - pos["entry"]) * pos["qty"] * direction
        exit_fee = abs(exit_price * pos["qty"]) * cfg.fee_rate
        equity += gross - exit_fee
        fees = pos["entry_fee"] + exit_fee
        trades.append({
            "symbol": symbol.upper(), "direction": "LONG" if direction == 1 else "SHORT",
            "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
            "entry_price": float(pos["entry"]), "exit_price": float(exit_price),
            "quantity": float(pos["qty"]), "stop_price": float(pos["stop"]),
            "target_price": float(pos["target"]), "gross_pnl": float(gross),
            "fees": float(fees), "net_pnl": float(gross - fees), "exit_reason": reason,
        })
        position = None

    for i in range(1, len(frame)):
        row, ts = frame.iloc[i], frame.index[i]
        closed_this_bar = False
        if position is not None:
            d, stop, target = position["direction"], position["stop"], position["target"]
            if d == 1:
                if float(row["low"]) <= stop:
                    close_position(ts, min(stop, float(row["open"])), "stop")
                    closed_this_bar = True
                elif float(row["high"]) >= target:
                    close_position(ts, target, "target")
                    closed_this_bar = True
            else:
                if float(row["high"]) >= stop:
                    close_position(ts, max(stop, float(row["open"])), "stop")
                    closed_this_bar = True
                elif float(row["low"]) <= target:
                    close_position(ts, target, "target")
                    closed_this_bar = True

        if position is None and not closed_this_bar:
            sigrow = frame.iloc[i - 1]
            direction = int(sigrow["signal"])
            atr = float(sigrow["atr"]) if pd.notna(sigrow["atr"]) else 0.0
            if direction and (direction == 1 or cfg.allow_short) and math.isfinite(atr) and atr > 0:
                entry = float(row["open"]) * (1 + slip if direction == 1 else 1 - slip)
                distance = atr * cfg.stop_atr
                stop = entry - distance if direction == 1 else entry + distance
                target = entry + distance * cfg.reward_risk if direction == 1 else entry - distance * cfg.reward_risk
                qty = min((equity * cfg.risk_per_trade) / distance, (equity * cfg.max_leverage) / entry)
                if math.isfinite(qty) and qty > 0:
                    entry_fee = abs(entry * qty) * cfg.fee_rate
                    equity -= entry_fee
                    position = {"direction": direction, "entry": entry, "entry_time": ts,
                                "stop": stop, "target": target, "qty": qty, "entry_fee": entry_fee}
                    # Entry candle is eligible; ambiguous same-candle exits are stop-first.
                    if direction == 1:
                        if float(row["low"]) <= stop:
                            close_position(ts, min(stop, float(row["open"])), "stop")
                        elif float(row["high"]) >= target:
                            close_position(ts, target, "target")
                    else:
                        if float(row["high"]) >= stop:
                            close_position(ts, max(stop, float(row["open"])), "stop")
                        elif float(row["low"]) <= target:
                            close_position(ts, target, "target")

        marked = equity
        if position is not None:
            marked += (float(row["close"]) - position["entry"]) * position["qty"] * position["direction"]
        peak = max(peak, marked)
        dd = (peak - marked) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve.append({"time": str(ts), "equity": float(marked), "drawdown_pct": float(dd)})

    if position is not None:
        ts = frame.index[-1]
        close_position(ts, float(frame.iloc[-1]["close"]), "end_of_data")
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve[-1] = {"time": str(ts), "equity": float(equity), "drawdown_pct": float(dd)}

    net = equity - cfg.initial_equity
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "symbol": symbol.upper(), "timeframe": timeframe,
        "strategy": "EMA crossover with ATR stop and fixed-R target",
        "candles": len(frame), "initial_equity": float(cfg.initial_equity),
        "final_equity": float(equity), "net_pnl": float(net),
        "return_pct": float(net / cfg.initial_equity * 100),
        "max_drawdown_pct": float(max_dd), "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": float(len(wins) / len(trades) * 100) if trades else 0.0,
        "profit_factor": float(gp / gl) if gl > 0 else (None if gp == 0 else float("inf")),
        "expectancy_per_trade": float(np.mean([t["net_pnl"] for t in trades])) if trades else 0.0,
        "fees": float(sum(t["fees"] for t in trades)), "trades_detail": trades,
        "equity_curve": curve,
        "assumptions": {"entry": "next candle open after signal candle close",
                        "same_candle_stop_and_target": "stop first",
                        "fees_and_slippage": "charged on entry and exit", "live_orders_submitted": False},
    }


def main():
    parser = argparse.ArgumentParser(description="Run historical OHLCV backtest (no exchange orders).")
    parser.add_argument("--csv", required=True, help="CSV containing timestamp, open, high, low, close")
    parser.add_argument("--symbol", default="UNKNOWN")
    parser.add_argument("--timeframe", default="unknown")
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()
    data = pd.read_csv(args.csv)
    timestamp_col = next((c for c in data.columns if c.lower() in ("timestamp", "datetime", "date", "time")), None)
    if timestamp_col:
        data[timestamp_col] = pd.to_datetime(data[timestamp_col], utc=True, errors="coerce")
        data = data.set_index(timestamp_col)
    cfg = BacktestConfig(initial_equity=args.equity, risk_per_trade=args.risk,
                         fee_rate=args.fee_rate, slippage_bps=args.slippage_bps)
    result = run_backtest(data, cfg, symbol=args.symbol, timeframe=args.timeframe)
    if result["profit_factor"] == float("inf"):
        result["profit_factor"] = None
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
