# Real-time BTC / Gold data fix

## Problem
`api.binance.com` returns **451 geo-restricted** from many regions (including some Railway hosts).
`DataFeed` then failed because CCXT/yfinance were missing or also blocked, and there was **no TradingView / mirror fallback** for live mark prices and levels.

## Fix
New module: `realtime_prices.py`

| Source | BTC | Gold |
|--------|-----|------|
| data-api.binance.vision | ✅ | PAXG proxy |
| api.binance.us | ✅ | PAXG proxy |
| Coinbase spot | ✅ | — |
| TradingView scanner | ✅ | ✅ OANDA:XAUUSD |
| yfinance | fallback | GC=F |

## Commands
- `/price BTC` or `/price XAUUSD` — live mark (uses fixed stack via `get_price_info`)
- `/levels BTC` — PDH/PDL, pivots, session H/L, swings
- `/levels GOLD` or `/levels XAUUSD` — same for gold

## Deploy
```bash
git pull origin main
# restart Railway
```

No API keys required for public price endpoints.
"
