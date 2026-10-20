# Auto-learn on every trade close + live analysis

## Already built in design

`autonomous_learner.on_trade_closed` updates:
- live trade log
- agent weights
- setup bans after 3 consecutive losses
- AILearnerAgent
- periodic flaw mining every 10 trades

## What we enhanced

1. **Every close** → brain + AI suite learn
2. **Telegram live analysis** on close (`Learn+Close` message)
3. **CSV journal** → `data/live_trade_journal.csv`
4. **Live learn veto** (optional) → `brain.advise_signal` can block bad patterns

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/enable_auto_learn_live.py
# restart bot
```

## .env

```env
BOT_LEARN_LIVE_VETO=true
AI_LEARN_FREEZE=false
```

`BOT_LEARN_LIVE_VETO=false` → still learns on close, but does not block new entries from learned bans.

## Telegram examples

```text
✅ Learn+Close XAUUSD LONG
PnL: +3.20 · R: +1.10 · Live trades: 12
```

```text
🧠 Learn veto XAUUSD
AUTO: banned setup ...
```
