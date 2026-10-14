"""
Trade Journal + Auto-Learn Wiring
=================================
Persistent trade log. On every close:
  1. Append structured record
  2. Call AI-Learner.learn()
  3. Feed agent contribution stats for Arbiter weight adaptation

Usage:
    from trade_journal import TradeJournal
    journal = TradeJournal()
    journal.record_open(...)
    journal.record_close(..., features=feats)  # triggers learn
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

JOURNAL_FILE = "trade_journal.json"


@dataclass
class JournalEntry:
    id: str
    symbol: str
    direction: str
    entry: float
    stop: float
    tp1: float
    size: float
    risk_amount: float
    opened_at: str
    agents: List[str] = field(default_factory=list)
    regime: str = ""
    session: str = ""
    score: float = 0.0
    conviction: float = 0.0
    # filled on close
    exit_price: Optional[float] = None
    closed_at: Optional[str] = None
    r_multiple: Optional[float] = None
    pnl: Optional[float] = None
    won: Optional[bool] = None
    mae: Optional[float] = None
    mfe: Optional[float] = None
    hold_bars: Optional[int] = None
    notes: str = ""


class TradeJournal:
    def __init__(self, path: str = JOURNAL_FILE):
        self.path = path
        self.entries: List[Dict[str, Any]] = []
        self.open: Dict[str, JournalEntry] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.entries = data.get("closed", [])
                for raw in data.get("open", []):
                    e = JournalEntry(**{k: v for k, v in raw.items() if k in JournalEntry.__dataclass_fields__})
                    self.open[e.id] = e
            except Exception as e:
                print(f"[TradeJournal] load error: {e}")

    def _save(self):
        try:
            payload = {
                "closed": self.entries[-500:],  # keep last 500
                "open": [asdict(e) for e in self.open.values()],
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"[TradeJournal] save error: {e}")

    def record_open(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop: float,
        tp1: float,
        size: float,
        risk_amount: float,
        agents: Optional[List[str]] = None,
        regime: str = "",
        session: str = "",
        score: float = 0.0,
        conviction: float = 0.0,
        trade_id: Optional[str] = None,
    ) -> str:
        tid = trade_id or str(uuid.uuid4())[:8]
        e = JournalEntry(
            id=tid,
            symbol=symbol.upper(),
            direction=direction.upper(),
            entry=float(entry),
            stop=float(stop),
            tp1=float(tp1),
            size=float(size),
            risk_amount=float(risk_amount),
            opened_at=datetime.now(timezone.utc).isoformat(),
            agents=list(agents or []),
            regime=regime,
            session=session,
            score=float(score),
            conviction=float(conviction),
        )
        self.open[tid] = e
        self._save()
        return tid

    def record_close(
        self,
        trade_id: str,
        exit_price: float,
        r_multiple: Optional[float] = None,
        pnl: Optional[float] = None,
        mae: Optional[float] = None,
        mfe: Optional[float] = None,
        hold_bars: Optional[int] = None,
        features: Optional[np.ndarray] = None,
        conf: float = 0.6,
        notes: str = "",
        auto_learn: bool = True,
    ) -> Optional[Dict[str, Any]]:
        e = self.open.pop(trade_id, None)
        if e is None:
            # try match by symbol if only one open
            for k, v in list(self.open.items()):
                if v.symbol == trade_id.upper():
                    e = self.open.pop(k)
                    break
        if e is None:
            return None

        exit_price = float(exit_price)
        if r_multiple is None:
            risk = abs(e.entry - e.stop)
            if risk > 0:
                if e.direction == "LONG":
                    r_multiple = (exit_price - e.entry) / risk
                else:
                    r_multiple = (e.entry - exit_price) / risk
            else:
                r_multiple = 0.0

        if pnl is None:
            pnl = r_multiple * e.risk_amount

        won = r_multiple > 0
        e.exit_price = exit_price
        e.closed_at = datetime.now(timezone.utc).isoformat()
        e.r_multiple = round(float(r_multiple), 4)
        e.pnl = round(float(pnl), 4)
        e.won = won
        e.mae = mae
        e.mfe = mfe
        e.hold_bars = hold_bars
        e.notes = notes

        rec = asdict(e)
        self.entries.append(rec)
        self._save()

        if auto_learn:
            self._trigger_learn(e, features, conf)

        return rec

    def _trigger_learn(self, e: JournalEntry, features: Optional[np.ndarray], conf: float):
        try:
            from ai_learner import AILearnerAgent
            learner = AILearnerAgent()
            learner.learn(
                direction_correct=bool(e.won),
                conf=float(conf or e.conviction or 0.6),
                won=bool(e.won),
                pnl=float(e.pnl or 0),
                features=features,
            )
        except Exception as err:
            print(f"[TradeJournal] learn error: {err}")

        try:
            from agent_stats import AgentStats
            stats = AgentStats()
            stats.record_trade(e.agents, won=bool(e.won), r=float(e.r_multiple or 0))
        except Exception as err:
            print(f"[TradeJournal] agent_stats error: {err}")

        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            h = None
            wd = None
            if e.closed_at:
                try:
                    dt = datetime.fromisoformat(e.closed_at.replace("Z", "+00:00"))
                    h = dt.hour
                    wd = dt.weekday()
                except Exception:
                    pass
            brain.on_trade_closed(
                r_multiple=float(e.r_multiple or 0.0),
                won=bool(e.won),
                setup=f"{e.direction.lower()}|{e.regime or 'unknown'}|{e.session or 'unknown'}",
                regime=e.regime or "",
                session=e.session or "",
                hour=h,
                weekday=wd,
                agents=e.agents or [],
                conf=float(conf or e.conviction or 0.6),
            )
        except Exception as err:
            print(f"[TradeJournal] brain learn error: {err}")

    def open_summary(self) -> List[Dict[str, Any]]:
        return [asdict(e) for e in self.open.values()]

    def closed_summary(self, last_n: int = 20) -> List[Dict[str, Any]]:
        return self.entries[-last_n:]

    def expectancy(self) -> Dict[str, float]:
        if not self.entries:
            return {"trades": 0, "winrate": 0.0, "expectancy_r": 0.0, "avg_r": 0.0}
        rs = [float(e.get("r_multiple") or 0) for e in self.entries]
        wins = sum(1 for r in rs if r > 0)
        return {
            "trades": len(rs),
            "winrate": round(wins / len(rs) * 100, 1),
            "expectancy_r": round(sum(rs) / len(rs), 3),
            "avg_r": round(sum(rs) / len(rs), 3),
            "total_pnl": round(sum(float(e.get("pnl") or 0) for e in self.entries), 2),
        }

    def telegram_report(self) -> str:
        exp = self.expectancy()
        open_n = len(self.open)
        lines = [
            "📓 **Trade Journal**",
            f"Closed: `{exp['trades']}` | Open: `{open_n}`",
            f"WinRate: `{exp['winrate']}%` | Exp R: `{exp['expectancy_r']}`",
            f"Total PnL: `{exp.get('total_pnl', 0):+.2f}`",
        ]
        if self.entries:
            last = self.entries[-1]
            lines.append(
                f"Last: `{last.get('symbol')}` {last.get('direction')} "
                f"R=`{last.get('r_multiple')}` {'✅' if last.get('won') else '❌'}"
            )
        return "\n".join(lines)
