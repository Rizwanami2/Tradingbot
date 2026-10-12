#!/usr/bin/env python3
"""Run ~25% annual target profile on daily gold (moderate risk)."""
from __future__ import annotations

def main():
    try:
        from wire_backtest_v2 import apply_target25
        apply_target25()
    except Exception as e:
        print("wire warning:", e)

    from advanced_backtest import AdvancedSimulator, SimConfig, load_data, monte_carlo_from_trades
    try:
        from backtest_signals_v2 import ema_growth_signal as sig
    except ImportError:
        from advanced_backtest import ema_cross_signal as sig

    import argparse
    p = argparse.ArgumentParser(description="~25% annual target backtest")
    p.add_argument("--symbol", default="XAUTUSD")
    p.add_argument("--bars", type=int, default=400)
    p.add_argument("--risk", type=float, default=0.032)
    p.add_argument("--mc-runs", type=int, default=200)
    args = p.parse_args()

    try:
        df = load_data(args.symbol, args.bars, "ccxt", None, timeframe="1d")
    except TypeError:
        from realtime_prices import fetch_ohlcv
        df = fetch_ohlcv(args.symbol, interval="1d", limit=args.bars)

    cfg = SimConfig(initial_equity=10000, risk_per_trade=args.risk, tp_r=2.8, max_leverage=12)
    sim = AdvancedSimulator(cfg)
    m, trades, _ = sim.run(df, sig, args.symbol)
    print("=" * 60)
    print(f" TARGET25 BACKTEST — {args.symbol} ({len(df)} daily bars)")
    print("=" * 60)
    print(f" Return {m.total_return_pct:+.2f}% | DD {m.max_drawdown_pct:.2f}% | Sharpe {m.sharpe:.2f}")
    print(f" PF {m.profit_factor:.2f} | WR {m.win_rate:.1f}% | Trades {m.total_trades} | AvgR {m.avg_r:.2f}")
    if len(trades) >= 5:
        mc = monte_carlo_from_trades(trades, initial_equity=10000, n_runs=args.mc_runs)
        print(f" MC median {mc.median_return_pct:+.1f}% | P(profit) {mc.prob_profit*100:.0f}% | 95% DD {mc.p95_max_dd_pct:.1f}%")
    print("=" * 60)
    if 20 <= m.total_return_pct <= 35:
        print("TARGET: ~25% annual band reached on this sample.")
    print("Risk/trade default 3.2% — lower than growth (5%). Past ≠ future.")

if __name__ == "__main__":
    main()
