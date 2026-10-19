# Module risk fixes (applied)

Safer defaults and hard gates so module risks are reduced in code.

## Changes

| Module | Fix |
|--------|-----|
| **config** | `MAX_OPEN_TRADES` default **2** (was 6); per-symbol **1**; `SAFETY_MAX_OPEN_TRADES=3` hard cap; `SAFETY_REQUIRE_RISK_CAN_TRADE=true` |
| **risk_manager** | Daily loss **2%** (was 3%); consec losses **3** (was 4%); base risk **0.15%** (was 0.20%) |
| **autotrade_worker** | Clamps open slots to safety cap; **blocks new entries** when `risk_mgr.can_trade()` is false |
| **ai_advanced_suite** | Max daily trades **6** (was 12); consec losses **3** (was 4) |
| **.env.example** | Autotrade default **false**; leverage **10**; matching AI limits |

## Still your responsibility

- EA AutoTrading on/off
- Lot size / manual lots
- Live opt-in (`BOT_AUTOTRADE_LIVE_OPT_IN`)
- Telegram token security
- Demo before live

## Env overrides

```env
BOT_MAX_OPEN_TRADES=2
BOT_SAFETY_MAX_OPEN=3
BOT_SAFETY_REQUIRE_RISK=true
AI_MAX_DAILY_TRADES=6
AI_MAX_CONSEC_LOSSES=3
AI_DAILY_LOSS_R_LIMIT=2.0
BOT_AUTOTRADE=false
```

Turn autotrade on only after `/bridge ping` and demo checks.
