# Why /cooldown and new agents looked broken

## Root causes

### 1. CombinedBotAgent session filter (main issue for "agents not working")
Agent only allowed **London 07:00–10:00 UTC** and **London–NY 12:00–16:00 UTC**.
Outside those hours every signal got:

```text
COMBINED AGENT VETO — session_off
```

India daytime is often **outside** those windows → **no trades**.

**Fix:** Session filter **OFF by default**. Enable only if you want:

```env
COMBINED_AGENT_SESSION_FILTER=true
```

### 2. `/combined` showed empty metrics
Telegram created a **new** agent instance; autotrade used another.
Journal/metrics did not match.

**Fix:** Shared `get_combined_agent()` singleton.

### 3. `/cooldown 0` could not clear cooldowns
`set_entry_cooldown` used `max(5, seconds)` → minimum 5 seconds forever.

**Fix:** `max(0, seconds)` so `/cooldown 0` works.

### 4. ANTISPAM_MODE=off still applied time cooldowns
**Fix:** Mode `off` skips entry/loss/global time cooldowns (still keeps direction + max slots).

---

## Apply on your PC

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/fix_cooldown_and_agents.py
python -m py_compile combined_bot_agent.py anti_spam.py autotrade_worker.py
```

Restart bot.

## .env recommended

```env
COMBINED_AGENT_ENABLED=true
COMBINED_AGENT_SESSION_FILTER=false
ANTISPAM_MODE=off
```

## Telegram checks

```text
/cooldown
/cooldown 0
/cooldown reset
/combined
/status
/autotrade on
```

Logs should **not** spam `session_off` unless you turned session filter on.
