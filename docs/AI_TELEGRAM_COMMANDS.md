# AI Telegram Commands

## `/ai` — AI Advanced Suite control

| Command | Action |
|---------|--------|
| `/ai` or `/ai status` | Gates, persona, bias, daily R, learn freeze |
| `/ai persona low\|med\|high` | Risk persona |
| `/ai bias long\|short\|clear` | Day bias filter |
| `/ai freeze` | Stop weight updates |
| `/ai unfreeze` | Resume learning |
| `/ai on` / `/ai off` | Enable / disable suite gates |
| `/ai health` | Health + fill rejects |
| `/ai critique` | Weekly self-critique (Gemini if key set) |
| `/ai help` | This menu |

Registered in `telegram_advanced.register_all_advanced` and `main.py`.

Scorecard still applies in **balance** profile only by default (`AI_SCORECARD_BALANCE_ONLY=true`).
