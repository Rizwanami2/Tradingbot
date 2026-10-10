# Operations Dashboard and Telegram Controls (Phase 4)

## Start the bot

The bot writes a local status snapshot to `.runtime/bot_status.json` by default. Override with `BOT_STATUS_FILE` if needed. The monitoring loop updates the snapshot with last poll/success timestamps, consecutive errors, configured broker mode, equity, and in-process position counts.

Monitoring is OFF by default. Keep `BOT_AUTOTRADE=false` (the default); enable monitoring only when you intend to receive setup alerts.

Example `.env` settings:

    BOT_AUTOTRADE=false
    BOT_STATUS_FILE=.runtime/bot_status.json
    BOT_DASHBOARD_HOST=127.0.0.1
    BOT_DASHBOARD_PORT=8765

Telegram commands no longer use a numeric-user allowlist. Anyone who can send commands to the bot may invoke state-changing in-process controls such as `/autotrade on|off`, `/close`, `/cancel`, and leverage changes. Keep the bot private and restrict access through Telegram chat/group settings. These commands manage the bot's in-process setup state; they do not enable live exchange orders.

## Run the read-only dashboard

Start the bot in one terminal, then in another run:

    python ops_dashboard.py

Open http://127.0.0.1:8765. The dashboard polls the status API every five seconds and does not expose trade controls.

- `GET /health` — minimal health response; returns HTTP 503 when status is missing, stale, or monitoring errors are recorded.
- `GET /api/status` — read-only runtime snapshot. If a token is configured, send `Authorization: Bearer YOUR_TOKEN`.
- `GET /` — dashboard UI.

The dashboard binds to loopback by default. If you deliberately bind to a non-loopback address, configure a strong `BOT_DASHBOARD_TOKEN`; the server refuses non-loopback binds without one. Do not expose the dashboard directly to the public internet; use a private network or authenticated reverse proxy.

## Important semantics

- The displayed broker mode is configuration metadata, not proof that a live exchange connection is safe or active.
- The bot's monitoring toggle controls scanning and setup alerts; it is not authorization to place live exchange orders.
- Health is based on the timestamp of the last successful market-data poll and consecutive monitoring errors. It does not prove that the exchange is reachable for order execution.
- The dashboard is read-only and is not a substitute for exchange-side order/position reconciliation.
