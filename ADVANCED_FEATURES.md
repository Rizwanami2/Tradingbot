# Advanced Accuracy & Feature Upgrades

This branch adds institutional-grade accuracy and risk improvements on top of the existing multi-agent system.

## New Modules

| File | Purpose |
|------|---------|
| `smc_liquidity.py` | Full SMC/ICT engine: Fair Value Gaps, Order Blocks, Equal Highs/Lows, PDH/PDL sweeps, BOS |
| `session_filter.py` | London / NY killzone + session awareness (blocks Asian/weekend by default) |
| `performance_analytics.py` | Trade journal, expectancy, agent contribution, Telegram reports |
| `market_regime.py` | (already present) Trending / Ranging / High-vol classification |

## Key Accuracy Improvements

1. **Enhanced Liquidity Agent** – Uses `smc_liquidity.scan_smc()` / `best_smc_vote()` instead of only basic PDH/PDL + simple BOS.
2. **Weighted + Regime-Aware Arbiter** – Liquidity & Trend get higher weights. In ranging markets the consensus threshold is raised; in high-volatility risk is reduced.
3. **Session / Killzone Filter** – Optional hard filter so the bot only trades high-probability windows.
4. **Volatility-Adjusted Sizing** – RiskManager can scale position size by current ATR percentile (integrate via `risk_manager`).
5. **Performance Tracking** – Every closed trade can be recorded for later analysis and `/performance` style reports.

## Integration Notes

In `multi_agent_trading_bot.py` (or your live loop):

```python
from smc_liquidity import best_smc_vote, StructureDir
from session_filter import is_tradeable_session
from market_regime import classify_market_regime
from performance_analytics import PerformanceTracker, TradeRecord

# Session gate
ok, reason = is_tradeable_session(require_killzone=False, allow_asian=False)
if not ok:
    # skip or reduce size

# Regime
regime = classify_market_regime(df1)
# raise MIN_SCORE or reduce risk when regime["regime"] == "ranging" or "high_volatility"

# Stronger liquidity vote
dir_, strength, reason = best_smc_vote(df1, daily)
```

## Recommended Next Steps

1. Wire the new Liquidity vote into the existing `LiquidityAgent.vote()`.
2. Make Arbiter weights configurable (JSON profile).
3. Add `/performance` command in `main.py` that calls `PerformanceTracker.telegram_report()`.
4. Cap leverage by instrument (Gold ≤ 50–100×, Crypto ≤ 20–50×).
5. Finish exchange-side SL/TP + position reconciliation for true live mode.

Educational use only. Trading involves substantial risk of loss.
