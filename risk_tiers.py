"""
Multi-Tier Risk Circuits
========================
Daily / weekly loss limits + max concurrent open risk in R.
Works alongside KillSwitch (hard stop) as softer progressive gates.

Tiers:
  GREEN  – normal
  YELLOW – daily loss > soft limit → reduce size 50%
  ORANGE – daily loss > hard limit OR weekly soft → block new entries
  RED    – weekly hard or concurrent R exceeded → block + alert
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Dict, List, Optional, Tuple
import json
import os

STATE_FILE = "risk_tiers_state.json"


@dataclass
class DayBucket:
    day: str  # YYYY-MM-DD
    pnl_r: float = 0.0
    trades: int = 0
    wins: int = 0


@dataclass
class RiskTierConfig:
    daily_soft_r: float = -1.5
    daily_hard_r: float = -2.5
    weekly_soft_r: float = -4.0
    weekly_hard_r: float = -6.0
    max_concurrent_r: float = 2.0
    size_mult_yellow: float = 0.5


class RiskTiers:
    def __init__(self, cfg: Optional[RiskTierConfig] = None, path: str = STATE_FILE):
        self.cfg = cfg or RiskTierConfig()
        self.path = path
        self.history: Dict[str, DayBucket] = {}
        self.open_risk_r: Dict[str, float] = {}
        self._load()

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("history", {}).items():
                    self.history[k] = DayBucket(**v)
                self.open_risk_r = data.get("open_risk_r", {})
            except Exception:
                pass

    def _save(self):
        try:
            payload = {
                "history": {k: {"day": v.day, "pnl_r": v.pnl_r, "trades": v.trades, "wins": v.wins}
                            for k, v in self.history.items()},
                "open_risk_r": self.open_risk_r,
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def record_closed(self, r_multiple: float, won: bool):
        d = self._today()
        if d not in self.history:
            self.history[d] = DayBucket(day=d)
        b = self.history[d]
        b.pnl_r += float(r_multiple)
        b.trades += 1
        if won:
            b.wins += 1
        self._save()

    def add_open_risk(self, trade_id: str, risk_r: float = 1.0):
        self.open_risk_r[trade_id] = float(risk_r)
        self._save()

    def remove_open_risk(self, trade_id: str):
        self.open_risk_r.pop(trade_id, None)
        self._save()

    def reset_daily(self):
        """Reset daily R loss counter so daily limit circuits clear."""
        d = self._today()
        if d in self.history:
            self.history[d].pnl_r = max(0.0, self.history[d].pnl_r)
        self._save()

    def reset_weekly(self):
        """Reset weekly R loss counters for the past 7 days so weekly circuits clear."""
        today = datetime.now(timezone.utc).date()
        for i in range(7):
            d = (today.fromordinal(today.toordinal() - i)).isoformat()
            if d in self.history:
                self.history[d].pnl_r = max(0.0, self.history[d].pnl_r)
        self._save()

    def reset_all(self):
        """Reset all tier history and open risk."""
        self.history.clear()
        self.open_risk_r.clear()
        self._save()

    def daily_pnl_r(self) -> float:
        b = self.history.get(self._today())
        return b.pnl_r if b else 0.0

    def weekly_pnl_r(self) -> float:
        today = datetime.now(timezone.utc).date()
        total = 0.0
        for i in range(7):
            d = (today.fromordinal(today.toordinal() - i)).isoformat()
            if d in self.history:
                total += self.history[d].pnl_r
        return total

    def concurrent_r(self) -> float:
        return sum(self.open_risk_r.values())

    def status(self) -> Tuple[str, str, float]:
        daily = self.daily_pnl_r()
        weekly = self.weekly_pnl_r()
        conc = self.concurrent_r()
        cfg = self.cfg

        if weekly <= cfg.weekly_hard_r:
            return "RED", f"Weekly loss {weekly:.2f}R <= {cfg.weekly_hard_r}R", 0.0
        if conc >= cfg.max_concurrent_r:
            return "RED", f"Concurrent risk {conc:.2f}R >= {cfg.max_concurrent_r}R", 0.0
        if daily <= cfg.daily_hard_r:
            return "ORANGE", f"Daily loss {daily:.2f}R <= {cfg.daily_hard_r}R — no new entries", 0.0
        if weekly <= cfg.weekly_soft_r:
            return "ORANGE", f"Weekly soft limit {weekly:.2f}R", 0.0
        if daily <= cfg.daily_soft_r:
            return "YELLOW", f"Daily soft {daily:.2f}R — size ×{cfg.size_mult_yellow}", cfg.size_mult_yellow
        return "GREEN", "ok", 1.0

    def allow_new_entry(self) -> Tuple[bool, str, float]:
        tier, reason, mult = self.status()
        if tier in ("ORANGE", "RED"):
            return False, f"RISK TIER {tier}: {reason}", 0.0
        return True, reason, mult

    def telegram_line(self) -> str:
        tier, reason, mult = self.status()
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(tier, "⚪")
        return (
            f"{icon} **Risk Tier {tier}** | Daily `{self.daily_pnl_r():+.2f}R` | "
            f"Week `{self.weekly_pnl_r():+.2f}R` | OpenR `{self.concurrent_r():.1f}`\n"
            f"_{reason}_ | size×`{mult}`"
        )
