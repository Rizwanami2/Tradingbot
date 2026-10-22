# Telegram commands not showing (cooldown etc.)

## Important

Handlers can work while the **menu list** is empty or stale.

- Typing `/cooldown` manually still works if the handler is registered.
- The **/** menu is a separate Telegram API list (`set_my_commands`).

## Causes

1. **Stale Telegram cache** (phone app)
2. **@BotFather** `/setcommands` overrides API menu
3. Menu list missing useful commands (bridge, ai, …)
4. Sync failed at bot startup

## Apply

```powershell
cd C:\Tradingbot
git pull origin main
python scripts/fix_telegram_menu.py
```

Restart bot, then in Telegram:

```text
/updatemenu
```

Force-close Telegram app and reopen. Type `/` — you should see cooldown, bridge, etc.

## BotFather clear (if still empty)

1. Open @BotFather  
2. `/mybots` → your bot → **Edit Bot** → **Edit Commands**  
3. Send `/empty`  
4. Back in your bot: `/updatemenu`

## Menu now includes

start, help, status, bridge, mt5, signal, combined, autotrade, positions, close, balance, **cooldown**, spam, watchlist, profile, ai, risk, price, chart, settings, updatemenu, menu

## Still works if not in menu

You can always type the command:

`/cooldown` `/bridge` `/combined` `/autotrade` `/ai`
