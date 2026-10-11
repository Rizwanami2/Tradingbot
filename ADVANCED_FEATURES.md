# Advanced Accuracy & Feature Upgrades

Institutional-grade accuracy and risk improvements for the multi-agent trading bot.

## New Modules

| File | Purpose |
|------|---------|
| `smc_liquidity.py` | Full SMC/ICT: Fair Value Gaps, Order Blocks, Equal Highs/Lows, PDH/PDL sweeps, BOS |
| `session_filter.py` | London / NY killzone + session awareness (blocks Asian/weekend by default) |
| `performance_analytics.py` | Trade journal, expectancy, agent contribution, Telegram reports |
| `market_regime.py` | Trending / Ranging / High-vol classification (already present) |

## Key Improvements

1. **Enhanced Liquidity Agent** – Uses full SMC engine instead of only basic PDH/PDL + simple BOS.
2. **Weighted + Regime-Aware Arbiter** – Liquidity & Trend weighted higher. Stricter consensus in ranging markets.
3. **Session / Killzone Filter** – Optional hard filter for high-probability windows only.
4. **Performance Tracking** – Record closed trades for expectancy and agent stats.

## Quick test

```bash
python -c "from smc_liquidity import best_smc_vote; print('SMC OK')"
python -c "from session_filter import get_session_info; print(get_session_info())"
python -c "from performance_analytics import PerformanceTracker; print(PerformanceTracker().telegram_report())"
python multi_agent_trading_bot.py
```

Educational use only. Trading involves substantial risk of loss.
