# Trader Skills Integration

This folder is used by the free open-source **Trader skills** (https://github.com/Jakeschincariol/trader-skill).

The skills write research data here so your main bot can optionally read it.

## Quick Start

1. Copy the example config:

```bash
cp trader/config.example.json trader/config.json
```

2. Edit `trader/config.json` and put your real name + email in `sec_user_agent` (required by SEC).

3. Talk to the AI normally, for example:

- `copy Berkshire top 5`
- `I have $10000, size this portfolio`
- `any news on my targets`
- `what is Pelosi buying`
- `analyze NVDA earnings`

## Files created by the skills

| File | Created by | Purpose |
|------|------------|---------|
| `targets.json` | trader-copycat | Portfolio weights to copy |
| `orders.json` | trader-budget | Proposed trades sized to your money |
| `metrics.json` | trader-analyst | Earnings & fundamental metrics |
| `watchlist.json` | various | Tickers being monitored |
| `portfolio.json` | trader-tax / rebalance | Lots & current holdings |

## Important

- Keep `paper: true` until you are ready.
- Never commit `trader/config.json` or the runtime `*.json` files (already in `.gitignore`).
- These skills never place orders by themselves. Your existing bot / Alpaca / MT5 bridge stays in control.
