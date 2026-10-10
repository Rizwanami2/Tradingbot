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


def main():
    parser = argparse.ArgumentParser(description="Backtest Engine")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--bars", type=int, default=2000)
    parser.add_argument("--source", default="demo")
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.0025)
    args = parser.parse_args()

    print(f"Loading data for {args.symbol} ...")
    if DataFeed is None:
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=args.bars, freq="1min")
        np.random.seed(42)
        rets = np.random.randn(args.bars) * 0.0006
        price = 2650 * np.cumprod(1 + rets)
        df = pd.DataFrame({
            "open": price, "high": price * 1.0006, "low": price * 0.9994,
            "close": price, "volume": np.random.randint(100, 2000, args.bars),
        }, index=dates)
    else:
        feed = DataFeed(symbol=args.symbol, source=args.source)
        df = feed.fetch(limit=args.bars, timeframe="1m")

    if df.empty:
        print("No data available")
        return

    bt = SimpleBacktester(initial_equity=args.equity, risk_per_trade=args.risk)
    result = bt.run(df, demo_signal, symbol=args.symbol)
    print(result.summary())

    out = Path("backtest_result.json")
    with open(out, "w") as f:
        json.dump({
            "summary": {
                "final_equity": result.final_equity,
                "return_pct": result.total_return_pct,
                "max_dd": result.max_drawdown_pct,
                "sharpe": result.sharpe,
                "trades": result.total_trades,
            },
            "equity_curve": result.equity_curve[-500:],
        }, f, indent=2)
    print(f"Saved summary → {out}")


if __name__ == "__main__":
    main()
