"""
Agent Performance Stats + Weight Adaptation
===========================================
Tracks per-agent contribution to winning/losing trades.
Provides decayed weights that Arbiter can consume.

Usage:
    stats = AgentStats()
    stats.record_trade(["Trend", "Liquidity", "AI-LEARNER"], won=True, r=1.4)
    weights = stats.suggested_weights(base_weights)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

STATS_FILE = "agent_stats.json"


@dataclass
class AgentRecord:
    name: str
    trades: int = 0
    wins: int = 0
    total_r: float = 0.0
    last_r: float = 0.0

    @property
    def winrate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        return (self.total_r / self.trades) if self.trades else 0.0


class AgentStats:
    def __init__(self, path: str = STATS_FILE, min_trades_for_adapt: int = 12):
        self.path = path
        self.min_trades = min_trades_for_adapt
        self.agents: Dict[str, AgentRecord] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, raw in data.get("agents", {}).items():
                    self.agents[name] = AgentRecord(**raw)
            except Exception:
                pass

    def _save(self):
        try:
            payload = {
                "agents": {
                    k: {
                        "name": v.name,
                        "trades": v.trades,
                        "wins": v.wins,
                        "total_r": round(v.total_r, 4),
                        "last_r": round(v.last_r, 4),
                    }
                    for k, v in self.agents.items()
                }
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def record_trade(self, agent_names: List[str], won: bool, r: float):
        for name in agent_names:
            key = str(name).strip().upper()
            if not key:
                continue
            if key not in self.agents:
                self.agents[key] = AgentRecord(name=key)
            a = self.agents[key]
            a.trades += 1
            if won:
                a.wins += 1
            a.total_r += float(r)
            a.last_r = float(r)
        self._save()

    def suggested_weights(
        self,
        base: Optional[Dict[str, float]] = None,
        floor: float = 0.35,
        ceiling: float = 2.2,
    ) -> Dict[str, float]:
        """
        Scale base weights by relative expectancy.
        Agents with < min_trades keep base weight.
        """
        base = dict(base or {})
        if not self.agents:
            return base

        # average expectancy of agents that have enough samples
        samples = [a.expectancy for a in self.agents.values() if a.trades >= self.min_trades]
        if not samples:
            return base
        avg = sum(samples) / len(samples)
        avg = avg if abs(avg) > 1e-6 else 0.05

        out = {}
        for name, w in base.items():
            key = name.upper()
            if key in self.agents and self.agents[key].trades >= self.min_trades:
                rel = self.agents[key].expectancy / avg
                scaled = max(floor, min(ceiling, w * (0.55 + 0.45 * rel)))
                out[name] = round(scaled, 3)
            else:
                out[name] = w
        return out

    def telegram_report(self) -> str:
        if not self.agents:
            return "🤖 **Agent Stats** — no data yet"
        lines = ["🤖 **Agent Performance**"]
        ranked = sorted(self.agents.values(), key=lambda a: a.expectancy, reverse=True)
        for a in ranked:
            lines.append(
                f"• `{a.name}`: n={a.trades} WR={a.winrate:.0f}% "
                f"ExpR={a.expectancy:+.2f} last={a.last_r:+.2f}"
            )
        return "\n".join(lines)
