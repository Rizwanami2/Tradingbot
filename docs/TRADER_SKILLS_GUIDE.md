# 🧰 11 Trader Skills Suite — User & Developer Guide

## Overview

All 11 skills from [`Jakeschincariol/trader-skill`](https://github.com/Jakeschincariol/trader-skill) have been fully integrated into **nimble-darwin**.

This suite equips the bot with institutional reverse-engineering, congressional disclosure monitoring, fundamental SEC XBRL earnings analysis, portfolio sizing, strict risk firewalls, and tax lot accounting.

Everything runs on standard-library Python with zero external API key requirements.

---

## The 11 Skills & Telegram Commands

| Command | Skill | Description | Data Source |
| :--- | :--- | :--- | :--- |
| **`/skills`** | **Skills Hub** | Interactive dashboard with inline category buttons for all 11 skills. | Bot Dispatcher |
| **`/copycat [filer]`** | **13F Reverse Engineer** | Clones institutional portfolios (e.g. Buffett, Ackman, Burry, Dalio) from latest SEC 13F-HR filings. | SEC EDGAR 13F-HR |
| **`/pelosi [member]`** | **Congressional Tracker** | Monitors US House PTR disclosures filed under the STOCK Act as soon as posted. | House Clerk Disclosures |
| **`/secnews [tickers]`** | **8-K & News Monitor** | Watches breaking material SEC 8-K filings and financial headlines for your holdings. | SEC Atom Feeds & Yahoo RSS |
| **`/analyst [ticker]`** | **Earnings Analyst** | Scrapes official reported corporate financials from SEC XBRL (revenue, EPS, margins, growth). | SEC EDGAR XBRL (10-Q/10-K) |
| **`/screener [rules]`** | **Stock Screener** | Filters equities against configurable financial rules and ranks passing candidates. | SEC Metrics & Prices |
| **`/budget [amount]`** | **Budget Sizer** | Sizes target weights to budget with a mandatory cash buffer and max allocation caps. | Portfolio Targets |
| **`/riskcheck`** | **Risk Firewall** | Pre-trade compliance checker (blocks unauthorized tickers, oversized tickets, leverage). | Pre-Trade Validator |
| **`/ticket [show]`** | **Execution Gate** | Human-in-the-loop order tickets with explicit confirmation before routing. | Broker Tickets |
| **`/tax [report]`** | **Tax Optimizer** | Tracks cost-basis lots (FIFO/HIFO), wash-sale windows (30 days), and loss harvesting. | Cost-Basis Engine |
| **`/nyse`** | **Market Clock** | NYSE trading calendar, 2026/2027 holiday schedules, and early close countdowns. | NYSE Holiday Calendar |
| **`/rebalance [band]`** | **Rebalancer** | Calculates portfolio drift and generates minimal rebalancing orders (sells before buys). | Target Drift Engine |

---

## Programmatic Usage in Python

You can invoke any skill directly in Python from anywhere in the codebase:

```python
import trader_skills

# 1. Check NYSE market status
clock_res = trader_skills.alerts(["clock"])
print(clock_res["output"])

# 2. Search for an institutional filer
filer_res = trader_skills.copycat(["find", "buffett"])
print(filer_res["output"])

# 3. Check latest congressional disclosures
pelosi_res = trader_skills.pelosi(["index", "--member", "pelosi"])
print(pelosi_res["output"])

# 4. Pull official SEC XBRL earnings
analyst_res = trader_skills.analyst(["AAPL"])
print(analyst_res["output"])

# 5. Size a $10,000 budget with 10% cash buffer
budget_res = trader_skills.budget(["--budget", "10000"])
print(budget_res["output"])
```

Each wrapper returns a structured dictionary:
```python
{
    "exit_code": 0,
    "output": "string output...",
    "error": "",
    "ok": True
}
```

---

## Directory Architecture

```
trader_skills/
├── __init__.py               # Dynamic loader & high-level Python API
├── trader-alerts/            # NYSE calendar, clock & price alert rules
├── trader-analyst/           # SEC XBRL financial facts parser (10-Q / 10-K)
├── trader-broker/            # Order execution tickets & approval logger
├── trader-budget/            # Position sizing, cash buffer & caps
├── trader-copycat/           # 13F-HR scraper & portfolio cloner
├── trader-news/              # SEC 8-K Atom feeds & Yahoo headline monitor
├── trader-pelosi/            # House Clerk PTR PDF disclosure scraper
├── trader-rebalance/         # Target drift calculator & rebalance engine
├── trader-risk/              # Pre-trade compliance firewall
├── trader-screener/          # Quantitative rule-based equity screener
└── trader-tax/               # Tax lots, wash sales & harvest candidates
```

---

## Verification & Tests

To execute the full trader skills test suite:

```bash
# Telegram & Integration tests:
pytest tests/test_telegram_trader_skills.py -v

# 167 Offline Unit tests:
python3 -m unittest discover -s tests/trader_skills -v
```
