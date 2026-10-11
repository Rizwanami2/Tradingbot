# Tradingbot — Fully Coded & Complete Feature Set

All advanced modules are implemented **and wired** through `advanced_integration.py`.

## Architecture

```
AdvancedEngine (advanced_integration.py)
 ├── KillSwitch
 ├── Session filter
 ├── MarketDataFeed
 ├── HTF bias filter
 ├── Arbiter (weighted + regime + SMC Liquidity)
 ├── CVD proxy confirmation
 ├── Volatility-adjusted risk sizing
 ├── Instrument leverage caps
 ├── RiskManager (lots, BE, circuit breakers)
 ├── Portfolio risk (multi-symbol / sector)
 ├── Structure trailing helper
 ├── Strategy profiles
 └── PerformanceTracker
```

## Modules (all on main)

| Module | Status |
|--------|--------|
| `smc_liquidity.py` | Complete |
| `session_filter.py` | Complete |
| `performance_analytics.py` | Complete |
| `volatility_sizing.py` | Complete |
| `instrument_limits.py` | Complete |
| `structure_trailing.py` | Complete |
| `htf_bias.py` | Complete |
| `strategy_profiles.py` | Complete |
| `cvd_proxy.py` | Complete |
| `portfolio_risk.py` | Complete |
| `kill_switch.py` | Complete |
| `advanced_integration.py` | **Full wiring layer** |
| `multi_agent_trading_bot.py` | SMC + regime Arbiter integrated |

## How to use

### CLI / script
```python
from advanced_integration import AdvancedEngine

eng = AdvancedEngine(equity=10000, symbol="XAUUSD", profile="swing")
result = eng.evaluate()
print(result["status"])
if result.get("signal"):
    print(result["signal"])

print(eng.performance_report())
```

### Telegram — register extra commands in main.py

Add near the bottom where handlers are registered:

```python
from advanced_integration import register_advanced_commands
# after ApplicationBuilder and other handlers:
register_advanced_commands(application)
```

New commands:
- `/performance` — expectancy, win rate, agent contribution
- `/kill on|off` — activate/deactivate kill switch
- `/profile [name]` — show strategy profile (balanced/scalp/swing/gold_only/crypto)

### Live evaluate with all filters
```python
from advanced_integration import AdvancedEngine
eng = AdvancedEngine(profile="gold_only")
print(eng.evaluate("XAUUSD"))
```

## Safety defaults
- Instrument leverage caps enforced (Gold 50×, Crypto 15–25×, FX 30×)
- Kill switch on drawdown / loss streak / file flag
- Session + killzone gating
- HTF bias must align (strict mode)
- Portfolio sector + total risk limits
- Volatility shrinks size in high ATR regimes

Educational use only. Trading involves substantial risk of loss.
