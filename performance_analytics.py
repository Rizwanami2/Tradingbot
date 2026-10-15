"""
Performance Analytics Module
============================
Tracks trade outcomes, agent contribution, expectancy, Sharpe ratio, and Telegram reports.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Dict, List, Optional

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
    cleaned["entry"] = float(cleaned.get("entry") or 0.0)
    cleaned["exit"] = float(cleaned.get("exit") or cleaned["entry"])
    cleaned["r_multiple"] = float(cleaned.get("r_multiple") or 0.0)
    cleaned["pnl"] = float(cleaned.get("pnl") or 0.0)
    return TradeRecord(**cleaned)


class PerformanceTracker:
    def __init__(self, path: str = STATS_FILE, journal_path: Optional[str] = "trade_journal.json"):
        self.path = path
        self.journal_path = journal_path
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
        self._sync_from_journal()

    def _sync_from_journal(self):
        """Sync closed trades from trade_journal.json if not yet recorded."""
        if not self.journal_path:
            return
        try:
            if os.path.exists(self.journal_path):
                with open(self.journal_path, "r", encoding="utf-8") as f:
                    jdata = json.load(f)
                existing_ids = {t.id for t in self.trades if getattr(t, "id", None)}
                new_added = False
                for entry in jdata.get("closed", []):
                    tid = str(entry.get("id") or "")
                    if tid and tid not in existing_ids:
                        rec = TradeRecord(
                            id=tid,
                            symbol=str(entry.get("symbol", "")),
                            direction=str(entry.get("direction", "")),
                            entry=float(entry.get("entry") or 0.0),
                            exit=float(entry.get("exit_price") or entry.get("entry") or 0.0),
                            r_multiple=float(entry.get("r_multiple") or 0.0),
                            pnl=float(entry.get("pnl") or 0.0),
                            agents=list(entry.get("agents") or []),
                            regime=str(entry.get("regime") or ""),
                            session=str(entry.get("session") or ""),
                            closed_at=str(entry.get("closed_at") or datetime.now(timezone.utc).isoformat()),
                        )
                        self.trades.append(rec)
                        existing_ids.add(tid)
                        new_added = True
                        for agent in rec.agents or []:
                            st = self.agent_stats.setdefault(
                                agent, {"wins": 0, "losses": 0, "total_r": 0.0, "count": 0}
                            )
                            st["count"] += 1
                            st["total_r"] += float(rec.r_multiple)
                            if rec.r_multiple > 0:
                                st["wins"] += 1
                            else:
                                st["losses"] += 1
                if new_added:
                    self._save()
        except Exception:
            pass

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
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_r": 0.0,
                "expectancy_r": 0.0,
                "profit_factor": 0.0,
                "total_r": 0.0,
                "total_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown_r": 0.0,
                "best_trade_r": 0.0,
                "worst_trade_r": 0.0,
                "agents": {},
                "message": "No closed trades yet",
            }
        rs = [float(t.r_multiple) for t in self.trades]
        pnls = [float(t.pnl) for t in self.trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        total = len(rs)
        win_rate = len(wins) / total if total else 0.0
        avg_r = sum(rs) / total if total else 0.0
        total_pnl = sum(pnls)

        if losses and abs(sum(losses)) > 1e-12:
            profit_factor = sum(wins) / abs(sum(losses))
            pf_out = round(profit_factor, 2)
        elif wins:
            pf_out = "∞"
        else:
            pf_out = 0.0

        # Drawdown in cumulative R
        cum_r = 0.0
        peak_r = 0.0
        max_dd_r = 0.0
        for r in rs:
            cum_r += r
            if cum_r > peak_r:
                peak_r = cum_r
            dd = peak_r - cum_r
            if dd > max_dd_r:
                max_dd_r = dd

        # Sharpe and Sortino ratios
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        if total >= 2:
            try:
                import numpy as np
                r_arr = np.array(rs, dtype=float)
                mean_r = float(np.mean(r_arr))
                std_r = float(np.std(r_arr, ddof=1))
                if std_r > 1e-9:
                    sharpe_ratio = round(float(mean_r / std_r * np.sqrt(min(total, 252))), 2)

                downside = [r for r in rs if r < 0]
                if downside:
                    down_arr = np.array(downside, dtype=float)
                    down_std = float(np.std(down_arr, ddof=1)) if len(downside) > 1 else abs(downside[0])
                    if down_std > 1e-9:
                        sortino_ratio = round(float(mean_r / down_std * np.sqrt(min(total, 252))), 2)
                else:
                    sortino_ratio = sharpe_ratio
            except Exception:
                pass
        elif total == 1:
            sharpe_ratio = round(rs[0], 2) if rs[0] > 0 else 0.0
            sortino_ratio = sharpe_ratio

        agent_report = {}
        for name, st in self.agent_stats.items():
            cnt = st.get("count", 0) or 1
            agent_report[name] = {
                "count": st.get("count", 0),
                "wins": st.get("wins", 0),
                "losses": st.get("losses", 0),
                "win_rate": round(st.get("wins", 0) / cnt, 3),
                "avg_r": round(st.get("total_r", 0.0) / cnt, 3),
                "total_r": round(st.get("total_r", 0.0), 3),
            }

        return {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 3),
            "avg_r": round(avg_r, 3),
            "expectancy_r": round(avg_r, 3),
            "profit_factor": pf_out,
            "total_r": round(sum(rs), 2),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown_r": round(max_dd_r, 2),
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "best_trade_r": round(max(rs), 3) if rs else 0.0,
            "worst_trade_r": round(min(rs), 3) if rs else 0.0,
            "agents": agent_report,
        }

    def telegram_report(self) -> str:
        s = self.summary()
        if s.get("trades", 0) == 0:
            return (
                "📊 *Performance Analytics*\n"
                "• Closed Trades: `0`\n"
                "• Status: _No closed trades recorded yet._\n"
                "• Metrics (win rate, Sharpe ratio, profit factor, agent contribution) will automatically update as trades complete."
            )
        pnl_val = s.get("total_pnl", 0.0)
        pnl_str = f"+${pnl_val:,.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):,.2f}"
        tot_r = s.get("total_r", 0.0)

        lines = [
            "📊 *Performance Report*",
            f"• Closed Trades: `{s['trades']}` | Win Rate: `{s['win_rate']*100:.1f}%` ({s['wins']}W - {s['losses']}L)",
            f"• Realized PnL: `{pnl_str}` | Total R: `{tot_r:+.2f}R`",
            f"• Avg R: `{s['avg_r']:+.2f}` | Expectancy: `{s['expectancy_r']:+.2f}R`",
            f"• Profit Factor: `{s['profit_factor']}` | Sharpe: `{s.get('sharpe_ratio', 0.0):.2f}` | Sortino: `{s.get('sortino_ratio', 0.0):.2f}`",
            f"• Max Drawdown: `-{s.get('max_drawdown_r', 0.0):.2f}R` | Best: `{s.get('best_trade_r', 0.0):+.2f}R` | Worst: `{s.get('worst_trade_r', 0.0):+.2f}R`",
        ]
        if s.get("agents"):
            lines.append("")
            lines.append("*Agent Contribution*")
            sorted_agents = sorted(
                s["agents"].items(),
                key=lambda x: (x[1]["count"], x[1]["avg_r"]),
                reverse=True,
            )
            for name, a in sorted_agents[:8]:
                lines.append(
                    f"• {name}: {a['count']} trades | WR {a['win_rate']*100:.0f}% | AvgR {a['avg_r']:+.2f}"
                )
        return "\n".join(lines)

    def agent_report(self) -> str:
        s = self.summary()
        if not s.get("agents"):
            return "🤖 *Agent Performance*\nNo agent trade history recorded yet."
        lines = [
            "🤖 *Agent Performance Rankings*",
            f"Total Evaluated Trades: `{s.get('trades', 0)}`",
            "",
        ]
        sorted_agents = sorted(
            s["agents"].items(),
            key=lambda x: x[1]["total_r"],
            reverse=True,
        )
        for rank, (name, a) in enumerate(sorted_agents, 1):
            icon = "🟢" if a["avg_r"] > 0 else ("🔴" if a["avg_r"] < 0 else "⚪")
            lines.append(
                f"{rank}. {icon} *{name}:* {a['count']} trades | "
                f"WR `{a['win_rate']*100:.1f}%` ({a.get('wins', 0)}W - {a.get('losses', 0)}L) | "
                f"Total `{a.get('total_r', 0.0):+.2f}R` | Avg `{a['avg_r']:+.2f}R`"
            )
        return "\n".join(lines)

    def recent_trades_report(self, limit: int = 10) -> str:
        if not self.trades:
            return "📜 *Recent Closed Trades*\nNo closed trades recorded yet."
        recent = self.trades[-limit:]
        lines = [
            f"📜 *Recent Closed Trades (Last {len(recent)})*",
        ]
        for t in reversed(recent):
            won = float(t.r_multiple) > 0
            icon = "✅" if won else "❌"
            pnl_str = f"{float(t.pnl):+.2f}" if t.pnl is not None else "0.00"
            lines.append(
                f"• {icon} `#{t.id}` *{t.symbol}* {t.direction} | "
                f"R: `{float(t.r_multiple):+.2f}R` | PnL: `${pnl_str}` | "
                f"Exit: `${float(t.exit):,.2f}`"
            )
        return "\n".join(lines)

    def reset(self) -> Dict:
        self.trades = []
        self.agent_stats = {}
        self._save()
        return {"status": "reset", "trades": 0}
