# Tradingbot — FULLY CODED & COMPLETE

Every advanced feature is implemented as a module **and** wired through a single integration layer.

## Quick start (complete stack)

```bash
git pull origin main
pip install -r requirements.txt
python wire_main.py          # registers /performance /kill /profile + upgrades /signal
python main.py --cli         # or with TELEGRAM_TOKEN set
```

## One entry point for everything

```python
from advanced_integration import AdvancedEngine

eng = AdvancedEngine(equity=10000, symbol="XAUUSD", profile="gold_only")
result = eng.evaluate()
# Applies: kill-switch → session → HTF bias → weighted Arbiter (SMC) → CVD
#          → vol sizing → instrument leverage cap → portfolio risk → full plan

print(result["status"])
if result.get("signal"):
    print(result["signal"])

print(eng.performance_report())
print(eng.kill_status())
new_stop, why = eng.trail_stop("LONG", entry=2650, current_stop=2640, df=df1)
```

## Complete module list (all on `main`)

| Module | Role | Wired |
|--------|------|-------|
| `smc_liquidity.py` | FVG, Order Blocks, equal H/L, PDH/PDL, BOS | LiquidityAgent |
| `session_filter.py` | London/NY killzones | Arbiter + AdvancedEngine |
| `market_regime.py` | Trending/ranging/HV | Arbiter thresholds |
| `performance_analytics.py` | Expectancy, agent stats | `/performance` |
| `volatility_sizing.py` | ATR-percentile risk scale | AdvancedEngine |
| `instrument_limits.py` | Gold 50×, Crypto 15–25×, FX 30× | AdvancedEngine |
| `structure_trailing.py` | Swing-based trail | `eng.trail_stop()` |
| `htf_bias.py` | Daily/1H alignment gate | AdvancedEngine |
| `strategy_profiles.py` | balanced/scalp/swing/gold_only/crypto | AdvancedEngine |
| `cvd_proxy.py` | Volume-delta confirmation | AdvancedEngine votes |
| `portfolio_risk.py` | Multi-symbol / sector caps | AdvancedEngine |
| `kill_switch.py` | Manual/DD/loss/file kill | AdvancedEngine + `/kill` |
| `advanced_integration.py` | **Full facade** | All of the above |
| `wire_main.py` | Auto-patch Telegram handlers | Run once |

## Telegram commands (after `python wire_main.py`)

| Command | Action |
|---------|--------|
| `/signal [SYM]` | Full advanced evaluation |
| `/performance` | Win rate, expectancy, agent contribution |
| `/kill on\|off` | Kill switch |
| `/profile [name]` | Show profile settings |

## Safety stack (always on in AdvancedEngine)

1. Kill switch (drawdown / consecutive losses / `.kill_switch` file)
2. Session / killzone filter
3. Higher-timeframe bias must align (strict profiles)
4. Weighted multi-agent consensus + SMC liquidity
5. CVD confirmation (informational vote)
6. Volatility shrinks size in high ATR
7. Instrument max leverage enforced
8. Portfolio total + sector risk limits

## Not exchange-live yet

Live exchange order placement with verified SL/TP remains intentionally restricted until reconciliation is finished. Paper / monitoring modes are fully supported.

Educational use only. Trading involves substantial risk of loss.
