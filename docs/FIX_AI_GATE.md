# Fix: AI suite gate + veto alerts

## Problem
Autotrade had AI gate stubbed to always allow (`return True, sig`), so suite risk gates were not applied.

## Fix
- Restore `evaluate_signal_dict` on every setup
- `risk_mgr.can_trade()` hard halt
- Telegram alerts for risk/AI/quality vetoes (rate-limited 120s)

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/fix_ai_gate.py
# restart bot
```

## Expect
Logs: `AI VETO`, `RISK VETO`  
Telegram: `🤖 AI veto` / `🛡️ Risk veto`

Keep `AI_ADVANCED_ENABLED=true`.
