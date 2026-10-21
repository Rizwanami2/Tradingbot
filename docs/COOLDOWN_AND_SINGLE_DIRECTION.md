# Cooldown & Single-Direction Protection

Both **Automatic Trading** (`autotrade_worker.py`) and **Manual Trading** (`/signal`, `/asignal`, `signal_pipeline.py`) strictly enforce cooldowns and single-direction position isolation.

---

## 1. Strict Single-Direction Rule (No Simultaneous BUY/SELL)

- **Hedging Prohibited**: The bot will **never** hold or open BUY and SELL positions on the same currency at the same time.
- **Scope**:
  - If an active or pending BUY position exists on Gold (`XAUUSD`), all SELL orders are strictly rejected.
  - If an active or pending SELL position exists on Bitcoin (`BTCUSDT`), all BUY orders are strictly rejected.
- **Broker & Local Reconciliation**:
  - `ExecutionEngine.register_setup()` checks `self.active_positions`, `self.pending_setups`, and `self.broker.get_positions()`.
  - Even if a trade was opened externally in MetaTrader 5 terminal, the bot detects it and forbids opening an opposing trade.
  - Rule applies unconditionally across all operating modes (including Spam Trade Mode).

---

## 2. Cooldown Enforcement (Auto & Manual)

Cooldowns prevent rapid trade stacking, spread choking, and stop-loss cascades.

| Cooldown Type | Default Duration | Trigger Event | Scope |
|---|---|---|---|
| **Entry Cooldown** | 45s (configurable via `/cooldown <sec>`) | After any trade fill | Blocks subsequent entry on same symbol for both Auto and Manual trades |
| **Loss Cooldown** | 60s - 90s (escalates on streak) | After a trade exits at a loss | Protects against revenge-trading / SL cascades on both Auto and Manual |
| **Global Cooldown** | 5s - 10s | After any trade across any symbol | Prevents multi-symbol burst triggers at the exact same second |
| **Price Proximity** | 0.03% (~$0.80 on Gold) | Proximity to existing entry | Prevents duplicate orders stacking at the identical price |

---

## 3. Telegram Commands

- `/cooldown` or `/cooldown status`: View live anti-spam, single-direction, and cooldown timers.
- `/cooldown <seconds>` (e.g. `/cooldown 30`): Set custom entry cooldown duration in seconds (applies to Auto & Manual).
- `/cooldown auto`: Adaptive profile-aware cooldown mode (scalp vs swing).
- `/cooldown on`: Strict manual cooldown mode.
- `/cooldown off`: Deactivate time cooldowns (single-direction and capacity safety limits remain strictly active).
- `/cooldown reset`: Reset all active timers and consecutive loss streaks.
- `/spam on` / `/spam off`: Toggle burst spam mode up to 6 positions (single-direction protection remains active).
