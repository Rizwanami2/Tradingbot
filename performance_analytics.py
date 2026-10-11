"""
Performance Analytics Module
============================
Tracks trade outcomes, agent contribution, expectancy, and Telegram reports.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Dict, List

STATS_FILE = "performance_stats.json"


@dataclass
class TradeRecord:
    id: str
    symbol: str
    direction: str
    entry: float
    exit: float
    r_multiple: float
    pnl: float
    agents: List[str]
    regime: str = ""
    session: str = ""
    closed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _safe_trade(data: dict) -> TradeRecord:
    allowed = {f.name for f in fields(TradeRecord)}
    cleaned = {k: v for k, v in data.items() if k in allowed}
    cleaned.setdefault("agents", [])
    cleaned.setdefault("regime", "")
    cleaned.setdefault("session", "")
    return TradeRecord(**cleaned)


class PerformanceTracker:
    def __init__(self, path: str = STATS_FILE):
        self.path = path
        self.trades: List[TradeRecord] = []
        self.agent_stats: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.trades = [_safe_trade(t) for t in data.get("trades", [])]
                self.agent_stats = data.get("agent_stats", {}) or {}
            except Exception:
                self.trades = []
                self.agent_stats = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "trades": [asdict(t) for t in self.trades[-500:]],
                        "agent_stats": self.agent_stats,
                    },
                    f,
                    indent=2,
                )
        except Exception:
            pass

    def record_trade(self, trade: TradeRecord):
        self.trades.append(trade)
        for agent in trade.agents or []:
            st = self.agent_stats.setdefault(
                agent, {"wins": 0, "losses": 0, "total_r": 0.0, "count": 0}
            )
            st["count"] += 1
            st["total_r"] += float(trade.r_multiple)
            if trade.r_multiple > 0:
                st["wins"] += 1
            else:
                st["losses"] += 1
        self._save()

    def summary(self) -> Dict:
        if not self.trades:
            return {"trades": 0, "message": "No closed trades yet"}
        rs = [t.r_multiple for t in self.trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        total = len(rs)
        win_rate = len(wins) / total if total else 0.0
        avg_r = sum(rs) / total if total else 0.0
        if losses and abs(sum(losses)) > 1e-12:
            profit_factor = sum(wins) / abs(sum(losses))
            pf_out = round(profit_factor, 2)
        elif wins:
            pf_out = "∞"
        else:
            pf_out = 0.0
        agent_report = {}
        for name, st in self.agent_stats.items():
            cnt = st["count"] or 1
            agent_report[name] = {
                "count": st["count"],
                "win_rate": round(st["wins"] / cnt, 3),
                "avg_r": round(st["total_r"] / cnt, 3),
            }
        return {
            "trades": total,
            "win_rate": round(win_rate, 3),
            "avg_r": round(avg_r, 3),
            "expectancy_r": round(avg_r, 3),
            "profit_factor": pf_out,
            "total_r": round(sum(rs), 2),
            "agents": agent_report,
        }

    def telegram_report(self) -> str:
        s = self.summary()
        if s.get("trades", 0) == 0:
            return "📊 **Performance**\nNo closed trades yet."
        lines = [
            "📊 **Performance Report**",
            f"Trades: `{s['trades']}` | Win rate: `{s['win_rate']*100:.1f}%`",
            f"Avg R: `{s['avg_r']:+.2f}` | Expectancy: `{s['expectancy_r']:+.2f}R`",
            f"Profit Factor: `{s['profit_factor']}` | Total R: `{s['total_r']:+.1f}`",
            "",
            "**Agent Contribution**",
        ]
        for name, a in s.get("agents", {}).items():
            lines.append(
                f"• {name}: {a['count']} trades | WR {a['win_rate']*100:.0f}% | AvgR {a['avg_r']:+.2f}"
            )
        return "\n".join(lines)
