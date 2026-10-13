#!/usr/bin/env python3
"""Auto Backtest — trend-pullback daily suite with walk-forward + Monte Carlo."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pandas as pd

try:
    from wire_backtest_v2 import apply as _wire_bt
    _wire_bt()
except Exception:
    pass

try:
    import improved_backtest_signals as _bt
except Exception:
    import advanced_backtest as _bt

AdvancedSimulator = _bt.AdvancedSimulator
SimConfig = _bt.SimConfig
ema_cross_signal = _bt.ema_cross_signal
multiagent_proxy_signal = _bt.multiagent_proxy_signal
walk_forward = _bt.walk_forward
monte_carlo_from_trades = _bt.monte_carlo_from_trades
parameter_sweep = getattr(_bt, "parameter_sweep", None)
load_data = _bt.load_data
Metrics = _bt.Metrics
adaptive_signal = getattr(_bt, "adaptive_signal", multiagent_proxy_signal)


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
        lines = []
        lines.append("=" * 60)
        lines.append(f" AUTO BACKTEST SUITE — {self.symbol} ({self.bars} bars)")
        lines.append("=" * 60)
        if self.single:
            lines.append("\n[1] TREND-PULLBACK DAILY (EMA20/50 ADX≥16 RR2.5 + BE/trail)")
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
            lines.append("\n[2] ADAPTIVE (regime router)")
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
            for w in getattr(wf, "windows", []) or []:
                lines.append(
                    f"  W{w['window']}: IS {w['is_return']:+.1f}% → OOS {w['oos_return']:+.1f}% "
                    f"(DD {w['oos_dd']:.1f}%, n={w['oos_trades']})"
                )
            lines.append(
                f"  Combined OOS Return {wf.combined_return_pct:+.2f}% | Max DD {wf.combined_max_dd_pct:.2f}%"
            )
            if getattr(wf, "oos_metrics", None):
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
        if self.sweep_top is not None and len(self.sweep_top):
            lines.append("\n[5] PARAM SWEEP (top 5 by Sharpe)")
            for _, row in self.sweep_top.head(5).iterrows():
                lines.append(
                    f"  EMA {int(row['fast'])}/{int(row['slow'])} ATR×{row['atr_mult']} → "
                    f"Sharpe {row['sharpe']:.2f} | Ret {row['return_pct']:+.1f}% | "
                    f"DD {row['max_dd']:.1f}% | n={int(row['trades'])}"
                )
        if self.errors:
            lines.append("\n[!] " + "; ".join(self.errors[:5]))
        lines.append("\n" + "-" * 60)
        lines.append(self._verdict())
        lines.append("=" * 60)
        text = "\n".join(lines)
        return text if len(text) <= max_len else text[: max_len - 20] + "\n…(truncated)"

    def _verdict(self) -> str:
        scores = []
        if self.single and self.single.total_trades >= 5:
            if self.single.sharpe >= 1.0 and self.single.profit_factor >= 1.15:
                scores.append("Primary edge: constructive")
            elif self.single.sharpe > 0:
                scores.append("Primary edge: mild")
            else:
                scores.append("Primary edge: weak/negative")
        if self.walkforward and self.walkforward.combined_return_pct != 0:
            if self.walkforward.combined_return_pct > 0 and self.walkforward.combined_max_dd_pct < 25:
                scores.append("Walk-forward OOS: holds up")
            else:
                scores.append("Walk-forward OOS: fragile")
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
    bars: int = 730,
    risk: float = 0.01,
    do_sweep: bool = True,
) -> AutoBacktestReport:
    report = AutoBacktestReport(symbol=symbol, bars=bars)
    cfg = SimConfig(risk_per_trade=risk, initial_equity=10000.0, tp_r=2.5, min_bars=60)
    try:
        df = load_data(symbol, bars=bars, source="ccxt", csv=None)
    except Exception as e:
        report.errors.append(f"load_data: {e}")
        return report
    primary = ema_cross_signal
    primary_trades = []
    try:
        report.single, primary_trades, _ = AdvancedSimulator(cfg).run(df, primary, symbol)
    except Exception as e:
        report.errors.append(f"primary: {e}")
    try:
        report.multiagent, _, _ = AdvancedSimulator(cfg).run(df, adaptive_signal, symbol)
    except Exception as e:
        report.errors.append(f"adaptive: {e}")
    try:
        if len(df) >= 250:
            report.walkforward = walk_forward(df, primary, n_windows=4, cfg=cfg, symbol=symbol)
        else:
            report.errors.append("walkforward: need >= 250 bars")
    except Exception as e:
        report.errors.append(f"walkforward: {e}")
    try:
        if len(primary_trades) >= 5:
            report.montecarlo = monte_carlo_from_trades(
                primary_trades, n_runs=400, initial_equity=cfg.initial_equity
            )
        else:
            report.errors.append(f"montecarlo: need >= 5 trades (got {len(primary_trades)})")
    except Exception as e:
        report.errors.append(f"montecarlo: {e}")
    if do_sweep and parameter_sweep is not None:
        try:
            report.sweep_top = parameter_sweep(df, cfg=cfg, symbol=symbol)
        except Exception as e:
            report.errors.append(f"sweep: {e}")
    return report


def main():
    p = argparse.ArgumentParser(description="Auto backtest suite")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--bars", type=int, default=730)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--no-sweep", action="store_true")
    args = p.parse_args()
    print(run_auto_backtest(args.symbol, args.bars, args.risk, do_sweep=not args.no_sweep).text())


if __name__ == "__main__":
    main()
