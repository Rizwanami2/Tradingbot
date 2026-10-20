# Upgrades 1, 2, 4, 5

```bash
git pull origin main
python apply_upgrades.py
python main.py   # or --cli
```

| # | Upgrade | Result |
|---|---------|--------|
| 1 | Register advanced commands | `/asignal` `/abacktest` `/performance` `/kill` `/profile` |
| 2 | Structure trailing | After TP1, stop trails under/above swings |
| 4 | Real data preferred | CSV → CCXT → TV → synthetic last |
| 5 | `/backtest` = auto suite | One command runs full WF+MC+sweep report |

## Usage

```
/backtest
/backtest 2500
/backtest 2500 XAUUSD
```

CLI:
```bash
python auto_backtest.py --symbol XAUUSD --bars 2500
```
