# Accuracy & Stability Upgrade: XAUUSD Trading Strategy

## Overview & Reality Constraints
- **Goal:** Higher-probability setups, disciplined risk-to-reward (RR ≥ 1.9 for balanced mode), structural entry precision, and elimination of false vetoes/self-sabotage.
- **No Guaranteed Win Rate:** Trading markets contain inherent uncertainty. This upgrade focuses on positive net expectancy after transaction costs (spread and commission), avoiding garbage trades, strictly enforcing stop losses, and disciplined execution.
- **Primary Instrument:** XAUUSD (Gold).
- **Execution Path:** Telegram → multi_agent Arbiter → CombinedBotAgent (FIT Skills) → AntiSpam / RiskManager → autotrade_worker → EA Bridge → MetaTrader 5 (MT5).

---

## FIX SET A — Critical Bugs & Gate Fixes
1. **CombinedFIT `setup_blocked` Resolution**:
   - In `autotrade_worker.py`: The evaluation context now strictly passes `"setup": str(sig.get("setup") or "consensus")` instead of passing free-text reason strings like `"consensus long score=28.62"`.
   - In `combined_bot_agent.py` `_structure_ok`: Free-text score reasons (`"="`, `"score"`, or length > 24) and all institutional setup keywords (`consensus`, `score`, `long`, `short`, `agent`, `arbiter`, `swarm`, `pull`, `sweep`, `liquid`, `trend`, `scalp`, `balanced`, `perfect`, `entry`, `fvg`, `ob`, `ote`, `bos`, `smc`, `ict`) are unconditionally permitted. It will never false-veto multi-agent consensus signals.
2. **Shared Combined Agent Singleton**:
   - `get_combined_agent()` provides a unified singleton instance across autotrade, manual `/combined` commands, and Arbiter.
   - Session filter defaults to OFF unless `COMBINED_AGENT_SESSION_FILTER=true` is explicitly set.
   - Spread gate allows execution when spread is 0 or unmeasured (`spread_unknown_allow`), avoiding false blocks during demo/unquoted feeds.
3. **Anti-Spam Limiter Enhancements**:
   - `set_entry_cooldown` uses `max(0, seconds)`, allowing complete removal of entry cooldowns when desired (`/cooldown 0`).
   - Setting `ANTISPAM_MODE=off` (or `disabled`, `none`, `0`) bypasses time cooldowns while strictly preserving single-direction protection (never buying and selling simultaneously) and maximum slot limits.
4. **Arbiter-CombinedFIT Integration**:
   - Supports direct Arbiter merge via `COMBINED_MERGE_IN_ARBITER=true`. When active, `sig["combined_merged"] = True` is stamped and autotrade skips duplicate evaluation.

---

## FIX SET B — Balanced & Scalp Profiles for XAUUSD

### Balanced Profile (Primary: Disciplined Intraday & Structural Swing)
- **Description:** `XAUUSD balanced — HTF + liquidity, RR~1.9, not scalp`
- **Core Strategy:** Pullbacks into Higher-Timeframe institutional zones, liquidity reclaim, order blocks, and OTE Fibonacci continuation.
- **Rules:**
  - Strict HTF alignment (`strict_htf = True`)
  - Min Agreement: `2` (can be bumped to `3` for ultra-selectivity)
  - Min Consensus Score: `1.35`
  - Min Effective RR: `1.90` (never drops to micro-scalp $2–$3 targets)
  - Require Killzone: `True` (London & London-NY overlap focus)
  - Asian Session: `False` (avoids low-liquidity Asian consolidation chop)
  - Risk per Trade: `0.20%` equity
  - Max Open Trades: `3` (max `2` per symbol)
  - Max Leverage: `20x`

### Scalp Profile (High-Frequency Micro-Scalp)
- **Description:** Tight micro targets ($2.50 TP / $3.00 SL) with dynamic trailing stop and break-even lock.
- **Spread Safety Gate:** Evaluates spread against planned target distance (`max_spread_frac_of_tp = 0.30`). If spread exceeds 30% of target distance, new entries are blocked to prevent death by spread.

---

## Parameter Comparison Table

