# Anti-spam cooldowns removed

Entry, loss, and global cooldowns no longer block autotrade.
Price-proximity cooldown checks are also disabled.

## Still enforced
- Max open slots (`max_open`)
- Max positions per currency (if configured)

## Apply on server

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/remove_antispam_cooldowns.py
# restart bot
```

Or pull if `anti_spam.py` already contains `cooldowns disabled`.

## Telegram
`/cooldown` / `/antispam` may still show status but cooldowns report as disabled / 0s remaining.
