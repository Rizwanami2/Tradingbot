# Combined automatic path

When `/autotrade on` is active:

1. Agents decide setup
2. Autonomous Learner may veto / resize
3. **AI Advanced Suite** gates (scorecard in balance mode, revenge, news, FOMO, DD, etc.)
4. `register_setup` → MT5/EA execution
5. On open/close → suite memory + learner updates

Control without restart:

- `/ai status` — see gates state
- `/ai on|off` — enable/disable suite only
- `/ai persona med` — risk profile
- `/ai freeze` — stop weight learning
- `/autotrade on` — automatic trading

Both run together by default (`AI_ADVANCED_ENABLED=true`).