| Parameter | Old Balanced | New Balanced (XAUUSD Disciplined) | Old Scalp | New Scalp (XAUUSD Tuned) |
| :--- | :--- | :--- | :--- | :--- |
| **Description** | Default multi-agent balance | XAUUSD balanced — HTF + liquidity, RR~1.9 | High-frequency micro-scalp | High-frequency micro-scalp |
| **min_score** | 1.25 | **1.35** | 1.00 | **1.00** (xau_scalp: 1.20) |
| **min_rr** | 1.90 | **1.90** | 1.00 | **1.00** (xau_scalp: 1.20) |
| **min_agreement** | 2 | **2** (try 3 if desired) | 2 | **2** |
| **require_killzone** | False | **True** (London/NY overlap) | False | False |
| **allow_asian** | True | **False** (bypasses Asian chop) | True | True |
| **strict_htf** | False | **True** | False | False |
| **is_scalp** | False | **False** | True | True |
| **base_risk_pct** | 0.25% | **0.20%** | 0.20% | **0.20%** |
| **max_open_trades** | 6 | **3** | 6 | **3** (xau_scalp: 2) |
| **max_positions/symbol** | 3 | **2** | 3 | **2** (xau_scalp: 1) |
| **TREND weight** | 1.25 | **1.40** | 1.20 | 1.10 |
| **LIQUIDITY weight** | 1.40 | **1.50** | 1.40 | **1.50** |
| **MTF-CONFLUENCE weight** | 1.40 | **1.45** | 1.35 | 1.40 |
| **SUPPLY-DEMAND weight** | 1.45 | **1.45** | 1.45 | 1.20 |
| **OTE-STRATEGY weight** | 1.45 | **1.45** | 1.45 | 1.20 |
| **VOLATILITY-SQUEEZE weight** | 1.50 | **0.75** (de-weighted scalp) | 1.60 | 1.60 |
| **RIBBON-SCALP weight** | 1.45 | **0.75** (de-weighted scalp) | 1.55 | 1.55 |
| **STOCH-RSI-SCALP weight** | 1.50 | **0.75** (de-weighted scalp) | 1.60 | 1.60 |
| **AUTO-TREND-1M weight** | 1.60 | **0.80** (de-weighted scalp) | 1.60 | 1.60 |
| **ASIAN-BREAKOUT weight** | 1.25 | **0.70** (de-weighted Asian) | 1.30 | 0.80 |
| **NEWS weight** | 0.70 | **0.50** (reduced noise) | 0.60 | 0.50 |

---

## Noise Reduction & Useless Agents Removed from Signal Path
1. **150 Swarm Agents Bypassed by Default (`SWARM_ENABLED=false`)**:
   - The 150 experimental swarm agents added significant latency and conflicting votes during live trading, leading to score stagnation and consensus deadlock.
   - Core institutional multi-agents (Trend, Liquidity, MTF Confluence, Supply & Demand, OTE Fibonacci, Fake Breakout, Gautam Jha, and MT5 Chart) provide clean, high-conviction signals without swarm lag.
2. **Pure Scalp Agents De-weighted in Balanced**:
   - 1-minute momentum agents (`VOLATILITY-SQUEEZE`, `RIBBON-SCALP`, `STOCH-RSI-SCALP`) were previously triggering premature micro-signals in balanced mode. Their weights were cut by 50% (~0.75) for balanced mode.
3. **Asian Breakout De-weighted for Gold (`ASIAN-BREAKOUT: 0.70`)**:
   - Gold during Asian session frequently displays fake breakouts and low-volume compression.
4. **News Agent Noise Lowered (`NEWS: 0.50`)**:
   - Prevents stale or unverified headline proxies from dragging down high-probability technical consensus.

---

## Recommended `.env` Configuration

```bash
# Core Strategy & Profile
BOT_PROFILE=balanced
BOT_SYMBOL=XAUUSD
BOT_AUTOTRADE_SYMBOLS=XAUUSD

# Performance & Speed
SWARM_ENABLED=false
BOT_FAST_MODE=true

# Execution & Bridge
MT5_BACKEND=ea_bridge
BOT_AUTOTRADE=false
BOT_AUTOTRADE_LIVE_OPT_IN=false

# Gates & Anti-Spam
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
ANTISPAM_MODE=off
BOT_ACCURACY_MODE=true

# Capital & Safety
INITIAL_EQUITY=10000.0
BOT_MAX_OPEN_TRADES=3
BOT_MAX_POSITIONS_PER_SYMBOL=2
```

---

## Backtest & Validation Commands

To test on your machine before running live:

1. **Standard Multi-Agent Backtest (3,000 bars XAUUSD)**:
   ```bash
   python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode multiagent
   ```
2. **Walk-Forward Out-Of-Sample Validation (5 windows)**:
   ```bash
   python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode walkforward --windows 5
   ```
3. **Monte Carlo Robustness Simulation (500 runs)**:
   ```bash
   python advanced_backtest.py --symbol XAUUSD --bars 3000 --mode montecarlo --mc-runs 500
   ```
4. **Via Telegram**:
   ```text
   /profile balanced
   /abacktest multiagent 3000 XAUUSD
   ```

### Success Bar Before Real Capital
- Walk-forward Out-Of-Sample (OOS) returns are not consistently negative.
- Profit Factor > 1.10 after realistic broker spread and commissions.
- Sufficient trade sample (>30 trades) to confirm statistical edge.
- Demo behavior demonstrates patient entries with zero `setup_blocked` false vetoes.

---

## Windows / EC2 Restart & Test Sequence

1. **Stop the Bot**:
   Press `Ctrl+C` in the terminal running `python main.py`.

2. **Restart the Bot**:
   ```powershell
   python main.py
   ```

3. **Verify via Telegram**:
   - `/profile balanced` — switch to disciplined XAUUSD balanced mode
   - `/bridge` — verify EA Bridge status and MT5 terminal ping
   - `/status` — verify Swarm is OFF, Fast Mode is ON, profile is BALANCED
   - `/cooldown 0` — set entry cooldown to 0 seconds
   - `/signal` — request immediate multi-agent consensus signal for XAUUSD
   - `/autotrade on` — activate autonomous background autotrade monitoring

4. **Logs That Should No Longer Appear**:
   - `COMBINED AGENT VETO — setup_blocked: consensus long score=...` (completely eliminated)
   - Stale private command mass-deletion timeouts on every startup
   - Phantom paper fills when live bridge is disconnected (fail-closed enforced)
