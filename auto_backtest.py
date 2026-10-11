#!/usr/bin/env python3
"""
Auto Backtest — one command, full suite
=======================================
Runs in sequence:
  1. Single (EMA) metrics
  2. Multi-agent proxy metrics
  3. Walk-forward OOS summary
  4. Monte Carlo stress
  5. Top parameter-sweep rows

CLI:
  python auto_backtest.py --symbol XAUUSD --bars 2500
  python auto_backtest.py --csv data.csv --bars 3000 --mc-runs 400

Telegram (via telegram_advanced):
  /abacktest
  /abacktest 2000
  /abacktest 2000 XAUUSD
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from advanced_backtest import (
    AdvancedSimulator,
    SimConfig,
    ema_cross_signal,
    multiagent_proxy_signal,
    walk_forward,
    monte_carlo_from_trades,
    parameter_sweep,
    load_data,
    Metrics,
)


@dataclass
class AutoBacktestReport:
    symbol: str
    bars: int
    single: Optional[Metrics] = None
    multiagent: Optional[Metrics] = None
    walkforward: Optional[Any] = None
    montecarlo: Optional[Any] = None
    sweep_top: Optional[pd.DataFrame] = None
    errors: List[str] = field(default_factory=list)

    def text(self, max_len: int = 3800) -> str:
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append(f" AUTO BACKTEST SUITE — {self.symbol} ({self.bars} bars)")
        lines.append("=" * 60)

        if self.single:
            lines.append("\n[1] EMA SINGLE")
            lines.append(
                f"  Return {self.single.total_return_pct:+.2f}% | DD {self.single.max_drawdown_pct:.2f}% | "
                f"Sharpe {self.single.sharpe:.2f} | SQN {self.single.sqn:.2f}"
            )
            lines.append(
                f"  PF {self.single.profit_factor:.2f} | WR {self.single.win_rate:.1f}% | "
                f"Trades {self.single.total_trades} | AvgR {self.single.avg_r:.2f} | "
                f"MAE/MFE {self.single.avg_mae:.2f}/{self.single.avg_mfe:.2f}R"
            )

        if self.multiagent:
            lines.append("\n[2] MULTI-AGENT PROXY")
            lines.append(
                f"  Return {self.multiagent.total_return_pct:+.2f}% | DD {self.multiagent.max_drawdown_pct:.2f}% | "
                f"Sharpe {self.multiagent.sharpe:.2f} | SQN {self.multiagent.sqn:.2f}"
            )
            lines.append(
                f"  PF {self.multiagent.profit_factor:.2f} | WR {self.multiagent.win_rate:.1f}% | "
                f"Trades {self.multiagent.total_trades} | AvgR {self.multiagent.avg_r:.2f}"
            )

        if self.walkforward:
            wf = self.walkforward
            lines.append("\n[3] WALK-FORWARD (OOS)")
            for w in wf.windows:
                lines.append(
                    f"  W{w['window']}: IS {w['is_return']:+.1f}% → OOS {w['oos_return']:+.1f}% "
                    f"(DD {w['oos_dd']:.1f}%, n={w['oos_trades']})"
                )
            lines.append(
                f"  Combined OOS Return {wf.combined_return_pct:+.2f}% | "
                f"Max DD {wf.combined_max_dd_pct:.2f}%"
            )
            if wf.oos_metrics:
                lines.append(
                    f"  OOS Sharpe {wf.oos_metrics.sharpe:.2f} | "
                    f"SQN {wf.oos_metrics.sqn:.2f} | PF {wf.oos_metrics.profit_factor:.2f}"
                )

        if self.montecarlo:
            mc = self.montecarlo
            lines.append("\n[4] MONTE CARLO")
            lines.append(
                f"  Runs {mc.n_runs} | Median return {mc.median_return_pct:+.2f}% "
                f"(5–95%: {mc.p5_return_pct:+.2f}% … {mc.p95_return_pct:+.2f}%)"
            )
            lines.append(
                f"  Median DD {mc.median_max_dd_pct:.2f}% | 95% DD {mc.p95_max_dd_pct:.2f}%"
            )
            lines.append(
                f"  P(profit) {mc.prob_profit*100:.1f}% | P(DD>20%) {mc.prob_dd_over_20*100:.1f}%"
            )

        if self.sweep_top is not None and not self.sweep_top.empty:
            lines.append("\n[5] PARAM SWEEP (top 5 by Sharpe)")
            top = self.sweep_top.head(5)
            for _, row in top.iterrows():
                lines.append(
                    f"  EMA {int(row['fast'])}/{int(row['slow'])} ATR×{row['atr_mult']} → "
                    f"Sharpe {row['sharpe']:.2f} | Ret {row['return_pct']:+.1f}% | "
                    f"DD {row['max_dd']:.1f}% | n={int(row['trades'])}"
                )

        if self.errors:
            lines.append("\n[!] NOTES")
            for e in self.errors:
                lines.append(f"  - {e}")

        # Verdict
        lines.append("\n" + "-" * 60)
        lines.append(self._verdict())
        lines.append("=" * 60)
        text = "\n".join(lines)
        if len(text) > max_len:
            text = text[: max_len - 20] + "\n…(truncated)"
        return text

    def _verdict(self) -> str:
        scores = []
        if self.single and self.single.total_trades >= 5:
            if self.single.sharpe > 1.0 and self.single.profit_factor > 1.2:
                scores.append("EMA edge: constructive")
            elif self.single.sharpe < 0:
                scores.append("EMA edge: weak/negative")
            else:
                scores.append("EMA edge: mixed")
        if self.walkforward and self.walkforward.combined_return_pct != 0:
            if self.walkforward.combined_return_pct > 0 and self.walkforward.combined_max_dd_pct < 25:
                scores.append("Walk-forward OOS: holds up")
            else:
                scores.append("Walk-forward OOS: fragile or negative")
        if self.montecarlo:
            if self.montecarlo.prob_profit >= 0.55 and self.montecarlo.p95_max_dd_pct < 35:
                scores.append("Monte Carlo: acceptable risk tail")
            else:
                scores.append("Monte Carlo: heavy tail risk")
        if not scores:
            return "VERDICT: insufficient trades/data for a strong conclusion."
        return "VERDICT: " + " | ".join(scores)


def run_auto_backtest(
    symbol: str = "XAUUSD",
    bars: int = 2500,
    source: str = "demo",
    csv: Optional[str] = None,
    equity: float = 10000.0,
    risk: float = 0.0025,
    mc_runs: int = 400,
    wf_windows: int = 4,
    do_sweep: bool = True,
) -> AutoBacktestReport:
    report = AutoBacktestReport(symbol=symbol.upper(), bars=bars)
    df = load_data(symbol, bars, source, csv)
    report.bars = len(df)
    cfg = SimConfig(initial_equity=equity, risk_per_trade=risk)
    sim = AdvancedSimulator(cfg)

    # 1) Single EMA
    try:
        m, trades, _ = sim.run(df, ema_cross_signal, symbol)
        report.single = m
        ema_trades = trades
    except Exception as e:
        report.errors.append(f"single: {e}")
        ema_trades = []

    # 2) Multi-agent proxy
    try:
        m2, _, _ = sim.run(df, multiagent_proxy_signal, symbol)
        report.multiagent = m2
    except Exception as e:
        report.errors.append(f"multiagent: {e}")

    # 3) Walk-forward
    try:
        if len(df) >= 300:
            report.walkforward = walk_forward(
                df, ema_cross_signal, n_windows=wf_windows, cfg=cfg, symbol=symbol
            )
        else:
            report.errors.append("walkforward: need >= 300 bars")
    except Exception as e:
        report.errors.append(f"walkforward: {e}")

    # 4) Monte Carlo from EMA trades
    try:
        if len(ema_trades) >= 5:
            report.montecarlo = monte_carlo_from_trades(
                ema_trades, initial_equity=equity, n_runs=mc_runs
            )
        else:
            report.errors.append("montecarlo: need >= 5 trades from single run")
    except Exception as e:
        report.errors.append(f"montecarlo: {e}")

    # 5) Parameter sweep (optional, can be slower)
    if do_sweep:
        try:
            if len(df) >= 200:
                report.sweep_top = parameter_sweep(df, cfg=cfg, symbol=symbol)
            else:
                report.errors.append("sweep: need >= 200 bars")
        except Exception as e:
            report.errors.append(f"sweep: {e}")

    return report


def main():
    p = argparse.ArgumentParser(description="Auto full-suite backtest (one command)")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--bars", type=int, default=2500)
    p.add_argument("--source", default="demo")
    p.add_argument("--csv", default=None)
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--risk", type=float, default=0.0025)
    p.add_argument("--mc-runs", type=int, default=400)
    p.add_argument("--windows", type=int, default=4)
    p.add_argument("--no-sweep", action="store_true")
    args = p.parse_args()

    report = run_auto_backtest(
        symbol=args.symbol,
        bars=args.bars,
        source=args.source,
        csv=args.csv,
        equity=args.equity,
        risk=args.risk,
        mc_runs=args.mc_runs,
        wf_windows=args.windows,
        do_sweep=not args.no_sweep,
    )
    print(report.text(max_len=100000))


if __name__ == "__main__":
    main()
