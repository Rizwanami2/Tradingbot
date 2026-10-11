# Advanced Features V2

All modules pushed to `main` for institutional-grade accuracy, risk, and control.

## New Modules

| File | Feature |
|------|---------|
| `volatility_sizing.py` | ATR-percentile + regime risk multiplier |
| `instrument_limits.py` | Per-asset leverage & risk caps (Gold 50×, Crypto 15–25×, FX 30×) |
| `structure_trailing.py` | Trail under/above swing structure instead of pure ATR |
| `htf_bias.py` | Higher-timeframe EMA + structure bias filter |
| `strategy_profiles.py` | balanced / scalp / swing / gold_only / crypto profiles |
| `cvd_proxy.py` | Cumulative volume delta proxy + divergence from OHLCV |
| `portfolio_risk.py` | Multi-symbol exposure, sector limits, correlation block |
| `kill_switch.py` | Manual / drawdown / loss-streak / file-flag kill switch |
| `smc_liquidity.py` | Full SMC (FVG, OB, equal highs, PDH/PDL, BOS) |
| `session_filter.py` | London/NY killzones |
| `performance_analytics.py` | Expectancy & agent contribution reports |

## Quick integration examples

```python
from volatility_sizing import adjust_risk_pct
from instrument_limits import clamp_leverage, get_max_leverage
from structure_trailing import structure_trail_stop
from htf_bias import allows_direction
from strategy_profiles import get_profile
from cvd_proxy import cvd_signal
from portfolio_risk import PortfolioRisk, Position
from kill_switch import KillSwitch
from performance_analytics import PerformanceTracker

# Profile
profile = get_profile("swing")

# Leverage safety
lev, msg = clamp_leverage("XAUUSD", requested=100)  # → 50x

# Vol sizing
risk, reason = adjust_risk_pct(0.25, df1, regime="trending")

# HTF filter
ok, why = allows_direction("LONG", {"1h": df1h, "daily": daily})

# Kill switch
ks = KillSwitch()
active, msg = ks.is_active(equity=9500, peak=10000, consecutive_losses=2)

# Performance report (wire to Telegram /performance)
print(PerformanceTracker().telegram_report())
```

## Still recommended (larger work)

- Wire `/performance` into `main.py` Telegram handlers
- Full exchange-side SL/TP + position reconciliation for live mode
- Walk-forward / Monte-Carlo backtest harness
- Structured JSON logging + optional metrics export

Educational use only. Trading involves substantial risk of loss.
