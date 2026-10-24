# Stronger Results for XAUUSD (Gold Trading Playbook)

## Recommended Setup for Disciplined Gold Results

### 1. Recommended `.env` File
```bash
BOT_PROFILE=balanced
BOT_SYMBOL=XAUUSD
BOT_AUTOTRADE_SYMBOLS=XAUUSD
SWARM_ENABLED=false
BOT_FAST_MODE=true
ANTISPAM_MODE=off
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
MT5_BACKEND=ea_bridge
BOT_AUTOTRADE=false
```

### 2. Strategy Rules (Plain English)
- **Market:** Focuses exclusively on XAUUSD. Only executes during London and London-NY overlap sessions (`require_killzone=True`) when institutional gold volatility is genuine. Avoids low-liquidity Asian consolidation chop (`allow_asian=False`).
- **Direction:** Strictly requires Higher Timeframe (H1/H4) trend confluence (`strict_htf=True`). No chasing mid-range momentum or fomo entries without key structural confirmation.
- **Entry & Structure:** Pullback into institutional order blocks (Demand/Supply), OTE Fibonacci levels, or liquidity reclaim zones. Never trades micro $2–$3 targets in balanced mode; enforces target RR ≥ 1.90.
- **Risk:** Modest 0.20% equity risk per trade, max 3 open positions total, max 2 on Gold.
- **Fail-Closed Execution:** MT5 orders route via EA Bridge. If the bridge or terminal is disconnected, autotrade fails closed—zero phantom paper fills.

### 3. Telegram Verification Sequence
1. `/profile balanced`
2. `/bridge`
3. `/status`
4. `/cooldown 0`
5. `/signal`
6. `/autotrade on`

### 4. Backtest Commands
- Backtest 3000 bars:
  `python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode multiagent`
- Walk-forward 5 windows:
  `python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode walkforward --windows 5`
- Telegram:
  `/profile balanced` then `/abacktest multiagent 3000 XAUUSD`

### 5. Success Bar Before Scaling Size
- Walk-forward OOS not consistently negative
- Profit factor > 1.10 with realistic spread + commission costs
- Trade sample size adequate (>30 trades)
- Demo fills execute cleanly without false `setup_blocked` vetoes
