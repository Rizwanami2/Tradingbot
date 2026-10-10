# Operations Dashboard and Telegram Controls (Phase 4)

## Start the bot

The bot writes a local status snapshot to .runtime/bot_status.json by default. Override with BOT_STATUS_FILE if needed. The monitoring loop updates the snapshot with last poll/success timestamps, consecutive errors, configured broker mode, equity, and in-process position counts.

Monitoring is OFF by default. Keep BOT_AUTOTRADE=false (the default); only explicitly enable monitoring after configuring the Telegram control allowlist.

## Telegram control authorization

State-changing commands are denied unless the sender's numeric Telegram user ID is listed in TELEGRAM_ALLOWED_USER_IDS as a comma-separated allowlist.

Example .env settings (replace the ID with your own numeric Telegram user ID):

    TELEGRAM_ALLOWED_USER_IDS=123456789
    BOT_AUTOTRADE=false
    BOT_STATUS_FILE=.runtime/bot_status.json
    BOT_DASHBOARD_HOST=127.0.0.1
    BOT_DASHBOARD_PORT=8765

Use your own Telegram numeric user ID, not a chat ID or bot ID. Keep .env private. The allowlist protects /autotrade on|off, /close, /cancel, and leverage changes. Read-only commands remain available. If the allowlist is empty, these controls fail closed.

## Run the read-only dashboard

Start the bot in one terminal, then in another run:

    python ops_dashboard.py

Open http://127.0.0.1:8765. The dashboard polls the status API every five seconds and does not expose trade controls.

- GET /health — minimal health response; returns HTTP 503 when status is missing, stale, or monitoring errors are recorded.
- GET /api/status — read-only runtime snapshot. If a token is configured, send Authorization: Bearer YOUR_TOKEN.
- GET / — dashboard UI.

The dashboard binds to loopback by default. If you deliberately bind to a non-loopback address, configure a strong BOT_DASHBOARD_TOKEN; the server refuses non-loopback binds without one. Do not expose the dashboard directly to the public internet; use a private network or authenticated reverse proxy.

## Important semantics

- The displayed broker mode is configuration metadata, not proof that a live exchange connection is safe or active.
- The bot's monitoring toggle controls scanning and setup alerts; it is not authorization to place live exchange orders.
- Health is based on the timestamp of the last successful market-data poll and consecutive monitoring errors. It does not prove that the exchange is reachable for order execution.
- The dashboard is read-only and is not a substitute for exchange-side order/position reconciliation.
