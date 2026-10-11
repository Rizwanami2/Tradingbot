"""
Ops & Reliability (62-66)
=========================
- Daily Telegram digest builder
- Health self-heal counter
- Graceful data-feed fallback helper
- Trade replay formatter
- Journal CSV export
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

HEALTH_FILE = "ops_health.json"


class HealthMonitor:
    def __init__(self, max_errors: int = 5, path: str = HEALTH_FILE):
        self.max_errors = max_errors
        self.path = path
        self.consecutive_errors = 0
        self.last_error = ""
        self.last_ok = datetime.now(timezone.utc).isoformat()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.consecutive_errors = int(data.get("consecutive_errors", 0))
                self.last_error = data.get("last_error", "")
                self.last_ok = data.get("last_ok", self.last_ok)
            except Exception:
                pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "consecutive_errors": self.consecutive_errors,
                    "last_error": self.last_error,
                    "last_ok": self.last_ok,
                }, f, indent=2)
        except Exception:
            pass

    def record_ok(self):
        self.consecutive_errors = 0
        self.last_ok = datetime.now(timezone.utc).isoformat()
        self._save()

    def record_error(self, msg: str) -> bool:
        """Returns True if self-heal (restart) is recommended."""
        self.consecutive_errors += 1
        self.last_error = str(msg)[:300]
        self._save()
        return self.consecutive_errors >= self.max_errors

    def status(self) -> str:
        return (
            f"Health: errors={self.consecutive_errors}/{self.max_errors} | "
            f"last_ok={self.last_ok} | last_err={self.last_error or 'none'}"
        )


def data_feed_fallback_order() -> List[str]:
    """Preferred order of data sources."""
    return ["ccxt", "tradingview", "data_feed", "cache", "freeze"]


def build_daily_digest(
    equity: float,
    peak: float,
    daily_r: float,
    weekly_r: float,
    open_positions: int,
    journal_exp: Dict[str, Any],
    tier_line: str,
    learner_summary: str = "",
    health: str = "",
) -> str:
    dd = (peak - equity) / peak * 100 if peak else 0
    lines = [
        "📅 **Daily Digest**",
        f"Equity `${equity:,.2f}` | Peak `${peak:,.2f}` | DD `{dd:.1f}%`",
        f"Daily R `{daily_r:+.2f}` | Weekly R `{weekly_r:+.2f}`",
        f"Open positions: `{open_positions}`",
        tier_line,
        f"Journal: n=`{journal_exp.get('trades', 0)}` WR=`{journal_exp.get('winrate', 0)}%` "
        f"ExpR=`{journal_exp.get('expectancy_r', 0)}`",
    ]
    if learner_summary:
        lines.append(learner_summary)
    if health:
        lines.append(health)
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def format_trade_replay(entry: Dict[str, Any], votes: Optional[List[Dict]] = None) -> str:
    """Pretty replay of a single trade for /replay."""
    lines = [
        f"🎬 **Replay `{entry.get('id', '?')}`**",
        f"`{entry.get('symbol')}` {entry.get('direction')} | session `{entry.get('session')}` | regime `{entry.get('regime')}`",
        f"Entry `{entry.get('entry')}` → Exit `{entry.get('exit_price')}`",
        f"R=`{entry.get('r_multiple')}` PnL=`{entry.get('pnl')}` {'✅' if entry.get('won') else '❌'}",
        f"Score `{entry.get('score')}` | Conviction `{entry.get('conviction')}`",
        f"Agents: {', '.join(entry.get('agents') or [])}",
        f"MAE=`{entry.get('mae')}` MFE=`{entry.get('mfe')}` hold=`{entry.get('hold_bars')}` bars",
    ]
    if votes:
        lines.append("\n**Votes at entry:**")
        for v in votes:
            lines.append(f"• {v.get('agent')}: {v.get('direction')} ({v.get('strength')}) — {v.get('reason', '')}")
    if entry.get("notes"):
        lines.append(f"Notes: {entry.get('notes')}")
    return "\n".join(lines)


def journal_to_csv(entries: List[Dict[str, Any]]) -> str:
    """Return CSV string of closed trades."""
    if not entries:
        return "id,symbol,direction,entry,exit,r_multiple,pnl,won\n"
    fields = [
        "id", "symbol", "direction", "entry", "exit_price", "stop", "tp1",
        "r_multiple", "pnl", "won", "mae", "mfe", "hold_bars",
        "session", "regime", "score", "conviction", "opened_at", "closed_at",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for e in entries:
        writer.writerow(e)
    return buf.getvalue()
